from __future__ import annotations

from collections.abc import Mapping

MAX_CUSTOM_SHORTCUT_CHORDS = 3

FILE_PREVIOUS_ACTION = "previous_file"
FILE_NEXT_ACTION = "next_file"
FILE_SHORTCUT_ACTIONS = (FILE_PREVIOUS_ACTION, FILE_NEXT_ACTION)
FILE_SHORTCUT_LABELS = {
    FILE_PREVIOUS_ACTION: "Previous PO file",
    FILE_NEXT_ACTION: "Next PO file",
}

# Legacy constants remain available to callers that only need the defaults.
FILE_PREVIOUS_SHORTCUT = "Alt+Up"
FILE_NEXT_SHORTCUT = "Alt+Down"

WRAP_PRESET_ACTIONS = ("preset_1", "preset_2", "preset_3", "preset_4")
WRAP_ENTIRE_FILE_ACTION = "entire_file"
WRAP_SHORTCUT_ACTIONS = WRAP_PRESET_ACTIONS + (WRAP_ENTIRE_FILE_ACTION,)
WRAP_SHORTCUT_LABELS = {
    "preset_1": "Wrap preset 1",
    "preset_2": "Wrap preset 2",
    "preset_3": "Wrap preset 3",
    "preset_4": "Wrap preset 4",
    "entire_file": "Wrap entire file",
}

CUSTOM_SHORTCUT_ACTIONS = WRAP_SHORTCUT_ACTIONS + FILE_SHORTCUT_ACTIONS
CUSTOM_SHORTCUT_LABELS = {**WRAP_SHORTCUT_LABELS, **FILE_SHORTCUT_LABELS}


def default_wrap_shortcuts() -> dict[str, str]:
    """Direct defaults that remain repeatable while Shift stays held."""

    return {
        "preset_1": "Shift+1",
        "preset_2": "Shift+2",
        "preset_3": "Shift+3",
        "preset_4": "Shift+4",
        "entire_file": "Shift+Return",
    }


def default_file_shortcuts() -> dict[str, str]:
    return {
        FILE_PREVIOUS_ACTION: FILE_PREVIOUS_SHORTCUT,
        FILE_NEXT_ACTION: FILE_NEXT_SHORTCUT,
    }


def shortcut_chords(sequence: str) -> tuple[str, ...]:
    """Return a case-insensitive portable representation of each chord."""

    return tuple(part.strip().casefold() for part in str(sequence).split(",") if part.strip())


def shortcut_sequences_conflict(left: str, right: str) -> bool:
    """Return True for duplicates or prefix-overlapping multi-chord sequences."""

    left_parts = shortcut_chords(left)
    right_parts = shortcut_chords(right)
    if not left_parts or not right_parts:
        return False
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def _clean_candidate(raw: object, default: str) -> str:
    candidate = str(raw).strip() if raw is not None else ""
    if len(shortcut_chords(candidate)) > MAX_CUSTOM_SHORTCUT_CHORDS:
        return default
    return candidate


def _normalize_actions(
    value: object,
    actions: tuple[str, ...],
    defaults: Mapping[str, str],
) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    normalized: dict[str, str] = {}

    for action in actions:
        candidate = _clean_candidate(source.get(action, defaults[action]), defaults[action])
        if candidate and any(shortcut_sequences_conflict(candidate, used) for used in normalized.values() if used):
            candidate = defaults[action]
            if candidate and any(shortcut_sequences_conflict(candidate, used) for used in normalized.values() if used):
                candidate = ""
        normalized[action] = candidate
    return normalized


def normalize_wrap_shortcuts(value: object) -> dict[str, str]:
    """Return a complete, non-conflicting wrap shortcut mapping."""

    return _normalize_actions(value, WRAP_SHORTCUT_ACTIONS, default_wrap_shortcuts())


def normalize_file_shortcuts(value: object) -> dict[str, str]:
    """Return complete previous/next-file shortcut assignments."""

    return _normalize_actions(value, FILE_SHORTCUT_ACTIONS, default_file_shortcuts())


def normalize_custom_shortcuts(wrap_value: object, file_value: object) -> dict[str, str]:
    """Normalize all editable shortcuts together so no action shadows another."""

    defaults = {**default_wrap_shortcuts(), **default_file_shortcuts()}
    wrap_source = wrap_value if isinstance(wrap_value, Mapping) else {}
    file_source = file_value if isinstance(file_value, Mapping) else {}
    combined_source = {**wrap_source, **file_source}
    normalized: dict[str, str] = {}

    for action in CUSTOM_SHORTCUT_ACTIONS:
        candidate = _clean_candidate(combined_source.get(action, defaults[action]), defaults[action])
        reserved_conflict = candidate and any(
            shortcut_sequences_conflict(candidate, reserved) for reserved in RESERVED_SHORTCUTS
        )
        custom_conflict = candidate and any(
            shortcut_sequences_conflict(candidate, used) for used in normalized.values() if used
        )
        if reserved_conflict or custom_conflict:
            candidate = defaults[action]
            reserved_conflict = candidate and any(
                shortcut_sequences_conflict(candidate, reserved) for reserved in RESERVED_SHORTCUTS
            )
            custom_conflict = candidate and any(
                shortcut_sequences_conflict(candidate, used) for used in normalized.values() if used
            )
            if reserved_conflict or custom_conflict:
                candidate = ""
        normalized[action] = candidate
    return normalized


PRESET_REPLACE_SHORTCUT = "Ctrl+R"
GEMINI_TRANSLATE_SHORTCUT = "Ctrl+G"

# Suggestions are direct held-Ctrl shortcuts and can be pressed repeatedly in
# any order without releasing Ctrl.
SUGGESTION_SHORTCUTS = tuple(f"Ctrl+{number}" for number in range(1, 4))
SUGGESTION_REFRESH_SHORTCUT = "Alt+0"

# Editable assignments must not shadow these fixed actions. File navigation is
# intentionally omitted because it is now part of the editable shortcut set.
RESERVED_SHORTCUTS = {
    PRESET_REPLACE_SHORTCUT: "Preset Replace",
    GEMINI_TRANSLATE_SHORTCUT: "Gemini",
    SUGGESTION_REFRESH_SHORTCUT: "Refresh suggestions",
    "Ctrl+1": "Suggestion 1",
    "Ctrl+2": "Suggestion 2",
    "Ctrl+3": "Suggestion 3",
    "Ctrl+Z": "Undo",
    "Ctrl+S": "Save",
    "Ctrl+F": "Find/replace",
    "Ctrl+Up": "Previous entry",
    "Ctrl+Down": "Next entry",
    "Ctrl+E": "Focus Vietnamese editor",
    "F2": "Focus Vietnamese editor",
}


def editable_shortcut_sequences(*, include_file_navigation: bool = True) -> tuple[str, ...]:
    sequences = [
        *default_wrap_shortcuts().values(),
        PRESET_REPLACE_SHORTCUT,
        GEMINI_TRANSLATE_SHORTCUT,
    ]
    if include_file_navigation:
        sequences.extend(default_file_shortcuts().values())
    return tuple(sequences)
