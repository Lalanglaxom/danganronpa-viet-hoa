import tempfile
from pathlib import Path

from dr_po_toolkit import gemini_web
from dr_po_toolkit.po_io import load_po


TWO_ENTRY_PO = '''msgctxt "A"
msgid "Hello"
msgstr ""

msgctxt "B"
msgid "Goodbye"
msgstr ""
'''


class _FakeGeminiSession:
    def __init__(self, po_path: Path):
        self.po_path = po_path
        self.send_count = 0
        self.saved_marks = 0

    def send(self, prompt: str, max_wait_seconds: int = 180, stop_requested=None) -> str:
        self.send_count += 1
        if self.send_count == 1:
            assert 'msgctxt "A"' in prompt
            return 'msgctxt "A"\nmsgstr "Xin chào"'
        assert self.send_count == 2
        # The first batch must already be on disk before the second request.
        assert load_po(self.po_path).entries[0].msgstr == "Xin chào"
        assert 'msgctxt "B"' in prompt
        return 'msgctxt "B"\nmsgstr "Tạm biệt"'

    def note_entries_saved(self) -> None:
        self.saved_marks += 1

    def recover_after_error(self) -> None:
        pass

    def refresh_page(self) -> None:
        pass


def test_gemini_web_saves_each_batch_then_waits_at_least_2_5_seconds():
    with tempfile.TemporaryDirectory() as tmp:
        po_path = Path(tmp) / "sample.po"
        po_path.write_text(TWO_ENTRY_PO, encoding="utf-8")
        session = _FakeGeminiSession(po_path)
        delays: list[float] = []
        original_sleep = gemini_web.sleep_with_stop
        gemini_web.sleep_with_stop = lambda seconds, stop_requested=None, interval=0.2: delays.append(float(seconds))
        try:
            result = gemini_web.translate_po_file_via_web(
                session,
                po_path,
                max_entries_per_batch=1,
                max_lines_per_batch=600,
                wait_between_batches=0.0,
                rename_folder=False,
                retry_count=0,
            )
        finally:
            gemini_web.sleep_with_stop = original_sleep

        saved = load_po(po_path)
        assert [entry.msgstr for entry in saved.entries] == ["Xin chào", "Tạm biệt"]
        assert result.translated == 2
        assert session.saved_marks == 2
        assert len(delays) == 1
        assert abs(delays[0] - 2.5) < 0.001


def test_gemini_session_waits_remaining_time_after_entries_are_saved():
    session = gemini_web.GeminiWebSession()
    session._last_entries_saved_at = 100.0
    delays: list[float] = []
    original_monotonic = gemini_web.time.monotonic
    original_sleep = gemini_web.sleep_with_stop
    gemini_web.time.monotonic = lambda: 100.4
    gemini_web.sleep_with_stop = lambda seconds, stop_requested=None, interval=0.2: delays.append(float(seconds))
    try:
        session._wait_after_saved_entries()
    finally:
        gemini_web.time.monotonic = original_monotonic
        gemini_web.sleep_with_stop = original_sleep

    assert len(delays) == 1
    assert abs(delays[0] - 2.1) < 0.001
    assert session._last_entries_saved_at is None
