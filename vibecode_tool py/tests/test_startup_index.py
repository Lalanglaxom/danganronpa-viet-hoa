from __future__ import annotations

from pathlib import Path

from dr_po_toolkit.startup_index import configured_index_roots, configured_po_files


def _write_po(path: Path, text: str = "Hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'msgid "{text}"\nmsgstr ""\n', encoding="utf-8")


def test_startup_index_collects_all_configured_working_folders(tmp_path: Path):
    e01 = tmp_path / "e01"
    e02 = tmp_path / "e02"
    first = e01 / "first.po"
    second = e02 / "second.po"
    ignored_copy = e02 / "second - Copy.po"
    _write_po(first)
    _write_po(second)
    _write_po(ignored_copy)

    config = {
        "working_e01_path": str(e01),
        "working_e02_path": str(e02),
        # A duplicate root must not duplicate files.
        "working_e03_path": str(e01),
    }

    assert configured_index_roots(config) == [e01, e02]
    assert configured_po_files(config) == [first, second]


def test_startup_index_includes_enabled_extra_path_only(tmp_path: Path):
    extra = tmp_path / "extra"
    extra_po = extra / "extra.po"
    _write_po(extra_po)

    disabled = {"last_path": str(extra), "search_include_extra_path": False}
    enabled = {"last_path": str(extra), "search_include_extra_path": True}

    assert configured_index_roots(disabled) == []
    assert configured_po_files(enabled) == [extra_po]
