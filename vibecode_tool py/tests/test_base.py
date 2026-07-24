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




def test_translafixer_never_applies_empty_source_msgstr_even_if_requested():
    import tempfile
    from dr_po_toolkit.translafixer import apply_translafix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        correct = root / "correct"
        target = root / "target"
        correct.mkdir()
        target.mkdir()
        (correct / "blank.po").write_text('msgctxt "A"\nmsgid "Erase"\nmsgstr ""\n', encoding="utf-8")
        target_po = target / "target.po"
        target_po.write_text('msgctxt "T"\nmsgid "Erase"\nmsgstr "Keep me"\n', encoding="utf-8")

        result = apply_translafix(correct, target, dry_run=False, create_backup=False, include_empty=True)

        assert result.empty_source_entries == 1
        assert result.usable_translations == 0
        assert result.total_changed == 0
        assert 'msgstr "Keep me"' in target_po.read_text(encoding="utf-8")

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

        spaced_results = search_path(root, "hello world", search_msgid=True, search_msgstr=False)
        folded_results = search_path(root, "helloworld", search_msgid=True, search_msgstr=False)

        assert len(spaced_results) == 1
        assert spaced_results[0].msgid == "Hello\nworld"
        assert len(folded_results) == 1
        assert folded_results[0].msgid == "Hello\nworld"


def test_search_accepts_or_and_criteria_and_speaker_filter():
    import tempfile
    from dr_po_toolkit.search import search_path

    sample = (
        'msgctxt "0001 | MAKOTO NAEGI"\n'
        'msgid "Hello"\n'
        'msgstr "Xin chào"\n'
        '\n'
        'msgctxt "0002 | KYOKO KIRIGIRI"\n'
        'msgid "Goodbye"\n'
        'msgstr "Tạm biệt"\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        po = root / "e01.po"
        po.write_text(sample, encoding="utf-8")

        text_results = search_path(root, "missing | goodbye", speaker="kyoko")
        speaker_results = search_path(root, "", speaker="makoto | kyoko")
        combined_results = search_path(root, "hello | goodbye", speaker="makoto")
        and_results = search_path(root, "good & bye", speaker="kyoko")
        cross_field_results = search_path(root, "goodbye & tạm biệt", speaker="kyoko")

        assert [item.msgctxt for item in text_results] == ["0002 | KYOKO KIRIGIRI"]
        assert {item.msgctxt for item in speaker_results} == {"0001 | MAKOTO NAEGI", "0002 | KYOKO KIRIGIRI"}
        assert [item.msgctxt for item in combined_results] == ["0001 | MAKOTO NAEGI"]
        assert [item.msgctxt for item in and_results] == ["0002 | KYOKO KIRIGIRI"]
        assert [item.msgctxt for item in cross_field_results] == ["0002 | KYOKO KIRIGIRI"]
        assert cross_field_results[0].hit_msgid and cross_field_results[0].hit_msgstr
        assert all(item.hit_speaker for item in speaker_results)


def test_search_raw_keeps_clt_and_other_text():
    import tempfile
    from dr_po_toolkit.search import search_path

    sample = (
        'msgctxt "0001 | MAKOTO NAEGI"\n'
        'msgid "<CLT 4>[Hello]; world<CLT>"\n'
        'msgstr "Xin chào"\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        po = root / "e01.po"
        po.write_text(sample, encoding="utf-8")

        visible_results = search_path(root, "<CLT 4>", search_msgstr=False)
        raw_results = search_path(root, "<CLT 4>", search_msgstr=False, raw=True)
        semicolon_results = search_path(root, "; world", search_msgstr=False, raw=True)

        assert visible_results == []
        assert len(raw_results) == 1
        assert len(semicolon_results) == 1


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
        suggestions = index.suggest("<CLT 4>Hello there\n<CLT>", min_score=0.70)
        unrelated = index.suggest("Nothing in common here", min_score=0.70)

        assert result.source_files == 1
        assert result.usable_translations == 2
        assert suggestions
        assert suggestions[0].score == 1.0
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


def test_translation_suggestion_preserves_target_clt_wrapper_when_reference_is_plain():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.po"
        source.write_text(
            'msgctxt "A"\nmsgid "Hello there"\nmsgstr "Xin chào đó"\n',
            encoding="utf-8",
        )
        index, _result = TranslationSuggestionIndex.from_translafixer_sources(source)

        suggestions = index.suggest("<CLT 4>Hello there\n<CLT>", min_score=0.60)

        assert suggestions
        assert suggestions[0].translation == "<CLT 4>Xin chào đó\n<CLT>"


def test_translation_suggestion_never_rewrites_existing_clt_tags():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.po"
        source.write_text(
            'msgctxt "A"\nmsgid "Hello there"\nmsgstr ""\n"<CLT 5>Xin chào đó\\n"\n"<CLT>"\n',
            encoding="utf-8",
        )
        index, _result = TranslationSuggestionIndex.from_translafixer_sources(source)

        suggestions = index.suggest("<CLT 4>Hello there\n<CLT>", min_score=0.60)

        assert suggestions
        assert suggestions[0].translation == "<CLT 5>Xin chào đó\n<CLT>"


def test_translation_suggestion_percentage_counts_line_breaks_and_clt_tags_raw():
    import tempfile
    from dr_po_toolkit.translafixer import TranslationSuggestionIndex

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.po"
        source.write_text(
            'msgctxt "A"\nmsgid ""\n"<CLT 4>Hello there\\n"\n"<CLT>"\nmsgstr "Xin chào"\n',
            encoding="utf-8",
        )
        index, _result = TranslationSuggestionIndex.from_translafixer_sources(source)

        exact = index.suggest("<CLT 4>Hello there\n<CLT>", min_score=0.0)
        different_newline = index.suggest("<CLT 4>Hello there <CLT>", min_score=0.0)
        different_clt = index.suggest("<CLT 5>Hello there\n<CLT>", min_score=0.0)
        plain = index.suggest("Hello there", min_score=0.0)

        assert exact and exact[0].score == 1.0
        assert different_newline and different_newline[0].score < 1.0
        assert different_clt and different_clt[0].score < 1.0
        assert plain and plain[0].score < different_clt[0].score


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


def test_sync_option_from_dedicated_working_folder_copies_all_files_when_unfiltered():
    import tempfile
    from dr_po_toolkit.backup import sync_option_from_working_folder

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        working = root / "working_e01"
        sync = root / "sync_e01"
        nested = working / "nested"
        nested.mkdir(parents=True)
        # Name intentionally does not contain e01; dedicated Working folders should sync all files.
        source = nested / "scene.po"
        source.write_text('msgctxt "A"\nmsgid "Hi"\nmsgstr "Xin chào"\n', encoding="utf-8")

        result = sync_option_from_working_folder(working, sync, "e01", filter_by_option=False)

        assert result.matched == 1
        assert result.copied == 1
        assert (sync / "nested" / "scene.po").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_reference_duplicate_conflicts_find_different_translations_only():
    import tempfile
    from dr_po_toolkit.translafixer import find_reference_duplicate_sources, find_reference_translation_conflicts, reference_duplicate_msgid_key

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        one = root / "one.po"
        two = root / "two.po"
        three = root / "three.po"
        one.write_text(
            'msgctxt "A | MAKOTO"\nmsgid "<CLT 4>Hello there\\n"\n"<CLT>"\nmsgstr "Xin chào"\n\n'
            'msgctxt "SAME"\nmsgid "Same"\nmsgstr "Giống"\n',
            encoding="utf-8",
        )
        two.write_text(
            'msgctxt "B | KYOKO"\nmsgid "Hello there"\nmsgstr "Chào đó"\n\n'
            'msgctxt "SAME2"\nmsgid "Same"\nmsgstr "Giống"\n',
            encoding="utf-8",
        )
        three.write_text('msgctxt "C | AOI"\nmsgid "Hello there"\nmsgstr "Chào khác"\n', encoding="utf-8")

        conflicts, result = find_reference_translation_conflicts([root])

        assert result.source_files == 3
        assert result.ambiguous_msgids == 1
        assert {item.translation for item in conflicts} == {"Chào đó", "Chào khác"}
        assert {item.speaker for item in conflicts} == {"KYOKO", "AOI"}
        assert all(item.key == "Hello there" for item in conflicts)

        all_duplicates, all_result = find_reference_duplicate_sources([root])
        assert {item.key for item in all_duplicates} == {"Hello there", "Same"}
        assert len(all_duplicates) == 4
        assert all_result.duplicate_same == 1
        assert all_result.ambiguous_msgids == 1
        assert {item.variants for item in all_duplicates if item.key == "Same"} == {1}
        assert {item.variants for item in all_duplicates if item.key == "Hello there"} == {2}

        assert reference_duplicate_msgid_key('<CLT 4>Hello there\n<CLT>') != reference_duplicate_msgid_key('Hello there')
        assert reference_duplicate_msgid_key('<clt_4>Hello there\n<CLT>') != reference_duplicate_msgid_key('<CLT 4>Hello there<CLT>')



def test_reference_duplicate_diff_uses_raw_newlines_and_clt():
    import tempfile
    from dr_po_toolkit.translafixer import find_reference_duplicate_sources, find_reference_translation_conflicts

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "one.po").write_text(
            'msgctxt "A"\nmsgid "Same source"\nmsgstr "Xin\\n"\n"chào"\n\n'
            'msgctxt "B"\nmsgid "Line\\n"\n"break"\nmsgstr "Một"\n\n'
            'msgctxt "C"\nmsgid "<CLT 4>Tagged<CLT>"\nmsgstr "Ba"\n',
            encoding="utf-8",
        )
        (root / "two.po").write_text(
            'msgctxt "D"\nmsgid "Same source"\nmsgstr "Xin chào"\n\n'
            'msgctxt "E"\nmsgid "Line break"\nmsgstr "Hai"\n\n'
            'msgctxt "F"\nmsgid "<clt_4>Tagged<CLT>"\nmsgstr "Bốn"\n',
            encoding="utf-8",
        )
        (root / "three.po").write_text(
            'msgctxt "G"\nmsgid "Same source"\nmsgstr "<CLT 4>Xin chào<CLT>"\n',
            encoding="utf-8",
        )

        conflicts, _result = find_reference_translation_conflicts(root)
        assert {item.source for item in conflicts} == {"Same source"}
        assert {item.translation for item in conflicts} == {"Xin\nchào", "Xin chào", "<CLT 4>Xin chào<CLT>"}
        assert {item.variants for item in conflicts} == {3}

        all_duplicates, _all_result = find_reference_duplicate_sources(root)
        assert {item.source for item in all_duplicates} == {"Same source"}
        assert "Line\nbreak" not in {item.source for item in all_duplicates}
        assert "Line break" not in {item.source for item in all_duplicates}
        assert "<CLT 4>Tagged<CLT>" not in {item.source for item in all_duplicates}
        assert "<clt_4>Tagged<CLT>" not in {item.source for item in all_duplicates}

def test_duplicate_paths_use_selected_checkbox_working_folders():
    import tempfile

    try:
        from dr_po_toolkit.gui import MainWindow
    except ImportError:
        return

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        e01 = root / "e01"
        e02 = root / "e02"
        e01.mkdir()
        e02.mkdir()

        class Dummy:
            config = {
                "working_e01_path": str(e01),
                "working_e02_path": str(e02),
                "translafixer_dr_options": ["e02"],
            }
            _dr_option_widgets = {}
            _path_key = MainWindow._path_key
            _initial_dr_options = MainWindow._initial_dr_options
            _selected_dr_options = MainWindow._selected_dr_options
            _selected_working_paths = MainWindow._selected_working_paths

        dummy = Dummy()

        assert MainWindow._duplicate_scan_paths(dummy, "translafixer") == [e02]

        dummy.config["translafixer_dr_options"] = []
        assert MainWindow._duplicate_scan_paths(dummy, "translafixer") == []


def test_reference_duplicate_views_include_mixed_empty_but_skip_all_empty_groups():
    import tempfile
    from dr_po_toolkit.translafixer import find_reference_duplicate_sources, find_reference_translation_conflicts

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "one.po").write_text(
            'msgctxt "A"\nmsgid "Needs translation"\nmsgstr "Có bản dịch"\n\n'
            'msgctxt "E1"\nmsgid "Still blank"\nmsgstr ""\n',
            encoding="utf-8",
        )
        (root / "two.po").write_text(
            'msgctxt "B"\nmsgid "Needs translation"\nmsgstr ""\n\n'
            'msgctxt "E2"\nmsgid "Still blank"\nmsgstr ""\n',
            encoding="utf-8",
        )

        conflicts, result = find_reference_translation_conflicts(root)
        assert {item.key for item in conflicts} == {"Needs translation"}
        assert {item.translation for item in conflicts} == {"Có bản dịch", ""}
        assert {item.variants for item in conflicts} == {2}
        assert result.empty_source_entries == 3

        all_duplicates, _all_result = find_reference_duplicate_sources(root)
        assert {item.key for item in all_duplicates} == {"Needs translation"}
        assert {item.translation for item in all_duplicates} == {"Có bản dịch", ""}

        old_style_conflicts, _old_result = find_reference_translation_conflicts(root, include_empty=False)
        assert old_style_conflicts == []


def test_shared_search_replace_ignores_linebreak_shape():
    from dr_po_toolkit.text_utils import compile_search_replace_pattern, search_replace_replacement

    pattern = compile_search_replace_pattern("hello world")
    assert pattern.search("hello\nworld")
    assert pattern.search(r"hello\nworld")

    folded_pattern = compile_search_replace_pattern("helloworld")
    assert folded_pattern.search("hello\nworld")
    assert folded_pattern.search(r"hello\nworld")

    replaced, count = folded_pattern.subn(search_replace_replacement(r"xin\nchao"), "hello\nworld")
    assert count == 1
    assert replaced == "xin\nchao"


def test_semicolon_search_replace_pairs_order_and_escape():
    from dr_po_toolkit.text_utils import search_replace_pairs

    assert search_replace_pairs("foo; bar", "one;two") == [("foo", "one"), ("bar", "two")]
    assert search_replace_pairs(r"foo\;bar;baz", r"semi\;colon;") == [("foo;bar", "semi;colon"), ("baz", "")]


def test_ordered_search_replace_sequence_matches_gui_behavior():
    from dr_po_toolkit.text_utils import apply_search_replace_sequence, compile_search_replace_sequence

    compiled = compile_search_replace_sequence("one;two", "1;2")
    text, hits = apply_search_replace_sequence("one two one", compiled)

    assert hits == 3
    assert text == "1 2 1"


def test_search_replace_sequence_reports_bad_item_index():
    from dr_po_toolkit.text_utils import SearchReplaceCompileError, compile_search_replace_sequence

    try:
        compile_search_replace_sequence("one;(", "1;2", regex=True)
    except SearchReplaceCompileError as exc:
        assert exc.index == 2
    else:  # pragma: no cover - defensive
        raise AssertionError("expected SearchReplaceCompileError")


def test_validator_entry_link_roundtrip_handles_unicode_and_symbols():
    import tempfile
    from dr_po_toolkit.app_links import build_entry_url, parse_entry_url

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tệp & scene #1.po"
        url = build_entry_url(path, context="001 | Kyoko & Makoto", line=42)
        parsed = parse_entry_url(url)

        assert parsed is not None
        assert parsed.file == path.resolve()
        assert parsed.context == "001 | Kyoko & Makoto"
        assert parsed.line == 42


def test_validator_html_has_direct_open_entry_link():
    import tempfile
    from dr_po_toolkit.models import ValidationIssue
    from dr_po_toolkit.validation import build_html_report

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scene.po"
        issue = ValidationIssue(
            level="WARN",
            check="clt",
            detail="CLT mismatch",
            file=path,
            msgctxt="0001 | MAKOTO",
            line=17,
        )
        report = build_html_report({path: [issue]}, root=tmp)

        assert "Open in app" in report
        assert "drpo://open?" in report
        assert "context=0001+%7C+MAKOTO" in report
        assert "line=17" in report


def test_validation_reports_replace_old_files_without_accumulating():
    import tempfile
    from dr_po_toolkit.models import ValidationIssue
    from dr_po_toolkit.validation import write_reports

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        po_path = root / "scene.po"
        legacy_log = root / "validation_20260101_010101.log"
        legacy_html = root / "validation_20260101_010101.html"
        unrelated = root / "keep_me.html"
        legacy_log.write_text("old", encoding="utf-8")
        legacy_html.write_text("old", encoding="utf-8")
        unrelated.write_text("keep", encoding="utf-8")

        first_issue = ValidationIssue(
            level="WARN",
            check="first",
            detail="first report",
            file=po_path,
        )
        txt, html = write_reports({po_path: [first_issue]}, root, root)

        assert txt == root / "validation.log"
        assert html == root / "validation.html"
        assert not legacy_log.exists()
        assert not legacy_html.exists()
        assert unrelated.read_text(encoding="utf-8") == "keep"

        second_issue = ValidationIssue(
            level="ERROR",
            check="second",
            detail="second report",
            file=po_path,
        )
        txt2, html2 = write_reports({po_path: [second_issue]}, root, root)

        assert (txt2, html2) == (txt, html)
        assert "second report" in txt.read_text(encoding="utf-8")
        assert "first report" not in txt.read_text(encoding="utf-8")
        assert "second report" in html.read_text(encoding="utf-8")
        assert list(root.glob("validation*.log")) == [txt]
        assert list(root.glob("validation*.html")) == [html]


def test_config_uses_shared_extracted_destination_and_drops_legacy_sync_paths():
    import json
    import tempfile

    from dr_po_toolkit.config import DEFAULT_CONFIG, load_config, save_config
    from dr_po_toolkit.dr_options import DR_FILE_OPTION_KEYS

    assert "extracted_path" in DEFAULT_CONFIG
    assert all(f"sync_{key}_path" not in DEFAULT_CONFIG for key in DR_FILE_OPTION_KEYS)

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "extracted_path": "D:/project/extracted",
                    "working_e01_path": "D:/project/working/e01",
                    "sync_e01_path": "D:/old/sync/e01",
                }
            ),
            encoding="utf-8",
        )

        loaded = load_config(config_path)
        assert loaded["extracted_path"] == "D:/project/extracted"
        assert loaded["working_e01_path"] == "D:/project/working/e01"
        assert "sync_e01_path" not in loaded

        loaded["sync_e02_path"] = "D:/old/sync/e02"
        save_config(loaded, config_path)
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert "sync_e01_path" not in saved
        assert "sync_e02_path" not in saved
        assert saved["extracted_path"] == "D:/project/extracted"

def test_sync_option_creates_duplicate_filenames_at_their_relative_extracted_paths():
    import tempfile

    from dr_po_toolkit.backup import sync_option_from_working_folder

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        working = root / "working"
        extracted = root / "extracted"
        first = working / "one" / "scene.po"
        second = working / "two" / "scene.po"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        first.write_text('msgid "One"\nmsgstr "Một"\n', encoding="utf-8")
        second.write_text('msgid "Two"\nmsgstr "Hai"\n', encoding="utf-8")

        result = sync_option_from_working_folder(working, extracted, "e01", filter_by_option=False)

        assert result.matched == 2
        assert result.copied == 2
        assert (extracted / "one" / "scene.po").read_text(encoding="utf-8") == first.read_text(encoding="utf-8")
        assert (extracted / "two" / "scene.po").read_text(encoding="utf-8") == second.read_text(encoding="utf-8")



def test_config_has_danganviethoa_repository_folder_path():
    from dr_po_toolkit.config import DEFAULT_CONFIG

    assert "danganviethoa_path" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["danganviethoa_path"] == ""


def test_git_commands_use_visible_four_step_push_flow():
    import tempfile

    from dr_po_toolkit.git_tools import (
        DANGANVIETHOA_REPOSITORY_URL,
        build_pull_command,
        build_push_command,
        create_commit_message_file,
        create_push_script,
    )

    assert DANGANVIETHOA_REPOSITORY_URL == "https://github.com/Lalanglaxom/danganronpa-viet-hoa.git"
    pull = build_pull_command()
    assert "git remote -v" in pull
    assert "git status --short --branch" in pull
    assert "git fetch --prune origin" in pull
    assert pull.endswith("git pull --rebase --autostash")

    with tempfile.TemporaryDirectory():
        message_file = create_commit_message_file("Update Vietnamese translation")
        push_script = None
        try:
            assert message_file.read_text(encoding="utf-8") == "Update Vietnamese translation\n"
            push_script = create_push_script(message_file)
            script = push_script.read_text(encoding="utf-8")
            assert "[1/4] Scanning files..." in script
            assert "[2/4] Checking staged changes..." in script
            assert "[3/4] Creating commit..." in script
            assert "[4/4] Uploading to remote..." in script
            assert "git add ." in script
            assert "git diff --cached --quiet" in script
            assert "git commit --quiet -F" in script
            assert "git push origin main" in script
            assert "git diff --cached --stat" not in script
            assert "git fetch --prune origin" not in script
            assert "git ls-files -ci --exclude-standard" not in script
            assert 'set "FINAL_EXIT=0"' in script
            assert 'set "FINAL_EXIT=!GIT_EXIT!"' in script
            assert "endlocal & exit /b %FINAL_EXIT%" in script
            assert str(message_file.resolve()) in script

            push = build_push_command(push_script)
            assert push.startswith("call ")
            assert str(push_script.resolve()) in push
        finally:
            message_file.unlink(missing_ok=True)
            if push_script is not None:
                push_script.unlink(missing_ok=True)


def test_project_gitignore_excludes_python_generated_files():
    project_root = Path(__file__).resolve().parents[1]
    ignored = (project_root / ".gitignore").read_text(encoding="utf-8")

    assert "__pycache__/" in ignored
    assert "*.py[cod]" in ignored
    assert ".pytest_cache/" in ignored
    assert ".ruff_cache/" in ignored

def test_git_repository_validation_requires_dot_git_folder():
    import tempfile

    from dr_po_toolkit.git_tools import validate_repository_folder

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            validate_repository_folder(root)
        except ValueError as exc:
            assert "not a Git repository" in str(exc)
        else:
            raise AssertionError("Expected non-repository folder to be rejected")

        (root / ".git").mkdir()
        assert validate_repository_folder(root) == root


def test_git_launcher_keeps_console_open_and_reports_exit_code(monkeypatch):
    import subprocess

    from dr_po_toolkit import git_tools

    captured = {}

    class DummyProcess:
        pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return DummyProcess()

    repo = Path("/tmp/fake-git-repository")
    monkeypatch.setattr(git_tools, "validate_repository_folder", lambda _folder: repo)
    monkeypatch.setattr(git_tools.os, "name", "nt")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)

    git_tools.launch_windows_cmd("ignored", "git status")

    assert captured["args"][:4] == ["cmd.exe", "/D", "/V:ON", "/K"]
    persistent = captured["args"][4]
    assert persistent.startswith("git status")
    assert "DR_GIT_EXIT=!ERRORLEVEL!" in persistent
    assert "will not close automatically" in persistent
    assert "pause >nul" in persistent
    assert captured["kwargs"]["creationflags"] == 16
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]


def test_linewrap_presets_start_with_base64_and_all_remain_editable():
    from dr_po_toolkit.linewrap import normalize_wrap_presets

    presets = normalize_wrap_presets(
        None,
        legacy_soft=51,
        legacy_hard=63,
        legacy_max_cuts=3,
    )

    assert presets[0] == {"soft": 58, "hard": 64, "max_cuts": 2}
    assert presets[1:] == [
        {"soft": 51, "hard": 63, "max_cuts": 3},
        {"soft": 51, "hard": 63, "max_cuts": 3},
        {"soft": 51, "hard": 63, "max_cuts": 3},
    ]

    edited = normalize_wrap_presets(
        [
            {"soft": 1, "hard": 2, "max_cuts": 9},
            {"soft": "40", "hard": "48", "max_cuts": "1"},
            {"soft": 60, "hard": 72, "max_cuts": 4},
            {"soft": 70, "hard": 80, "max_cuts": 5},
        ]
    )
    assert edited[0] == {"soft": 1, "hard": 2, "max_cuts": 9}
    assert edited[1] == {"soft": 40, "hard": 48, "max_cuts": 1}
    assert edited[3] == {"soft": 70, "hard": 80, "max_cuts": 5}


def test_config_migrates_old_single_linewrap_setting_to_four_presets():
    import json
    import tempfile

    from dr_po_toolkit.config import load_config

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(
            json.dumps({"soft_limit": 47, "hard_limit": 59, "max_cuts": 4}),
            encoding="utf-8",
        )
        config = load_config(config_path)

    assert config["linewrap_active_preset"] == 0
    assert config["linewrap_presets"][0] == {"soft": 58, "hard": 64, "max_cuts": 2}
    assert config["linewrap_presets"][1] == {"soft": 47, "hard": 59, "max_cuts": 4}
    assert len(config["linewrap_presets"]) == 4


def test_config_preserves_edited_first_linewrap_preset():
    import json
    import tempfile

    from dr_po_toolkit.config import load_config

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "linewrap_presets": [
                        {"soft": 44, "hard": 52, "max_cuts": 3},
                        {"soft": 50, "hard": 60, "max_cuts": 2},
                        {"soft": 58, "hard": 68, "max_cuts": 4},
                        {"soft": 66, "hard": 76, "max_cuts": 5},
                    ]
                }
            ),
            encoding="utf-8",
        )
        config = load_config(config_path)
        assert config["linewrap_presets"][0] == {"soft": 44, "hard": 52, "max_cuts": 3}


def test_html_spacing_preserves_double_spaces_around_hidden_clt_boundaries():
    from dr_po_toolkit.text_utils import html_escape_preserve_spacing

    assert html_escape_preserve_spacing("normal words") == "normal words"
    assert html_escape_preserve_spacing(" left") == "&nbsp;left"
    assert html_escape_preserve_spacing("right ") == "right&nbsp;"
    assert html_escape_preserve_spacing("two  spaces") == "two&nbsp;&nbsp;spaces"


def test_visible_character_counts_by_line_ignores_control_tags_and_placeholders():
    from dr_po_toolkit.text_utils import visible_character_counts_by_line

    text = "<CLT 4>Hello, brave player %TEXT%!\n<CLT>{player_name} found %s [color=red]three gems[/color].\n"

    assert visible_character_counts_by_line(text) == [21, 19, 0]


def test_visible_character_counts_by_line_counts_vietnamese_spaces_and_punctuation():
    from dr_po_toolkit.text_utils import visible_character_counts_by_line

    assert visible_character_counts_by_line("<CLT 4>Xin chào người chơi\nBạn khỏe không?<CLT>") == [19, 15]


def test_visible_character_counts_by_line_preserves_double_spaces_around_hidden_tags():
    from dr_po_toolkit.text_utils import visible_character_counts_by_line

    assert visible_character_counts_by_line("Hello <CLT 4> world") == [12]


def test_search_files_reports_file_progress():
    import tempfile

    from dr_po_toolkit.search import search_files

    po_text = '''msgctxt "0001 | MAKOTO"
msgid "Hello"
msgstr "Xin chào"
'''
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = root / "a.po"
        second = root / "b.po"
        first.write_text(po_text, encoding="utf-8")
        second.write_text(po_text.replace("0001", "0002"), encoding="utf-8")
        progress = []

        results = search_files(
            [first, second],
            "hello",
            progress=lambda done, total, path: progress.append((done, total, path.name)),
        )

    assert len(results) == 2
    assert progress == [(1, 2, "a.po"), (2, 2, "b.po")]


def test_sync_reuses_prebuilt_target_index_without_collecting_unmatched_targets():
    import tempfile

    from dr_po_toolkit.backup import index_po_files_by_name, sync_by_filename_report

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "target"
        source.mkdir()
        target.mkdir()
        (source / "match.po").write_text('msgid "A"\nmsgstr "new"\n', encoding="utf-8")
        (target / "match.po").write_text('msgid "A"\nmsgstr "old"\n', encoding="utf-8")
        (target / "unrelated.po").write_text('msgid "B"\nmsgstr "keep"\n', encoding="utf-8")

        target_index, target_count = index_po_files_by_name(target)
        result = sync_by_filename_report(
            source,
            target,
            target_index=target_index,
            target_file_count=target_count,
            collect_target_without_source=False,
        )

    assert result.copied == 1
    assert result.target_files == 2
    assert result.target_without_source == []
