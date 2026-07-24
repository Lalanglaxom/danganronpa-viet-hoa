from __future__ import annotations

# Shared shortcuts used by every editable translation view.
FILE_PREVIOUS_SHORTCUT = "Alt+Up"
FILE_NEXT_SHORTCUT = "Alt+Down"
WRAP_CURRENT_PRESET_SHORTCUT = "Ctrl+Space"
WRAP_PRESET_SHORTCUTS = tuple(f"Ctrl+{number}, Ctrl+Space" for number in range(1, 5))
WRAP_ENTIRE_FILE_SHORTCUTS = ("Ctrl+Alt+Return", "Ctrl+Alt+Enter")
PRESET_REPLACE_SHORTCUT = "Ctrl+R"
GEMINI_TRANSLATE_SHORTCUT = "Ctrl+G"

# Ctrl+1..4 are prefixes of the wrap-preset key chords, so suggestion shortcuts
# use Alt+digits instead. This prevents Qt shortcut ambiguity/delays.
SUGGESTION_SHORTCUTS = tuple(f"Alt+{number}" for number in range(1, 10))
SUGGESTION_REFRESH_SHORTCUT = "Alt+0"


def editable_shortcut_sequences(*, include_file_navigation: bool = True) -> tuple[str, ...]:
    sequences = [
        WRAP_CURRENT_PRESET_SHORTCUT,
        *WRAP_PRESET_SHORTCUTS,
        *WRAP_ENTIRE_FILE_SHORTCUTS,
        PRESET_REPLACE_SHORTCUT,
        GEMINI_TRANSLATE_SHORTCUT,
    ]
    if include_file_navigation:
        sequences.extend((FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT))
    return tuple(sequences)
