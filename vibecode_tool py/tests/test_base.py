from pathlib import Path

from dr_po_toolkit.po_io import dump_po, parse_po_text
from dr_po_toolkit.rules import ReplacementRule, apply_rules_to_po
from dr_po_toolkit.translator import build_payload, parse_translation_response, validate_translations

SAMPLE = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#. <CLT 4>テスト
#. <CLT>
msgctxt "0001 | MAKOTO NAEGI"
msgid ""
"<CLT 4>Hello...\\n"
"<CLT>"
msgstr ""
"<CLT 4>Tôi chào...\\n"
"<CLT>"
'''


def test_parse_dump_roundtrip():
    po = parse_po_text(SAMPLE)
    assert not [i for i in po.issues if i.level == "ERROR"]
    assert len(po.entries) == 1
    assert po.entries[0].msgid == "<CLT 4>Hello...\n<CLT>"
    out = dump_po(po)
    po2 = parse_po_text(out)
    assert po2.entries[0].msgstr == po.entries[0].msgstr


def test_rules_msgstr_only():
    po = parse_po_text(SAMPLE)
    rule = ReplacementRule(id="r1", speaker="MAKOTO", find="Tôi", replace="Tớ", whole_word=True)
    changes = apply_rules_to_po(po, [rule], Path("x.po"))
    assert len(changes) == 1
    assert "Tớ" in po.entries[0].msgstr
    assert "Hello" in po.entries[0].msgid


def test_translation_json_validation():
    po = parse_po_text(SAMPLE)
    entry = po.entries[0]
    payload = build_payload([entry])
    assert payload["entries"][0]["uid"] == entry.uid
    response = {"entries": [{"uid": entry.uid, "translation": "<CLT 4>Xin chào...\n<CLT>"}]}
    parsed = parse_translation_response(response)
    assert not validate_translations([entry], parsed)


def test_normalize_duplicate_names_skips_copy_po():
    import tempfile
    from dr_po_toolkit.cleanup import normalize_duplicate_names

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        copy = root / "e01 - Copy (1).po"
        copy.write_text("copy", encoding="utf-8")
        work = root / "e01 (1).po"
        work.write_text("work", encoding="utf-8")

        changes = normalize_duplicate_names(root)

        assert copy.exists()
        assert not (root / "e01 - Copy.po").exists()
        assert (root / "e01.po").exists()
        assert any(c.skipped and c.reason == "Copy.po protected" for c in changes)


def test_gemini_web_parse_msgctxt_msgstr_only_html_escaped():
    from dr_po_toolkit.gemini_web import parse_translated_po_response

    response = '''<code data-test-id="code-content">msgctxt "M_057_000_156"
msgstr "&lt;CLT 4&gt;Một cảm giác bất an dâng trào trong tôi.&lt;CLT&gt;"

msgctxt "M_057_000_157"
msgstr "Hina đã mất bình tĩnh..."
</code>'''
    parsed = parse_translated_po_response(response)
    assert parsed["M_057_000_156"] == "<CLT 4>Một cảm giác bất an dâng trào trong tôi.<CLT>"
    assert parsed["M_057_000_157"] == "Hina đã mất bình tĩnh..."


def test_gemini_web_parse_full_po_response():
    from dr_po_toolkit.gemini_web import parse_translated_po_response

    response = '''```po
msgctxt "0001 | MAKOTO NAEGI"
msgid "What is this place...?"
msgstr "Nơi này là gì...?"
```'''
    assert parse_translated_po_response(response)["0001 | MAKOTO NAEGI"] == "Nơi này là gì...?"


def test_restore_working_po_from_copy_multiple_recursive_folders():
    import tempfile
    from dr_po_toolkit.backup import restore_working_po_from_copies

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "A"
        b = root / "B" / "nested"
        a.mkdir()
        b.mkdir(parents=True)

        copy1 = a / "e01 - Copy.po"
        work1 = a / "e01.po"
        copy1.write_text('msgid "A"\nmsgstr ""\n', encoding="utf-8")
        work1.write_text('msgid "A"\nmsgstr "dirty"\n', encoding="utf-8")

        copy2 = b / "e02 - Copy.po"
        work2 = b / "e02.po"
        copy2.write_text('msgid "B"\nmsgstr ""\n', encoding="utf-8")

        results = restore_working_po_from_copies([a, root / "B"])

        assert len([r for r in results if r.ok]) == 2
        assert work1.read_text(encoding="utf-8") == copy1.read_text(encoding="utf-8")
        assert work2.exists()
        assert work2.read_text(encoding="utf-8") == copy2.read_text(encoding="utf-8")
        assert copy1.read_text(encoding="utf-8") == 'msgid "A"\nmsgstr ""\n'
        assert copy2.read_text(encoding="utf-8") == 'msgid "B"\nmsgstr ""\n'


def test_make_backups_does_not_overwrite_existing_copy_by_default():
    import tempfile
    from dr_po_toolkit.backup import make_backups

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "e01.po"
        copy = root / "e01 - Copy.po"
        work.write_text("dirty", encoding="utf-8")
        copy.write_text("clean", encoding="utf-8")

        written = make_backups(root)

        assert written == 0
        assert copy.read_text(encoding="utf-8") == "clean"


def test_gemini_web_prompt_uses_safe_tag_tokens():
    from dr_po_toolkit.gemini_web import entry_to_po_prompt_block, parse_translated_po_response

    po = parse_po_text(SAMPLE)
    block = entry_to_po_prompt_block(po.entries[0])
    assert "msgctxt" in block
    assert "msgid" in block
    assert "⟦CLT 4⟧" in block
    assert "<CLT 4>" not in block

    parsed = parse_translated_po_response('msgctxt "0001 | MAKOTO NAEGI"\nmsgstr "⟦CLT 4⟧Xin chào...\n⟦CLT⟧"')
    assert parsed["0001 | MAKOTO NAEGI"] == "<CLT 4>Xin chào...\n<CLT>"
