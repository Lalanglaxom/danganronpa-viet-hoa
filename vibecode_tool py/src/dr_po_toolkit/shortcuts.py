from __future__ import annotations

# Shared shortcuts used by every editable translation view.
FILE_PREVIOUS_SHORTCUT = "Alt+Up"
FILE_NEXT_SHORTCUT = "Alt+Down"

# Persistent line-wrap mode: hold Ctrl, tap Space once, then press 1/2/3/4
# repeatedly in any order. Enter wraps the entire file. Releasing Ctrl exits.
WRAP_MODE_SHORTCUT = "Ctrl+Space"
WRAP_PRESET_KEYS = ("1", "2", "3", "4")
WRAP_ENTIRE_FILE_KEYS = ("Return", "Enter")

PRESET_REPLACE_SHORTCUT = "Ctrl+R"
GEMINI_TRANSLATE_SHORTCUT = "Ctrl+G"

# Suggestions are direct held-Ctrl shortcuts and can be pressed repeatedly in
# any order without releasing Ctrl.
SUGGESTION_SHORTCUTS = tuple(f"Ctrl+{number}" for number in range(1, 4))
SUGGESTION_REFRESH_SHORTCUT = "Alt+0"


def editable_shortcut_sequences(*, include_file_navigation: bool = True) -> tuple[str, ...]:
    sequences = [
        WRAP_MODE_SHORTCUT,
        PRESET_REPLACE_SHORTCUT,
        GEMINI_TRANSLATE_SHORTCUT,
    ]
    if include_file_navigation:
        sequences.extend((FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT))
    return tuple(sequences)
