from pathlib import Path

from dr_po_toolkit.po_io import load_po
from dr_po_toolkit.translator import translate_entries_with_client, translate_file_with_client


def _po_text() -> str:
    blocks = []
    for number in range(1, 8):
        translation = f'Bản dịch {number}' if number <= 5 else ''
        blocks.append(
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

        if len(self.payloads) == 1:
            assert current["source_en"] == "English 6"
            assert [item["translation_vi"] for item in context] == [
                "Bản dịch 1",
                "Bản dịch 2",
                "Bản dịch 3",
                "Bản dịch 4",
                "Bản dịch 5",
            ]
            assert [item["relative_position"] for item in context] == [-5, -4, -3, -2, -1]
            return {current["uid"]: "Bản dịch 6"}

        assert len(self.payloads) == 2
        assert current["source_en"] == "English 7"
        assert [item["translation_vi"] for item in context] == [
            "Bản dịch 2",
            "Bản dịch 3",
            "Bản dịch 4",
            "Bản dịch 5",
            "Bản dịch 6",
        ]
        return {current["uid"]: "Bản dịch 7"}


class _MassTranslateFakeGeminiApiClient:
    prompt = "test prompt\n"

    def __init__(self):
        self.payloads: list[dict] = []

    def translate_payload(self, payload: dict, prompt: str | None = None) -> dict[str, str]:
        self.payloads.append(payload)
        assert len(payload["entries"]) == 2
        assert payload["previous_vietnamese_context"] == []
        return {
            payload["entries"][0]["uid"]: "Bản dịch 6",
            payload["entries"][1]["uid"]: "Bản dịch 7",
        }


def test_interactive_gemini_api_translates_one_entry_and_uses_previous_five_context(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(), encoding="utf-8")
    po = load_po(po_path)
    client = _InteractiveFakeGeminiApiClient()

    translations, errors = translate_entries_with_client(
        po.entries[5:],
        client,  # type: ignore[arg-type]
        batch_size=20,
        sleep_seconds=0,
        context_entries=po.entries,
    )

    assert not errors
    assert len(client.payloads) == 2
    assert translations == {
        po.entries[5].uid: "Bản dịch 6",
        po.entries[6].uid: "Bản dịch 7",
    }


def test_mass_translate_file_keeps_configured_batch_size(tmp_path: Path):
    po_path = tmp_path / "sample.po"
    po_path.write_text(_po_text(), encoding="utf-8")
    client = _MassTranslateFakeGeminiApiClient()

    changed, errors = translate_file_with_client(
        po_path,
        client,  # type: ignore[arg-type]
        batch_size=20,
        sleep_seconds=0,
    )

    assert not errors
    assert changed == 2
    assert len(client.payloads) == 1
    assert [entry.msgstr for entry in load_po(po_path).entries] == [
        "Bản dịch 1",
        "Bản dịch 2",
        "Bản dịch 3",
        "Bản dịch 4",
        "Bản dịch 5",
        "Bản dịch 6",
        "Bản dịch 7",
    ]
