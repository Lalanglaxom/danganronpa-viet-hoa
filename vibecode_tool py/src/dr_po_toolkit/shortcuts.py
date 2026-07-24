from __future__ import annotations

# Shared shortcuts used by every editable translation view.
FILE_PREVIOUS_SHORTCUT = "Alt+Up"
FILE_NEXT_SHORTCUT = "Alt+Down"

# Qt represents multi-stroke shortcuts with commas. Keeping Ctrl held through
# both strokes matches the requested Ctrl+Space+digit / Ctrl+Space+Enter input.
WRAP_PRESET_SHORTCUTS = tuple(f"Ctrl+Space, Ctrl+{number}" for number in range(1, 5))
WRAP_ENTIRE_FILE_SHORTCUTS = ("Ctrl+Space, Ctrl+Return", "Ctrl+Space, Ctrl+Enter")
PRESET_REPLACE_SHORTCUT = "Ctrl+R"
GEMINI_TRANSLATE_SHORTCUT = "Ctrl+G"

SUGGESTION_SHORTCUTS = tuple(f"Ctrl+{number}" for number in range(1, 4))
SUGGESTION_REFRESH_SHORTCUT = "Alt+0"


def editable_shortcut_sequences(*, include_file_navigation: bool = True) -> tuple[str, ...]:
    sequences = [
        *WRAP_PRESET_SHORTCUTS,
        *WRAP_ENTIRE_FILE_SHORTCUTS,
        PRESET_REPLACE_SHORTCUT,
        GEMINI_TRANSLATE_SHORTCUT,
    ]
    if include_file_navigation:
        sequences.extend((FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT))
    return tuple(sequences)
