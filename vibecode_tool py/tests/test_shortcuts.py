from pathlib import Path
from tempfile import TemporaryDirectory

from dr_po_toolkit.config import load_config, save_config
from dr_po_toolkit.shortcuts import (
    CUSTOM_SHORTCUT_ACTIONS,
    FILE_NEXT_ACTION,
    FILE_NEXT_SHORTCUT,
    FILE_PREVIOUS_ACTION,
    FILE_PREVIOUS_SHORTCUT,
    FILE_SHORTCUT_ACTIONS,
    GEMINI_TRANSLATE_SHORTCUT,
    MAX_CUSTOM_SHORTCUT_CHORDS,
    PRESET_REPLACE_SHORTCUT,
    RESERVED_SHORTCUTS,
    SUGGESTION_REFRESH_SHORTCUT,
    SUGGESTION_SHORTCUTS,
    WRAP_SHORTCUT_ACTIONS,
    default_file_shortcuts,
    default_wrap_shortcuts,
    editable_shortcut_sequences,
    normalize_custom_shortcuts,
    normalize_wrap_shortcuts,
    shortcut_chords,
    shortcut_sequences_conflict,
)


def _parts(sequence: str) -> tuple[str, ...]:
    return shortcut_chords(sequence)


def test_editable_default_shortcuts_are_unique_and_have_no_prefix_overlap():
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


def test_default_wrap_and_file_shortcuts_are_direct_repeatable_keys():
    assert (FILE_PREVIOUS_SHORTCUT, FILE_NEXT_SHORTCUT) == ("Alt+Up", "Alt+Down")
    assert default_file_shortcuts() == {
        FILE_PREVIOUS_ACTION: "Alt+Up",
        FILE_NEXT_ACTION: "Alt+Down",
    }
    assert default_wrap_shortcuts() == {
        "preset_1": "Shift+1",
        "preset_2": "Shift+2",
        "preset_3": "Shift+3",
        "preset_4": "Shift+4",
        "entire_file": "Shift+Return",
    }
    assert MAX_CUSTOM_SHORTCUT_CHORDS == 3
    assert SUGGESTION_SHORTCUTS == ("Ctrl+1", "Ctrl+2", "Ctrl+3")
    assert PRESET_REPLACE_SHORTCUT == "Ctrl+R"
    assert GEMINI_TRANSLATE_SHORTCUT == "Ctrl+G"
    assert set(default_wrap_shortcuts()).issubset(WRAP_SHORTCUT_ACTIONS)
    assert set(default_file_shortcuts()).issubset(FILE_SHORTCUT_ACTIONS)
    assert set(CUSTOM_SHORTCUT_ACTIONS) == set(WRAP_SHORTCUT_ACTIONS) | set(FILE_SHORTCUT_ACTIONS)
    assert set(SUGGESTION_SHORTCUTS).issubset(RESERVED_SHORTCUTS)


def test_custom_wrap_and_file_shortcuts_normalize_and_persist():
    custom_wrap = {
        "preset_1": "Ctrl+Shift+J",
        "preset_2": "Ctrl+K, Ctrl+2",
        "preset_3": "Alt+L",
        "preset_4": "Alt+;",
        "entire_file": "Ctrl+K, Ctrl+W, Ctrl+Return",
    }
    custom_files = {
        FILE_PREVIOUS_ACTION: "Ctrl+K, Ctrl+Up",
        FILE_NEXT_ACTION: "Ctrl+K, Ctrl+Down",
    }
    normalized = normalize_custom_shortcuts(custom_wrap, custom_files)
    assert {action: normalized[action] for action in WRAP_SHORTCUT_ACTIONS} == custom_wrap
    assert {action: normalized[action] for action in FILE_SHORTCUT_ACTIONS} == custom_files

    with TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config = load_config(config_path)
        config["wrap_shortcuts"] = custom_wrap
        config["file_navigation_shortcuts"] = custom_files
        save_config(config, config_path)
        loaded = load_config(config_path)
        assert loaded["wrap_shortcuts"] == custom_wrap
        assert loaded["file_navigation_shortcuts"] == custom_files


def test_three_chord_limit_and_prefix_conflicts_are_enforced():
    assert shortcut_chords("Ctrl+K, Ctrl+W, Ctrl+1") == ("ctrl+k", "ctrl+w", "ctrl+1")
    assert shortcut_sequences_conflict("Ctrl+K", "Ctrl+K, Ctrl+W")
    assert shortcut_sequences_conflict("Ctrl+K, Ctrl+W", "Ctrl+K")
    assert not shortcut_sequences_conflict("Ctrl+K, Ctrl+W", "Ctrl+K, Ctrl+E")

    custom = default_wrap_shortcuts()
    custom["preset_1"] = "Ctrl+K, Ctrl+W, Ctrl+E, Ctrl+R"
    assert normalize_wrap_shortcuts(custom)["preset_1"] == "Shift+1"


def test_duplicate_custom_wrap_shortcut_falls_back_to_its_default():
    custom = default_wrap_shortcuts()
    custom["preset_2"] = custom["preset_1"]
    normalized = normalize_wrap_shortcuts(custom)
    assert normalized["preset_1"] == "Shift+1"
    assert normalized["preset_2"] == "Shift+2"


def test_duplicate_fallback_never_creates_a_second_duplicate():
    custom = default_wrap_shortcuts()
    custom["preset_1"] = "Shift+2"
    custom["preset_2"] = "Shift+2"
    normalized = normalize_wrap_shortcuts(custom)
    assert normalized["preset_1"] == "Shift+2"
    assert normalized["preset_2"] == ""


def test_configurable_multi_chord_filter_is_wired_to_every_editable_view():
    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert "search_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert "duplicate_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert "po_viewer_wrap_filter = PersistentWrapShortcutFilter(" in gui_source
    assert gui_source.count("self._custom_shortcut_sequences,") >= 3
    assert gui_source.count("lambda: switch_search_file(") >= 2
    assert gui_source.count("lambda: switch_duplicate_file(") >= 2
    assert gui_source.count("lambda: switch_file(") >= 2
    assert "suggestion_shortcut_filter = RepeatedSuggestionShortcutFilter(" in gui_source
    assert "event.keyCombination()" in gui_source
    assert "self._pending" in gui_source
    assert "MAX_CUSTOM_SHORTCUT_CHORDS" in gui_source
    assert "self._armed" not in gui_source


def test_shortcuts_use_settings_style_window_with_file_assignment_and_reset():
    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert 'shortcuts_btn = self._button("Shortcuts"' in gui_source
    assert "shortcuts_btn.clicked.connect(self._open_shortcuts_dialog)" in gui_source
    assert "def _open_shortcuts_dialog(self) -> None:" in gui_source
    assert 'dialog.setWindowTitle("Shortcut Settings")' in gui_source
    assert "dialog.exec()" in gui_source
    assert "self._build_shortcuts_tab()" not in gui_source
    assert 'self._new_tab("Shortcuts")' not in gui_source
    assert "editor.setMaximumSequenceLength(MAX_CUSTOM_SHORTCUT_CHORDS)" in gui_source
    assert '"PO file switching"' in gui_source
    assert 'self._button("Reset Shortcut Defaults"' in gui_source
    assert 'self.config["file_navigation_shortcuts"]' in gui_source
    assert "three held keys such as Ctrl+Shift+1" in gui_source


def test_english_and_vietnamese_editors_are_four_visible_rows():
    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert "def _set_plain_text_visible_rows(editor: QPlainTextEdit, rows: int = 4)" in gui_source
    assert gui_source.count("self._set_plain_text_visible_rows(en_box, 4)") >= 2
    assert gui_source.count("self._set_plain_text_visible_rows(vi_box, 4)") >= 2
    assert "self._set_plain_text_visible_rows(msgid_box, 4)" in gui_source
    assert "self._set_plain_text_visible_rows(msgstr_box, 4)" in gui_source


def test_gui_keeps_compact_po_viewer_and_live_suggestion_features():
    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert 'language_split = QSplitter(Qt.Orientation.Vertical)' in gui_source
    assert 'Japanese note — read only / copyable' in gui_source
    assert 'undo_suggest_btn = self._button("Undo"' in gui_source
    assert 'index.add_po_file(po, path)' in gui_source
    assert 'Choose which chapters/file groups this tab should target.' not in gui_source
    assert 'theme_note = QLabel' not in gui_source


def test_interactive_gemini_context_and_global_po_viewer_undo_are_wired():
    gui_source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    assert "context_entries=context_by_file.get(file_path, [])" in gui_source
    assert "context_entries=context_by_path.get(path, [])" in gui_source
    assert "context_entries=context_entries" in gui_source
    assert "context_entries = copy.deepcopy(list(po.entries))" in gui_source
    assert 'undo_edit_btn = self._tool_button("Undo"' in gui_source
    assert 'begin_po_undo_batch("wrap")' in gui_source
    assert 'undo_label="suggestion"' in gui_source
    assert 'begin_po_undo_batch("gemini")' in gui_source
    assert 'begin_po_undo_batch("replace all")' in gui_source
    assert 'begin_po_undo_batch("preset replace")' in gui_source
    assert 'begin_po_undo_batch("translafix")' in gui_source


def test_gemini_previous_file_context_has_compact_opt_in_toggle():
    root = Path(__file__).parents[1]
    gui_source = (root / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    config_source = (root / "src" / "dr_po_toolkit" / "config.py").read_text(encoding="utf-8")

    assert '"gemini_api_single_context_across_files": False' in config_source
    assert '"gemini_api_mass_context_across_files": False' in config_source
    assert 'api_single_context_entries.setFixedWidth(72)' in gui_source
    assert 'api_mass_context_entries.setFixedWidth(72)' in gui_source
    assert 'QCheckBox("Include previous files")' in gui_source
    assert 'Single-entry API' in gui_source
    assert 'Mass-translation API' in gui_source
    assert 'previous_file_context_entries=previous_file_context if use_previous_files else None' in gui_source


def test_gui_routes_ctrl_z_to_unified_undo_history():
    source = (Path(__file__).parents[1] / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")

    assert "class RoutedUndoShortcutFilter" in source
    assert "event.matches(QKeySequence.StandardKey.Undo)" in source
    assert "RoutedUndoShortcutFilter(_tab, undo_last_search_change)" in source
    assert "RoutedUndoShortcutFilter(dialog, undo_duplicate_change)" in source
    assert "RoutedUndoShortcutFilter(_tab, undo_last_po_change)" in source


def test_search_character_counts_and_async_gemini_workers_are_wired():
    root = Path(__file__).parents[1]
    gui_source = (root / "src" / "dr_po_toolkit" / "gui.py").read_text(encoding="utf-8")
    config_source = (root / "src" / "dr_po_toolkit" / "config.py").read_text(encoding="utf-8")

    assert 'en_character_count_label = QLabel("—")' in gui_source
    assert 'vi_character_count_label = QLabel("—")' in gui_source
    assert "msgid_box.textChanged.connect(update_search_character_counts)" in gui_source
    assert "msgstr_box.textChanged.connect(update_search_character_counts)" in gui_source
    assert gui_source.count("threading.Thread(target=gemini_worker, daemon=True)") >= 3
    assert 'self._gemini_api_timeout_seconds("single")' in gui_source
    assert 'self._gemini_api_timeout_seconds("mass")' in gui_source
    assert '"gemini_api_single_timeout_seconds": 90' in config_source
    assert '"gemini_api_mass_timeout_seconds": 90' in config_source
