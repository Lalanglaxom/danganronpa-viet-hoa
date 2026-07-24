from dr_po_toolkit.shortcuts import (
    FILE_NEXT_SHORTCUT,
    FILE_PREVIOUS_SHORTCUT,
    GEMINI_TRANSLATE_SHORTCUT,
    PRESET_REPLACE_SHORTCUT,
    SUGGESTION_REFRESH_SHORTCUT,
    SUGGESTION_SHORTCUTS,
    WRAP_ENTIRE_FILE_KEYS,
    WRAP_MODE_SHORTCUT,
    WRAP_PRESET_KEYS,
    editable_shortcut_sequences,
)


def _parts(sequence: str) -> tuple[str, ...]:
    return tuple(part.strip().casefold() for part in sequence.split(","))


def test_editable_shortcuts_are_unique_and_have_no_prefix_overlap():
    sequences = (
        editable_shortcut_sequences()
        + SUGGESTION_SHORTCUTS
        + (SUGGESTION_REFRESH_SHORTCUT, "Ctrl+Z", "Ctrl+S", "Ctrl+F", "Ctrl+Up", "Ctrl+Down", "Ctrl+E", "F2")
    )
    normalized = [_parts(sequence) for sequence in sequences]
    assert len(normalized) == len(set(normalized))
    for index, left in enumerate(normalized):
        for right_index, right in enumerate(normalized):
            if index == right_index:
                continue
            assert not (len(left) < len(right) and right[: len(left)] == left)


def test_requested_shortcut_map_uses_persistent_ctrl_space_wrap_mode():
    assert (FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT) == ("Alt+Up", "Alt+Down")
    assert WRAP_MODE_SHORTCUT == "Ctrl+Space"
    assert WRAP_PRESET_KEYS == ("1", "2", "3", "4")
    assert WRAP_ENTIRE_FILE_KEYS == ("Return", "Enter")
    assert SUGGESTION_SHORTCUTS == ("Ctrl+1", "Ctrl+2", "Ctrl+3")
    assert PRESET_REPLACE_SHORTCUT == "Ctrl+R"
    assert GEMINI_TRANSLATE_SHORTCUT == "Ctrl+G"
    assert "Ctrl+Space" in editable_shortcut_sequences()
    assert "Ctrl+Alt+Enter" not in editable_shortcut_sequences()
    assert "Shift+Up" not in editable_shortcut_sequences()
    assert "Shift+Down" not in editable_shortcut_sequences()


def test_persistent_modes_are_wired_to_every_editable_view():
    from pathlib import Path

    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert "search_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert "duplicate_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert "po_viewer_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert "suggestion_shortcut_filter = RepeatedSuggestionShortcutFilter(" in gui_source
    assert "blocked_when=lambda: po_viewer_wrap_filter.armed" in gui_source
    assert "WRAP_PRESET_SHORTCUTS" not in gui_source
    assert "WRAP_ENTIRE_FILE_SHORTCUTS" not in gui_source
