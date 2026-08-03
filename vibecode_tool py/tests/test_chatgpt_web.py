from pathlib import Path

import pytest

from dr_po_toolkit import chatgpt_web, gemini_web
from dr_po_toolkit.po_io import load_po


ONE_ENTRY_PO = '''msgctxt "A | MAKOTO NAEGI"
msgid "Hello"
msgstr ""
'''


class _FakeChatGPTSession:
    provider_name = "ChatGPT"
    allow_page_refresh_retry = False
    allow_retry_after_response = False

    def __init__(self, cdp_url: str = ""):
        self.cdp_url = cdp_url
        self.send_count = 0
        self.saved_marks = 0
        self.recover_count = 0
        self.refresh_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def send(self, prompt: str, max_wait_seconds: int = 180, stop_requested=None) -> str:
        self.send_count += 1
        assert "ChatGPT memory and custom instructions" in prompt
        assert 'msgctxt "A | MAKOTO NAEGI"' in prompt
        return '```po\nmsgctxt "A | MAKOTO NAEGI"\nmsgstr "Xin chào"\n```'

    def note_entries_saved(self) -> None:
        self.saved_marks += 1

    def recover_after_error(self) -> None:
        self.recover_count += 1

    def refresh_page(self) -> None:
        self.refresh_count += 1


def test_chatgpt_web_path_uses_chatgpt_prompt_and_shared_po_writer(tmp_path: Path, monkeypatch):
    po_path = tmp_path / "sample.po"
    po_path.write_text(ONE_ENTRY_PO, encoding="utf-8")
    sessions: list[_FakeChatGPTSession] = []

    class SessionFactory(_FakeChatGPTSession):
        def __init__(self, cdp_url: str = ""):
            super().__init__(cdp_url)
            sessions.append(self)

    monkeypatch.setattr(chatgpt_web, "ChatGPTWebSession", SessionFactory)

    result = chatgpt_web.run_chatgpt_web_path(
        po_path,
        rename_duplicates=False,
        create_missing_backups=False,
        rename_folders=False,
        retry_count=0,
    )

    assert result.total_translated == 1
    assert load_po(po_path).entries[0].msgstr == "Xin chào"
    assert len(sessions) == 1
    assert sessions[0].send_count == 1
    assert sessions[0].saved_marks == 1
    assert sessions[0].refresh_count == 0


class _MalformedChatGPTSession(_FakeChatGPTSession):
    def send(self, prompt: str, max_wait_seconds: int = 180, stop_requested=None) -> str:
        self.send_count += 1
        return "I translated it, but forgot the requested PO block."


def test_chatgpt_unparseable_response_is_not_resent(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(ONE_ENTRY_PO, encoding="utf-8")
    session = _MalformedChatGPTSession()

    with pytest.raises(gemini_web.PromptAlreadySentError):
        gemini_web.translate_po_file_via_web(
            session,
            po_path,
            rename_folder=False,
            retry_count=9,
            prompt_template=chatgpt_web.CHATGPT_TRANSLATE_PROMPT_TEMPLATE,
        )

    assert session.send_count == 1
    assert session.recover_count == 0
    assert session.refresh_count == 0
    assert load_po(po_path).entries[0].msgstr == ""
    assert "forgot the requested PO block" in po_path.with_name("sample_translated.txt").read_text(encoding="utf-8")


def test_chatgpt_session_disables_refresh_and_post_response_retries():
    assert chatgpt_web.ChatGPTWebSession.provider_name == "ChatGPT"
    assert chatgpt_web.ChatGPTWebSession.allow_page_refresh_retry is False
    assert chatgpt_web.ChatGPTWebSession.allow_retry_after_response is False


def test_chatgpt_embedded_javascript_keeps_newline_escapes():
    captured: list[str] = []

    class FakePage:
        def evaluate(self, source: str, _payload=None):
            captured.append(source)
            return None

    chatgpt_web.ChatGPTWebSession._set_prompt_with_dom_paste(FakePage(), "line 1\nline 2")

    assert captured
    assert r"split('\n')" in captured[0]


def test_response_snapshot_detects_replaced_turn_when_count_is_unchanged():
    before = [
        {"key": "assistant:0", "text": "Old response A"},
        {"key": "assistant:1", "text": "Old response B"},
    ]
    current = [
        {"key": "assistant:0", "text": "Old response A"},
        {"key": "assistant:1", "text": 'msgctxt "A"\nmsgstr "Xin chào"'},
    ]

    assert chatgpt_web.ChatGPTWebSession._select_new_assistant_text(before, current) == current[-1]["text"]


def test_response_snapshot_ignores_unchanged_history():
    turns = [
        {"key": "turn-a", "text": "Old response A"},
        {"key": "turn-b", "text": "Old response B"},
    ]

    assert chatgpt_web.ChatGPTWebSession._select_new_assistant_text(turns, list(turns)) == ""


def test_response_wait_uses_full_configured_timeout_before_no_output(monkeypatch):
    session = chatgpt_web.ChatGPTWebSession()
    clock = {"value": 0.0}

    monkeypatch.setattr(chatgpt_web.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(chatgpt_web, "sleep_with_stop", lambda seconds, _stop=None: clock.__setitem__("value", clock["value"] + seconds))
    monkeypatch.setattr(session, "_raise_if_rate_limited", lambda _page: None)
    monkeypatch.setattr(session, "_latest_new_response", lambda _page, _before: "")
    monkeypatch.setattr(session, "_is_generation_running", lambda _page: False)
    monkeypatch.setattr(session, "_click_stop", lambda _page: False)

    with pytest.raises(TimeoutError, match=r"configured timeout \(30 seconds\)"):
        session._wait_for_response(
            object(),
            before_assistant_turns=[],
            max_wait_seconds=30,
        )

    assert clock["value"] >= 30.0


def test_response_wait_returns_replaced_turn_after_it_stabilizes(monkeypatch):
    session = chatgpt_web.ChatGPTWebSession()
    clock = {"value": 0.0}
    response = 'msgctxt "A"\nmsgstr "Xin chào"'

    monkeypatch.setattr(chatgpt_web.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(chatgpt_web, "sleep_with_stop", lambda seconds, _stop=None: clock.__setitem__("value", clock["value"] + seconds))
    monkeypatch.setattr(session, "_raise_if_rate_limited", lambda _page: None)
    monkeypatch.setattr(session, "_latest_new_response", lambda _page, _before: response)
    monkeypatch.setattr(session, "_is_generation_running", lambda _page: False)
    monkeypatch.setattr(session, "_click_stop", lambda _page: False)

    actual = session._wait_for_response(
        object(),
        before_assistant_turns=[{"key": "assistant:0", "text": "Old"}],
        max_wait_seconds=30,
    )

    assert actual == response
    assert clock["value"] >= 4.0


def test_rate_limit_popup_clicks_only_safe_dismiss_controls():
    captured: list[str] = []

    class FakePage:
        def evaluate(self, source: str):
            captured.append(source)
            return {
                "detected": True,
                "dismissed": True,
                "text": "Too many requests. Try again later.",
            }

    status = chatgpt_web.ChatGPTWebSession._rate_limit_popup_status(FakePage())

    assert status["detected"] is True
    assert status["dismissed"] is True
    assert captured
    assert "safeCloseControl.click()" in captured[0]
    assert "try again|retry|resend|submit|send" in captured[0]
    assert r"(^|\b)(close|dismiss|cancel|okay|ok|got it)(\b|$)" in captured[0]


def test_rate_limit_popup_is_dismissed_then_reported():
    session = chatgpt_web.ChatGPTWebSession()

    class FakeKeyboard:
        def __init__(self):
            self.keys: list[str] = []

        def press(self, key: str):
            self.keys.append(key)

    class FakePage:
        def __init__(self):
            self.keyboard = FakeKeyboard()

        def evaluate(self, _source: str):
            return {
                "detected": True,
                "dismissed": True,
                "text": "Too many requests",
            }

    page = FakePage()
    with pytest.raises(RuntimeError, match="popup was dismissed"):
        session._raise_if_rate_limited(page)

    assert page.keyboard.keys == []


def test_rate_limit_popup_uses_escape_when_no_safe_close_control():
    session = chatgpt_web.ChatGPTWebSession()

    class FakeKeyboard:
        def __init__(self):
            self.keys: list[str] = []

        def press(self, key: str):
            self.keys.append(key)

    class FakePage:
        def __init__(self):
            self.keyboard = FakeKeyboard()
            self.waits: list[int] = []

        def evaluate(self, _source: str):
            return {
                "detected": True,
                "dismissed": False,
                "text": "You've reached the current usage cap",
            }

        def wait_for_timeout(self, milliseconds: int):
            self.waits.append(milliseconds)

    page = FakePage()
    with pytest.raises(RuntimeError, match="No safe close control"):
        session._raise_if_rate_limited(page)

    assert page.keyboard.keys == ["Escape"]
    assert page.waits == [250]


def test_rate_limit_popup_clicks_only_safe_dismiss_controls():
    captured: list[str] = []

    class FakePage:
        def evaluate(self, source: str):
            captured.append(source)
            return {
                "detected": True,
                "dismissed": True,
                "text": "Too many requests. Try again later.",
            }

    status = chatgpt_web.ChatGPTWebSession._rate_limit_popup_status(FakePage())

    assert status["detected"] is True
    assert status["dismissed"] is True
    assert captured
    assert "safeCloseControl.click()" in captured[0]
    assert "try again|retry|resend|submit|send" in captured[0]


def test_rate_limit_popup_is_dismissed_then_reported():
    session = chatgpt_web.ChatGPTWebSession()

    class FakeKeyboard:
        def __init__(self):
            self.keys: list[str] = []

        def press(self, key: str):
            self.keys.append(key)

    class FakePage:
        def __init__(self):
            self.keyboard = FakeKeyboard()

        def evaluate(self, _source: str):
            return {
                "detected": True,
                "dismissed": True,
                "text": "Too many requests",
            }

    page = FakePage()
    with pytest.raises(RuntimeError, match="popup was dismissed"):
        session._raise_if_rate_limited(page)

    assert page.keyboard.keys == []


def test_rate_limit_popup_uses_escape_when_no_safe_close_control():
    session = chatgpt_web.ChatGPTWebSession()

    class FakeKeyboard:
        def __init__(self):
            self.keys: list[str] = []

        def press(self, key: str):
            self.keys.append(key)

    class FakePage:
        def __init__(self):
            self.keyboard = FakeKeyboard()
            self.waits: list[int] = []

        def evaluate(self, _source: str):
            return {
                "detected": True,
                "dismissed": False,
                "text": "You've reached the current usage cap",
            }

        def wait_for_timeout(self, milliseconds: int):
            self.waits.append(milliseconds)

    page = FakePage()
    with pytest.raises(RuntimeError, match="No safe close control"):
        session._raise_if_rate_limited(page)

    assert page.keyboard.keys == ["Escape"]
    assert page.waits == [250]
