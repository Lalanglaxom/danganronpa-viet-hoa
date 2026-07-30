from pathlib import Path

from dr_po_toolkit.models import POEntry
from dr_po_toolkit.po_io import load_po
from dr_po_toolkit.translator import (
    build_payload,
    build_previous_vietnamese_context,
    translate_entries_with_client,
    translate_file_with_client,
)


def _po_text(total: int = 23, translated_through: int = 21) -> str:
    blocks = []
    for number in range(1, total + 1):
        translation = f'Bản dịch {number}' if number <= translated_through else ''
        blocks.append(
            f'#. 日本語 {number}\n'
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
        assert "japanese_context" not in current
        assert all("japanese_context" not in item for item in context)

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
        assert all("japanese_context" not in entry for entry in payload["entries"])
        return {
            payload["entries"][0]["uid"]: "Bản dịch 22",
            payload["entries"][1]["uid"]: "Bản dịch 23",
        }


def test_gemini_api_payload_uses_english_source_only(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(total=1, translated_through=0), encoding="utf-8")
    entry = load_po(po_path).entries[0]

    payload = build_payload([entry])

    assert payload["entries"][0]["source_en"] == "English 1"
    assert "japanese_context" not in payload["entries"][0]
    assert "日本語" not in str(payload)


def test_interactive_gemini_api_uses_configurable_previous_twenty_context(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(), encoding="utf-8")
    po = load_po(po_path)
    client = _InteractiveFakeGeminiApiClient()

    translations, errors = translate_entries_with_client(
        po.entries[21:],
        client,  # type: ignore[arg-type]
        batch_size=20,
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
