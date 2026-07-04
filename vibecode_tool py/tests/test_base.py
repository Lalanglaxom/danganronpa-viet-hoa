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


def test_translafixer_rewrites_target_by_msgid():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        correct = root / "correct"
        target = root / "target"
        correct.mkdir()
        target.mkdir()
        (correct / "good.po").write_text(
            'msgctxt "A"\nmsgid "Hello"\nmsgstr "Xin chào"\n\n'
            'msgctxt "B"\nmsgid "Bye"\nmsgstr "Tạm biệt"\n',
            encoding="utf-8",
        )
        target_po = target / "bad.po"
        target_po.write_text(
            'msgctxt "X"\nmsgid "Hello"\nmsgstr "Sai"\n\n'
            'msgctxt "Y"\nmsgid "Other"\nmsgstr "Giữ"\n',
            encoding="utf-8",
        )

        result = apply_translafix(correct, target, dry_run=False, create_backup=True)

        assert result.total_changed == 1
        assert (target / "bad.po.translafixer.bak").exists()
        fixed = target_po.read_text(encoding="utf-8")
        assert 'msgstr "Xin chào"' in fixed
        assert 'msgstr "Giữ"' in fixed


def test_translafixer_skips_conflicting_source_msgid():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        correct = root / "correct"
        target = root / "target"
        correct.mkdir()
        target.mkdir()
        (correct / "one.po").write_text('msgctxt "A"\nmsgid "Same"\nmsgstr "Một"\n', encoding="utf-8")
        (correct / "two.po").write_text('msgctxt "B"\nmsgid "Same"\nmsgstr "Hai"\n', encoding="utf-8")
        target_po = target / "target.po"
        target_po.write_text('msgctxt "T"\nmsgid "Same"\nmsgstr "Old"\n', encoding="utf-8")

        result = apply_translafix(correct, target, dry_run=False, create_backup=False)

        assert result.ambiguous_msgids == 1
        assert result.total_changed == 0
        assert 'msgstr "Old"' in target_po.read_text(encoding="utf-8")


def test_translafixer_source_file_list_skips_selected_source_inside_target():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "target"
        target.mkdir()
        source_po = target / "source_good.po"
        target_po = target / "bad.po"
        source_po.write_text('msgctxt "A"\nmsgid "Hello"\nmsgstr "Xin chào"\n', encoding="utf-8")
        target_po.write_text('msgctxt "B"\nmsgid "Hello"\nmsgstr "Sai"\n', encoding="utf-8")

        result = apply_translafix([source_po], target, dry_run=False, create_backup=False)

        assert result.source_files == 1
        assert result.skipped_source_targets == 1
        assert result.target_files == 1
        assert result.total_changed == 1
        assert 'msgstr "Xin chào"' in target_po.read_text(encoding="utf-8")
        assert source_po.read_text(encoding="utf-8") == 'msgctxt "A"\nmsgid "Hello"\nmsgstr "Xin chào"\n'



def test_translafixer_source_picker_accepts_folders_and_dedupes_files():
    import tempfile
    from dr_po_toolkit.translafixer import collect_source_po_files

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        nested = source / "nested"
        nested.mkdir(parents=True)
        first = source / "one.po"
        second = nested / "two.po"
        copy = nested / "two - Copy.po"
        first.write_text('msgid "A"\nmsgstr "Một"\n', encoding="utf-8")
        second.write_text('msgid "B"\nmsgstr "Hai"\n', encoding="utf-8")
        copy.write_text('msgid "COPY"\nmsgstr "Copy"\n', encoding="utf-8")

        from_folder = collect_source_po_files([source, first])
        assert {p.name for p in from_folder} == {"one.po", "two.po"}
        assert len(from_folder) == 2

        with_explicit_copy = collect_source_po_files([source, copy])
        assert {p.name for p in with_explicit_copy} == {"one.po", "two.po", "two - Copy.po"}


def test_translafixer_source_folder_inside_target_is_not_rewritten():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "target"
        source_folder = target / "correct"
        source_folder.mkdir(parents=True)
        source_po = source_folder / "source_good.po"
        target_po = target / "bad.po"
        source_po.write_text('msgctxt "A"\nmsgid "Hello"\nmsgstr "Xin chào"\n', encoding="utf-8")
        target_po.write_text('msgctxt "B"\nmsgid "Hello"\nmsgstr "Sai"\n', encoding="utf-8")

        result = apply_translafix([source_folder], target, dry_run=False, create_backup=False)

        assert result.source_files == 1
        assert result.skipped_source_targets == 1
        assert result.target_files == 1
        assert result.total_changed == 1
        assert 'msgstr "Xin chào"' in target_po.read_text(encoding="utf-8")
        assert source_po.read_text(encoding="utf-8") == 'msgctxt "A"\nmsgid "Hello"\nmsgstr "Xin chào"\n'

def test_sync_by_filename_skips_identical_and_rejects_nested_folders():
    import tempfile
    import pytest
    from dr_po_toolkit.backup import sync_by_filename_report

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "target"
        source.mkdir()
        target.mkdir()
        (source / "e01.po").write_text('msgid "A"\nmsgstr "same"\n', encoding="utf-8")
        (target / "e01.po").write_text('msgid "A"\nmsgstr "same"\n', encoding="utf-8")

        result = sync_by_filename_report(source, target)

        assert result.copied == 0
        assert result.skipped_identical == 1

        nested = target / "nested_source"
        nested.mkdir()
        with pytest.raises(ValueError):
            sync_by_filename_report(nested, target)


def test_search_finds_wrapped_po_text_after_fast_prefilter():
    import tempfile
    from dr_po_toolkit.search import search_path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        po = root / "e01.po"
        po.write_text('msgid "Hello\\n"\n"world"\nmsgstr "Xin chào"\n', encoding="utf-8")

        results = search_path(root, "hello world", search_msgid=True, search_msgstr=False)

        assert len(results) == 1
        assert results[0].msgid == "Hello\nworld"


def test_translafixer_ignores_clt_tags_when_matching_msgid():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix, msgid_match_key

    assert msgid_match_key('<CLT 4>Hello there\n<CLT>') == 'Hello there'
    assert msgid_match_key('Hello there') == 'Hello there'

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / 'source.po'
        target_dir = root / 'target'
        target_dir.mkdir()
        target = target_dir / 'target.po'
        source.write_text(
            'msgctxt "A"\nmsgid "<CLT 4>Hello there\\n"\n"<CLT>"\nmsgstr "<CLT 4>Xin chào đó\\n"\n"<CLT>"\n',
            encoding='utf-8',
        )
        target.write_text('msgctxt "B"\nmsgid "Hello there"\nmsgstr "Old"\n', encoding='utf-8')

        result = apply_translafix([source], target_dir, dry_run=False, create_backup=False)

        assert result.total_changed == 1
        assert '<CLT 4>Xin chào đó' in target.read_text(encoding='utf-8')


def test_translafixer_suggestion_index_uses_sources_and_filters_low_scores():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_dir = root / "sources"
        source_dir.mkdir()
        source_po = source_dir / "memory.po"
        source_po.write_text(
            'msgctxt "A"\nmsgid "<CLT 4>Hello there\\n"\n"<CLT>"\nmsgstr "<CLT 4>Xin chào đó\\n"\n"<CLT>"\n\n'
            'msgctxt "B"\nmsgid "Completely different words"\nmsgstr "Câu khác hẳn"\n',
            encoding="utf-8",
        )

        index, result = TranslationSuggestionIndex.from_translafixer_sources([source_dir])
        suggestions = index.suggest("Hello there", min_score=0.70)
        unrelated = index.suggest("Nothing in common here", min_score=0.70)

        assert result.source_files == 1
        assert result.usable_translations == 2
        assert suggestions
        assert suggestions[0].score > 0.95
        assert suggestions[0].translation.startswith("<CLT 4>Xin chào")
        assert all(item.score > 0.70 for item in suggestions)
        assert unrelated == []


def test_translafixer_suggestion_index_dedupes_translations_and_stops_at_five():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_po = root / "memory.po"
        body = []
        for i in range(8):
            body.append(f'msgctxt "A{i}"\nmsgid "Open the red door {i}"\nmsgstr "Mở cửa đỏ"\n')
        for i in range(8):
            body.append(f'msgctxt "B{i}"\nmsgid "Open the red door now {i}"\nmsgstr "Bản dịch {i}"\n')
        source_po.write_text("\n".join(body), encoding="utf-8")

        index, _result = TranslationSuggestionIndex.from_translafixer_sources([source_po])
        suggestions = index.suggest("Open the red door now", min_score=0.70)
        translations = [item.translation for item in suggestions]

        assert len(suggestions) <= 5
        assert len(translations) == len(set(translations))
        assert translations.count("Mở cửa đỏ") <= 1
        assert all(item.score > 0.70 for item in suggestions)


def test_sync_by_filename_reports_unmatched_and_copied_files():
    import tempfile
    from dr_po_toolkit.backup import sync_by_filename_report

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "target"
        source.mkdir()
        target.mkdir()
        source_copy = source / "copyme.po"
        target_copy = target / "copyme.po"
        source_only = source / "source_only.po"
        target_only = target / "target_only.po"
        source_copy.write_text('msgid "A"\nmsgstr "new"\n', encoding="utf-8")
        target_copy.write_text('msgid "A"\nmsgstr "old"\n', encoding="utf-8")
        source_only.write_text('msgid "B"\nmsgstr "source"\n', encoding="utf-8")
        target_only.write_text('msgid "C"\nmsgstr "target"\n', encoding="utf-8")

        result = sync_by_filename_report(source, target)

        assert result.copied == 1
        assert result.copied_files == [(source_copy, target_copy)]
        assert result.source_without_target == [source_only]
        assert result.target_without_source == [target_only]
        assert target_copy.read_text(encoding="utf-8") == source_copy.read_text(encoding="utf-8")


def test_translation_suggestions_can_search_below_seventy_percent():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.po"
        source.write_text('msgid "Hello"\nmsgstr "Xin chào"\n', encoding="utf-8")
        index, _result = TranslationSuggestionIndex.from_translafixer_sources(source)

        suggestions = index.suggest("Hello there", min_score=0.50, limit=5)

        assert suggestions
        assert suggestions[0].translation == "Xin chào"
        assert suggestions[0].score < 0.70
