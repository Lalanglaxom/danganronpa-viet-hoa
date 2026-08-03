from __future__ import annotations

import time
from pathlib import Path

from dr_po_toolkit.search import clear_search_cache, search_path
from dr_po_toolkit.text_index import load_po_clone, text_index_stats
from dr_po_toolkit.translafixer import (
    TranslationSuggestionIndex,
    clear_translafixer_caches,
    find_reference_translation_conflicts,
)


def _write_po(path: Path, translation: str) -> None:
    path.write_text(
        'msgctxt "0001 | MAKOTO"\n'
        'msgid "Hello there"\n'
        f'msgstr "{translation}"\n',
        encoding="utf-8",
    )


def test_shared_text_index_reuses_parsing_across_search_suggestions_and_diff(tmp_path: Path, monkeypatch):
    import dr_po_toolkit.text_index as text_index

    first = tmp_path / "one.po"
    second = tmp_path / "two.po"
    _write_po(first, "Xin chào")
    _write_po(second, "Chào bạn")

    clear_search_cache()
    clear_translafixer_caches()
    real_parse = text_index.parse_po_text
    parse_count = 0

    def counted_parse(*args, **kwargs):
        nonlocal parse_count
        parse_count += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(text_index, "parse_po_text", counted_parse)

    assert len(search_path(tmp_path, "hello there")) == 2
    assert parse_count == 2
    assert text_index_stats()["parsed_files"] == 2

    suggestion_index, suggestion_result = TranslationSuggestionIndex.from_translafixer_sources(tmp_path)
    assert suggestion_result.source_files == 2
    assert suggestion_index.suggest("Hello there")[0].score == 1.0
    assert parse_count == 2

    conflicts, conflict_result = find_reference_translation_conflicts(tmp_path)
    assert len(conflicts) == 2
    assert conflict_result.ambiguous_msgids == 1
    assert parse_count == 2

    # Whole-corpus caches make reopening the same suggestion/diff views constant-time.
    same_index, _ = TranslationSuggestionIndex.from_translafixer_sources(tmp_path)
    same_conflicts, _ = find_reference_translation_conflicts(tmp_path)
    assert same_index is suggestion_index
    assert same_conflicts == conflicts
    assert parse_count == 2

    # Only the edited file is reparsed; the unchanged file stays shared.
    time.sleep(0.002)
    _write_po(second, "Xin chào mới")
    refreshed_index, _ = TranslationSuggestionIndex.from_translafixer_sources(tmp_path)
    refreshed_conflicts, _ = find_reference_translation_conflicts(tmp_path)
    assert refreshed_index is not suggestion_index
    assert {item.translation for item in refreshed_conflicts} == {"Xin chào", "Xin chào mới"}
    assert parse_count == 3


def test_mutable_po_clone_does_not_modify_shared_cached_document(tmp_path: Path):
    from dr_po_toolkit.text_index import get_cached_po, invalidate_text_index

    path = tmp_path / "sample.po"
    _write_po(path, "Bản gốc")
    invalidate_text_index()

    cached = get_cached_po(path)
    editable = load_po_clone(path)
    editable.entries[0].msgstr = "Đã sửa nhưng chưa lưu"

    assert cached.entries[0].msgstr == "Bản gốc"
    assert get_cached_po(path).entries[0].msgstr == "Bản gốc"
