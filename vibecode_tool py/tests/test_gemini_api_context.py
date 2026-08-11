import json
from pathlib import Path

from dr_po_toolkit.models import POEntry
from dr_po_toolkit.po_io import load_po
from dr_po_toolkit.config import load_config
from dr_po_toolkit.translator import (
    API_SYSTEM_INSTRUCTIONS,
    GeminiApiClient,
    SYSTEM_INSTRUCTIONS,
    build_api_prompt,
    build_payload,
    build_previous_vietnamese_context,
    build_prompt,
    _thinking_config,
    parse_api_translation_response,
    translate_entries_with_client,
    translate_file_with_client,
)


def _po_text(total: int = 23, translated_through: int = 21) -> str:
    blocks = []
    for number in range(1, total + 1):
        translation = f'Bản dịch {number}' if number <= translated_through else ''
        blocks.append(
            f'#. <CLT 4>日本語 {number}\n'
            f'#. <CLT>\n'
            f'msgctxt "{number:04d} | MAKOTO NAEGI"\n'
            f'msgid "English {number}"\n'
            f'msgstr "{translation}"'
        )
    return "\n\n".join(blocks) + "\n"


class _InteractiveFakeGeminiApiClient:
    prompt = "test prompt\n"

    def __init__(self):
        self.payloads: list[dict] = []

    def translate_payload(self, payload: dict, prompt: str | None = None) -> dict[str, str]:
        self.payloads.append(payload)
        assert len(payload["entries"]) == 1
        current = payload["entries"][0]
        context = payload["previous_vietnamese_context"]
        assert current["japanese_hint_if_english_ambiguous"].startswith("日本語")
        assert "<CLT" not in current["japanese_hint_if_english_ambiguous"]
        assert all("japanese_hint_if_english_ambiguous" not in item for item in context)

        if len(self.payloads) == 1:
            assert current["source_en"] == "English 22"
            assert [item["source_en"] for item in context] == [f"English {number}" for number in range(2, 22)]
            assert [item["translation_vi"] for item in context] == [f"Bản dịch {number}" for number in range(2, 22)]
            assert [item["relative_position"] for item in context] == list(range(-20, 0))
            return {current["uid"]: "Bản dịch 22"}

        assert len(self.payloads) == 2
        assert current["source_en"] == "English 23"
        assert [item["source_en"] for item in context] == [f"English {number}" for number in range(3, 23)]
        assert [item["translation_vi"] for item in context] == [f"Bản dịch {number}" for number in range(3, 23)]
        return {current["uid"]: "Bản dịch 23"}


class _MassTranslateFakeGeminiApiClient:
    prompt = "test prompt\n"

    def __init__(self):
        self.payloads: list[dict] = []

    def translate_payload(self, payload: dict, prompt: str | None = None) -> dict[str, str]:
        self.payloads.append(payload)
        assert len(payload["entries"]) == 2
        assert payload["previous_vietnamese_context"] == []
        assert all("japanese_hint_if_english_ambiguous" in entry for entry in payload["entries"])
        return {
            payload["entries"][0]["uid"]: "Bản dịch 22",
            payload["entries"][1]["uid"]: "Bản dịch 23",
        }


def test_gemini_api_payload_keeps_english_authoritative_and_japanese_as_hint(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(total=1, translated_through=0), encoding="utf-8")
    entry = load_po(po_path).entries[0]

    payload = build_payload([entry])
    item = payload["entries"][0]

    assert item["source_en"] == "English 1"
    assert item["japanese_hint_if_english_ambiguous"] == "日本語 1"
    assert "absolute source of truth" in payload["instructions"]
    assert "only when the English is genuinely unclear or ambiguous" in payload["instructions"]
    assert "English `en` is absolute source truth" in API_SYSTEM_INSTRUCTIONS
    assert "Consult `ja` only when English is genuinely ambiguous" in API_SYSTEM_INSTRUCTIONS


def test_compact_api_prompt_avoids_duplicate_schema_and_uids(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(total=3, translated_through=2), encoding="utf-8")
    po = load_po(po_path)
    context = build_previous_vietnamese_context(po.entries, po.entries[2], limit=2)
    payload = build_payload([po.entries[2]], previous_vietnamese_context=context)

    compact = build_api_prompt(payload)
    verbose = build_prompt(payload)
    request = json.loads(compact)

    assert len(compact) < len(verbose) * 0.35
    assert payload["entries"][0]["uid"] not in compact
    assert "required_response_schema" not in compact
    assert "instructions" not in compact
    assert request["e"] == [{"en": "English 3", "ja": "日本語 3", "sp": "MAKOTO NAEGI"}]
    assert [item["en"] for item in request["c"]] == ["English 1", "English 2"]
    assert [item["vi"] for item in request["c"]] == ["Bản dịch 1", "Bản dịch 2"]


def test_api_response_uses_order_when_gemini_echoes_wrong_uids():
    expected = ["00001|A", "00002|B"]
    response = {
        "entries": [
            {"uid": "wrong-A", "translation": "Một"},
            {"uid": "wrong-B", "translation": "Hai"},
        ]
    }

    assert parse_api_translation_response(response, expected) == {
        "00001|A": "Một",
        "00002|B": "Hai",
    }
    assert parse_api_translation_response({"t": ["Một", "Hai"]}, expected) == {
        "00001|A": "Một",
        "00002|B": "Hai",
    }


def test_api_response_honours_correct_uids_when_batch_is_shuffled():
    expected = ["00001|A", "00002|B"]
    response = {
        "entries": [
            {"uid": "00002|B", "translation": "Hai"},
            {"uid": "00001|A", "translation": "Một"},
        ]
    }
    assert parse_api_translation_response(response, expected) == {
        "00001|A": "Một",
        "00002|B": "Hai",
    }


def test_gemini_client_sends_compact_request_once_and_normalizes_bad_uid():
    calls: list[dict] = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            usage = type(
                "Usage",
                (),
                {
                    "prompt_token_count": 30,
                    "candidates_token_count": 8,
                    "thoughts_token_count": 0,
                    "cached_content_token_count": 0,
                    "total_token_count": 38,
                },
            )()
            return type(
                "Response",
                (),
                {"text": '{"entries":[{"uid":"mismatch","translation":"Xin chào"}]}', "usage_metadata": usage},
            )()

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

        @staticmethod
        def ThinkingConfig(**kwargs):
            return kwargs

    client = GeminiApiClient.__new__(GeminiApiClient)
    client._client = type("Client", (), {"models": FakeModels()})()
    client._types = FakeTypes
    client.model = "gemini-2.5-flash"
    client.prompt = SYSTEM_INSTRUCTIONS
    client.thinking_mode = "off"
    client.max_output_tokens = 0
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="Hello", msgstr="", extracted_comments=["こんにちは"])
    payload = build_payload([entry])

    result = client.translate_payload(payload)

    assert result == {entry.uid: "Xin chào"}
    assert len(calls) == 1
    assert calls[0]["contents"] == build_api_prompt(payload)
    assert entry.uid not in calls[0]["contents"]
    assert calls[0]["config"]["system_instruction"] == API_SYSTEM_INSTRUCTIONS.strip()
    assert calls[0]["config"]["temperature"] == 0
    assert calls[0]["config"]["thinking_config"] == {"thinking_budget": 0}
    assert "response_schema" not in calls[0]["config"]
    assert calls[0]["config"]["response_json_schema"]["properties"]["t"]["minItems"] == 1
    assert "additionalProperties" not in calls[0]["config"]["response_json_schema"]
    assert calls[0]["config"]["max_output_tokens"] >= 256
    assert client.total_usage.as_dict() == {
        "requests": 1,
        "prompt_tokens": 30,
        "candidate_tokens": 8,
        "thought_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 38,
    }


def test_gemini_client_retries_without_schema_on_additional_properties_400():
    calls: list[dict] = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError(
                    "400 INVALID_ARGUMENT: Invalid JSON payload received. Unknown name "
                    "'additional_properties' at 'generation_config.response_schema': Cannot find field."
                )
            return type("Response", (), {"text": '{"t":["Xin chào"]}'})()

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

        @staticmethod
        def ThinkingConfig(**kwargs):
            return kwargs

    client = GeminiApiClient.__new__(GeminiApiClient)
    client._client = type("Client", (), {"models": FakeModels()})()
    client._types = FakeTypes
    client.model = "gemini-2.5-flash"
    client.prompt = SYSTEM_INSTRUCTIONS
    client.timeout_seconds = 5
    client.thinking_mode = "off"
    client.max_output_tokens = 0
    client.last_usage = None
    client.total_usage = type("UsageTotal", (), {"add": lambda self, other: None})()
    client._usage_lock = __import__("threading").Lock()
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="Hello", msgstr="")

    result = client.translate_payload(build_payload([entry]))

    assert result == {entry.uid: "Xin chào"}
    assert len(calls) == 2
    assert "response_json_schema" in calls[0]["config"]
    assert "additionalProperties" not in calls[0]["config"]["response_json_schema"]
    assert "response_json_schema" not in calls[1]["config"]
    assert "response_schema" not in calls[1]["config"]
    assert calls[1]["config"]["response_mime_type"] == "application/json"


def test_incomplete_gemini_response_explains_finish_reason_on_missing_entry():
    class FakeModels:
        def generate_content(self, **_kwargs):
            candidate = type("Candidate", (), {"finish_reason": "MAX_TOKENS", "finish_message": "Output limit reached"})()
            return type(
                "Response",
                (),
                {"text": '{"t":["Một"]}', "candidates": [candidate]},
            )()

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

        @staticmethod
        def ThinkingConfig(**kwargs):
            return kwargs

    client = GeminiApiClient.__new__(GeminiApiClient)
    client._client = type("Client", (), {"models": FakeModels()})()
    client._types = FakeTypes
    client.model = "gemini-2.5-flash"
    client.prompt = SYSTEM_INSTRUCTIONS
    client.timeout_seconds = 5
    client.thinking_mode = "off"
    client.max_output_tokens = 0
    entries = [
        POEntry(index=0, msgctxt="0001", msgid="One", msgstr=""),
        POEntry(index=1, msgctxt="0002", msgid="Two", msgstr=""),
    ]

    translations, errors = translate_entries_with_client(
        entries,
        client,
        batch_size=2,
        sleep_seconds=0,
    )

    # An incomplete order-only response cannot be mapped safely, so the batch
    # is rejected instead of assigning a translation to the wrong entry.
    assert translations == {}
    assert len(errors) == 2
    assert {error.uid for error in errors} == {entry.uid for entry in entries}
    assert all("finish_reason=MAX_TOKENS" in error.reason for error in errors)
    assert all("items=1" in error.reason for error in errors)
    assert all("mapped=0/2" in error.reason for error in errors)


def test_gemini_three_uses_lowest_supported_thinking_level():
    class FakeTypes:
        @staticmethod
        def ThinkingConfig(**kwargs):
            return kwargs

    assert _thinking_config(FakeTypes, "gemini-3.5-flash", "off") == {"thinking_level": "minimal"}
    assert _thinking_config(FakeTypes, "gemini-3.1-flash-lite", "minimal") == {"thinking_level": "minimal"}
    assert _thinking_config(FakeTypes, "gemini-3.1-pro", "off") == {"thinking_level": "low"}
    assert _thinking_config(FakeTypes, "gemini-3.1-pro", "minimal") == {"thinking_level": "low"}
    assert _thinking_config(FakeTypes, "gemini-3.5-flash", "medium") == {"thinking_level": "medium"}
    assert _thinking_config(FakeTypes, "gemini-3.5-flash", "dynamic") is None


def test_interactive_gemini_api_uses_configurable_previous_twenty_context(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(), encoding="utf-8")
    po = load_po(po_path)
    client = _InteractiveFakeGeminiApiClient()

    translations, errors = translate_entries_with_client(
        po.entries[21:],
        client,  # type: ignore[arg-type]
        batch_size=1,
        sleep_seconds=0,
        context_entries=po.entries,
        context_limit=20,
    )

    assert not errors
    assert len(client.payloads) == 2
    assert translations == {
        po.entries[21].uid: "Bản dịch 22",
        po.entries[22].uid: "Bản dịch 23",
    }


def test_mass_translate_file_keeps_configured_batch_size_when_context_disabled(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(), encoding="utf-8")
    client = _MassTranslateFakeGeminiApiClient()

    changed, errors = translate_file_with_client(
        po_path,
        client,  # type: ignore[arg-type]
        batch_size=20,
        sleep_seconds=0,
        context_limit=0,
    )

    assert not errors
    assert changed == 2
    assert len(client.payloads) == 1
    assert [entry.msgstr for entry in load_po(po_path).entries[-2:]] == ["Bản dịch 22", "Bản dịch 23"]


def test_mass_translation_batches_current_entries_and_sends_context_once_per_batch(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(total=8, translated_through=2), encoding="utf-8")

    class ContextBatchClient:
        prompt = "test prompt\n"

        def __init__(self):
            self.payloads: list[dict] = []

        def translate_payload(self, payload: dict, prompt: str | None = None) -> dict[str, str]:
            self.payloads.append(payload)
            return {
                item["uid"]: f"Dịch {item['source_en'].split()[-1]}"
                for item in payload["entries"]
            }

    client = ContextBatchClient()
    changed, errors = translate_file_with_client(
        po_path,
        client,  # type: ignore[arg-type]
        batch_size=3,
        sleep_seconds=0,
        context_limit=2,
    )

    assert not errors
    assert changed == 6
    assert len(client.payloads) == 2
    assert [[item["source_en"] for item in payload["entries"]] for payload in client.payloads] == [
        ["English 3", "English 4", "English 5"],
        ["English 6", "English 7", "English 8"],
    ]
    assert [item["source_en"] for item in client.payloads[0]["previous_vietnamese_context"]] == [
        "English 1",
        "English 2",
    ]
    assert [item["source_en"] for item in client.payloads[1]["previous_vietnamese_context"]] == [
        "English 4",
        "English 5",
    ]
    assert [item["translation_vi"] for item in client.payloads[1]["previous_vietnamese_context"]] == [
        "Dịch 4",
        "Dịch 5",
    ]


def test_legacy_shared_api_settings_migrate_to_both_profiles(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "gemini_api_model": "gemini-2.5-flash-lite",
                "gemini_api_timeout_seconds": 45,
                "gemini_api_context_entries": 7,
                "gemini_api_context_across_files": True,
                "gemini_api_sleep_seconds": 2.5,
                "gemini_api_use_key": True,
                "gemini_web_max_entries": 17,
                "gemini_web_max_files": 11,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["gemini_api_single_model"] == "gemini-2.5-flash-lite"
    assert config["gemini_api_mass_model"] == "gemini-2.5-flash-lite"
    assert config["gemini_api_single_timeout_seconds"] == 45
    assert config["gemini_api_mass_timeout_seconds"] == 45
    assert config["gemini_api_single_context_entries"] == 7
    assert config["gemini_api_mass_context_entries"] == 7
    assert config["gemini_api_single_context_across_files"] is True
    assert config["gemini_api_mass_context_across_files"] is True
    assert config["gemini_api_single_sleep_seconds"] == 2.5
    assert config["gemini_api_mass_sleep_seconds"] == 2.5
    assert config["gemini_api_mass_batch_entries"] == 17
    assert config["gemini_api_mass_max_files"] == 11
    assert config["gemini_translate_mode"] == "api"
    assert "gemini_api_use_key" not in config
    assert "gemini_api_model" not in config
    assert "gemini_api_context_entries" not in config


def test_previous_context_keeps_english_sentences_when_translation_is_missing(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(total=5, translated_through=2), encoding="utf-8")
    po = load_po(po_path)

    context = build_previous_vietnamese_context(po.entries, po.entries[4], limit=4)

    assert [item["source_en"] for item in context] == ["English 1", "English 2", "English 3", "English 4"]
    assert [item["translation_vi"] for item in context] == ["Bản dịch 1", "Bản dịch 2", "", ""]


def test_previous_file_context_is_opt_in_and_only_fills_missing_local_slots():
    previous_entries = [
        POEntry(index=index, msgctxt=f"P{index}", msgid=f"Previous {index}", msgstr=f"Dịch trước {index}")
        for index in range(1, 5)
    ]
    current_entries = [
        POEntry(index=index, msgctxt=f"C{index}", msgid=f"Current {index}", msgstr=f"Dịch hiện tại {index}")
        for index in range(1, 6)
    ]
    current_entry = current_entries[2]

    same_file_only = build_previous_vietnamese_context(current_entries, current_entry, limit=5)
    across_files = build_previous_vietnamese_context(
        current_entries,
        current_entry,
        limit=5,
        previous_file_entries=previous_entries,
    )

    assert [item["source_en"] for item in same_file_only] == ["Current 1", "Current 2"]
    assert [item["source_en"] for item in across_files] == [
        "Previous 2",
        "Previous 3",
        "Previous 4",
        "Current 1",
        "Current 2",
    ]
    assert [item["relative_position"] for item in across_files] == [-5, -4, -3, -2, -1]


def test_gemini_client_configures_http_timeout_in_milliseconds(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    captured: dict[str, object] = {}

    class FakeHttpOptions:
        def __init__(self, **kwargs):
            captured["http_options_kwargs"] = kwargs

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.models = SimpleNamespace()

    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")
    genai_module.Client = FakeClient  # type: ignore[attr-defined]
    genai_module.types = SimpleNamespace(HttpOptions=FakeHttpOptions)  # type: ignore[attr-defined]
    google_module.genai = genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)

    client = GeminiApiClient(api_key="test-key", timeout_seconds=12.5)

    assert client.timeout_seconds == 12.5
    assert captured["http_options_kwargs"] == {"timeout": 12500}
    client_kwargs = captured["client_kwargs"]
    assert isinstance(client_kwargs, dict)
    assert client_kwargs["api_key"] == "test-key"
    assert isinstance(client_kwargs["http_options"], FakeHttpOptions)


def test_gemini_timeout_error_is_clear():
    class FakeModels:
        def generate_content(self, **_kwargs):
            raise RuntimeError("read timeout")

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

    client = GeminiApiClient.__new__(GeminiApiClient)
    client._client = type("Client", (), {"models": FakeModels()})()
    client._types = FakeTypes
    client.model = "fake-model"
    client.prompt = SYSTEM_INSTRUCTIONS
    client.timeout_seconds = 7
    entry = POEntry(index=0, msgctxt="0001", msgid="Hello", msgstr="")

    import pytest

    with pytest.raises(TimeoutError, match=r"timed out after 7 seconds"):
        client.translate_payload(build_payload([entry]))


def test_gemini_client_enforces_wall_clock_deadline_even_if_sdk_hangs():
    import time
    import pytest

    class FakeModels:
        def generate_content(self, **_kwargs):
            time.sleep(0.25)
            return type("Response", (), {"text": '{"t":["Xin chào"]}'})()

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

    client = GeminiApiClient.__new__(GeminiApiClient)
    client._client = type("Client", (), {"models": FakeModels()})()
    client._types = FakeTypes
    client.model = "fake-model"
    client.prompt = SYSTEM_INSTRUCTIONS
    client.timeout_seconds = 0.01
    entry = POEntry(index=0, msgctxt="0001", msgid="Hello", msgstr="")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match=r"timed out after 0.01 seconds"):
        client.translate_payload(build_payload([entry]))
    assert time.monotonic() - started < 0.15
