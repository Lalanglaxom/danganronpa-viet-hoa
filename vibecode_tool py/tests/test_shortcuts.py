from dr_po_toolkit.shortcuts import (
    FILE_NEXT_SHORTCUT,
    FILE_PREVIOUS_SHORTCUT,
    GEMINI_TRANSLATE_SHORTCUT,
    PRESET_REPLACE_SHORTCUT,
    SUGGESTION_REFRESH_SHORTCUT,
    SUGGESTION_SHORTCUTS,
    WRAP_ENTIRE_FILE_SHORTCUTS,
    WRAP_PRESET_SHORTCUTS,
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


def test_requested_shortcut_map_replaces_old_file_wrap_and_suggestion_bindings():
    assert (FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT) == ("Alt+Up", "Alt+Down")
    assert WRAP_PRESET_SHORTCUTS == (
        "Ctrl+Space, Ctrl+1",
        "Ctrl+Space, Ctrl+2",
        "Ctrl+Space, Ctrl+3",
        "Ctrl+Space, Ctrl+4",
    )
    assert WRAP_ENTIRE_FILE_SHORTCUTS == ("Ctrl+Space, Ctrl+Return", "Ctrl+Space, Ctrl+Enter")
    assert SUGGESTION_SHORTCUTS == ("Ctrl+1", "Ctrl+2", "Ctrl+3")
    assert PRESET_REPLACE_SHORTCUT == "Ctrl+R"
    assert GEMINI_TRANSLATE_SHORTCUT == "Ctrl+G"
    assert "Ctrl+Space" not in editable_shortcut_sequences()
    assert "Ctrl+Alt+Enter" not in editable_shortcut_sequences()
    assert "Shift+Up" not in editable_shortcut_sequences()
    assert "Shift+Down" not in editable_shortcut_sequences()
