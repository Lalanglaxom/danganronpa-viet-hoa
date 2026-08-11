from __future__ import annotations

import copy
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, pyqtSignal, QRectF, QSize, QEventLoop
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QBrush, QTextDocument, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QKeySequenceEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

from .app_links import APP_LINK_SERVER_NAME, is_entry_url, parse_entry_url, register_url_protocol
from .backup import index_po_files_by_name, sync_by_filename_report
from .drat_repack import (
    DratRepackError,
    deploy_filename_plans,
    plan_files_by_filename,
    repack_all_formats,
    repack_all_wads,
    resolve_drat_workspace,
)
from .cancel import OperationCancelled
from .config import load_config, save_config
from .discovery import iter_po_files
from .dr_options import DR_FILE_OPTIONS, DR_FILE_OPTION_KEYS, default_selected_options, option_name
from .git_tools import (
    DANGANVIETHOA_REPOSITORY_URL,
    build_pull_command,
    build_push_command,
    create_commit_message_file,
    create_push_script,
    launch_windows_cmd,
    validate_repository_folder,
)
from .gemini_web import (
    DEFAULT_BATCH_RETRIES,
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_GEMINI_URL,
    DEFAULT_MAX_ENTRIES_PER_BATCH,
    discover_untranslated_po_files,
    open_chrome_debug,
    run_gemini_web_path,
)
from .chatgpt_web import DEFAULT_CHATGPT_URL, run_chatgpt_web_path
from .linewrap import normalize_wrap_presets, wrap_msgstr, wrap_po_file
from .models import POEntry
from .notifications import play_task_notification
from .po_io import load_po, save_po
from .text_index import get_cached_po, load_po_clone, prime_text_index
from .rules import apply_rules_to_entry, apply_rules_to_file, load_rules, rule_to_dict
from .search import SearchResult, search_files
from .startup_index import configured_po_files
from .shortcuts import (
    CUSTOM_SHORTCUT_ACTIONS,
    CUSTOM_SHORTCUT_LABELS,
    FILE_NEXT_ACTION,
    FILE_PREVIOUS_ACTION,
    FILE_SHORTCUT_ACTIONS,
    GEMINI_TRANSLATE_SHORTCUT,
    MAX_CUSTOM_SHORTCUT_CHORDS,
    PRESET_REPLACE_SHORTCUT,
    RESERVED_SHORTCUTS,
    SUGGESTION_REFRESH_SHORTCUT,
    WRAP_ENTIRE_FILE_ACTION,
    WRAP_PRESET_ACTIONS,
    WRAP_SHORTCUT_ACTIONS,
    default_file_shortcuts,
    default_wrap_shortcuts,
    normalize_custom_shortcuts,
    shortcut_sequences_conflict,
)
from .translator import GeminiApiClient, SYSTEM_INSTRUCTIONS, translate_entries_with_client, translate_file_with_client
from .translafixer import (
    ReferenceTranslationConflictEntry,
    TranslationSuggestionIndex,
    apply_translafix,
    build_translation_map,
    collect_source_po_files,
    find_reference_duplicate_sources,
    find_reference_translation_conflicts,
    msgid_match_key,
    suggestion_match_key,
)
from .validation import format_text_report, validate_path, write_reports
from .text_utils import (
    SearchReplaceCompileError,
    apply_search_replace_sequence,
    compile_search_replace_sequence,
    html_escape_preserve_spacing,
    user_multiline_text,
    visible_character_counts_by_line,
)

# Chiaki Nanami inspired theme palette: sleepy gamer, soft pink, muted teal,
# dusty lavender, cream text, and deep charcoal blue panels.
BG = "#151a26"
PANEL = "#202638"
PANEL_2 = "#2a3046"
PANEL_3 = "#343a55"
TEXT = "#f7eef5"
WHITE = "#fff8fc"
MUTED = "#b8afc5"
ACCENT = "#f2a6c7"
ACCENT_DARK = "#b95d8b"
ACCENT_SOFT = "#ffd7e8"
TEAL = "#5f8d94"
TEAL_DARK = "#24444b"
WARN = "#f6d58b"
BAD = "#f38aa3"
GOOD = "#91d7b7"
BLUE = "#8bbcf5"
CYAN = "#7fe7ff"
YELLOW = "#ffe66d"
ORANGE = "#ffb05c"
PURPLE = "#c7a7ee"
CLT_COLOR_BY_CODE = {
    "3": YELLOW,
    "4": CYAN,
    "9": ORANGE,
    "23": GOOD,
    "n": PURPLE,
}
CLT_CODE_BY_STATE = {index: code for index, code in enumerate(CLT_COLOR_BY_CODE, start=1)}
CLT_STATE_BY_CODE = {code: state for state, code in CLT_CODE_BY_STATE.items()}



def _normalize_clt_code(raw: str | None) -> str:
    return re.sub(r"[\s_]+", "", raw or "").lower()


def clt_color_for_code(raw: str | None) -> str:
    code = _normalize_clt_code(raw)
    return CLT_COLOR_BY_CODE.get(code, BLUE)


def _clt_state_for_code(raw: str | None) -> int:
    code = _normalize_clt_code(raw)
    if not code:
        return 0
    return CLT_STATE_BY_CODE.get(code, CLT_STATE_BY_CODE["4"])


def _clt_code_for_state(state: int) -> str | None:
    return CLT_CODE_BY_STATE.get(state)
# Search result colors. EN is no longer dark yellow: it is now a dusty
# periwinkle-lavender that matches the Chiaki-inspired app theme.
EN_BG = "#3b3458"
VI_BG = "#1f3b42"
EN_HIT_BG = "#67508e"
VI_HIT_BG = "#2f5d66"
ENTRY_FOCUS_BG = "#7b648f"
CONTEXT_BG = "#1b2030"
HTML_ROLE = 0x0100 + 91


class WorkerSignals(QObject):
    log = pyqtSignal(str, str)
    result = pyqtSignal(object)
    progress = pyqtSignal(int, int, str)
    error = pyqtSignal(str)
    done = pyqtSignal()


class LogBox(QTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 8))
        self.setAcceptRichText(True)
        self.setObjectName("logBox")

    def append_log(self, text: str, level: str = "") -> None:
        color = {
            "good": GOOD,
            "bad": BAD,
            "warn": WARN,
            "info": BLUE,
        }.get(level or "", TEXT)
        escaped = html.escape(str(text)).replace("\n", "<br>")
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertHtml(f'<span style="color:{color}; white-space:pre-wrap;">{escaped}</span><br>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class PathDropList(QListWidget):
    pathsDropped = pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setObjectName("pathList")

    def dragEnterEvent(self, event):  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # type: ignore[override]
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.pathsDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class CltHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor(BLUE))
        self.text_token_format = QTextCharFormat()
        self.text_token_format.setForeground(QColor(PURPLE))
        self.pattern = re.compile(r"<\s*clt(?P<code>[\s_]*(?:\d+|n))?\s*>", re.IGNORECASE)
        self.text_token_pattern = re.compile(r"%TEXT%", re.IGNORECASE)
        self.color_spans = False
        self._span_formats: dict[str, QTextCharFormat] = {}

    def _span_format(self, code: str | None) -> QTextCharFormat:
        key = _normalize_clt_code(code) or "4"
        fmt = self._span_formats.get(key)
        if fmt is None:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(clt_color_for_code(key)))
            self._span_formats[key] = fmt
        return fmt

    def set_color_spans(self, enabled: bool) -> None:
        self.color_spans = enabled
        self.rehighlight()

    def _highlight_text_tokens(self, text: str) -> None:
        for match in self.text_token_pattern.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.text_token_format)

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        if not self.color_spans:
            for match in self.pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self.tag_format)
            self._highlight_text_tokens(text)
            self.setCurrentBlockState(0)
            return

        active_code = _clt_code_for_state(self.previousBlockState())
        cursor = 0
        for match in self.pattern.finditer(text):
            if active_code and match.start() > cursor:
                self.setFormat(cursor, match.start() - cursor, self._span_format(active_code))
            # Keep raw tags visible in the editor, but color the in-game text
            # between tags like the game does. Table/suggestion color view hides tags.
            self.setFormat(match.start(), match.end() - match.start(), self.tag_format)
            if match.group("code"):
                active_code = _normalize_clt_code(match.group("code")) or "4"
            else:
                active_code = None
            cursor = match.end()
        if active_code and cursor < len(text):
            self.setFormat(cursor, len(text) - cursor, self._span_format(active_code))
        self._highlight_text_tokens(text)
        self.setCurrentBlockState(_clt_state_for_code(active_code))


class VisibleNewlinePlainTextEdit(QPlainTextEdit):
    """Plain PO text editor; per-line metrics are shown above the field."""


CLT_TAG_RE = re.compile(r"<\s*clt(?P<code>[\s_]*(?:\d+|n))?\s*>", re.IGNORECASE)
TEXT_TOKEN_RE = re.compile(r"%TEXT%", re.IGNORECASE)


def _text_token_html(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in TEXT_TOKEN_RE.finditer(text or ""):
        parts.append(html_escape_preserve_spacing((text or "")[last:match.start()]))
        parts.append(f'<span style="color:{PURPLE};">{html_escape_preserve_spacing(match.group(0))}</span>')
        last = match.end()
    parts.append(html_escape_preserve_spacing((text or "")[last:]))
    return "".join(parts)


def _text_with_newline_markers_html(text: str) -> str:
    parts: list[str] = []
    chunks = (text or "").split("\n")
    for idx, chunk in enumerate(chunks):
        parts.append(_text_token_html(chunk))
        if idx < len(chunks) - 1:
            parts.append(f'<span style="color:{BAD};">\\n</span><br>')
    return "".join(parts)


def clt_rich_html(text: str, *, color_mode: bool = False) -> str:
    parts: list[str] = []
    value = text or ""
    last = 0
    active_code: str | None = None
    for match in CLT_TAG_RE.finditer(value):
        segment = value[last:match.start()]
        if color_mode and active_code and segment:
            parts.append(
                f'<span style="color:{clt_color_for_code(active_code)};">'
                f'{_text_with_newline_markers_html(segment)}</span>'
            )
        else:
            parts.append(_text_with_newline_markers_html(segment))
        if color_mode:
            active_code = _normalize_clt_code(match.group("code")) if match.group("code") else None
        else:
            parts.append(f'<span style="color:{BLUE};">{html.escape(match.group(0))}</span>')
        last = match.end()
    tail = value[last:]
    if color_mode and active_code and tail:
        parts.append(
            f'<span style="color:{clt_color_for_code(active_code)};">'
            f'{_text_with_newline_markers_html(tail)}</span>'
        )
    else:
        parts.append(_text_with_newline_markers_html(tail))
    return "".join(parts)


class NoFocusCellDelegate(QStyledItemDelegate):
    """Hide focus rectangles while keeping a visible current-entry highlight.

    PO Viewer navigation often keeps focus in the Vietnamese editor. In that state Qt can
    draw the table selection as inactive, or leave older multi-row selections looking like
    the active entry. For the PO Viewer table, always paint only the current row as the
    highlighted entry, regardless of which widget has keyboard focus.
    """

    def _view_option(self, option, index) -> QStyleOptionViewItem:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus

        widget = opt.widget
        current_index_getter = getattr(widget, "currentIndex", None) if widget is not None else None
        if widget is not None and widget.objectName() == "poViewerTable" and callable(current_index_getter):
            current = current_index_getter()
            if current.isValid() and current.row() == index.row():
                opt.state |= QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_Active
            else:
                opt.state &= ~QStyle.StateFlag.State_Selected
        return opt

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        opt = self._view_option(option, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)


class RichTextCellDelegate(NoFocusCellDelegate):
    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        html_text = index.data(HTML_ROLE)
        if not html_text:
            super().paint(painter, option, index)
            return
        opt = self._view_option(option, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(4)
        doc.setHtml(str(html_text))
        width = max(40, opt.rect.width() - 8)
        height = max(20, opt.rect.height() - 8)
        doc.setTextWidth(width)

        painter.save()
        painter.translate(opt.rect.left() + 4, opt.rect.top() + 4)
        doc.drawContents(painter, QRectF(0, 0, width, height))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        html_text = index.data(HTML_ROLE)
        if not html_text:
            return super().sizeHint(option, index)
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(4)
        doc.setHtml(str(html_text))
        width = max(180, option.rect.width() - 8) if option.rect.width() > 0 else 360
        doc.setTextWidth(width)
        return QSize(int(width) + 8, int(doc.size().height()) + 8)



def _shortcut_digit_from_key(key: int) -> int | None:
    first = Qt.Key.Key_0.value
    last = Qt.Key.Key_9.value
    value = int(key)
    if first <= value <= last:
        return value - first
    return None


def _shortcut_focus_is_inside(root: QWidget) -> bool:
    focus = QApplication.focusWidget()
    return focus is root or (focus is not None and root.isAncestorOf(focus))


class PersistentWrapShortcutFilter(QObject):
    """Apply editable wrap and file shortcuts, including three-chord sequences.

    Direct one-chord assignments stay repeatable while a modifier remains held.
    Longer assignments may contain up to three comma-separated key chords.
    """

    _SEQUENCE_TIMEOUT_SECONDS = 1.5
    _MODIFIER_KEYS = {
        Qt.Key.Key_Shift.value,
        Qt.Key.Key_Control.value,
        Qt.Key.Key_Alt.value,
        Qt.Key.Key_Meta.value,
    }

    def __init__(
        self,
        root: QWidget,
        preset_callbacks: dict[int, Callable[[], None]],
        wrap_entire_file: Callable[[], None],
        shortcut_sequences: Callable[[], dict[str, str]],
        previous_file: Callable[[], None] | None = None,
        next_file: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(root)
        self._root = root
        self._callbacks: dict[str, Callable[[], None]] = {
            WRAP_PRESET_ACTIONS[number - 1]: callback
            for number, callback in preset_callbacks.items()
            if 1 <= number <= len(WRAP_PRESET_ACTIONS)
        }
        self._callbacks[WRAP_ENTIRE_FILE_ACTION] = wrap_entire_file
        if previous_file is not None:
            self._callbacks[FILE_PREVIOUS_ACTION] = previous_file
        if next_file is not None:
            self._callbacks[FILE_NEXT_ACTION] = next_file
        self._shortcut_sequences = shortcut_sequences
        self._pending: tuple[object, ...] = ()
        self._pending_at = 0.0
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    @staticmethod
    def _parts(sequence_text: str) -> tuple[object, ...]:
        if not sequence_text:
            return ()
        sequence = QKeySequence(sequence_text)
        return tuple(sequence[index] for index in range(min(sequence.count(), MAX_CUSTOM_SHORTCUT_CHORDS)))

    @staticmethod
    def _starts_with(sequence: tuple[object, ...], prefix: tuple[object, ...]) -> bool:
        return len(prefix) <= len(sequence) and sequence[: len(prefix)] == prefix

    def _matching_actions(
        self,
        candidate: tuple[object, ...],
        sequences: dict[str, str],
    ) -> list[tuple[str, tuple[object, ...]]]:
        matches: list[tuple[str, tuple[object, ...]]] = []
        for action in CUSTOM_SHORTCUT_ACTIONS:
            callback = self._callbacks.get(action)
            parts = self._parts(sequences.get(action, ""))
            if callback is not None and parts and self._starts_with(parts, candidate):
                matches.append((action, parts))
        return matches

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.KeyPress:
            return False
        if not _shortcut_focus_is_inside(self._root):
            self._pending = ()
            return False
        if int(event.key()) in self._MODIFIER_KEYS:  # type: ignore[attr-defined]
            return False

        now = time.monotonic()
        if self._pending and now - self._pending_at > self._SEQUENCE_TIMEOUT_SECONDS:
            self._pending = ()

        try:
            chord = event.keyCombination()  # type: ignore[attr-defined]
        except AttributeError:
            return False

        sequences = self._shortcut_sequences()
        candidate = self._pending + (chord,)
        matches = self._matching_actions(candidate, sequences)
        if not matches and self._pending:
            self._pending = ()
            candidate = (chord,)
            matches = self._matching_actions(candidate, sequences)
        if not matches:
            return False

        exact = next(((action, parts) for action, parts in matches if len(parts) == len(candidate)), None)
        if exact is not None:
            self._pending = ()
            callback = self._callbacks.get(exact[0])
            if callback is not None and not event.isAutoRepeat():  # type: ignore[attr-defined]
                callback()
            return True

        if event.isAutoRepeat():  # type: ignore[attr-defined]
            return True
        self._pending = candidate
        self._pending_at = now
        return True


class RepeatedSuggestionShortcutFilter(QObject):
    """Apply Ctrl+1/2/3 repeatedly while Ctrl remains held in PO Viewer."""

    def __init__(self, root: QWidget, callbacks: dict[int, Callable[[], None]]) -> None:
        super().__init__(root)
        self._root = root
        self._callbacks = dict(callbacks)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.KeyPress:
            return False
        if not _shortcut_focus_is_inside(self._root):
            return False

        modifiers = event.modifiers()  # type: ignore[attr-defined]
        ctrl_held = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        blocked_modifier = bool(
            modifiers
            & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        if not ctrl_held or blocked_modifier:
            return False

        digit = _shortcut_digit_from_key(event.key())  # type: ignore[attr-defined]
        callback = self._callbacks.get(digit or -1)
        if callback is None:
            return False
        if not event.isAutoRepeat():  # type: ignore[attr-defined]
            callback()
        return True


class RoutedUndoShortcutFilter(QObject):
    """Route the platform Undo shortcut through a view's unified undo handler.

    Qt text editors otherwise consume Ctrl+Z/Command+Z before the parent view's
    shortcut can restore non-typing actions such as suggestions or line wrapping.
    """

    def __init__(self, root: QWidget, callback: Callable[[], None]) -> None:
        super().__init__(root)
        self._root = root
        self._callback = callback
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.KeyPress or not _shortcut_focus_is_inside(self._root):
            return False
        try:
            is_undo = event.matches(QKeySequence.StandardKey.Undo)  # type: ignore[attr-defined]
        except Exception:
            is_undo = False
        if not is_undo:
            return False
        if not event.isAutoRepeat():  # type: ignore[attr-defined]
            self._callback()
        return True


class ToolkitGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._stop_event = threading.Event()
        self._active_thread: threading.Thread | None = None
        self._active_signals: list[WorkerSignals] = []
        self._startup_index_thread: threading.Thread | None = None
        self._startup_index_started = False
        self._startup_index_file_count = 0
        self._startup_index_error = ""
        self._active_log: LogBox | None = None
        self._task_progress_token = 0
        self._task_progress_active = False
        self.search_results: list[SearchResult] = []
        self.search_source_paths: list[str] = []
        self.search_last_index: int = -1
        self.rule_list_data: list[dict] = []
        self._dr_option_widgets: dict[str, dict[str, QCheckBox]] = {}
        self._reference_duplicates_dialog: QDialog | None = None
        self.rule_loading_fields = False
        self.rule_auto_timer: QTimer | None = None

        self.setWindowTitle("Chiaki PO Toolkit — PyQt")
        self.resize(1180, 720)
        self._apply_style()
        self._build()

    def start_initial_indexing(self) -> None:
        """Warm all configured PO files in the background after the window opens."""

        if self._startup_index_started:
            return
        self._startup_index_started = True
        config_snapshot = dict(self.config)

        def worker() -> None:
            try:
                po_files = configured_po_files(config_snapshot)
                self._startup_index_file_count = len(po_files)
                prime_text_index(po_files, parse_entries=True)
            except Exception as exc:
                # Startup warming is an optimisation. Feature calls still refresh
                # files on demand, so an indexing error must never prevent launch.
                self._startup_index_error = str(exc)

        thread = threading.Thread(target=worker, name="startup-po-index", daemon=True)
        self._startup_index_thread = thread
        thread.start()

    def open_app_link(self, value: str) -> bool:
        link = parse_entry_url(value)
        if link is None:
            return False
        opener = getattr(self, "_open_file_in_po_viewer", None)
        if not callable(opener):
            return False
        return bool(opener(link.file, context=link.context or None, line=link.line or None))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: Segoe UI, Arial;
                font-size: 9pt;
            }}
            QWidget#compactSuggestion {{ background: transparent; }}
            QTabWidget::pane {{
                border: 1px solid #3a4058;
                border-radius: 12px;
                background: {BG};
            }}
            QTabBar::tab {{
                background: {PANEL};
                color: {MUTED};
                padding: 6px 12px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 3px;
                border: 1px solid #30364d;
                border-bottom: 0;
            }}
            QTabBar::tab:selected {{
                background: {PANEL_2};
                color: {ACCENT};
                font-weight: 800;
            }}
            QTabBar::tab:hover {{
                color: {ACCENT_SOFT};
                background: {PANEL_3};
            }}
            QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QTableWidget, QListWidget {{
                background: {PANEL};
                color: {TEXT};
                border: 1px solid #3a4058;
                border-radius: 7px;
                padding: 4px;
                selection-background-color: {ACCENT_DARK};
                selection-color: {WHITE};
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
                border: 1px solid {ACCENT};
            }}
            QTableWidget {{
                gridline-color: #3a4058;
                alternate-background-color: #1a2030;
            }}
            QTableWidget::item {{
                padding: 3px;
                border-bottom: 1px solid #2a3144;
            }}
            QTableWidget::item:selected {{
                background: #6f5a88;
                color: {WHITE};
            }}
            QTableWidget#poViewerTable::item:selected,
            QTableWidget#poViewerTable::item:selected:!active {{
                background: {ENTRY_FOCUS_BG};
                color: {WHITE};
                outline: none;
            }}
            QTableWidget#searchResultsTable::item:selected {{
                background: #6f5a88;
                color: {WHITE};
                outline: none;
            }}
            QHeaderView::section {{
                background: {PANEL_2};
                color: {ACCENT_SOFT};
                padding: 4px;
                border: 0;
                border-right: 1px solid #3a4058;
                font-weight: 800;
            }}
            QPushButton {{
                background: {ACCENT};
                color: #28131e;
                border: 0;
                border-radius: 7px;
                padding: 6px 10px;
                font-weight: 900;
            }}
            QPushButton:hover {{ background: {ACCENT_SOFT}; }}
            QPushButton:pressed {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QPushButton:disabled {{ background: #465066; color: #968da6; }}
            QPushButton#primaryButton {{ background: {ACCENT}; color: #28131e; }}
            QPushButton#successButton {{ background: {GOOD}; color: #10251d; }}
            QPushButton#infoButton {{ background: {BLUE}; color: #0d2138; }}
            QPushButton#warnButton {{ background: {WARN}; color: #302108; }}
            QPushButton#deployButton {{ background: {ORANGE}; color: #2c1700; }}
            QPushButton#dangerButton {{ background: {BAD}; color: #241018; }}
            QPushButton#primaryButton:hover, QPushButton#successButton:hover, QPushButton#infoButton:hover, QPushButton#warnButton:hover, QPushButton#deployButton:hover, QPushButton#dangerButton:hover {{ background: {ACCENT_SOFT}; color: #28131e; }}
            QPushButton#secondaryButton {{ background: {PANEL_3}; color: {TEXT}; border: 1px solid #46506a; }}
            QPushButton#secondaryButton:hover {{ background: #46506a; color: {ACCENT_SOFT}; }}
            QToolButton {{
                background: {PANEL_3};
                color: {TEXT};
                border: 1px solid #46506a;
                border-radius: 7px;
                padding: 4px;
                font-weight: 900;
            }}
            QToolButton:hover {{ background: #46506a; color: {ACCENT_SOFT}; }}
            QToolButton:pressed {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QToolButton[wrapPreset="true"] {{ padding: 3px 6px; }}
            QToolButton[wrapActive="true"] {{ background: {TEAL}; color: {WHITE}; border: 1px solid {CYAN}; }}
            QCheckBox {{ spacing: 5px; color: {TEXT}; }}
            QCheckBox::indicator {{ width: 13px; height: 13px; }}
            QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT_SOFT}; border-radius: 4px; }}
            QCheckBox::indicator:unchecked {{ background: {PANEL}; border: 1px solid #657089; border-radius: 4px; }}
            QGroupBox {{
                border: 1px solid #3a4058;
                border-radius: 9px;
                margin-top: 8px;
                padding: 8px;
                font-weight: 800;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {ACCENT};
            }}
            QLabel#title {{
                font-size: 15pt;
                font-weight: 900;
                color: {ACCENT};
                letter-spacing: 0.5px;
            }}
            QLabel#muted {{ color: {MUTED}; }}
            QTextEdit#logBox {{ background: #101521; border: 1px solid #3a4058; }}
            QListWidget#pathList::item {{ padding: 4px; border-radius: 6px; }}
            QListWidget#pathList::item:selected {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QProgressBar {{
                background: transparent;
                color: {ACCENT_SOFT};
                border: none;
                padding: 0 4px;
                text-align: center;
                font-weight: 800;
            }}
            QProgressBar::chunk {{ background: transparent; border: none; }}
            QSplitter::handle {{ background: #2a3144; }}
            """
        )

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("☾ Chiaki PO Toolkit")
        title.setObjectName("title")
        top.addWidget(title)
        self.task_progress = QProgressBar()
        self.task_progress.setTextVisible(True)
        self.task_progress.setMinimumHeight(18)
        self.task_progress.setMinimumWidth(280)
        self.task_progress.setMaximumWidth(560)
        self.task_progress.hide()
        top.addWidget(self.task_progress, 1)
        top.addStretch()
        settings_btn = self._button("Settings", secondary=True)
        settings_btn.clicked.connect(self._open_settings_dialog)
        top.addWidget(settings_btn)
        shortcuts_btn = self._button("Shortcuts", secondary=True)
        shortcuts_btn.setToolTip("Open shortcut assignments and instructions.")
        shortcuts_btn.clicked.connect(self._open_shortcuts_dialog)
        top.addWidget(shortcuts_btn)
        self.stop_button = QPushButton("Stop Current Action")
        self.stop_button.setToolTip("Request the current long-running action to stop at the next safe checkpoint.")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_stop)
        top.addWidget(self.stop_button)
        layout.addLayout(top)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        self._build_validate_tab()
        self._build_replace_tab()
        self._build_linewrap_tab()
        self._build_search_tab()
        self._build_translafixer_tab()
        self._build_po_viewer_tab()
        self._build_translate_tab()
        self._build_repack_tab()

    def _new_tab(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.tabs.addTab(tab, title)
        return tab, layout

    def _custom_shortcut_sequences(self) -> dict[str, str]:
        shortcuts = normalize_custom_shortcuts(
            self.config.get("wrap_shortcuts"),
            self.config.get("file_navigation_shortcuts"),
        )
        self.config["wrap_shortcuts"] = {action: shortcuts[action] for action in WRAP_SHORTCUT_ACTIONS}
        self.config["file_navigation_shortcuts"] = {action: shortcuts[action] for action in FILE_SHORTCUT_ACTIONS}
        return dict(shortcuts)

    def _wrap_shortcut_sequences(self) -> dict[str, str]:
        shortcuts = self._custom_shortcut_sequences()
        return {action: shortcuts[action] for action in WRAP_SHORTCUT_ACTIONS}

    def _store_custom_shortcut_sequences(self, shortcuts: dict[str, str]) -> None:
        self.config["wrap_shortcuts"] = {action: shortcuts.get(action, "") for action in WRAP_SHORTCUT_ACTIONS}
        self.config["file_navigation_shortcuts"] = {
            action: shortcuts.get(action, "") for action in FILE_SHORTCUT_ACTIONS
        }
        save_config(self.config)
        self._refresh_linewrap_preset_buttons()

    @staticmethod
    def _portable_shortcut_text(sequence: QKeySequence | str) -> str:
        value = sequence if isinstance(sequence, QKeySequence) else QKeySequence(sequence)
        return value.toString(QKeySequence.SequenceFormat.PortableText).strip()

    @staticmethod
    def _set_plain_text_visible_rows(editor: QPlainTextEdit, rows: int = 4) -> None:
        """Keep compact editors at an exact number of visible text rows."""

        row_count = max(1, int(rows))
        margins = editor.contentsMargins()
        document_padding = int(editor.document().documentMargin() * 2)
        height = (editor.fontMetrics().lineSpacing() * row_count) + document_padding
        height += margins.top() + margins.bottom() + (editor.frameWidth() * 2) + 8
        editor.setMinimumHeight(height)
        editor.setMaximumHeight(height)

    def _shortcut_instructions_for_tab(self, title: str, shortcuts: dict[str, str]) -> str:
        """Return context-sensitive shortcut/use instructions for a top-level tab."""

        def key(action: str) -> str:
            value = shortcuts.get(action, "")
            return html.escape(value or "Disabled")

        common_editor = (
            "<b>Ctrl+S</b> saves, <b>Ctrl+Z</b> undoes, <b>Ctrl+F</b> opens find/replace, "
            "<b>Ctrl+R</b> applies enabled preset replacement rules, and <b>Ctrl+G</b> runs Gemini "
            "for the selected/current entries."
        )
        wrap_keys = (
            f"Wrap presets: <b>{key('preset_1')}</b>, <b>{key('preset_2')}</b>, "
            f"<b>{key('preset_3')}</b>, <b>{key('preset_4')}</b>. "
            f"Whole-file wrap: <b>{key(WRAP_ENTIRE_FILE_ACTION)}</b>."
        )
        file_keys = (
            f"Switch PO files with <b>{key(FILE_PREVIOUS_ACTION)}</b> / "
            f"<b>{key(FILE_NEXT_ACTION)}</b>."
        )

        instructions = {
            "Validate": (
                "Choose the working folders and optional extra path, then use <b>Run Validate</b>. "
                "This tab has no direct action shortcut; validation runs only when you start it here."
            ),
            "Rules & Replace": (
                "Replacement rules execute <b>strictly from top to bottom</b>. Drag a rule to change its position; "
                "each lower rule receives the text produced by the rules above it. Use <b>Run Replace</b> for mass replacement. "
                "<b>Ctrl+R</b> is the preset-replace shortcut in Search, duplicate views, and PO Viewer."
            ),
            "Line Wrap": (
                "Configure the four wrap presets here. The assigned wrap keys are used from Search, duplicate views, "
                f"and PO Viewer. {wrap_keys}"
            ),
            "Search": (
                f"{common_editor} {wrap_keys} {file_keys} Select rows to apply an action to several results; "
                "otherwise the current row is used."
            ),
            "Translafixer": (
                "Use this tab to scan/apply reference translations and open duplicate/conflict views. "
                f"Inside the duplicate view, {common_editor} {wrap_keys} {file_keys}"
            ),
            "PO Viewer": (
                f"{common_editor} <b>Ctrl+Up</b> / <b>Ctrl+Down</b> moves between entries; "
                "<b>Ctrl+E</b> or <b>F2</b> focuses Vietnamese; hold Ctrl and press <b>1</b>/<b>2</b>/<b>3</b> "
                f"for suggestions; <b>Alt+0</b> rebuilds suggestions. {wrap_keys} {file_keys}"
            ),
            "AI Translation": (
                "Configure Web/API translation and run the selected mode from this tab. "
                "<b>Ctrl+G</b> is used for single-entry Gemini translation in Search, duplicate views, and PO Viewer; "
                "it does not start the mass-translation run here."
            ),
            "Repack": (
                "Choose the configured targets, then use <b>Repack</b> or <b>Sync by Filename</b>. "
                "This tab has no direct action shortcut so a repack cannot be triggered accidentally."
            ),
        }
        return instructions.get(title, "Use the controls shown in this tab. No tab-specific shortcut instructions are defined.")

    def _open_shortcuts_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Shortcut Settings")
        dialog.resize(820, 650)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "Assign shortcuts for wrapping and switching PO files. A shortcut may be one chord, including "
            "three held keys such as Ctrl+Shift+1, or a sequence of up to three chords. Single-chord "
            "wrap shortcuts remain repeatable while their modifier is held. The first section below shows instructions "
            "for the tab that was active when you opened this window."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(10)

        current = self._custom_shortcut_sequences()
        shortcut_editors: dict[str, QKeySequenceEdit] = {}

        current_tab_title = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else ""
        current_tab_group = QGroupBox(f"Current tab — {current_tab_title or 'Unknown'}")
        current_tab_layout = QVBoxLayout(current_tab_group)
        current_tab_help = QLabel(self._shortcut_instructions_for_tab(current_tab_title, current))
        current_tab_help.setWordWrap(True)
        current_tab_help.setTextFormat(Qt.TextFormat.RichText)
        current_tab_help.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        current_tab_layout.addWidget(current_tab_help)
        content_layout.addWidget(current_tab_group)

        def add_assignment_group(title: str, actions: tuple[str, ...], note_text: str) -> None:
            group = QGroupBox(title)
            grid = QGridLayout(group)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(7)
            note = QLabel(note_text)
            note.setWordWrap(True)
            note.setObjectName("muted")
            grid.addWidget(note, 0, 0, 1, 2)
            for row, action in enumerate(actions, start=1):
                label = QLabel(CUSTOM_SHORTCUT_LABELS[action])
                editor = QKeySequenceEdit()
                editor.setMaximumSequenceLength(MAX_CUSTOM_SHORTCUT_CHORDS)
                editor.setClearButtonEnabled(True)
                editor.setKeySequence(QKeySequence(current[action]))
                editor.setToolTip(
                    "Press one chord or a sequence of up to three chords. Backspace or the clear button disables it."
                )
                grid.addWidget(label, row, 0)
                grid.addWidget(editor, row, 1)
                shortcut_editors[action] = editor
            grid.setColumnStretch(1, 1)
            content_layout.addWidget(group)

        add_assignment_group(
            "Wrap shortcuts",
            WRAP_SHORTCUT_ACTIONS,
            "Direct one-chord presets can be pressed repeatedly in any order without releasing a held modifier.",
        )
        add_assignment_group(
            "PO file switching",
            FILE_SHORTCUT_ACTIONS,
            "These assignments switch to the previous or next loaded PO file in Search, duplicate views, and PO Viewer.",
        )

        shortcut_status = QLabel("Changes save when each shortcut field finishes recording.")
        shortcut_status.setWordWrap(True)
        shortcut_status.setObjectName("muted")
        content_layout.addWidget(shortcut_status)

        help_view = QTextEdit()
        help_view.setReadOnly(True)
        help_view.setMinimumHeight(250)
        help_view.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        help_view.setHtml(
            f"""
            <h2 style='color:{ACCENT_SOFT}; margin-top:0;'>Shortcuts &amp; instructions</h2>
            <p><b>Ctrl+S</b> save. <b>Ctrl+Z</b> undo. <b>Ctrl+F</b> find/replace.
            <b>Ctrl+G</b> Gemini. <b>Ctrl+R</b> enabled preset replacements.</p>
            <p><b>Rules &amp; Replace:</b> replacement rules always execute in visible top-to-bottom order;
            each rule receives the output of the one above it.</p>
            <p><b>Custom shortcuts:</b> a chord may include modifiers, such as Ctrl+Shift+1.
            A sequence may contain up to three chords. Direct single-chord wrap assignments remain repeatable: keep
            the modifier held and press the assigned preset keys in any order.</p>
            <p><b>PO Viewer:</b> Ctrl+E or F2 focuses Vietnamese. Ctrl+Up / Ctrl+Down changes entry.
            Hold Ctrl and press 1/2/3 for suggestions. Alt+0 rebuilds suggestions.</p>
            <p>Custom wrap and file-switch assignments apply immediately in Search, duplicate views, and PO Viewer.</p>
            """
        )
        content_layout.addWidget(help_view)
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        def canonical(sequence: QKeySequence | str) -> str:
            return self._portable_shortcut_text(sequence)

        def restore_editor(action: str, sequence_text: str) -> None:
            editor = shortcut_editors[action]
            editor.blockSignals(True)
            editor.setKeySequence(QKeySequence(sequence_text))
            editor.blockSignals(False)

        def save_assignment(action: str) -> None:
            editor = shortcut_editors[action]
            text = canonical(editor.keySequence())
            sequence = QKeySequence(text)
            if sequence.count() > MAX_CUSTOM_SHORTCUT_CHORDS:
                restore_editor(action, current[action])
                shortcut_status.setText(f"Use at most {MAX_CUSTOM_SHORTCUT_CHORDS} chords.")
                return

            for reserved_sequence, label in RESERVED_SHORTCUTS.items():
                if text and shortcut_sequences_conflict(text, canonical(reserved_sequence)):
                    restore_editor(action, current[action])
                    shortcut_status.setText(
                        f"{text} conflicts with fixed shortcut {canonical(reserved_sequence)} ({label})."
                    )
                    return

            for other_action, other_text in current.items():
                if other_action == action or not text or not other_text:
                    continue
                if shortcut_sequences_conflict(text, other_text):
                    restore_editor(action, current[action])
                    shortcut_status.setText(
                        f"{text} conflicts with {CUSTOM_SHORTCUT_LABELS[other_action]} ({other_text})."
                    )
                    return

            current[action] = text
            self._store_custom_shortcut_sequences(current)
            shortcut_status.setText(f"{CUSTOM_SHORTCUT_LABELS[action]}: {text or 'Disabled'}. Saved.")

        for action, editor in shortcut_editors.items():
            editor.editingFinished.connect(lambda shortcut_action=action: save_assignment(shortcut_action))

        buttons = QHBoxLayout()
        reset_btn = self._button("Reset Shortcut Defaults", secondary=True)
        reset_btn.setToolTip(
            "Restore Shift+1/2/3/4, Shift+Return, Alt+Up, and Alt+Down."
        )

        def reset_shortcuts() -> None:
            defaults = {**default_wrap_shortcuts(), **default_file_shortcuts()}
            current.clear()
            current.update(defaults)
            self._store_custom_shortcut_sequences(current)
            for action, sequence_text in defaults.items():
                restore_editor(action, sequence_text)
            shortcut_status.setText(
                "All shortcut defaults restored: Shift+1/2/3/4, Shift+Return, Alt+Up, and Alt+Down."
            )

        reset_btn.clicked.connect(reset_shortcuts)
        close_btn = self._button("Close")
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(reset_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        root.addLayout(buttons)
        dialog.exec()

    def _make_log(self) -> LogBox:
        log = LogBox()
        log.setMinimumHeight(210)
        return log

    def _gemini_api_context_limit(self, profile: str = "single") -> int:
        key = f"gemini_api_{profile}_context_entries"
        default = 3
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        value = max(0, min(200, value))
        self.config[key] = value
        return value

    def _gemini_api_cross_file_context_enabled(self, profile: str = "single") -> bool:
        key = f"gemini_api_{profile}_context_across_files"
        value = bool(self.config.get(key, False))
        self.config[key] = value
        return value

    def _gemini_api_timeout_seconds(self, profile: str = "single") -> float:
        key = f"gemini_api_{profile}_timeout_seconds"
        try:
            value = float(self.config.get(key, 90))
        except (TypeError, ValueError):
            value = 90.0
        value = max(5.0, min(3600.0, value))
        self.config[key] = int(value) if value.is_integer() else value
        return value

    def _gemini_api_profile_model(self, profile: str = "single") -> str:
        key = f"gemini_api_{profile}_model"
        value = str(self.config.get(key, "gemini-2.5-flash")).strip() or "gemini-2.5-flash"
        self.config[key] = value
        return value

    def _gemini_api_profile_sleep_seconds(self, profile: str = "single") -> float:
        key = f"gemini_api_{profile}_sleep_seconds"
        default = 0.0 if profile == "single" else 1.0
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        value = max(0.0, min(300.0, value))
        self.config[key] = value
        return value

    def _gemini_api_profile_thinking_mode(self, profile: str = "single") -> str:
        key = f"gemini_api_{profile}_thinking_mode"
        value = str(self.config.get(key, "off")).strip().casefold()
        if value not in {"off", "minimal", "low", "medium", "high", "dynamic"}:
            value = "off"
        self.config[key] = value
        return value

    def _gemini_api_profile_max_output_tokens(self, profile: str = "single") -> int:
        key = f"gemini_api_{profile}_max_output_tokens"
        try:
            value = int(self.config.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        value = max(0, min(65536, value))
        self.config[key] = value
        return value

    def _linewrap_presets(self) -> list[dict[str, int]]:
        presets = normalize_wrap_presets(
            self.config.get("linewrap_presets"),
            legacy_soft=self.config.get("soft_limit", 54),
            legacy_hard=self.config.get("hard_limit", 64),
            legacy_max_cuts=self.config.get("max_cuts", 2),
        )
        self.config["linewrap_presets"] = presets
        return presets

    def _active_linewrap_preset_index(self) -> int:
        try:
            index = int(self.config.get("linewrap_active_preset", 0))
        except (TypeError, ValueError):
            index = 0
        return max(0, min(3, index))

    def _linewrap_settings(self, preset_index: int | None = None) -> tuple[int, int, int]:
        """Return one of the four shared line-wrap presets."""

        index = self._active_linewrap_preset_index() if preset_index is None else max(0, min(3, int(preset_index)))
        preset = self._linewrap_presets()[index]
        return int(preset["soft"]), int(preset["hard"]), int(preset["max_cuts"])

    def _linewrap_preset_label(self, preset_index: int) -> str:
        soft, _hard, _cuts = self._linewrap_settings(preset_index)
        return str(soft)

    def _linewrap_preset_tooltip(self, preset_index: int, action: str = "Wrap") -> str:
        soft, hard, cuts = self._linewrap_settings(preset_index)
        shortcut = self._wrap_shortcut_sequences().get(WRAP_PRESET_ACTIONS[preset_index], "") or "Disabled"
        base_note = " Editable in the Line Wrap tab; shortcut editable in the Shortcuts window."
        return (
            f"{action} with preset W{preset_index + 1}: Soft={soft}, Hard={hard}, Cuts={cuts}. "
            f"Shortcut={shortcut}.{base_note}"
        )

    def _refresh_linewrap_preset_buttons(self) -> None:
        active = self._active_linewrap_preset_index()
        registered = getattr(self, "_linewrap_preset_button_registry", [])
        alive: list[tuple[QToolButton, int, str]] = []
        for button, index, action in registered:
            try:
                button.setText(self._linewrap_preset_label(index))
                button.setToolTip(self._linewrap_preset_tooltip(index, action))
                button.setProperty("wrapActive", index == active)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
                alive.append((button, index, action))
            except RuntimeError:
                # The owning dialog was closed and Qt already deleted the button.
                continue
        self._linewrap_preset_button_registry = alive

    def _set_active_linewrap_preset(self, preset_index: int, *, persist: bool = True) -> None:
        index = max(0, min(3, int(preset_index)))
        self.config["linewrap_active_preset"] = index
        soft, hard, cuts = self._linewrap_settings(index)
        # Keep the old keys synchronized for CLI/backward compatibility.
        self.config["soft_limit"] = soft
        self.config["hard_limit"] = hard
        self.config["max_cuts"] = cuts
        if persist:
            save_config(self.config)
        editor_loader = getattr(self, "_load_linewrap_preset_editor", None)
        if callable(editor_loader):
            editor_loader(index)
        self._refresh_linewrap_preset_buttons()

    def _add_linewrap_preset_buttons(
        self,
        layout: QHBoxLayout,
        callback: Callable[[int], None] | None,
        *,
        action: str = "Wrap",
    ) -> list[QToolButton]:
        registry = getattr(self, "_linewrap_preset_button_registry", None)
        if registry is None:
            registry = []
            self._linewrap_preset_button_registry = registry
        buttons: list[QToolButton] = []
        for index in range(4):
            button = self._tool_button(self._linewrap_preset_label(index), "", width=54)
            button.setProperty("wrapPreset", True)

            def pressed(_checked: bool = False, preset_index: int = index) -> None:
                self._set_active_linewrap_preset(preset_index)
                if callback is not None:
                    callback(preset_index)

            button.clicked.connect(pressed)
            layout.addWidget(button)
            registry.append((button, index, action))
            buttons.append(button)
        self._refresh_linewrap_preset_buttons()
        return buttons

    def _initial_clt_color_mode(self) -> bool:
        """Return the shared CLT display mode used by every text view."""
        return bool(
            self.config.get(
                "text_view_clt_color_mode",
                self.config.get("po_viewer_clt_color_mode", False),
            )
        )

    def _save_clt_color_mode(self, enabled: bool) -> None:
        """Persist the shared CLT display mode and its legacy PO Viewer key."""
        self.config["text_view_clt_color_mode"] = bool(enabled)
        self.config["po_viewer_clt_color_mode"] = bool(enabled)
        save_config(self.config)

    def _button_tooltip(self, text: str) -> str:
        clean = " ".join((text or "").split())
        tooltips = {
            "Settings": "Open folder and app settings.",
            "All": "Select all items in this section.",
            "None": "Clear all selections in this section.",
            "Browse": "Choose a file or folder.",
            "Close": "Close this window.",
            "Run Validate": "Validate selected PO files and report issues.",
            "Run Replace": "Apply mass replacement rules to selected PO files.",
            "Load": "Load data from the selected file or path.",
            "Save": "Save current changes.",
            "Save msgstr": "Save the current Vietnamese translation.",
            "Add Rule": "Create a new replacement rule.",
            "Delete": "Delete the selected rule or item.",
            "Enable Selected": "Enable the selected rules.",
            "Disable Selected": "Disable the selected rules.",
            "Apply Wrap": "Apply line wrapping to the test text.",
            "Clear": "Clear the current list or text box.",
            "Run Line Wrap": "Wrap msgstr lines in selected PO files.",
            "Search": "Search selected PO files.",
            "Open File": "Open the selected result in PO Viewer.",
            "Find Prev": "Move to the previous match.",
            "Find Next": "Move to the next match.",
            "Replace": "Replace the current match.",
            "Replace Current": "Replace matches in the current row only.",
            "Replace Selected": "Replace matches in selected rows only.",
            "Replace All": "Replace all matches in this view.",
            "Preset Replace": "Apply all enabled ordered replacement rules to the selected/current entries.",
            "Add .po": "Add one or more PO source files.",
            "Add folder": "Add a folder containing PO files.",
            "Add Folders": "Add one or more folders.",
            "Remove": "Remove selected items from this list.",
            "Diff Dupes": "Show duplicate English entries with different Vietnamese translations.",
            "All Dupes": "Show all duplicate English entries.",
            "Run Translafixer": "Apply Translafixer suggestions to selected targets.",
            "Open": "Open the selected item in PO Viewer.",
            "Find": "Open search and replace for this view.",
            "Apply": "Apply the selected or current value.",
            "Undo": "Undo the most recent edit action.",
            "Hide": "Hide selected duplicate groups.",
            "Unhide": "Show selected hidden duplicate groups again.",
            "Reload": "Reload data from disk.",
            "Refresh": "Refresh the current list or suggestions.",
            "Run Translation": "Run Gemini translation on selected PO files.",
            "Open Chrome": "Open Chrome with remote debugging for Gemini Web.",
            "Repack": "Sync selected Working folders, repack DRAT LIN/PAK files, update Script, rebuild WAD, and deploy it to the Game Folder.",
            "Sync by Filename": "Sync PO files by matching filename from source to target.",
        }
        return tooltips.get(clean, clean or "Button")

    def _button_role(self, text: str, *, secondary: bool = False, danger: bool = False, role: str | None = None) -> str:
        clean = " ".join((text or "").split())
        role_map = {
            "primary": "primaryButton",
            "success": "successButton",
            "info": "infoButton",
            "warn": "warnButton",
            "deploy": "deployButton",
            "danger": "dangerButton",
            "secondary": "secondaryButton",
        }
        if role in role_map:
            return role_map[role]
        if danger or clean in {"Delete", "Remove"}:
            return "dangerButton"
        if clean in {"Clear", "Disable Selected", "Hide"}:
            return "warnButton"
        if clean in {"Repack", "Sync by Filename"}:
            return "deployButton"
        if clean in {"Save", "Save msgstr", "Apply", "Apply Wrap"}:
            return "successButton"
        if clean.startswith("Run") or clean in {"Search", "Replace All", "Add Rule", "Add .po", "Add folder", "Add Folders", "Enable Selected", "Load"}:
            return "primaryButton" if not secondary else "infoButton"
        if secondary:
            return "secondaryButton"
        return "primaryButton"

    def _button(self, text: str, *, secondary: bool = False, danger: bool = False, role: str | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(self._button_tooltip(text))
        btn.setObjectName(self._button_role(text, secondary=secondary, danger=danger, role=role))
        return btn

    def _tool_button(
        self,
        text: str,
        tooltip: str,
        icon: QStyle.StandardPixmap | None = None,
        *,
        width: int = 32,
    ) -> QToolButton:
        btn = QToolButton()
        btn.setToolTip(tooltip or self._button_tooltip(text))
        btn.setText(text)
        if icon is not None:
            btn.setIcon(self.style().standardIcon(icon))
            btn.setIconSize(QSize(16, 16))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        else:
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setMinimumWidth(width)
        btn.setFixedHeight(28)
        return btn

    def _dr_option_config_key(self, tab_key: str) -> str:
        return f"{tab_key}_dr_options"

    def _initial_dr_options(self, tab_key: str) -> list[str]:
        stored = self.config.get(self._dr_option_config_key(tab_key))
        if isinstance(stored, list):
            return [str(item) for item in stored if str(item) in DR_FILE_OPTION_KEYS]
        return default_selected_options()

    def _save_dr_options(self, tab_key: str) -> None:
        checks = self._dr_option_widgets.get(tab_key, {})
        self.config[self._dr_option_config_key(tab_key)] = [key for key, checkbox in checks.items() if checkbox.isChecked()]
        save_config(self.config)

    def _selected_dr_options(self, tab_key: str) -> list[str]:
        checks = self._dr_option_widgets.get(tab_key)
        if checks is not None:
            return [key for key, checkbox in checks.items() if checkbox.isChecked()]
        return self._initial_dr_options(tab_key)

    def _dr_option_selector(self, layout: QVBoxLayout, tab_key: str) -> dict[str, QCheckBox]:
        box = QGroupBox("File groups")
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(8, 3, 8, 5)
        outer.setSpacing(2)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        selected = set(self._initial_dr_options(tab_key))
        checks: dict[str, QCheckBox] = {}
        for index, option in enumerate(DR_FILE_OPTIONS):
            checkbox = QCheckBox(option.name)
            checkbox.setChecked(option.key in selected)
            checkbox.setToolTip(option.description or option.name)
            checkbox.setMinimumHeight(18)
            checkbox.setMinimumWidth(checkbox.sizeHint().width())
            checkbox.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            checkbox.setStyleSheet("font-size:8pt;")
            grid.addWidget(checkbox, 0, index, Qt.AlignmentFlag.AlignHCenter)
            grid.setColumnStretch(index, 1)
            checks[option.key] = checkbox
        all_btn = self._tool_button("All", "Select every file group", width=38)
        none_btn = self._tool_button("None", "Clear every file group", width=38)
        all_btn.setFixedHeight(21)
        none_btn.setFixedHeight(21)
        controls_col = len(DR_FILE_OPTIONS)
        grid.addWidget(all_btn, 0, controls_col)
        grid.addWidget(none_btn, 0, controls_col + 1)
        grid.setColumnStretch(controls_col, 0)
        grid.setColumnStretch(controls_col + 1, 0)
        outer.addLayout(grid)
        self._dr_option_widgets[tab_key] = checks

        def set_all(value: bool) -> None:
            for checkbox in checks.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(value)
                checkbox.blockSignals(False)
            self._save_dr_options(tab_key)

        for checkbox in checks.values():
            checkbox.stateChanged.connect(lambda _state, key=tab_key: self._save_dr_options(key))
        all_btn.clicked.connect(lambda: set_all(True))
        none_btn.clicked.connect(lambda: set_all(False))
        layout.addWidget(box)
        return checks

    def _include_extra_config_key(self, tab_key: str) -> str:
        return f"{tab_key}_include_extra_path"

    def _extra_path_row(
        self,
        layout: QVBoxLayout | QFormLayout,
        tab_key: str,
        label: str,
        key: str,
        *,
        file: bool = False,
        include_label: str = "Include extra path",
    ) -> tuple[QLineEdit, QCheckBox]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        include = QCheckBox(include_label)
        include.setChecked(bool(self.config.get(self._include_extra_config_key(tab_key), False)))
        include.setToolTip("When on, this manual path is processed together with selected Working folders.")
        edit = QLineEdit(str(self.config.get(key, "")))
        edit.setPlaceholderText("Optional extra file/folder...")
        browse = self._button("Browse", secondary=True)

        def save_path() -> None:
            self.config[key] = edit.text().strip()
            save_config(self.config)

        def save_include() -> None:
            self.config[self._include_extra_config_key(tab_key)] = include.isChecked()
            save_config(self.config)

        def browse_path() -> None:
            if file:
                path, _ = QFileDialog.getOpenFileName(self, label, edit.text() or str(Path.cwd()), "PO/JSON/Text (*.po *.json *.txt);;All files (*.*)")
            else:
                path = QFileDialog.getExistingDirectory(self, label, edit.text() or str(Path.cwd()))
            if path:
                edit.setText(path)
                include.setChecked(True)
                save_path()
                save_include()

        browse.clicked.connect(browse_path)
        edit.editingFinished.connect(save_path)
        include.stateChanged.connect(lambda _state: save_include())
        row.addWidget(include)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        if isinstance(layout, QFormLayout):
            layout.addRow(label, wrap)
        else:
            outer = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(120)
            lab.setStyleSheet("font-weight:700;")
            outer.addWidget(lab)
            outer.addWidget(wrap, 1)
            layout.addLayout(outer)
        return edit, include

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return str(path.expanduser().resolve(strict=False))
        except Exception:
            return str(path.expanduser())

    def _selected_working_paths(
        self,
        tab_key: str,
        logwrite: Callable[[str, str], None] | None = None,
    ) -> list[Path]:
        selected = self._selected_dr_options(tab_key)
        paths: list[Path] = []
        seen: set[str] = set()
        missing: list[str] = []
        invalid: list[str] = []
        for option_key in selected:
            label = option_name(option_key)
            raw = str(self.config.get(f"working_{option_key}_path", "")).strip()
            if not raw:
                missing.append(label)
                continue
            path = Path(raw).expanduser()
            if not path.exists():
                invalid.append(f"{label}: {path}")
                continue
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        if logwrite is not None:
            if missing:
                logwrite("Working folder not set for selected groups: " + ", ".join(missing), "warn")
            if invalid:
                preview = "; ".join(invalid[:8])
                if len(invalid) > 8:
                    preview += f"; ... {len(invalid) - 8} more"
                logwrite("Working folder not found: " + preview, "warn")
        return paths

    def _processing_paths(
        self,
        tab_key: str,
        *,
        extra_edit: QLineEdit | None = None,
        include_extra: QCheckBox | None = None,
        logwrite: Callable[[str, str], None] | None = None,
        require_any: bool = True,
    ) -> list[Path]:
        paths = self._selected_working_paths(tab_key, logwrite=logwrite)
        seen = {self._path_key(path) for path in paths}
        if include_extra is not None and include_extra.isChecked():
            raw = extra_edit.text().strip() if extra_edit is not None else ""
            if not raw:
                if logwrite is not None:
                    logwrite("Extra path is enabled but empty.", "warn")
            else:
                extra = Path(raw).expanduser()
                if not extra.exists():
                    if logwrite is not None:
                        logwrite(f"Extra path not found: {extra}", "warn")
                else:
                    key = self._path_key(extra)
                    if key not in seen:
                        seen.add(key)
                        paths.append(extra)
        if require_any and not paths and logwrite is not None:
            logwrite("No input paths. Select file groups with Working folders in Settings, or enable Extra path.", "warn")
        return paths

    def _iter_unique_po_paths(self, paths: list[Path], *, include_copy: bool = False) -> list[Path]:
        found: list[Path] = []
        seen: set[str] = set()
        for base in paths:
            for po_path in iter_po_files(base, include_copy=include_copy):
                key = self._path_key(po_path)
                if key in seen:
                    continue
                seen.add(key)
                found.append(po_path)
        return found

    def _open_settings_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(820, 520)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(6, 6, 6, 6)

        option_box = QGroupBox("Danganronpa working folders")
        option_form = QFormLayout(option_box)
        option_form.setSpacing(8)
        option_form.setContentsMargins(8, 8, 8, 8)
        for option in DR_FILE_OPTIONS:
            self._path_row(option_form, f"Working {option.name}", f"working_{option.key}_path")
        content_layout.addWidget(option_box)

        general_box = QGroupBox("Other folders")
        general_form = QFormLayout(general_box)
        general_form.setSpacing(8)
        general_form.setContentsMargins(8, 8, 8, 8)
        self._path_row(general_form, "DRAT Folder", "drat_folder_path")
        self._path_row(general_form, "Script", "script_path")
        self._path_row(general_form, "Game Folder", "game_folder_path")
        content_layout.addWidget(general_box)

        git_box = QGroupBox("Danganronpa Việt Hóa Git")
        git_form = QFormLayout(git_box)
        git_form.setSpacing(8)
        git_form.setContentsMargins(8, 8, 8, 8)
        self._git_path_row(git_form)
        content_layout.addWidget(git_box)
        content_layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close_btn = self._button("Close")
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)
        dialog.exec()

    def _path_row(self, layout: QVBoxLayout | QFormLayout, label: str, key: str, *, file: bool = False) -> QLineEdit:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        edit = QLineEdit(str(self.config.get(key, "")))
        edit.setPlaceholderText("Choose file/folder or paste path...")
        browse = self._button("Browse", secondary=True)
        open_folder = self._tool_button("", "Open this configured folder in the file explorer", QStyle.StandardPixmap.SP_DirOpenIcon)

        def save_path() -> None:
            self.config[key] = edit.text().strip()
            save_config(self.config)

        def browse_path() -> None:
            if file:
                path, _ = QFileDialog.getOpenFileName(self, label, edit.text() or str(Path.cwd()), "PO/JSON/Text (*.po *.json *.txt);;All files (*.*)")
            else:
                path = QFileDialog.getExistingDirectory(self, label, edit.text() or str(Path.cwd()))
            if path:
                edit.setText(path)
                save_path()

        def open_configured_path() -> None:
            raw = edit.text().strip()
            if not raw:
                QMessageBox.warning(self, "Open folder", "No folder path is set for this row.")
                return
            target = Path(raw).expanduser()
            if target.is_file():
                target = target.parent
            if not target.exists() or not target.is_dir():
                QMessageBox.warning(self, "Open folder", f"Folder not found:\n{target}")
                return
            if not self._open_external(target):
                QMessageBox.warning(self, "Open folder", f"Could not open folder:\n{target}")

        browse.clicked.connect(browse_path)
        open_folder.clicked.connect(open_configured_path)
        edit.editingFinished.connect(save_path)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        row.addWidget(open_folder)
        if isinstance(layout, QFormLayout):
            layout.addRow(label, wrap)
        else:
            outer = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(120)
            lab.setStyleSheet("font-weight:700;")
            outer.addWidget(lab)
            outer.addWidget(wrap, 1)
            layout.addLayout(outer)
        return edit

    def _git_path_row(self, layout: QFormLayout) -> QLineEdit:
        key = "danganviethoa_path"
        label = "danganviethoa folder"
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        edit = QLineEdit(str(self.config.get(key, "")))
        edit.setPlaceholderText("Choose the cloned danganronpa-viet-hoa folder...")
        browse = self._button("Browse", secondary=True)
        open_folder = self._tool_button("", "Open this repository folder", QStyle.StandardPixmap.SP_DirOpenIcon)
        pull_btn = self._button("Git Pull", secondary=True)
        push_btn = self._button("Git Push")

        def save_path() -> None:
            self.config[key] = edit.text().strip()
            save_config(self.config)

        def browse_path() -> None:
            path = QFileDialog.getExistingDirectory(self, label, edit.text() or str(Path.cwd()))
            if path:
                edit.setText(path)
                save_path()

        def repository_path() -> Path | None:
            save_path()
            try:
                return validate_repository_folder(edit.text())
            except ValueError as exc:
                QMessageBox.warning(self, "Danganronpa Việt Hóa Git", str(exc))
                return None

        def open_configured_path() -> None:
            repo = repository_path()
            if repo is not None and not self._open_external(repo):
                QMessageBox.warning(self, "Open folder", f"Could not open folder:\n{repo}")

        def open_pull_cmd() -> None:
            repo = repository_path()
            if repo is None:
                return
            try:
                launch_windows_cmd(repo, build_pull_command())
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "Git Pull", str(exc))

        def open_push_cmd() -> None:
            repo = repository_path()
            if repo is None:
                return
            message, accepted = QInputDialog.getText(
                self,
                "Git Push",
                "Commit message:",
                QLineEdit.EchoMode.Normal,
            )
            if not accepted:
                return
            message_file: Path | None = None
            push_script: Path | None = None
            try:
                message_file = create_commit_message_file(message)
                push_script = create_push_script(message_file)
                launch_windows_cmd(repo, build_push_command(push_script))
            except (OSError, RuntimeError, ValueError) as exc:
                if message_file is not None:
                    message_file.unlink(missing_ok=True)
                if push_script is not None:
                    push_script.unlink(missing_ok=True)
                QMessageBox.warning(self, "Git Push", str(exc))

        browse.clicked.connect(browse_path)
        open_folder.clicked.connect(open_configured_path)
        pull_btn.clicked.connect(open_pull_cmd)
        push_btn.clicked.connect(open_push_cmd)
        edit.editingFinished.connect(save_path)

        row.addWidget(edit, 1)
        row.addWidget(browse)
        row.addWidget(open_folder)
        row.addWidget(pull_btn)
        row.addWidget(push_btn)
        layout.addRow(label, wrap)
        return edit

    def _begin_task_progress(self, label: str, total: int = 0) -> int:
        """Show the shared progress bar for any long-running GUI action."""
        self._task_progress_token += 1
        token = self._task_progress_token
        self._task_progress_active = True
        self.task_progress.show()
        if total > 0:
            self.task_progress.setRange(0, total)
            self.task_progress.setValue(0)
            self.task_progress.setFormat(f"{label}: %v/%m (%p%)")
        else:
            self.task_progress.setRange(0, 0)
            self.task_progress.setFormat(label)
        return token

    def _update_task_progress(self, done: int, total: int, label: str = "") -> None:
        self._task_progress_active = True
        self.task_progress.show()
        if total > 0:
            if self.task_progress.minimum() != 0 or self.task_progress.maximum() != total:
                self.task_progress.setRange(0, total)
            self.task_progress.setValue(max(0, min(int(done), int(total))))
            if label:
                self.task_progress.setFormat(f"{label}: %v/%m (%p%)")
        else:
            self.task_progress.setRange(0, 0)
            if label:
                self.task_progress.setFormat(label)

    def _pump_task_progress(self) -> None:
        """Paint synchronous progress updates without accepting more user input."""
        self.task_progress.repaint()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _finish_task_progress(self, label: str = "Done") -> None:
        token = self._task_progress_token
        self._task_progress_active = False
        if self.task_progress.maximum() > 0:
            self.task_progress.setValue(self.task_progress.maximum())
        else:
            self.task_progress.setRange(0, 1)
            self.task_progress.setValue(1)
        self.task_progress.setFormat(label)

        def hide_finished() -> None:
            if self._task_progress_token == token and not self._task_progress_active:
                self.task_progress.hide()

        QTimer.singleShot(800, hide_finished)

    def _request_stop(self) -> None:
        self._stop_event.set()
        if self._active_log is not None:
            self._active_log.append_log("Stop requested. Waiting for current safe checkpoint...", "warn")

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise OperationCancelled("Stopped by user.")

    def _enabled_preset_rules(self):
        rules_path = Path(str(self.config.get("rules_file", "rules/mass_replace_rules.json")).strip())
        if not rules_path.exists():
            raise FileNotFoundError(f"Rules file not found: {rules_path}")
        return [rule for rule in load_rules(rules_path) if rule.enabled]

    def _notify_task_complete(self, status: str = "success") -> None:
        try:
            QApplication.alert(self, 2500)
        except Exception:
            pass
        play_task_notification(status, fallback=QApplication.beep)

    def _run_threaded(
        self,
        button: QPushButton,
        log: LogBox,
        fn: Callable[[Callable[[str, str], None], Callable[[int, int, str], None]], None],
    ) -> None:
        if self._active_thread is not None and self._active_thread.is_alive():
            QMessageBox.warning(self, "Busy", "Another action is running. Press Stop Current Action first.")
            return

        signals = WorkerSignals()
        self._active_signals.append(signals)
        signals.log.connect(log.append_log)
        signals.progress.connect(self._update_task_progress)
        action_label = button.text().replace("&", "").strip() or "Working"
        outcome = {"label": f"{action_label} complete", "status": "success"}

        def done() -> None:
            button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self._active_log = None
            self._active_thread = None
            self._finish_task_progress(str(outcome["label"]))
            self._notify_task_complete(str(outcome["status"]))
            try:
                self._active_signals.remove(signals)
            except ValueError:
                pass

        signals.done.connect(done)

        pending_logs: list[tuple[str, str]] = []
        last_log_flush = 0.0

        def flush_logs() -> None:
            nonlocal last_log_flush
            if not pending_logs:
                return
            grouped: list[tuple[list[str], str]] = []
            for text, tag in pending_logs:
                if grouped and grouped[-1][1] == tag:
                    grouped[-1][0].append(text)
                else:
                    grouped.append(([text], tag))
            pending_logs.clear()
            for texts, tag in grouped:
                signals.log.emit("\n".join(texts), tag)
            last_log_flush = time.monotonic()

        def logwrite(text: str, tag: str = "") -> None:
            nonlocal last_log_flush
            pending_logs.append((str(text), str(tag or "")))
            now = time.monotonic()
            if len(pending_logs) >= 32 or now - last_log_flush >= 0.075:
                flush_logs()

        last_progress_emit = 0.0
        last_progress_stage = ""
        last_progress_total: int | None = None

        def progresswrite(done_count: int, total_count: int = 0, label: str = "") -> None:
            nonlocal last_progress_emit, last_progress_stage, last_progress_total
            self._check_stop()
            done_value = int(done_count)
            total_value = int(total_count)
            label_value = str(label or action_label)
            match = re.match(r"\s*(\d+/\d+)", label_value)
            stage = match.group(1) if match else action_label
            now = time.monotonic()
            force = (
                done_value <= 0
                or (total_value > 0 and done_value >= total_value)
                or stage != last_progress_stage
                or total_value != last_progress_total
                or now - last_progress_emit >= 0.075
            )
            if not force:
                return
            if pending_logs and (stage != last_progress_stage or now - last_log_flush >= 0.075):
                flush_logs()
            signals.progress.emit(done_value, total_value, label_value)
            last_progress_emit = now
            last_progress_stage = stage
            last_progress_total = total_value

        def worker() -> None:
            self._stop_event.clear()
            self._active_log = log
            try:
                self._check_stop()
                fn(logwrite, progresswrite)
                self._check_stop()
            except OperationCancelled:
                outcome["label"] = f"{action_label} stopped"
                outcome["status"] = "stopped"
                logwrite("Stopped by user.", "warn")
            except Exception as exc:
                outcome["label"] = f"{action_label} failed"
                outcome["status"] = "failed"
                logwrite(f"ERROR: {exc}", "bad")
            finally:
                flush_logs()
                signals.done.emit()

        log.clear()
        button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._begin_task_progress(action_label)
        thread = threading.Thread(target=worker, daemon=True)
        self._active_thread = thread
        thread.start()

    @staticmethod
    def _open_external(path: Path) -> bool:
        try:
            return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))))
        except Exception:
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(path))  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])
                return True
            except Exception:
                return False

    @staticmethod
    def _undo_text_editor(widget: QWidget | None) -> bool:
        if widget is None:
            return False
        try:
            if isinstance(widget, QLineEdit) and widget.isUndoAvailable():
                widget.undo()
                return True
            if isinstance(widget, (QPlainTextEdit, QTextEdit)) and widget.document().isUndoAvailable():
                widget.undo()
                return True
        except RuntimeError:
            return False
        except Exception:
            return False
        return False

    @staticmethod
    def _undo_focused_text_editor() -> bool:
        """Run the native undo stack for the focused text widget, if one exists."""
        widget = QApplication.focusWidget()
        seen: set[int] = set()
        while widget is not None and id(widget) not in seen:
            seen.add(id(widget))
            if ToolkitGUI._undo_text_editor(widget):
                return True
            widget = widget.parentWidget()
        return False

    @staticmethod
    def _clear_text_editor_undo(widget: QPlainTextEdit | QTextEdit | QLineEdit) -> None:
        try:
            if isinstance(widget, QLineEdit):
                widget.setText(widget.text())
                return
            document = widget.document()
            clear = getattr(document, "clearUndoRedoStacks", None)
            if callable(clear):
                clear()
        except Exception:
            pass

    # ---------------- Validate ----------------
    def _build_validate_tab(self) -> None:
        _tab, layout = self._new_tab("Validate")
        self._dr_option_selector(layout, "validate")
        path_edit, include_extra = self._extra_path_row(layout, "validate", "Extra Folder/File", "last_path")
        options = QHBoxLayout()
        save_reports = QCheckBox("Save reports")
        save_reports.setChecked(True)
        options.addWidget(save_reports)
        options.addStretch()
        run_btn = self._button("Run Validate")
        options.addWidget(run_btn)
        layout.addLayout(options)
        log = self._make_log()
        layout.addWidget(log, 1)

        def run(logwrite, progresswrite):
            self._check_stop()
            paths = self._processing_paths("validate", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            results = {}
            seen_files: set[str] = set()
            progresswrite(0, 0, "Discovering validation files")
            for input_index, input_path in enumerate(paths, start=1):
                self._check_stop()
                logwrite(f"Validate input: {input_path}")
                validation_results = validate_path(
                    input_path,
                    progress=lambda done, total, path, input_index=input_index: progresswrite(
                        done,
                        total,
                        f"Validate {path.name} [{input_index}/{len(paths)} input]",
                    ),
                )
                for file_path, issues in validation_results.items():
                    key = self._path_key(file_path)
                    if key in seen_files:
                        continue
                    seen_files.add(key)
                    results[file_path] = issues
            root_for_report = paths[0] if len(paths) == 1 else None
            logwrite(format_text_report(results, root_for_report))
            if save_reports.isChecked():
                out_base = paths[0]
                out_dir = out_base if out_base.is_dir() else out_base.parent
                txt, html_path = write_reports(results, out_dir, root_for_report)
                logwrite(f"Saved report: {txt}", "good")
                logwrite(f"Saved report: {html_path}", "good")
                if self._open_external(html_path):
                    logwrite("Opened HTML report.", "good")
                else:
                    logwrite("Could not auto-open HTML report.", "warn")

        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run))

    # ---------------- Rules + Mass Replace ----------------
    def _build_replace_tab(self) -> None:
        _tab, layout = self._new_tab("Rules & Replace")
        order_note = QLabel(
            "Execution order: top → bottom. Each rule runs on the result produced by the rule above it; drag rules to reorder."
        )
        order_note.setWordWrap(True)
        order_note.setObjectName("muted")
        layout.addWidget(order_note)
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(main_splitter, 1)

        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)
        rules_edit = self._path_row(editor_layout, "Rules JSON", "rules_file", file=True)
        file_actions = QHBoxLayout()
        load_btn = self._button("Load", secondary=True)
        save_rules_btn = self._button("Save", secondary=True)
        file_actions.addStretch()
        file_actions.addWidget(load_btn)
        file_actions.addWidget(save_rules_btn)
        editor_layout.addLayout(file_actions)

        editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        editor_layout.addWidget(editor_splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        rule_list = QListWidget()
        rule_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        rule_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        rule_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        rule_list.setDragEnabled(True)
        rule_list.setAcceptDrops(True)
        rule_list.setDropIndicatorShown(True)
        left_layout.addWidget(rule_list, 1)
        rule_buttons = QGridLayout()
        add_btn = self._button("Add Rule")
        delete_btn = self._button("Delete", secondary=True)
        enable_btn = self._button("Enable Selected", secondary=True)
        disable_btn = self._button("Disable Selected", secondary=True)
        rule_buttons.addWidget(add_btn, 0, 0)
        rule_buttons.addWidget(enable_btn, 0, 1)
        rule_buttons.addWidget(disable_btn, 1, 0)
        rule_buttons.addWidget(delete_btn, 1, 1)
        left_layout.addLayout(rule_buttons)
        editor_splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        form.setContentsMargins(8, 0, 0, 0)
        form.setSpacing(7)
        enabled_field = QCheckBox("Enabled")
        speaker_field = QLineEdit()
        speaker_field.setPlaceholderText("Optional speaker/context filter")
        scope_field = QLineEdit()
        scope_field.setPlaceholderText("Optional, for example clt:4")
        find_field = QPlainTextEdit()
        find_field.setFixedHeight(62)
        find_field.setPlaceholderText(r"Find text; another find. Use \; for literal ;. Whitespace-only finds are kept exactly.")
        replace_field = QPlainTextEdit()
        replace_field.setFixedHeight(62)
        replace_field.setPlaceholderText(r"Replacement; another replacement. Missing replacements reuse the last one.")
        whole_field = QCheckBox("Whole word")
        case_field = QCheckBox("Case sensitive")
        stop_field = QCheckBox("Stop after this rule matches")
        notes_field = QLineEdit()
        fields: dict[str, QWidget] = {
            "enabled": enabled_field,
            "speaker": speaker_field,
            "scope": scope_field,
            "find": find_field,
            "replace": replace_field,
            "whole_word": whole_field,
            "case_sensitive": case_field,
            "stop_after": stop_field,
            "notes": notes_field,
        }
        form.addRow("", enabled_field)
        form.addRow("Speaker", speaker_field)
        form.addRow("Scope", scope_field)
        form.addRow("Find pair(s)", find_field)
        form.addRow("Replace pair(s)", replace_field)
        option_wrap = QWidget()
        option_layout = QHBoxLayout(option_wrap)
        option_layout.setContentsMargins(0, 0, 0, 0)
        option_layout.addWidget(whole_field)
        option_layout.addWidget(case_field)
        option_layout.addStretch()
        form.addRow("", option_wrap)
        form.addRow("", stop_field)
        form.addRow("Notes", notes_field)
        editor_splitter.addWidget(right)
        editor_splitter.setSizes([500, 650])
        main_splitter.addWidget(editor_panel)

        run_panel = QWidget()
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(0, 4, 0, 0)
        run_layout.setSpacing(6)
        run_title = QLabel("Mass replace files")
        run_title.setStyleSheet("font-weight:900;")
        run_layout.addWidget(run_title)
        self._dr_option_selector(run_layout, "replace")
        path_edit, include_extra = self._extra_path_row(run_layout, "replace", "Extra Folder/File", "last_path")
        run_row = QHBoxLayout()
        dry_run = QCheckBox("Dry run")
        dry_run.setChecked(True)
        run_row.addWidget(dry_run)
        run_row.addStretch()
        run_btn = self._button("Run Replace")
        run_row.addWidget(run_btn)
        run_layout.addLayout(run_row)
        log = self._make_log()
        log.setMinimumHeight(150)
        run_layout.addWidget(log, 1)
        main_splitter.addWidget(run_panel)
        main_splitter.setSizes([420, 300])

        list_refreshing = {"value": False}

        def label_for_rule(rule: dict) -> str:
            state = "ON " if rule.get("enabled", True) else "OFF"
            speaker_text = str(rule.get("speaker") or "GLOBAL")
            find_text = str(rule.get("find") or "<empty>").replace("\n", r"\n")
            replace_text = str(rule.get("replace") or "").replace("\n", r"\n")
            if len(find_text) > 72:
                find_text = find_text[:69] + "…"
            if len(replace_text) > 72:
                replace_text = replace_text[:69] + "…"
            return f"{state} | {speaker_text:<12} | {find_text} → {replace_text}"

        def selected_indices() -> list[int]:
            return sorted({rule_list.row(item) for item in rule_list.selectedItems()})

        def selected_index() -> int | None:
            current = rule_list.currentRow()
            if 0 <= current < len(self.rule_list_data):
                return current
            indices = selected_indices()
            return indices[0] if indices else None

        def style_rule_item(item: QListWidgetItem, rule: dict) -> None:
            normalized = self._normalize_rule_dict(rule)
            item.setText(label_for_rule(normalized))
            item.setData(Qt.ItemDataRole.UserRole, normalized)
            item.setForeground(QColor(GOOD if normalized.get("enabled", True) else BAD))
            item.setBackground(QColor(TEAL_DARK if normalized.get("enabled", True) else "#4a2938"))

        def refresh_list(keep: list[int] | None = None) -> None:
            keep = selected_indices() if keep is None else keep
            list_refreshing["value"] = True
            rule_list.blockSignals(True)
            try:
                rule_list.clear()
                for rule in self.rule_list_data:
                    item = QListWidgetItem()
                    style_rule_item(item, rule)
                    rule_list.addItem(item)
                for index in keep:
                    if 0 <= index < rule_list.count():
                        rule_list.item(index).setSelected(True)
                if keep and 0 <= keep[0] < rule_list.count():
                    rule_list.setCurrentRow(keep[0])
            finally:
                rule_list.blockSignals(False)
                list_refreshing["value"] = False

        def write_rules_file(show_message: bool = False) -> None:
            path_text = rules_edit.text().strip()
            if not path_text:
                return
            path = Path(path_text)
            path.parent.mkdir(parents=True, exist_ok=True)
            normalized = [self._normalize_rule_dict(rule) for rule in self.rule_list_data]
            path.write_text(
                json.dumps({"version": 3, "rules": normalized}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.rule_list_data = normalized
            self.config["rules_file"] = str(path)
            save_config(self.config)
            if show_message:
                QMessageBox.information(self, "Rules", "Rules saved in strict top-to-bottom execution order.")

        def widget_value(widget: QWidget) -> object:
            if isinstance(widget, QCheckBox):
                return widget.isChecked()
            if isinstance(widget, QLineEdit):
                return widget.text()
            if isinstance(widget, QPlainTextEdit):
                return widget.toPlainText()
            return ""

        def collect_form() -> dict:
            return self._normalize_rule_dict({name: widget_value(widget) for name, widget in fields.items()})

        def load_selected() -> None:
            index = selected_index()
            if index is None or index >= len(self.rule_list_data):
                return
            self.rule_loading_fields = True
            try:
                rule = self.rule_list_data[index]
                for name, widget in fields.items():
                    value = rule.get(name, True if name in {"enabled", "case_sensitive"} else False)
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(bool(value))
                    elif isinstance(widget, QLineEdit):
                        widget.setText(str(value or ""))
                    elif isinstance(widget, QPlainTextEdit):
                        widget.setPlainText(str(value or ""))
            finally:
                self.rule_loading_fields = False

        def apply_form(auto_save: bool = True) -> None:
            index = selected_index()
            if index is None or index >= len(self.rule_list_data) or self.rule_loading_fields:
                return
            rule = collect_form()
            self.rule_list_data[index] = rule
            item = rule_list.item(index)
            if item is not None:
                style_rule_item(item, rule)
            if auto_save:
                try:
                    write_rules_file(False)
                except Exception:
                    pass

        def schedule_auto_update() -> None:
            if self.rule_loading_fields:
                return
            if self.rule_auto_timer is None:
                self.rule_auto_timer = QTimer(self)
                self.rule_auto_timer.setSingleShot(True)
                self.rule_auto_timer.timeout.connect(lambda: apply_form(True))
            self.rule_auto_timer.start(180)

        def load_file() -> None:
            path = Path(rules_edit.text().strip())
            if not path.exists():
                QMessageBox.warning(self, "Rules", "Rules file not found.")
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                loaded = [rule_to_dict(rule) for rule in load_rules(path)]
            except Exception as exc:
                QMessageBox.critical(self, "Rules", f"Could not load rules:\n{exc}")
                return
            self.rule_list_data = [self._normalize_rule_dict(rule) for rule in loaded]
            refresh_list([0] if loaded else [])
            load_selected()
            self.config["rules_file"] = str(path)
            save_config(self.config)
            legacy = not isinstance(raw, dict) or int(raw.get("version", 0) or 0) < 3
            if isinstance(raw, dict):
                items = raw.get("rules", [])
                legacy = legacy or (isinstance(items, list) and any(isinstance(item, dict) and ("id" in item or "priority" in item) for item in items))
            if legacy:
                write_rules_file(False)

        def add_rule() -> None:
            self.rule_list_data.append(self._normalize_rule_dict({"enabled": True, "case_sensitive": True}))
            index = len(self.rule_list_data) - 1
            refresh_list([index])
            load_selected()
            write_rules_file(False)

        def delete_rules() -> None:
            indices = selected_indices()
            if not indices:
                return
            for index in reversed(indices):
                if 0 <= index < len(self.rule_list_data):
                    self.rule_list_data.pop(index)
            keep = [min(indices[0], len(self.rule_list_data) - 1)] if self.rule_list_data else []
            refresh_list(keep)
            load_selected()
            write_rules_file(False)

        def set_enabled_for_selected(value: bool) -> None:
            indices = selected_indices()
            for index in indices:
                if 0 <= index < len(self.rule_list_data):
                    self.rule_list_data[index]["enabled"] = value
                    item = rule_list.item(index)
                    if item is not None:
                        style_rule_item(item, self.rule_list_data[index])
            load_selected()
            write_rules_file(False)

        def sync_dragged_order(*_args) -> None:
            if list_refreshing["value"]:
                return
            ordered: list[dict] = []
            for row in range(rule_list.count()):
                data = rule_list.item(row).data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict):
                    ordered.append(self._normalize_rule_dict(data))
            if len(ordered) == rule_list.count():
                self.rule_list_data = ordered
                write_rules_file(False)
                load_selected()

        def run(logwrite, progresswrite):
            self._check_stop()
            paths = self._processing_paths("replace", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            rules = load_rules(rules_edit.text().strip())
            enabled_rules = [rule for rule in rules if rule.enabled]
            po_files = self._iter_unique_po_paths(paths)
            changes = []
            progresswrite(0, len(po_files), "Replacing PO files")
            for file_index, po_path in enumerate(po_files, start=1):
                self._check_stop()
                changes.extend(apply_rules_to_file(po_path, enabled_rules, dry_run=dry_run.isChecked()))
                progresswrite(file_index, len(po_files), f"Replace {po_path.name}")
            logwrite(f"Inputs: {len(paths)} | PO files: {len(po_files)}")
            logwrite(f"Rules loaded: {len(rules)} | enabled: {len(enabled_rules)}")
            logwrite(f"Changes: {len(changes)}", "good" if changes else "")
            for change in changes[:300]:
                self._check_stop()
                logwrite(f"{change.file.name} | {change.msgctxt} | replacements={change.count}")
                logwrite(f"- {change.before}", "warn")
                logwrite(f"+ {change.after}", "good")
            if len(changes) > 300:
                logwrite(f"... {len(changes) - 300} more changes", "warn")
            if dry_run.isChecked():
                logwrite("Dry run only. Uncheck Dry run to write files.", "warn")

        def start_run() -> None:
            apply_form(False)
            write_rules_file(False)
            self._run_threaded(run_btn, log, run)

        rule_list.itemSelectionChanged.connect(load_selected)
        rule_list.model().rowsMoved.connect(sync_dragged_order)
        for widget in fields.values():
            if isinstance(widget, QCheckBox):
                widget.stateChanged.connect(schedule_auto_update)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(schedule_auto_update)
            elif isinstance(widget, QPlainTextEdit):
                widget.textChanged.connect(schedule_auto_update)
        load_btn.clicked.connect(load_file)
        save_rules_btn.clicked.connect(lambda: (apply_form(False), write_rules_file(True)))
        add_btn.clicked.connect(add_rule)
        delete_btn.clicked.connect(delete_rules)
        enable_btn.clicked.connect(lambda: set_enabled_for_selected(True))
        disable_btn.clicked.connect(lambda: set_enabled_for_selected(False))
        run_btn.clicked.connect(start_run)
        QTimer.singleShot(0, load_file)

    # ---------------- Rule data ----------------
    def _normalize_rule_dict(self, raw: dict | None = None) -> dict:
        raw = dict(raw or {})
        return {
            "enabled": bool(raw.get("enabled", True)),
            "speaker": str(raw.get("speaker", raw.get("character")) or ""),
            "scope": str(raw.get("scope") or ""),
            "find": unicodedata.normalize("NFC", str(raw.get("find", ""))),
            "replace": unicodedata.normalize("NFC", str(raw.get("replace", ""))),
            "whole_word": bool(raw.get("whole_word", False)),
            "case_sensitive": bool(raw.get("case_sensitive", True)),
            "stop_after": bool(raw.get("stop_after", False)),
            "notes": str(raw.get("notes", raw.get("label", ""))),
        }

    # ---------------- Line Wrap ----------------
    def _build_linewrap_tab(self) -> None:
        _tab, layout = self._new_tab("Line Wrap")
        self._dr_option_selector(layout, "linewrap")
        path_edit, include_extra = self._extra_path_row(layout, "linewrap", "Extra Folder/File", "last_path")

        controls = QHBoxLayout()
        controls.setSpacing(4)
        dry = QCheckBox("Dry run")
        dry.setChecked(True)
        controls.addWidget(dry)
        controls.addSpacing(8)
        controls.addWidget(QLabel("Preset"))

        soft = QSpinBox(); soft.setRange(1, 999)
        hard = QSpinBox(); hard.setRange(1, 999)
        cuts = QSpinBox(); cuts.setRange(1, 20)
        self.linewrap_soft_spin = soft
        self.linewrap_hard_spin = hard
        self.linewrap_cuts_spin = cuts
        soft.setToolTip("Preferred width for the selected preset.")
        hard.setToolTip("Hard width for the selected preset.")
        cuts.setToolTip("Maximum automatic cuts for the selected preset.")

        preset_updating = {"value": False}

        def load_preset_editor(preset_index: int | None = None) -> None:
            index = self._active_linewrap_preset_index() if preset_index is None else max(0, min(3, int(preset_index)))
            values = self._linewrap_presets()[index]
            preset_updating["value"] = True
            try:
                soft.setValue(int(values["soft"]))
                hard.setValue(int(values["hard"]))
                cuts.setValue(int(values["max_cuts"]))
            finally:
                preset_updating["value"] = False
            editable = True
            for spin in (soft, hard, cuts):
                spin.setEnabled(editable)

        self._load_linewrap_preset_editor = load_preset_editor
        self._add_linewrap_preset_buttons(controls, None, action="Select")
        controls.addSpacing(8)

        def save_linewrap_settings() -> None:
            if preset_updating["value"]:
                return
            index = self._active_linewrap_preset_index()
            presets = self._linewrap_presets()
            presets[index] = {
                "soft": int(soft.value()),
                "hard": int(hard.value()),
                "max_cuts": int(cuts.value()),
            }
            self.config["linewrap_presets"] = presets
            self.config["soft_limit"] = int(soft.value())
            self.config["hard_limit"] = int(hard.value())
            self.config["max_cuts"] = int(cuts.value())
            save_config(self.config)
            self._refresh_linewrap_preset_buttons()

        for spin in (soft, hard, cuts):
            spin.valueChanged.connect(lambda _value: save_linewrap_settings())
        for label, spin in [("Soft", soft), ("Hard", hard), ("Max cuts", cuts)]:
            controls.addWidget(QLabel(label))
            controls.addWidget(spin)
        controls.addStretch()
        layout.addLayout(controls)
        load_preset_editor()

        tester = QGroupBox("Line Wrap Test")
        tester_layout = QVBoxLayout(tester)
        test_input = QPlainTextEdit()
        test_input.setFixedHeight(90)
        test_input._clt_highlighter = CltHighlighter(test_input.document())  # keep highlighter alive
        tester_layout.addWidget(test_input)
        bottom = QHBoxLayout()
        test_status = QLabel("")
        test_status.setObjectName("muted")
        bottom.addWidget(test_status, 1)
        apply_btn = self._button("Apply Wrap", secondary=True)
        clear_btn = self._button("Clear", secondary=True)
        bottom.addWidget(apply_btn)
        bottom.addWidget(clear_btn)
        tester_layout.addLayout(bottom)
        layout.addWidget(tester)

        run_row = QHBoxLayout()
        run_row.addStretch()
        run_btn = self._button("Run Line Wrap")
        run_row.addWidget(run_btn)
        layout.addLayout(run_row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def apply_test() -> None:
            preset_index = self._active_linewrap_preset_index()
            soft_value, hard_value, cuts_value = self._linewrap_settings(preset_index)
            fixed, changed = wrap_msgstr(
                test_input.toPlainText(),
                soft=soft_value,
                hard=hard_value,
                max_cuts=cuts_value,
            )
            test_input.setPlainText(fixed)
            lengths = [len(line) for line in fixed.splitlines()] or [0]
            test_status.setText(
                f"W{preset_index + 1}: {'changed' if changed else 'unchanged'} | "
                f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value} | Lines: {lengths}"
            )

        def run(logwrite, progresswrite):
            self._check_stop()
            paths = self._processing_paths("linewrap", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            preset_index = self._active_linewrap_preset_index()
            soft_value, hard_value, cuts_value = self._linewrap_settings(preset_index)
            po_files = self._iter_unique_po_paths(paths)
            results: dict[Path, int] = {}
            progresswrite(0, len(po_files), "Wrapping PO files")
            for file_index, po_path in enumerate(po_files, start=1):
                self._check_stop()
                results[po_path] = wrap_po_file(
                    po_path,
                    soft=soft_value,
                    hard=hard_value,
                    max_cuts=cuts_value,
                    dry_run=dry.isChecked(),
                )
                progresswrite(file_index, len(po_files), f"Line wrap {po_path.name}")
            logwrite(
                f"Preset W{preset_index + 1}: Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value} | "
                f"Inputs: {len(paths)} | PO files: {len(po_files)}"
            )
            for path, n in results.items():
                self._check_stop()
                if n:
                    logwrite(f"{path}: {n}", "good")
            logwrite(f"Total wrapped: {sum(results.values())}", "good")
            if dry.isChecked():
                logwrite("Dry run only.", "warn")

        apply_btn.clicked.connect(apply_test)
        clear_btn.clicked.connect(lambda: (test_input.clear(), test_status.setText("")))
        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run))

    # ---------------- Search ----------------
    def _build_search_tab(self) -> None:
        _tab, layout = self._new_tab("Search")
        self._dr_option_selector(layout, "search")
        path_edit, include_extra = self._extra_path_row(layout, "search", "Extra Folder/File", "last_path")

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search"))
        phrase = QLineEdit()
        phrase.setPlaceholderText("Text: | = OR, & = AND")
        phrase.setToolTip(r"Use | for OR, & for AND, and \| or \& to search for a literal operator.")
        search_row.addWidget(phrase, 2)
        search_row.addWidget(QLabel("Speaker"))
        speaker = QLineEdit()
        speaker.setPlaceholderText("Speaker/context: | = OR, & = AND")
        speaker.setToolTip(r"Use | for OR, & for AND, and \| or \& to search for a literal operator.")
        search_row.addWidget(speaker, 1)
        search_msgid = QCheckBox("EN")
        search_msgid.setChecked(True)
        search_msgstr = QCheckBox("VI")
        search_msgstr.setChecked(True)
        search_case = QCheckBox("Case")
        search_whole = QCheckBox("Whole word")
        search_raw = QCheckBox("Raw")
        search_raw.setToolTip("Match original parsed PO text exactly without removing CLT tags, brackets, quotes, or line breaks.")
        clt_color_btn = self._tool_button("CLT T", "CLT view: Tags", width=48)
        search_btn = self._button("Search")
        for w in [search_msgid, search_msgstr, search_case, search_whole, search_raw, clt_color_btn, search_btn]:
            search_row.addWidget(w)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        table = QTableWidget(0, 3)
        table.setObjectName("searchResultsTable")
        table.setHorizontalHeaderLabels(["Context", "English", "Vietnamese"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.setTabKeyNavigation(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 240)
        table.setItemDelegateForColumn(0, NoFocusCellDelegate(table))
        table.setItemDelegateForColumn(1, RichTextCellDelegate(table))
        table.setItemDelegateForColumn(2, RichTextCellDelegate(table))
        splitter.addWidget(table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        selected_info = QLabel("")
        selected_info.setObjectName("muted")
        selected_info.setWordWrap(True)
        right_layout.addWidget(selected_info)
        en_header = QHBoxLayout()
        en_label = QLabel("English / msgid")
        en_label.setStyleSheet(f"color: {ACCENT_SOFT}; font-weight: 900;")
        en_character_count_label = QLabel("—")
        en_character_count_label.setObjectName("muted")
        en_character_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        en_character_count_label.setToolTip(
            "Character count for each English line. Every character counts except CLT tags."
        )
        en_character_count_label.setStyleSheet(f"font-weight:800; color:{ACCENT_SOFT};")
        en_header.addWidget(en_label)
        en_header.addStretch()
        en_header.addWidget(en_character_count_label)
        right_layout.addLayout(en_header)
        msgid_box = QPlainTextEdit(); msgid_box.setReadOnly(True)
        msgid_box.setStyleSheet(
            f"QPlainTextEdit {{ color: {WHITE}; background: {EN_BG}; border: 1px solid {PURPLE}; border-radius: 9px; }}"
        )
        msgid_box._clt_highlighter = CltHighlighter(msgid_box.document())  # keep highlighter alive
        right_layout.addWidget(msgid_box)
        vi_header = QHBoxLayout()
        vi_label = QLabel("Vietnamese / msgstr")
        vi_label.setStyleSheet(f"color: {TEAL}; font-weight: 900;")
        vi_character_count_label = QLabel("—")
        vi_character_count_label.setObjectName("muted")
        vi_character_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vi_character_count_label.setToolTip(
            "Character count for each Vietnamese line. Every character counts except CLT tags."
        )
        vi_character_count_label.setStyleSheet(f"font-weight:800; color:{TEAL};")
        vi_header.addWidget(vi_label)
        vi_header.addStretch()
        vi_header.addWidget(vi_character_count_label)
        right_layout.addLayout(vi_header)
        msgstr_box = QPlainTextEdit()
        msgstr_box.setStyleSheet(
            f"QPlainTextEdit {{ color: {WHITE}; background: {VI_BG}; border: 1px solid {TEAL}; border-radius: 9px; }}"
        )
        self._set_plain_text_visible_rows(msgid_box, 4)
        self._set_plain_text_visible_rows(msgstr_box, 4)
        msgstr_box._clt_highlighter = CltHighlighter(msgstr_box.document())  # keep highlighter alive
        right_layout.addWidget(msgstr_box, 1)

        edit_buttons = QHBoxLayout()
        edit_buttons.setSpacing(4)
        open_btn = self._button("Open File", secondary=True)
        save_btn = self._button("Save msgstr")
        preset_replace_btn = self._button("Preset Replace", secondary=True)
        gemini_btn = self._button("Gemini", secondary=True)
        gemini_btn.setToolTip("Translate selected/current Search results with Gemini API (Ctrl+G).")
        edit_buttons.addWidget(save_btn)
        edit_buttons.addWidget(preset_replace_btn)
        edit_buttons.addWidget(gemini_btn)
        edit_buttons.addSpacing(8)
        wrap_label = QLabel("Wrap")
        wrap_label.setObjectName("muted")
        edit_buttons.addWidget(wrap_label)
        search_wrap_buttons = self._add_linewrap_preset_buttons(
            edit_buttons,
            lambda preset_index: wrap_selected_msgstrs(preset_index),
            action="Wrap selected Search results",
        )
        edit_buttons.addStretch()
        edit_buttons.addWidget(open_btn)
        right_layout.addLayout(edit_buttons)

        replace_group = QGroupBox("Find / Replace in Results")
        repl_layout = QGridLayout(replace_group)
        find_edit = QPlainTextEdit()
        find_edit.setFixedHeight(58)
        find_edit.setPlaceholderText(r"Find text; another find. Spaces or pasted line breaks also match line breaks in msgstr. Use \; for literal ;.")
        repl_edit = QPlainTextEdit()
        repl_edit.setFixedHeight(58)
        repl_edit.setPlaceholderText(r"Replacement text; another replacement. Use pasted line breaks or \n for a line break, and \; for literal ;.")
        replace_case = QCheckBox("Case")
        replace_whole = QCheckBox("Whole word")
        prev_btn = self._button("Find Prev", secondary=True)
        next_btn = self._button("Find Next", secondary=True)
        current_btn = self._button("Replace Current", secondary=True)
        selected_btn = self._button("Replace Selected", secondary=True)
        all_btn = self._button("Replace All")
        repl_layout.addWidget(QLabel("Find"), 0, 0)
        repl_layout.addWidget(find_edit, 0, 1, 1, 4)
        repl_layout.addWidget(QLabel("Replace"), 1, 0)
        repl_layout.addWidget(repl_edit, 1, 1, 1, 4)
        repl_layout.addWidget(replace_case, 2, 0)
        repl_layout.addWidget(replace_whole, 2, 1)
        repl_layout.addWidget(prev_btn, 3, 0)
        repl_layout.addWidget(next_btn, 3, 1)
        repl_layout.addWidget(current_btn, 3, 2)
        repl_layout.addWidget(selected_btn, 3, 3)
        repl_layout.addWidget(all_btn, 3, 4)
        right_layout.addWidget(replace_group)

        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        right_layout.addWidget(status)
        progress_bar = QProgressBar()
        progress_bar.setTextVisible(True)
        progress_bar.setMinimumHeight(18)
        progress_bar.hide()
        right_layout.addWidget(progress_bar)
        splitter.addWidget(right)
        splitter.setSizes([740, 430])

        search_undo_stack: list[list[dict[str, object]]] = []
        clt_view_state = {"enabled": self._initial_clt_color_mode()}
        result_rows: dict[int, int] = {}
        search_save_cache: dict[str, tuple[tuple[int, int], object]] = {}
        progress_state = {"token": 0, "active": False}
        save_state: dict[str, list[str]] = {"errors": []}

        def trim_search_undo_stack() -> None:
            if len(search_undo_stack) > 500:
                del search_undo_stack[:-500]

        def compact(text: str, limit: int = 1000) -> str:
            text = user_multiline_text(text)
            return text if len(text) <= limit else text[: limit - 1] + "…"

        def characters_per_line_text(text: str) -> str:
            counts = visible_character_counts_by_line(text)
            return "Chars: " + "  |  ".join(str(count) for count in counts)

        def update_search_character_counts() -> None:
            en_character_count_label.setText(characters_per_line_text(msgid_box.toPlainText()))
            vi_character_count_label.setText(characters_per_line_text(msgstr_box.toPlainText()))

        def wrap_settings() -> tuple[int, int, int]:
            return self._linewrap_settings()

        def repaint_progress() -> None:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        def begin_progress(label: str, total: int) -> int:
            self._begin_task_progress(label, total)
            progress_state["token"] += 1
            token = int(progress_state["token"])
            progress_state["active"] = True
            progress_bar.show()
            if total > 0:
                progress_bar.setRange(0, total)
                progress_bar.setValue(0)
                progress_bar.setFormat(f"{label}: %v/%m (%p%)")
            else:
                progress_bar.setRange(0, 0)
                progress_bar.setFormat(label)
            repaint_progress()
            return token

        def update_progress(
            done: int,
            total: int,
            label: str | None = None,
            *,
            pump_events: bool = True,
        ) -> None:
            self._update_task_progress(done, total, label or "Search")
            if total > 0 and (progress_bar.minimum() != 0 or progress_bar.maximum() != total):
                progress_bar.setRange(0, total)
            if label:
                progress_bar.setFormat(f"{label}: %v/%m (%p%)" if total > 0 else label)
            if total > 0:
                progress_bar.setValue(max(0, min(done, total)))
            if pump_events:
                repaint_progress()

        def finish_progress(label: str = "Done") -> None:
            self._finish_task_progress(label)
            token = int(progress_state["token"])
            progress_state["active"] = False
            if progress_bar.maximum() > 0:
                progress_bar.setValue(progress_bar.maximum())
            progress_bar.setFormat(label)
            repaint_progress()

            def hide_finished() -> None:
                if int(progress_state["token"]) == token and not bool(progress_state["active"]):
                    progress_bar.hide()

            QTimer.singleShot(650, hide_finished)

        def set_search_actions_enabled(enabled: bool) -> None:
            for widget in [
                table,
                msgstr_box,
                replace_group,
                clt_color_btn,
                open_btn,
                save_btn,
                preset_replace_btn,
                gemini_btn,
                *search_wrap_buttons,
                prev_btn,
                next_btn,
                current_btn,
                selected_btn,
                all_btn,
            ]:
                widget.setEnabled(enabled)

        def fill_table() -> None:
            total = len(self.search_results)
            begin_progress("Rendering results", total)
            table.setUpdatesEnabled(False)
            table.setRowCount(0)
            table.setRowCount(total)
            result_rows.clear()
            try:
                for row, result in enumerate(self.search_results):
                    result_rows[row] = row
                    context_bits = []
                    if result.msgctxt:
                        context_bits.append(result.msgctxt)
                    context_bits.append(result.file.name)
                    context_text = " | ".join(context_bits)
                    values = [
                        compact(context_text, 260),
                        compact(result.msgid),
                        compact(result.msgstr),
                    ]
                    for col, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        item.setData(Qt.ItemDataRole.UserRole, row)
                        if col == 0:
                            item.setForeground(QBrush(QColor(MUTED)))
                            item.setBackground(QBrush(QColor(CONTEXT_BG)))
                            item.setToolTip(f"{result.file}\n{result.msgctxt}")
                            if getattr(result, "hit_speaker", False):
                                font = item.font()
                                font.setBold(True)
                                item.setFont(font)
                        elif col == 1:
                            bg_color = EN_HIT_BG if result.hit_msgid else EN_BG
                            item.setForeground(QBrush(QColor(WHITE)))
                            item.setBackground(QBrush(QColor(bg_color)))
                            item.setData(
                                HTML_ROLE,
                                f'<span style="color:{WHITE};">'
                                f'{clt_rich_html(value, color_mode=bool(clt_view_state["enabled"]))}</span>',
                            )
                            if result.hit_msgid:
                                font = item.font()
                                font.setBold(True)
                                item.setFont(font)
                        elif col == 2:
                            bg_color = VI_HIT_BG if result.hit_msgstr else VI_BG
                            item.setForeground(QBrush(QColor(WHITE)))
                            item.setBackground(QBrush(QColor(bg_color)))
                            item.setData(
                                HTML_ROLE,
                                f'<span style="color:{WHITE};">'
                                f'{clt_rich_html(value, color_mode=bool(clt_view_state["enabled"]))}</span>',
                            )
                            if result.hit_msgstr:
                                font = item.font()
                                font.setBold(True)
                                item.setFont(font)
                        table.setItem(row, col, item)
                    if (row + 1) % 50 == 0 or row + 1 == total:
                        update_progress(row + 1, total, "Rendering results")
            finally:
                table.setUpdatesEnabled(True)

            # resizeRowsToContents is very expensive with thousands of hits.
            if total <= 350:
                table.resizeRowsToContents()
            else:
                table.verticalHeader().setDefaultSectionSize(54)
            status.setText(f"Results: {total}")
            finish_progress(f"Loaded {total} result(s)")

        def row_result_index(row: int) -> int | None:
            if row < 0 or row >= table.rowCount():
                return None
            item = table.item(row, 0)
            if item is None:
                return None
            idx = item.data(Qt.ItemDataRole.UserRole)
            return int(idx) if idx is not None else None

        def selected_result_indices() -> list[int]:
            rows = sorted({index.row() for index in table.selectedIndexes()})
            out: list[int] = []
            for row in rows:
                idx = row_result_index(row)
                if idx is not None and 0 <= idx < len(self.search_results):
                    out.append(idx)
            return out

        def current_result_index() -> int | None:
            row = table.currentRow()
            idx = row_result_index(row)
            if idx is not None and 0 <= idx < len(self.search_results):
                return idx
            selected = selected_result_indices()
            return selected[0] if selected else None

        def set_search_clt_color_mode(enabled: bool, *, persist: bool = True, quiet: bool = False) -> None:
            clt_view_state["enabled"] = bool(enabled)
            clt_color_btn.setText(f"CLT {'C' if enabled else 'T'}")
            clt_color_btn.setToolTip(f"CLT view: {'Color' if enabled else 'Tags'}")
            msgid_box._clt_highlighter.set_color_spans(enabled)
            msgstr_box._clt_highlighter.set_color_spans(enabled)
            table.setUpdatesEnabled(False)
            try:
                for row in range(table.rowCount()):
                    idx = row_result_index(row)
                    if idx is None or idx < 0 or idx >= len(self.search_results):
                        continue
                    result = self.search_results[idx]
                    for col, value in ((1, compact(result.msgid)), (2, compact(result.msgstr))):
                        item = table.item(row, col)
                        if item is not None:
                            item.setData(
                                HTML_ROLE,
                                f'<span style="color:{WHITE};">'
                                f'{clt_rich_html(value, color_mode=enabled)}</span>',
                            )
            finally:
                table.setUpdatesEnabled(True)
            table.viewport().update()
            if len(self.search_results) <= 350:
                table.resizeRowsToContents()
            if persist:
                self._save_clt_color_mode(enabled)
            if not quiet:
                status.setText(
                    "CLT color view enabled. Tags are hidden in results; text uses in-game colors."
                    if enabled
                    else "CLT tag view enabled. Raw CLT tags are visible in results."
                )

        def toggle_search_clt_color_mode() -> None:
            set_search_clt_color_mode(not bool(clt_view_state["enabled"]))

        def load_selected() -> None:
            idx = current_result_index()
            if idx is None:
                return
            result = self.search_results[idx]
            context_text = f"{result.msgctxt} | {result.file.name}" if result.msgctxt else result.file.name
            selected_info.setText(context_text)
            selected_info.setToolTip(str(result.file))
            msgid_box.setPlainText(result.msgid)
            msgstr_box.setPlainText(result.msgstr)
            self._clear_text_editor_undo(msgstr_box)

        def file_signature(path: Path) -> tuple[int, int]:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size

        def load_search_po(path: Path):
            key = self._path_key(path)
            signature = file_signature(path)
            cached = search_save_cache.get(key)
            if cached is not None and cached[0] == signature:
                return cached[1]
            po = load_po(path)
            search_save_cache[key] = (signature, po)
            return po

        def cache_saved_po(path: Path, po: object) -> None:
            key = self._path_key(path)
            try:
                search_save_cache[key] = (file_signature(path), po)
            except OSError:
                search_save_cache.pop(key, None)

        def save_error_suffix() -> str:
            errors = save_state["errors"]
            if not errors:
                return ""
            first = errors[0]
            extra = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            return f" Save issue: {first}{extra}."

        def update_result_row(idx: int) -> int | None:
            row = result_rows.get(idx)
            if row is None or row < 0 or row >= table.rowCount():
                return None
            item = table.item(row, 2)
            if item is None:
                return None
            value = compact(self.search_results[idx].msgstr)
            bg_color = VI_HIT_BG if self.search_results[idx].hit_msgstr else VI_BG
            item.setText(value)
            item.setForeground(QBrush(QColor(WHITE)))
            item.setBackground(QBrush(QColor(bg_color)))
            item.setData(
                HTML_ROLE,
                f'<span style="color:{WHITE};">'
                f'{clt_rich_html(value, color_mode=bool(clt_view_state["enabled"]))}</span>',
            )
            return row

        def result_indices_for_entry(path: Path, uid: str) -> list[int]:
            path_key = self._path_key(path)
            return [
                idx
                for idx, result in enumerate(self.search_results)
                if result.uid == uid and self._path_key(result.file) == path_key
            ]

        def apply_search_change_records(
            changes: list[dict[str, object]],
            *,
            value_key: str,
            record_undo: bool,
            progress_label: str,
            finish_label: str,
        ) -> tuple[int, int]:
            save_state["errors"] = []
            filtered: list[dict[str, object]] = []
            for change in changes:
                path_value = change.get("file")
                uid = str(change.get("uid") or "")
                if not isinstance(path_value, Path) or not uid:
                    continue
                target = unicodedata.normalize("NFC", str(change.get(value_key, "")))
                old_text = unicodedata.normalize("NFC", str(change.get("old", "")))
                new_text = unicodedata.normalize("NFC", str(change.get("new", "")))
                if old_text == new_text:
                    continue
                normalized_change = dict(change)
                normalized_change["file"] = path_value
                normalized_change["uid"] = uid
                normalized_change["old"] = old_text
                normalized_change["new"] = new_text
                normalized_change["target"] = target
                filtered.append(normalized_change)
            if not filtered:
                return 0, 0

            grouped: dict[Path, list[dict[str, object]]] = defaultdict(list)
            for change in filtered:
                grouped[change["file"]].append(change)  # type: ignore[index]

            begin_progress(progress_label, len(grouped))
            successful_changes: list[dict[str, object]] = []
            for file_number, (path, file_changes) in enumerate(grouped.items(), start=1):
                try:
                    po = load_search_po(path)
                    by_uid = po.by_uid()
                    missing = 0
                    changed_on_disk = 0
                    for change in file_changes:
                        uid = str(change["uid"])
                        entry = by_uid.get(uid)
                        if entry is None:
                            missing += 1
                            continue
                        target = str(change["target"])
                        if entry.msgstr != target:
                            entry.msgstr = target
                            changed_on_disk += 1
                        if entry.msgstr == target:
                            successful_changes.append(change)
                    if changed_on_disk:
                        save_po(po, path)
                    cache_saved_po(path, po)
                    if missing:
                        save_state["errors"].append(
                            f"{path.name}: {missing} entr{'y was' if missing == 1 else 'ies were'} not found"
                        )
                except Exception as exc:
                    search_save_cache.pop(self._path_key(path), None)
                    save_state["errors"].append(f"{path.name}: {exc}")
                update_progress(file_number, len(grouped), progress_label)

            changed_indices: set[int] = set()
            for change in successful_changes:
                path = change["file"]
                uid = str(change["uid"])
                target = str(change["target"])
                if not isinstance(path, Path):
                    continue
                for idx in result_indices_for_entry(path, uid):
                    if self.search_results[idx].msgstr != target:
                        self.search_results[idx].msgstr = target
                    changed_indices.add(idx)

            if record_undo and successful_changes:
                search_undo_stack.append(
                    [
                        {key: value for key, value in change.items() if key != "target"}
                        for change in successful_changes
                    ]
                )
                trim_search_undo_stack()

            changed_rows: list[int] = []
            table.setUpdatesEnabled(False)
            try:
                for idx in sorted(changed_indices):
                    row = update_result_row(idx)
                    if row is not None:
                        changed_rows.append(row)
            finally:
                table.setUpdatesEnabled(True)

            if len(changed_rows) <= 80:
                for row in changed_rows:
                    table.resizeRowToContents(row)
            elif table.rowCount() <= 350:
                table.resizeRowsToContents()
            else:
                table.viewport().update()

            current_idx = current_result_index()
            if current_idx is not None and current_idx in changed_indices:
                current_text = self.search_results[current_idx].msgstr
                if msgstr_box.toPlainText() != current_text:
                    msgstr_box.setPlainText(current_text)
                    self._clear_text_editor_undo(msgstr_box)
            finish_progress(finish_label.format(total=len(successful_changes), visible=len(changed_indices)))
            return len(successful_changes), len(changed_indices)

        def save_updates(
            updates: dict[int, str],
            *,
            record_undo: bool = True,
            progress_label: str = "Saving search changes",
        ) -> int:
            changes: list[dict[str, object]] = []
            for idx, new_text in updates.items():
                if idx < 0 or idx >= len(self.search_results):
                    continue
                result = self.search_results[idx]
                normalized_new = unicodedata.normalize("NFC", new_text)
                if result.msgstr == normalized_new:
                    continue
                changes.append(
                    {
                        "index": idx,
                        "file": result.file,
                        "uid": result.uid,
                        "old": result.msgstr,
                        "new": normalized_new,
                    }
                )
            _total, visible = apply_search_change_records(
                changes,
                value_key="new",
                record_undo=record_undo,
                progress_label=progress_label,
                finish_label="Saved {visible} result(s)",
            )
            return visible

        def save_current() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            changed = save_updates(
                {idx: msgstr_box.toPlainText()},
                progress_label="Saving current result",
            )
            current_file = self.search_results[idx].file.name
            message = f"Saved {current_file}." if changed else f"No change in {current_file}."
            status.setText(message + save_error_suffix())

        def wrap_selected_msgstrs(preset_index: int | None = None) -> None:
            save_state["errors"] = []
            indices = selected_result_indices()
            if not indices:
                current = current_result_index()
                indices = [current] if current is not None else []
            if not indices:
                status.setText("Select one or more results first.")
                return

            current_idx = current_result_index()
            soft_value, hard_value, cuts_value = self._linewrap_settings(preset_index)
            updates: dict[int, str] = {}
            wrapped_count = 0
            unique_indices = sorted(set(indices))
            begin_progress("Wrapping selected results", len(unique_indices))
            for position, idx in enumerate(unique_indices, start=1):
                if idx < 0 or idx >= len(self.search_results):
                    continue
                source = msgstr_box.toPlainText() if idx == current_idx else self.search_results[idx].msgstr
                fixed, changed = wrap_msgstr(source, soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                if changed:
                    wrapped_count += 1
                if changed or source != self.search_results[idx].msgstr:
                    updates[idx] = fixed
                if position % 25 == 0 or position == len(unique_indices):
                    update_progress(position, len(unique_indices), "Wrapping selected results")

            if updates:
                changed = save_updates(updates, progress_label="Saving wrapped results")
            else:
                changed = 0
                finish_progress("No wrapping changes")
            if current_idx is not None and 0 <= current_idx < len(self.search_results):
                current_text = self.search_results[current_idx].msgstr
                if msgstr_box.toPlainText() != current_text:
                    msgstr_box.setPlainText(current_text)
                    self._clear_text_editor_undo(msgstr_box)
            status.setText(
                f"W{self._active_linewrap_preset_index() + 1}: wrapped {wrapped_count} selected result(s), saved {changed}. "
                f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}."
                + save_error_suffix()
            )

        def wrap_current_search_file() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            result = self.search_results[idx]
            try:
                po = load_search_po(result.file)
            except Exception as exc:
                status.setText(f"Could not load {result.file.name}: {exc}")
                return
            soft_value, hard_value, cuts_value = self._linewrap_settings()
            changes: list[dict[str, object]] = []
            wrapped_count = 0
            current_text = msgstr_box.toPlainText()
            for entry in po.entries:
                source = current_text if entry.uid == result.uid else entry.msgstr
                fixed, did_change = wrap_msgstr(
                    source,
                    soft=soft_value,
                    hard=hard_value,
                    max_cuts=cuts_value,
                )
                if did_change:
                    wrapped_count += 1
                if fixed != entry.msgstr:
                    matching = result_indices_for_entry(result.file, entry.uid)
                    changes.append(
                        {
                            "index": matching[0] if matching else -1,
                            "file": result.file,
                            "uid": entry.uid,
                            "old": entry.msgstr,
                            "new": fixed,
                        }
                    )
            if not changes:
                status.setText(f"No wrapping changes in {result.file.name}.")
                return
            total, visible = apply_search_change_records(
                changes,
                value_key="new",
                record_undo=True,
                progress_label=f"Wrapping {result.file.name}",
                finish_label="Wrapped {total} file entries ({visible} visible)",
            )
            status.setText(
                f"W{self._active_linewrap_preset_index() + 1}: wrapped entire {result.file.name}; "
                f"wrapped={wrapped_count}, changed={total}, visible results={visible}. "
                f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}."
                + save_error_suffix()
            )

        def translate_search_with_gemini_api() -> None:
            if self._active_thread is not None and self._active_thread.is_alive():
                status.setText("Another action is already running. Stop it first.")
                return
            indices = selected_result_indices()
            if not indices:
                current = current_result_index()
                indices = [current] if current is not None else []
            if not indices:
                status.setText("Select one or more results first.")
                return
            api_key = str(self.config.get("gemini_api_key", "")).strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                QMessageBox.warning(self, "Search", "Enter the Gemini API key in the AI Translation tab, or set GEMINI_API_KEY.")
                return
            current = current_result_index()
            work_by_file: dict[Path, list[tuple[int, POEntry]]] = {}
            context_by_file: dict[Path, list[POEntry]] = {}
            for result_index in sorted(set(indices)):
                if result_index < 0 or result_index >= len(self.search_results):
                    continue
                result = self.search_results[result_index]
                source_translation = msgstr_box.toPlainText() if result_index == current else result.msgstr
                try:
                    file_po = load_search_po(result.file)
                except Exception as exc:
                    status.setText(f"Could not load {result.file.name} for Gemini context: {exc}")
                    return
                actual_entry = next((entry for entry in file_po.entries if entry.uid == result.uid), None)
                if actual_entry is None:
                    actual_entry = next(
                        (
                            entry
                            for entry in file_po.entries
                            if (entry.msgctxt or "") == (result.msgctxt or "") and int(entry.line or 0) == int(result.line or 0)
                        ),
                        None,
                    )
                if actual_entry is None:
                    continue
                request_entry = POEntry(
                    index=actual_entry.index,
                    msgctxt=actual_entry.msgctxt,
                    msgid=result.msgid,
                    msgstr=source_translation,
                    comments=list(actual_entry.comments),
                    extracted_comments=list(actual_entry.extracted_comments),
                    line=actual_entry.line,
                )
                work_by_file.setdefault(result.file, []).append((result_index, request_entry))
                context_by_file[result.file] = copy.deepcopy(list(file_po.entries))
            if not work_by_file:
                status.setText("No matching PO entries were available for Gemini.")
                return

            model = self._gemini_api_profile_model("single")
            sleep_seconds = self._gemini_api_profile_sleep_seconds("single")
            timeout_seconds = self._gemini_api_timeout_seconds("single")
            thinking_mode = self._gemini_api_profile_thinking_mode("single")
            max_output_tokens = self._gemini_api_profile_max_output_tokens("single")
            total_entries = sum(len(items) for items in work_by_file.values())
            context_limit = self._gemini_api_context_limit("single")
            use_previous_files = self._gemini_api_cross_file_context_enabled("single") and context_limit > 0

            begin_progress("Gemini translating Search results", total_entries)
            status.setText(
                f"Gemini API translating {total_entries} Search result{'s' if total_entries != 1 else ''}..."
            )
            search_btn.setEnabled(False)
            set_search_actions_enabled(False)
            self.stop_button.setEnabled(True)
            self._stop_event.clear()

            signals = WorkerSignals()
            self._active_signals.append(signals)
            task_state = {"applied": False, "failed": False, "stopped": False, "cleaned": False}

            def gemini_progress(done: int, total: int, label: str) -> None:
                update_progress(done, total, label, pump_events=False)
                status.setText(f"{label}: {done}/{total}")

            def apply_gemini_result(payload: object) -> None:
                if not isinstance(payload, dict):
                    return
                updates = payload.get("updates", {})
                errors = payload.get("errors", [])
                usage = str(payload.get("usage") or "").strip()
                if not isinstance(updates, dict) or not isinstance(errors, list):
                    return
                task_state["applied"] = True
                finish_progress(f"Gemini prepared {len(updates)} translation(s)")
                changed = save_updates(updates, progress_label="Saving Gemini translations") if updates else 0
                status.setText(
                    f"Gemini translated and saved {changed} Search result{'s' if changed != 1 else ''}."
                    + (f" Tokens: {usage}." if usage else "")
                    + save_error_suffix()
                )
                if errors:
                    preview = "\n".join(f"{entry.msgctxt or entry.uid}: {entry.reason}" for entry in errors[:8])
                    more = f"\n... {len(errors) - 8} more" if len(errors) > 8 else ""
                    QMessageBox.warning(
                        self,
                        "Gemini API",
                        f"Saved {changed} result{'s' if changed != 1 else ''}, with {len(errors)} validation issue(s):\n{preview}{more}",
                    )

            def gemini_failed(message: str) -> None:
                stopped = message == "Gemini API translation stopped."
                task_state["stopped"] = stopped
                task_state["failed"] = not stopped
                finish_progress("Gemini API stopped" if stopped else "Gemini API failed")
                status.setText(message)
                if not stopped:
                    QMessageBox.critical(self, "Gemini API", message)

            def finish_gemini_task() -> None:
                if task_state["cleaned"]:
                    return
                task_state["cleaned"] = True
                search_btn.setEnabled(True)
                set_search_actions_enabled(True)
                self.stop_button.setEnabled(False)
                self._active_thread = None
                try:
                    self._active_signals.remove(signals)
                except ValueError:
                    pass
                if not task_state["applied"] and not task_state["failed"] and not task_state["stopped"]:
                    finish_progress("Gemini API stopped")
                    status.setText("Gemini API translation stopped.")
                    task_state["stopped"] = True
                notification_status = "failed" if task_state["failed"] else ("stopped" if task_state["stopped"] else "success")
                self._notify_task_complete(notification_status)

            signals.progress.connect(gemini_progress)
            signals.result.connect(apply_gemini_result)
            signals.error.connect(gemini_failed)
            signals.done.connect(finish_gemini_task)

            def gemini_worker() -> None:
                try:
                    client = GeminiApiClient(
                        api_key=api_key,
                        model=model,
                        prompt=SYSTEM_INSTRUCTIONS,
                        timeout_seconds=timeout_seconds,
                        thinking_mode=thinking_mode,
                        max_output_tokens=max_output_tokens,
                    )
                    updates: dict[int, str] = {}
                    errors = []
                    completed = 0
                    previous_file_context: list[POEntry] = []
                    groups = list(work_by_file.items())
                    for group_index, (file_path, items) in enumerate(groups):
                        self._check_stop()
                        request_entries = [entry for _result_index, entry in items]
                        result_by_uid = {entry.uid: result_index for result_index, entry in items}

                        def report_gemini_progress(done: int, _total: int, *, offset: int = completed) -> None:
                            self._check_stop()
                            signals.progress.emit(offset + done, total_entries, "Gemini translating Search results")

                        translations, entry_errors = translate_entries_with_client(
                            request_entries,
                            client,
                            batch_size=1,
                            sleep_seconds=sleep_seconds,
                            allow_partial=False,
                            prompt=SYSTEM_INSTRUCTIONS,
                            progress=report_gemini_progress,
                            context_entries=context_by_file.get(file_path, []),
                            context_limit=context_limit,
                            previous_file_context_entries=previous_file_context if use_previous_files else None,
                            cancel_check=self._check_stop,
                        )
                        errors.extend(entry_errors)
                        for uid, translation in translations.items():
                            result_index = result_by_uid.get(uid)
                            if result_index is not None:
                                updates[result_index] = translation
                        if use_previous_files:
                            file_context = context_by_file.get(file_path, [])
                            translated_by_uid = dict(translations)
                            for context_entry in file_context:
                                translated = translated_by_uid.get(context_entry.uid)
                                if translated is not None:
                                    context_entry.msgstr = translated
                            previous_file_context.extend(file_context)
                            if len(previous_file_context) > context_limit:
                                previous_file_context = previous_file_context[-context_limit:]
                        completed += len(items)
                        if sleep_seconds and group_index + 1 < len(groups):
                            deadline = time.monotonic() + sleep_seconds
                            while time.monotonic() < deadline:
                                self._check_stop()
                                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                    self._check_stop()
                    signals.result.emit({"updates": updates, "errors": errors, "usage": client.total_usage.summary()})
                except OperationCancelled:
                    signals.error.emit("Gemini API translation stopped.")
                except Exception as exc:
                    signals.error.emit(f"Gemini API translation failed:\n{exc}")
                finally:
                    signals.done.emit()

            thread = threading.Thread(target=gemini_worker, daemon=True)
            self._active_thread = thread
            thread.start()

        def switch_search_file(delta: int) -> None:
            current = current_result_index()
            if current is None or len(self.search_results) <= 1:
                return
            current_path = self._path_key(self.search_results[current].file)
            keep_editor_focus = msgstr_box.hasFocus()
            for offset in range(1, len(self.search_results) + 1):
                candidate = (current + delta * offset) % len(self.search_results)
                if self._path_key(self.search_results[candidate].file) == current_path:
                    continue
                select_result(candidate)
                if keep_editor_focus:
                    QTimer.singleShot(0, lambda: msgstr_box.setFocus(Qt.FocusReason.ShortcutFocusReason))
                status.setText(f"Switched to {self.search_results[candidate].file.name}.")
                return
            status.setText("Only one file is present in the Search results.")

        def run_search() -> None:
            text = phrase.text()
            speaker_text = speaker.text()
            if not text.strip() and not speaker_text.strip():
                status.setText("Set search text or speaker first.")
                return
            paths = self._processing_paths("search", extra_edit=path_edit, include_extra=include_extra, require_any=False)
            if not paths:
                status.setText("No input paths. Select file groups with Working folders in Settings, or enable Extra path.")
                return
            if self._active_thread is not None and self._active_thread.is_alive():
                status.setText("Another action is already running. Stop it first.")
                return

            begin_progress("Collecting PO files", 0)
            po_files: list[Path] = []
            seen_files: set[str] = set()
            for root in paths:
                for po_path in iter_po_files(root):
                    key = self._path_key(po_path)
                    if key in seen_files:
                        continue
                    seen_files.add(key)
                    po_files.append(po_path)
                    if len(po_files) % 100 == 0:
                        repaint_progress()
            if not po_files:
                finish_progress("No PO files found")
                status.setText("No PO files found in the selected input paths.")
                return

            search_options = {
                "search_msgid": search_msgid.isChecked(),
                "search_msgstr": search_msgstr.isChecked(),
                "case_sensitive": search_case.isChecked(),
                "whole_word": search_whole.isChecked(),
                "speaker": speaker_text,
                "raw": search_raw.isChecked(),
            }
            source_paths = [str(path) for path in paths]
            begin_progress("Searching files", len(po_files))
            status.setText(f"Searching {len(po_files)} PO file(s)...")
            search_btn.setEnabled(False)
            set_search_actions_enabled(False)
            self.stop_button.setEnabled(True)
            self._stop_event.clear()

            signals = WorkerSignals()
            self._active_signals.append(signals)
            search_state = {
                "finished": False,
                "failed": False,
                "focus_table": False,
                "applying": False,
                "pending_done": False,
                "cleaned": False,
            }

            def search_progress(done: int, total: int, filename: str) -> None:
                update_progress(done, total, "Searching files", pump_events=False)
                status.setText(f"Searching {filename} ({done}/{total})...")

            def apply_search_results(results: object) -> None:
                if not isinstance(results, list):
                    return
                search_state["applying"] = True
                try:
                    search_state["finished"] = True
                    search_state["focus_table"] = bool(results)
                    self.search_results = results
                    self.search_source_paths = source_paths
                    self.search_last_index = -1
                    search_undo_stack.clear()
                    search_save_cache.clear()
                    fill_table()
                    status.setText(f"Found {len(self.search_results)} result(s) from {len(po_files)} PO file(s).")
                    if self.search_results:
                        table.selectRow(0)
                        table.setCurrentCell(0, 0)
                        load_selected()
                    else:
                        selected_info.clear()
                        msgid_box.clear()
                        msgstr_box.clear()
                    if text.strip() and not user_multiline_text(find_edit.toPlainText()).strip():
                        first_text = next((part.strip() for part in text.split(";") if part.strip()), text.strip())
                        find_edit.setPlainText(first_text)
                finally:
                    search_state["applying"] = False
                    if search_state["pending_done"]:
                        finish_search_cleanup()

            def search_failed(message: str) -> None:
                search_state["failed"] = True
                finish_progress("Search stopped" if message == "Search stopped." else "Search failed")
                status.setText(message)

            def finish_search_cleanup() -> None:
                if search_state["cleaned"]:
                    return
                search_state["cleaned"] = True
                search_btn.setEnabled(True)
                set_search_actions_enabled(True)
                self.stop_button.setEnabled(False)
                self._active_thread = None
                try:
                    self._active_signals.remove(signals)
                except ValueError:
                    pass
                if not search_state["finished"] and not search_state["failed"]:
                    finish_progress("Search stopped")
                    status.setText("Search stopped.")
                if search_state["focus_table"]:
                    table.setFocus(Qt.FocusReason.OtherFocusReason)
                notification_status = "failed" if search_state["failed"] else ("success" if search_state["finished"] else "stopped")
                self._notify_task_complete(notification_status)

            def search_done() -> None:
                if search_state["applying"]:
                    search_state["pending_done"] = True
                    return
                finish_search_cleanup()

            signals.progress.connect(search_progress)
            signals.result.connect(apply_search_results)
            signals.error.connect(search_failed)
            signals.done.connect(search_done)

            def search_worker() -> None:
                try:
                    def report(done: int, total: int, path: Path) -> None:
                        self._check_stop()
                        signals.progress.emit(done, total, path.name)

                    results = search_files(
                        po_files,
                        text,
                        progress=report,
                        **search_options,
                    )
                    self._check_stop()
                    signals.result.emit(results)
                except OperationCancelled:
                    signals.error.emit("Search stopped.")
                except Exception as exc:
                    signals.error.emit(f"Search failed: {exc}")
                finally:
                    signals.done.emit()

            thread = threading.Thread(target=search_worker, daemon=True)
            self._active_thread = thread
            thread.start()

        def open_selected_file() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            result = self.search_results[idx]
            opener = getattr(self, "_open_file_in_po_viewer", None)
            if callable(opener):
                source_roots = getattr(self, "search_source_paths", None)
                if not isinstance(source_roots, list):
                    source_roots = [str(path) for path in self._processing_paths("search", extra_edit=path_edit, include_extra=include_extra, require_any=False)]
                if opener(result.file, uid=result.uid, source_paths=source_roots):
                    status.setText(f"Opened {result.file.name} in PO Viewer.")
                    return
            if not self._open_external(result.file):
                QMessageBox.warning(self, "Open file", f"Could not open file:\n{result.file}")

        def compile_replace_sequence() -> list[tuple[re.Pattern[str], object]] | None:
            try:
                compiled = compile_search_replace_sequence(
                    find_edit.toPlainText(),
                    repl_edit.toPlainText(),
                    case_sensitive=replace_case.isChecked(),
                    whole_word=replace_whole.isChecked(),
                )
            except SearchReplaceCompileError as exc:
                status.setText(f"Invalid find pattern in item {exc.index}: {exc.error}")
                return None
            if not compiled:
                status.setText("Find is empty.")
                return None
            return compiled

        def result_matches(idx: int, compiled: list[tuple[re.Pattern[str], object]]) -> bool:
            return any(pattern.search(self.search_results[idx].msgstr) for pattern, _replacement in compiled)

        def select_result(idx: int) -> None:
            row = result_rows.get(idx)
            if row is None or row < 0 or row >= table.rowCount():
                return
            table.selectRow(row)
            table.setCurrentCell(row, 0)
            item = table.item(row, 0)
            if item is not None:
                table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            load_selected()
            self.search_last_index = idx

        def find_step(direction: int) -> None:
            compiled = compile_replace_sequence()
            if compiled is None or not self.search_results:
                return
            current = current_result_index()
            start = current if current is not None else self.search_last_index
            n = len(self.search_results)
            for offset in range(1, n + 1):
                idx = (start + direction * offset) % n
                if result_matches(idx, compiled):
                    select_result(idx)
                    status.setText(f"Found result {idx + 1}/{n}.")
                    return
            status.setText("No match in current results.")

        def replace_indices(indices: list[int]) -> None:
            save_state["errors"] = []
            compiled = compile_replace_sequence()
            if compiled is None:
                return
            updates: dict[int, str] = {}
            total_hits = 0
            wrapped_count = 0
            soft_value, hard_value, cuts_value = wrap_settings()
            unique_indices = sorted(set(indices))
            begin_progress("Preparing replacements", len(unique_indices))
            for position, idx in enumerate(unique_indices, start=1):
                if idx < 0 or idx >= len(self.search_results):
                    continue
                before = self.search_results[idx].msgstr
                after = before
                after, row_hits = apply_search_replace_sequence(after, compiled)
                if row_hits:
                    after, wrapped = wrap_msgstr(after, soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                    updates[idx] = after
                    total_hits += row_hits
                    if wrapped:
                        wrapped_count += 1
                if position % 25 == 0 or position == len(unique_indices):
                    update_progress(position, len(unique_indices), "Preparing replacements")
            if updates:
                changed = save_updates(updates, progress_label="Saving replacements")
            else:
                changed = 0
                finish_progress("No replacement matches")
            status.setText(
                f"Replaced {total_hits} hit(s) in {changed} result(s). "
                f"Auto-wrapped {wrapped_count}."
                + save_error_suffix()
            )

        def replace_current() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            replace_indices([idx])

        def preset_replace_selected() -> None:
            indices = selected_result_indices()
            if not indices:
                current = current_result_index()
                indices = [current] if current is not None else []
            if not indices:
                status.setText("Select one or more results first.")
                return
            try:
                rules = self._enabled_preset_rules()
            except Exception as exc:
                status.setText(str(exc))
                return
            if not rules:
                status.setText("No enabled preset rules.")
                return
            current = current_result_index()
            updates: dict[int, str] = {}
            total_hits = 0
            matched_rules = 0
            for idx in sorted(set(indices)):
                if idx < 0 or idx >= len(self.search_results):
                    continue
                result = self.search_results[idx]
                source_text = msgstr_box.toPlainText() if idx == current else result.msgstr
                temp_entry = POEntry(
                    index=idx,
                    msgctxt=result.msgctxt or None,
                    msgid=result.msgid,
                    msgstr=source_text,
                    line=result.line,
                )
                new_text, hits = apply_rules_to_entry(temp_entry, rules)
                if hits and new_text != result.msgstr:
                    updates[idx] = new_text
                    total_hits += sum(count for _rule, count, _before, _after in hits)
                    matched_rules += len(hits)
            if updates:
                changed = save_updates(updates, progress_label="Applying preset rules")
            else:
                changed = 0
                finish_progress("No preset matches")
            status.setText(
                f"Preset rules: {total_hits} replacement hit(s), {matched_rules} rule match(es), "
                f"saved {changed} result(s)." + save_error_suffix()
            )

        def undo_last_search_change() -> None:
            if self._undo_focused_text_editor():
                return
            focus = QApplication.focusWidget()
            if (focus is table or (focus is not None and table.isAncestorOf(focus))) and self._undo_text_editor(msgstr_box):
                return
            if not search_undo_stack:
                status.setText("Nothing to undo.")
                return
            action = search_undo_stack.pop()
            restored_indices: list[int] = []
            for change in action:
                expected_file = change.get("file")
                expected_uid = str(change.get("uid", ""))
                if isinstance(expected_file, Path) and expected_uid:
                    restored_indices.extend(result_indices_for_entry(expected_file, expected_uid))
            changed, visible = apply_search_change_records(
                action,
                value_key="old",
                record_undo=False,
                progress_label="Restoring previous text",
                finish_label="Restored {total} entries ({visible} visible)",
            )
            if restored_indices:
                select_result(restored_indices[0])
            status.setText(
                f"Undid {changed} saved Search change{'s' if changed != 1 else ''} ({visible} visible)."
                + save_error_suffix()
            )

        set_search_clt_color_mode(bool(clt_view_state["enabled"]), persist=False, quiet=True)
        table.itemSelectionChanged.connect(load_selected)
        table.itemDoubleClicked.connect(lambda _item: open_selected_file())
        search_btn.clicked.connect(run_search)
        clt_color_btn.clicked.connect(toggle_search_clt_color_mode)
        phrase.returnPressed.connect(run_search)
        speaker.returnPressed.connect(run_search)
        open_btn.clicked.connect(open_selected_file)
        save_btn.clicked.connect(save_current)
        preset_replace_btn.clicked.connect(preset_replace_selected)
        gemini_btn.clicked.connect(translate_search_with_gemini_api)
        msgid_box.textChanged.connect(update_search_character_counts)
        msgstr_box.textChanged.connect(update_search_character_counts)
        update_search_character_counts()
        msgstr_box.installEventFilter(self)
        prev_btn.clicked.connect(lambda: find_step(-1))
        next_btn.clicked.connect(lambda: find_step(1))
        current_btn.clicked.connect(replace_current)
        selected_btn.clicked.connect(lambda: replace_indices(selected_result_indices()))
        all_btn.clicked.connect(lambda: replace_indices(list(range(len(self.search_results)))))

        search_shortcuts: list[QShortcut] = []

        def add_search_shortcut(sequence: str, callback: Callable[[], None]) -> None:
            shortcut = QShortcut(QKeySequence(sequence), _tab)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            search_shortcuts.append(shortcut)

        add_search_shortcut("Ctrl+Z", undo_last_search_change)
        add_search_shortcut("Ctrl+S", save_current)
        search_wrap_filter = PersistentWrapShortcutFilter(
            _tab,
            {
                index + 1: (
                    lambda preset_index=index: (
                        self._set_active_linewrap_preset(preset_index),
                        wrap_selected_msgstrs(preset_index),
                    )
                )
                for index in range(4)
            },
            wrap_current_search_file,
            self._custom_shortcut_sequences,
            lambda: switch_search_file(-1),
            lambda: switch_search_file(1),
        )
        add_search_shortcut(PRESET_REPLACE_SHORTCUT, preset_replace_selected)
        add_search_shortcut(GEMINI_TRANSLATE_SHORTCUT, translate_search_with_gemini_api)
        search_undo_filter = RoutedUndoShortcutFilter(_tab, undo_last_search_change)
        self._search_shortcuts = search_shortcuts
        self._search_wrap_filter = search_wrap_filter
        self._search_undo_filter = search_undo_filter


    # ---------------- Translafixer ----------------
    def _build_translafixer_tab(self) -> None:
        _tab, layout = self._new_tab("Translafixer")
        self._dr_option_selector(layout, "translafixer")

        def build_path_picker(
            title: str,
            hint_text: str,
            config_key: str,
            attr_name: str,
            tooltip: str,
        ) -> tuple[QGroupBox, PathDropList, Callable[[], list[str]], Callable[[], None]]:
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)

            list_widget = PathDropList()
            list_widget.setToolTip(hint_text)
            setattr(self, attr_name, list_widget)
            list_widget.setMinimumHeight(145)
            list_widget.setToolTip(tooltip)
            box_layout.addWidget(list_widget)

            buttons = QHBoxLayout()
            add_files_btn = self._button("Add .po", secondary=True)
            add_folder_btn = self._button("Add folder", secondary=True)
            remove_files_btn = self._button("Remove", secondary=True)
            clear_files_btn = self._button("Clear", secondary=True)
            status_label = QLabel("0 files")
            status_label.setObjectName("muted")
            buttons.addWidget(add_files_btn)
            buttons.addWidget(add_folder_btn)
            buttons.addWidget(remove_files_btn)
            buttons.addWidget(clear_files_btn)
            buttons.addStretch()
            buttons.addWidget(status_label)
            box_layout.addLayout(buttons)

            def paths() -> list[str]:
                return [list_widget.item(i).text() for i in range(list_widget.count())]

            def refresh_status() -> None:
                count = list_widget.count()
                status_label.setText(f"{count} file{'s' if count != 1 else ''}")

            def save_paths() -> None:
                self.config[config_key] = paths()
                save_config(self.config)
                refresh_status()

            def add_paths(raw_paths: list[str]) -> None:
                existing = {
                    str(Path(list_widget.item(i).text()).expanduser().resolve(strict=False))
                    for i in range(list_widget.count())
                }
                added = 0
                for candidate in collect_source_po_files(raw_paths):
                    resolved = str(Path(candidate).expanduser().resolve(strict=False))
                    if resolved in existing:
                        continue
                    existing.add(resolved)
                    list_widget.addItem(QListWidgetItem(str(candidate)))
                    added += 1
                if added:
                    save_paths()
                else:
                    refresh_status()

            stored = self.config.get(config_key, [])
            if isinstance(stored, list):
                add_paths([str(item) for item in stored])

            def browse_files() -> None:
                start_dir = str(Path.cwd())
                if list_widget.count():
                    start_dir = str(Path(list_widget.item(list_widget.count() - 1).text()).expanduser().parent)
                paths_selected, _ = QFileDialog.getOpenFileNames(self, title, start_dir, "PO files (*.po);;All files (*.*)")
                if paths_selected:
                    add_paths(paths_selected)

            def browse_folder() -> None:
                start_dir = str(Path.cwd())
                if list_widget.count():
                    start_dir = str(Path(list_widget.item(list_widget.count() - 1).text()).expanduser().parent)
                folder = QFileDialog.getExistingDirectory(self, title, start_dir)
                if folder:
                    add_paths([folder])

            def remove_selected() -> None:
                for item in list_widget.selectedItems():
                    row = list_widget.row(item)
                    list_widget.takeItem(row)
                save_paths()

            def clear_all() -> None:
                list_widget.clear()
                save_paths()

            list_widget.pathsDropped.connect(add_paths)
            add_files_btn.clicked.connect(browse_files)
            add_folder_btn.clicked.connect(browse_folder)
            remove_files_btn.clicked.connect(remove_selected)
            clear_files_btn.clicked.connect(clear_all)
            refresh_status()
            return box, list_widget, paths, save_paths

        source_box, _source_list, source_files, save_source_files = build_path_picker(
            "Translafixer Source — used to translafix/fill",
            "Known-good source .po files. Run Translafixer and PO Viewer TF fill read from here.",
            "translafixer_source_files",
            "translafixer_source_list_widget",
            "Drop known-good .po files or folders used for Translafixer fixing.",
        )
        layout.addWidget(source_box)

        target_edit, include_extra = self._extra_path_row(layout, "translafixer", "Extra target folder", "translafixer_target_folder")

        options = QHBoxLayout()
        dry_run = QCheckBox("Dry run")
        dry_run.setChecked(True)
        backup = QCheckBox("Create .translafixer.bak before write")
        backup.setChecked(True)
        for widget in [dry_run, backup]:
            options.addWidget(widget)
        dup_btn = self._button("Diff Dupes", secondary=True)
        dup_btn.setToolTip("Open duplicate source groups with different Vietnamese translations from selected checkbox Working folders.")
        all_dup_btn = self._button("All Dupes", secondary=True)
        all_dup_btn.setToolTip("Open all duplicate source groups from selected checkbox Working folders, including same translations.")
        options.addWidget(dup_btn)
        options.addWidget(all_dup_btn)
        options.addStretch()
        run_btn = self._button("Run Translafixer")
        options.addWidget(run_btn)
        layout.addLayout(options)

        log = self._make_log()
        layout.addWidget(log, 1)

        def save_paths() -> None:
            self.config["translafixer_source_files"] = source_files()
            self.config["translafixer_target_folder"] = target_edit.text().strip()
            save_config(self.config)

        target_edit.editingFinished.connect(save_paths)
        dup_btn.clicked.connect(lambda: self._open_reference_duplicates_dialog(show_all_duplicates=False, tab_key="translafixer"))
        all_dup_btn.clicked.connect(lambda: self._open_reference_duplicates_dialog(show_all_duplicates=True, tab_key="translafixer"))

        def run(logwrite, progresswrite):
            save_source_files()
            save_paths()
            sources = source_files()
            targets = self._processing_paths("translafixer", extra_edit=target_edit, include_extra=include_extra, logwrite=logwrite)
            if not sources or not targets:
                logwrite("Add Translafixer Source files and select target Working folders or enable Extra target.", "warn")
                return
            total_target_files = 0
            total_matched = 0
            total_changed = 0
            total_errors = 0
            progresswrite(0, 0, "Building Translafixer source map")
            for target_index, target in enumerate(targets, start=1):
                self._check_stop()
                logwrite(f"Translafixer target: {target}")
                result = apply_translafix(
                    sources,
                    target,
                    dry_run=dry_run.isChecked(),
                    create_backup=backup.isChecked(),
                    log=lambda msg: logwrite(msg),
                    stop_requested=self._stop_event.is_set,
                    progress=lambda done, total, path, target_index=target_index: progresswrite(
                        done,
                        total,
                        f"Translafixer {path.name} [{target_index}/{len(targets)} target]",
                    ),
                )
                logwrite(f"Source files scanned: {result.source_files}")
                logwrite(f"Source entries: {result.source_entries}")
                logwrite(f"Usable msgid translations: {result.usable_translations}", "good" if result.usable_translations else "warn")
                if result.skipped_source_targets:
                    logwrite(f"Skipped selected source files inside target folder: {result.skipped_source_targets}", "warn")
                if result.empty_source_entries:
                    logwrite(f"Skipped empty source translations: {result.empty_source_entries}", "warn")
                if result.duplicate_same:
                    logwrite(f"Duplicate same source translations: {result.duplicate_same}")
                if result.conflicts:
                    logwrite(f"Ambiguous source msgid skipped: {result.ambiguous_msgids}", "warn")
                    for conflict in result.conflicts[:25]:
                        preview = conflict.msgid.replace("\n", " ")
                        if len(preview) > 120:
                            preview = preview[:119] + "…"
                        logwrite(f"  conflict: {preview} | {conflict.file.name}:{conflict.line}", "warn")
                    if len(result.conflicts) > 25:
                        logwrite(f"  ... {len(result.conflicts) - 25} more conflicts", "warn")
                for item in result.files:
                    self._check_stop()
                    if item.error:
                        logwrite(f"ERR {item.file}: {item.error}", "bad")
                        continue
                    if not item.matched:
                        continue
                    tag = "good" if item.changed else ""
                    action = "would change" if dry_run.isChecked() else "changed"
                    logwrite(f"{item.file} | matched={item.matched} | {action}={item.changed} | unchanged={item.unchanged}", tag)
                    if item.backup_path:
                        logwrite(f"  backup: {item.backup_path}", "good")
                total_target_files += result.target_files
                total_matched += result.total_matched
                total_changed += result.total_changed
                total_errors += result.total_errors
            logwrite(f"Target files scanned: {total_target_files}")
            logwrite(f"Matched entries: {total_matched}")
            logwrite(f"Changed entries: {total_changed}", "good" if total_changed else "warn")
            if total_errors:
                logwrite(f"Errors: {total_errors}", "bad")
            if dry_run.isChecked():
                logwrite("Dry run only. Uncheck Dry run to write files.", "warn")

        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run))

    # ---------------- PO Viewer ----------------
    def _list_widget_or_config_paths(self, attr_name: str, config_key: str) -> list[str]:
        widget = getattr(self, attr_name, None)
        if widget is not None:
            return [widget.item(i).text() for i in range(widget.count())]
        stored = self.config.get(config_key, [])
        if isinstance(stored, list):
            return [str(item) for item in stored]
        return []

    def _translafixer_source_paths(self) -> list[str]:
        return self._list_widget_or_config_paths("translafixer_source_list_widget", "translafixer_source_files")

    def _all_configured_working_paths(
        self,
        logwrite: Callable[[str, str], None] | None = None,
    ) -> list[Path]:
        """Return every existing Working folder configured in Settings.

        This powers PO Viewer suggestions, which should use all available local
        translation files from configured game folders.
        """
        paths: list[Path] = []
        seen: set[str] = set()
        invalid: list[str] = []
        for option in DR_FILE_OPTIONS:
            raw = str(self.config.get(f"working_{option.key}_path", "")).strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.exists():
                invalid.append(f"{option.name}: {path}")
                continue
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        if logwrite is not None and invalid:
            preview = "; ".join(invalid[:8])
            if len(invalid) > 8:
                preview += f"; ... {len(invalid) - 8} more"
            logwrite("Working folder not found: " + preview, "warn")
        return paths

    def _duplicate_scan_paths(self, tab_key: str = "translafixer") -> list[Path]:
        """Duplicate views scan only the selected checkbox Working folders."""
        return self._selected_working_paths(tab_key)

    def _open_reference_duplicates_dialog(self, *, show_all_duplicates: bool = False, tab_key: str = "translafixer") -> None:
        dialog_mode = "all" if show_all_duplicates else "diff"
        view_title = "All Duplicate Sources" if show_all_duplicates else "Different Duplicate Translations"
        existing_dialog = getattr(self, "_reference_duplicates_dialog", None)
        if existing_dialog is not None and existing_dialog.isVisible():
            if (
                getattr(existing_dialog, "_reference_duplicate_mode", None) == dialog_mode
                and getattr(existing_dialog, "_reference_duplicate_scope", None) == tab_key
            ):
                existing_dialog.raise_()
                existing_dialog.activateWindow()
                return
            existing_dialog.close()

        references = self._duplicate_scan_paths(tab_key)
        if not references:
            QMessageBox.warning(self, view_title, "Select checkbox Working folders in this tab first.")
            return

        scan_duplicates = find_reference_duplicate_sources if show_all_duplicates else find_reference_translation_conflicts
        self._begin_task_progress(f"Scanning {view_title}")
        self._pump_task_progress()
        try:
            conflict_entries, result = scan_duplicates(references)
        except Exception as exc:
            self._finish_task_progress(f"{view_title} scan failed")
            QMessageBox.critical(self, view_title, f"Could not scan selected Working folders:\n{exc}")
            return

        if not conflict_entries:
            self._finish_task_progress("No duplicate entries found")
            no_duplicate_text = (
                "No repeated source entries found."
                if show_all_duplicates
                else "No duplicate entries with different translations found."
            )
            QMessageBox.information(
                self,
                view_title,
                f"{no_duplicate_text}\nWorking .po files scanned: {result.source_files}",
            )
            return

        all_conflict_entries = list(conflict_entries)
        hidden_config = self.config.get("translafixer_hidden_duplicate_keys", [])
        hidden_keys: set[str] = {str(key) for key in hidden_config if str(key)} if isinstance(hidden_config, list) else set()
        entries_by_key: dict[str, list[ReferenceTranslationConflictEntry]] = defaultdict(list)
        for conflict_entry in all_conflict_entries:
            entries_by_key[conflict_entry.key].append(conflict_entry)

        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setModal(False)
        dialog.setWindowTitle(view_title)
        dialog._reference_duplicate_mode = dialog_mode  # type: ignore[attr-defined]
        dialog._reference_duplicate_scope = tab_key  # type: ignore[attr-defined]
        dialog.resize(1180, 660)
        self._reference_duplicates_dialog = dialog

        def clear_reference_duplicates_dialog(_result: int, closed_dialog: QDialog = dialog) -> None:
            if getattr(self, "_reference_duplicates_dialog", None) is closed_dialog:
                setattr(self, "_reference_duplicates_dialog", None)

        dialog.finished.connect(clear_reference_duplicates_dialog)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        note_text = (
            "All repeated English/source sentences found in selected checkbox Working folders. "
            "Includes groups with same or different Vietnamese translations. "
            if show_all_duplicates
            else "Different Vietnamese translations found in selected checkbox Working folders. "
        )
        note = QLabel(
            note_text
            + "Raw comparison: line breaks, spaces, and CLT tags all count. "
            + "Edit Vietnamese, then Apply to copy it to this source group. "
            + "Open switches to PO Viewer on the selected file/entry."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        all_group_count = len(entries_by_key)
        hidden_group_count = len({key for key in entries_by_key if key in hidden_keys})
        visible_entry_count = len([entry for entry in all_conflict_entries if entry.key not in hidden_keys])
        status = QLabel(
            f"{visible_entry_count} visible entries | {all_group_count} duplicate source group(s) | "
            f"hidden groups={hidden_group_count} | {result.source_files} working .po file(s)"
        )
        status.setObjectName("muted")
        root.addWidget(status)

        split = QSplitter(Qt.Orientation.Vertical)
        table = QTableWidget()
        table.setObjectName("poViewerTable")
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["Grp", "Speaker", "English", "Vietnamese", "File", "Ln", "Vars"])
        table.setFont(QFont("Consolas", 8))
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.verticalHeader().setMinimumSectionSize(22)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalScrollBar().setSingleStep(8)
        table.horizontalScrollBar().setSingleStep(8)
        table.setItemDelegate(NoFocusCellDelegate(table))
        table.setItemDelegateForColumn(2, RichTextCellDelegate(table))
        table.setItemDelegateForColumn(3, RichTextCellDelegate(table))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        table.setColumnWidth(4, 150)
        split.addWidget(table)

        detail = QSplitter(Qt.Orientation.Horizontal)

        def labeled_box(label: str, box: QPlainTextEdit, extra_label: QLabel | None = None) -> QWidget:
            wrap = QWidget()
            box_layout = QVBoxLayout(wrap)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(3)
            lab = QLabel(label)
            lab.setStyleSheet("font-weight:800;")
            box_layout.addWidget(lab)
            if extra_label is not None:
                box_layout.addWidget(extra_label)
            box_layout.addWidget(box, 1)
            return wrap

        en_box = VisibleNewlinePlainTextEdit()
        en_box.setReadOnly(True)
        en_box.setPlaceholderText("English msgid")
        en_box.setFont(QFont("Consolas", 8))
        en_box.verticalScrollBar().setSingleStep(6)
        en_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        en_box._clt_highlighter = CltHighlighter(en_box.document())  # keep highlighter alive
        vi_box = VisibleNewlinePlainTextEdit()
        vi_box.setPlaceholderText("Edit Vietnamese msgstr here. Use Apply to copy it to this source group.")
        vi_box.setFont(QFont("Consolas", 8))
        vi_box.verticalScrollBar().setSingleStep(6)
        vi_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        vi_box._clt_highlighter = CltHighlighter(vi_box.document())  # keep highlighter alive
        self._set_plain_text_visible_rows(en_box, 4)
        self._set_plain_text_visible_rows(vi_box, 4)
        speaker_label = QLabel("Speaker: —")
        speaker_label.setObjectName("muted")
        speaker_label.setWordWrap(True)
        speaker_label.setStyleSheet(f"font-weight:900; color:{ACCENT_SOFT};")
        detail.addWidget(labeled_box("English / original — read only", en_box))
        detail.addWidget(labeled_box("Vietnamese / translation — editable", vi_box, speaker_label))
        detail.setSizes([1, 1])
        split.addWidget(detail)
        split.setSizes([500, 150])
        root.addWidget(split, 1)

        footer = QHBoxLayout()
        footer.setSpacing(4)
        prev_source_btn = self._button("Prev", secondary=True)
        prev_source_btn.setToolTip("Jump to the previous duplicate source group.")
        next_source_btn = self._button("Next", secondary=True)
        next_source_btn.setToolTip("Jump to the next duplicate source group.")
        open_file_btn = self._button("Open", secondary=True)
        open_file_btn.setToolTip("Open the selected .po file in PO Viewer and jump to this entry.")
        search_replace_btn = self._button("Find", secondary=True)
        search_replace_btn.setToolTip("Find/replace text in the duplicate view. Replacement edits Vietnamese only.")
        clt_color_btn = self._tool_button("CLT T", "CLT view: Tags", width=48)
        apply_group_btn = self._button("Apply", secondary=True)
        apply_group_btn.setToolTip("Copy the selected/current Vietnamese text to this same-source group.")
        preset_replace_btn = self._button("Preset Replace", secondary=True)
        preset_replace_btn.setToolTip("Apply all enabled ordered replacement rules to selected/current duplicate rows.")
        gemini_btn = self._button("Gemini", secondary=True)
        gemini_btn.setToolTip("Translate selected/current duplicate rows with Gemini API (Ctrl+G).")
        undo_apply_btn = self._button("Undo", secondary=True)
        undo_apply_btn.setToolTip("Undo the most recent Apply, Replace, edit, or Wrap change. Ctrl+Z also works.")
        undo_apply_btn.setEnabled(False)
        hide_group_btn = self._button("Hide", secondary=True)
        hide_group_btn.setToolTip("Hide selected duplicate source group(s) by default.")
        show_hidden_check = QCheckBox("Hidden")
        show_hidden_check.setToolTip("Show groups hidden with Hide.")
        unhide_group_btn = self._button("Unhide", secondary=True)
        unhide_group_btn.setToolTip("Remove selected hidden group(s) from the hidden list.")
        save_btn = self._button("Save")
        refresh_btn = self._button("Reload", secondary=True)
        close_btn = self._button("Close", secondary=True)
        footer.addWidget(prev_source_btn)
        footer.addWidget(next_source_btn)
        footer.addWidget(open_file_btn)
        footer.addWidget(search_replace_btn)
        footer.addWidget(clt_color_btn)
        footer.addSpacing(8)
        footer.addWidget(apply_group_btn)
        footer.addWidget(preset_replace_btn)
        footer.addWidget(gemini_btn)
        footer.addWidget(undo_apply_btn)
        wrap_label = QLabel("Wrap")
        wrap_label.setObjectName("muted")
        footer.addWidget(wrap_label)
        self._add_linewrap_preset_buttons(
            footer,
            lambda preset_index: breakline_selected(preset_index),
            action="Wrap selected duplicate rows",
        )
        footer.addSpacing(8)
        footer.addWidget(hide_group_btn)
        footer.addWidget(show_hidden_check)
        footer.addWidget(unhide_group_btn)
        footer.addStretch()
        footer.addWidget(save_btn)
        footer.addWidget(refresh_btn)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        po_cache: dict[Path, object] = {}
        changed_files: set[Path] = set()
        apply_undo_stack: list[list[dict[str, object]]] = []
        search_replace_state: dict[str, object] = {}
        loading = {"value": False}
        detail_loading = {"value": False}
        undoing = {"value": False}
        clt_view_state = {"enabled": self._initial_clt_color_mode()}
        HIDDEN_GROUP_BG = "#604268"
        HIDDEN_BG = "#49354f"
        HIDDEN_BG_2 = "#3d3146"
        HIDDEN_VI_BG = "#553a5f"
        HIDDEN_TEXT = "#dac9e8"

        def resolved(path: Path) -> Path:
            return path.expanduser().resolve(strict=False)

        conflict_files = sorted({resolved(entry.file) for entry in all_conflict_entries}, key=lambda item: str(item).casefold())
        self._begin_task_progress("Loading duplicate PO files", len(conflict_files))
        self._pump_task_progress()
        try:
            for file_index, po_path in enumerate(conflict_files, start=1):
                po_cache[po_path] = load_po_clone(po_path)
                self._update_task_progress(file_index, len(conflict_files), f"Loading duplicate {po_path.name}")
                self._pump_task_progress()
        except Exception as exc:
            self._finish_task_progress("Duplicate PO load failed")
            QMessageBox.critical(self, view_title, f"Could not load duplicate files:\n{exc}")
            return

        original_translations: dict[tuple[Path, str], str] = {}
        for po_path, po_file in po_cache.items():
            for entry in getattr(po_file, "entries", []):
                original_translations[(po_path, entry.uid)] = entry.msgstr

        def push_duplicate_undo(changes: list[dict[str, object]]) -> None:
            filtered = [change for change in changes if str(change.get("old", "")) != str(change.get("new", ""))]
            if not filtered or undoing["value"]:
                return
            apply_undo_stack.append(filtered)
            if len(apply_undo_stack) > 500:
                del apply_undo_stack[:-500]
            undo_apply_btn.setEnabled(True)

        def duplicate_change_for_row(row: int, old_text: str, new_text: str, label: str) -> dict[str, object] | None:
            item = table.item(row, 3)
            payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if not isinstance(payload, dict):
                return None
            path, entry = entry_from_payload(payload)
            if path is None or entry is None or old_text == new_text:
                return None
            return {"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": new_text, "label": label}

        def refresh_changed_file(path: Path) -> None:
            po_file = po_cache.get(path)
            if po_file is None:
                return
            has_changes = any(
                original_translations.get((path, entry.uid), entry.msgstr) != entry.msgstr
                for entry in getattr(po_file, "entries", [])
            )
            if has_changes:
                changed_files.add(path)
            else:
                changed_files.discard(path)

        def minimal_file_labels(paths: list[Path]) -> dict[Path, str]:
            """Return the shortest readable labels that still disambiguate files."""
            resolved_paths = [resolved(path) for path in paths]
            by_name: dict[str, list[Path]] = defaultdict(list)
            for path in resolved_paths:
                by_name[path.name.casefold()].append(path)
            labels: dict[Path, str] = {}
            for path in resolved_paths:
                same_name = by_name[path.name.casefold()]
                if len(same_name) == 1:
                    labels[path] = path.name
                    continue
                parts = path.parts
                for depth in range(2, len(parts) + 1):
                    candidate = "/".join(parts[-depth:])
                    duplicates = ["/".join(other.parts[-depth:]) for other in same_name]
                    if duplicates.count(candidate) == 1:
                        labels[path] = candidate
                        break
                else:
                    labels[path] = str(path)
            return labels

        file_labels = minimal_file_labels(conflict_files)
        FILE_LABEL_MAX_CHARS = 34

        def capped_file_label(label: str, max_chars: int = FILE_LABEL_MAX_CHARS) -> str:
            """Short table label; full path stays in tooltip."""
            text = str(label or "")
            if len(text) <= max_chars:
                return text
            text = text.replace("\\", "/")
            if "/" in text:
                prefix, name = text.rsplit("/", 1)
                if len(name) + 2 < max_chars:
                    prefix_budget = max_chars - len(name) - 1
                    return f"…{prefix[-max(1, prefix_budget - 1):]}/{name}"
                text = name
            if len(text) <= max_chars:
                return text
            if "." in text:
                stem, suffix = text.rsplit(".", 1)
                suffix = "." + suffix
            else:
                stem, suffix = text, ""
            tail_budget = max(8, max_chars - len(suffix) - 2)
            head_budget = max(4, max_chars - len(suffix) - tail_budget - 2)
            return f"{stem[:head_budget]}…{stem[-tail_budget:]}{suffix}"

        def save_hidden_duplicate_keys() -> None:
            self.config["translafixer_hidden_duplicate_keys"] = sorted(hidden_keys, key=str.casefold)
            save_config(self.config)

        def displayed_conflict_entries() -> list[ReferenceTranslationConflictEntry]:
            if show_hidden_check.isChecked():
                return list(all_conflict_entries)
            return [entry for entry in all_conflict_entries if entry.key not in hidden_keys]

        def clear_detail() -> None:
            detail_loading["value"] = True
            try:
                en_box.clear()
                vi_box.clear()
                speaker_label.setText("Speaker: —")
            finally:
                detail_loading["value"] = False

        def make_item(
            text: str,
            *,
            editable: bool = False,
            bg: str | None = None,
            fg: str | None = None,
            italic: bool = False,
        ) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if editable:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            if bg:
                item.setBackground(QColor(bg))
            if fg:
                item.setForeground(QBrush(QColor(fg)))
            if italic:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            return item

        def set_html(item: QTableWidgetItem, text: str, *, color: str = TEXT) -> None:
            item.setData(
                HTML_ROLE,
                f'<span style="color:{color};">'
                f'{clt_rich_html(text, color_mode=bool(clt_view_state["enabled"]))}</span>',
            )

        def payload_key_from_row(row: int) -> str:
            item = table.item(row, 3)
            payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(payload, dict):
                return str(payload.get("key") or "")
            return ""

        def row_is_hidden(row: int) -> bool:
            key = payload_key_from_row(row)
            return bool(key and key in hidden_keys)

        def style_non_translation_cell(row: int, column: int, *, refresh_group_label: bool = False) -> None:
            item = table.item(row, column)
            if item is None:
                return
            hidden = row_is_hidden(row)
            if column == 0:
                base = re.sub(r"\s+HIDDEN$", "", item.text()).strip()
                item.setText(f"{base} HIDDEN" if hidden and show_hidden_check.isChecked() else base)
                item.setBackground(QColor(HIDDEN_GROUP_BG if hidden else PANEL_3))
                item.setToolTip("Hidden duplicate group. It is skipped by default until Hidden is enabled." if hidden else "")
            elif column == 1:
                item.setBackground(QColor(HIDDEN_BG_2 if hidden else PANEL_2))
            elif column == 2:
                item.setBackground(QColor(HIDDEN_BG if hidden else EN_BG))
                set_html(item, item.text(), color=HIDDEN_TEXT if hidden else TEXT)
            else:
                item.setBackground(QColor(HIDDEN_BG_2 if hidden else PANEL))
            item.setForeground(QBrush(QColor(HIDDEN_TEXT if hidden else TEXT)))
            font = item.font()
            font.setItalic(hidden)
            item.setFont(font)

        def restyle_rows_for_keys(keys: set[str]) -> None:
            if not keys:
                return
            table.setUpdatesEnabled(False)
            try:
                for row in range(table.rowCount()):
                    if payload_key_from_row(row) not in keys:
                        continue
                    for column in (0, 1, 2, 4, 5, 6):
                        style_non_translation_cell(row, column)
                    vi_item = table.item(row, 3)
                    update_row_visual(row, vi_item.text() if vi_item is not None else "", refresh_height=False)
            finally:
                table.setUpdatesEnabled(True)
            table.viewport().update()

        def compact_row_height_for_text(*values: str) -> int:
            def estimated_lines(value: str) -> int:
                segments = str(value or "").split("\n") or [""]
                return sum(max(1, (len(segment) + 95) // 96) for segment in segments)

            lines = max((estimated_lines(value) for value in values if value is not None), default=1)
            return max(28, min(220, 17 * lines + 10))

        def refresh_table_row_height(row: int) -> None:
            en_item = table.item(row, 2)
            vi_item = table.item(row, 3)
            table.setRowHeight(
                row,
                compact_row_height_for_text(
                    en_item.text() if en_item is not None else "",
                    vi_item.text() if vi_item is not None else "",
                ),
            )

        def refresh_table_row_heights() -> None:
            for row in range(table.rowCount()):
                refresh_table_row_height(row)

        def ref_payload(entry: ReferenceTranslationConflictEntry) -> dict[str, object]:
            return {
                "path": str(entry.file),
                "row": entry.row,
                "uid": entry.uid,
                "key": entry.key,
                "line": entry.line,
                "source": entry.source,
                "speaker": entry.speaker,
                "msgctxt": entry.msgctxt,
            }

        def entry_from_payload(payload: dict[str, object]):
            path = resolved(Path(str(payload.get("path") or "")))
            po = po_cache.get(path)
            if po is None:
                return None, None
            uid = str(payload.get("uid") or "")
            try:
                row = int(payload.get("row", -1))
            except Exception:
                row = -1
            entries = getattr(po, "entries", [])
            if 0 <= row < len(entries) and (not uid or entries[row].uid == uid):
                return path, entries[row]
            if uid:
                for candidate in entries:
                    if candidate.uid == uid:
                        return path, candidate
            return path, None

        def current_payload() -> dict[str, object] | None:
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 3)
            if item is None:
                return None
            payload = item.data(Qt.ItemDataRole.UserRole)
            return payload if isinstance(payload, dict) else None

        def current_translation_text() -> str:
            if vi_box.hasFocus():
                return vi_box.toPlainText()
            row = table.currentRow()
            item = table.item(row, 3) if row >= 0 else None
            if item is not None:
                return item.text()
            return vi_box.toPlainText()

        def update_row_visual(row: int, text: str, *, refresh_height: bool = True) -> None:
            item = table.item(row, 3)
            if item is None:
                return
            hidden = row_is_hidden(row)
            color = HIDDEN_TEXT if hidden else (TEXT if text.strip() else WARN)
            if hidden:
                bg = HIDDEN_VI_BG if text.strip() else "#5b423f"
            else:
                bg = VI_BG if text.strip() else "#4a3828"
            item.setBackground(QColor(bg))
            item.setForeground(QBrush(QColor(color)))
            font = item.font()
            font.setItalic(hidden)
            item.setFont(font)
            set_html(item, text, color=color)
            item.setToolTip(("Hidden duplicate group.\n" if hidden else "") + text)
            if refresh_height:
                refresh_table_row_height(row)

        def set_duplicate_clt_color_mode(enabled: bool, *, persist: bool = True, quiet: bool = False) -> None:
            clt_view_state["enabled"] = bool(enabled)
            clt_color_btn.setText(f"CLT {'C' if enabled else 'T'}")
            clt_color_btn.setToolTip(f"CLT view: {'Color' if enabled else 'Tags'}")
            en_box._clt_highlighter.set_color_spans(enabled)
            vi_box._clt_highlighter.set_color_spans(enabled)
            table.setUpdatesEnabled(False)
            try:
                for row in range(table.rowCount()):
                    hidden = row_is_hidden(row)
                    en_item = table.item(row, 2)
                    if en_item is not None:
                        set_html(en_item, en_item.text(), color=HIDDEN_TEXT if hidden else TEXT)
                    vi_item = table.item(row, 3)
                    if vi_item is not None:
                        update_row_visual(row, vi_item.text(), refresh_height=False)
            finally:
                table.setUpdatesEnabled(True)
            table.viewport().update()
            refresh_table_row_heights()
            if persist:
                self._save_clt_color_mode(enabled)
            if not quiet:
                update_status()
                status.setText(
                    status.text()
                    + (
                        " | CLT color view: tags hidden, text colored"
                        if enabled
                        else " | CLT tag view: raw tags visible"
                    )
                )

        def toggle_duplicate_clt_color_mode() -> None:
            set_duplicate_clt_color_mode(not bool(clt_view_state["enabled"]))

        def set_entry_translation(row: int, text: str, *, update_detail: bool = False, undo_label: str | None = None) -> bool:
            item = table.item(row, 3)
            if item is None:
                return False
            payload = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                return False
            path, entry = entry_from_payload(payload)
            if path is None or entry is None:
                return False
            old_text = entry.msgstr
            if old_text == text:
                if update_detail:
                    detail_loading["value"] = True
                    try:
                        vi_box.setPlainText(text)
                    finally:
                        detail_loading["value"] = False
                return False
            if undo_label:
                push_duplicate_undo([{"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": text, "label": undo_label}])
            entry.msgstr = text
            refresh_changed_file(path)
            loading["value"] = True
            try:
                item.setText(text)
                update_row_visual(row, text)
            finally:
                loading["value"] = False
            if update_detail:
                detail_loading["value"] = True
                try:
                    vi_box.setPlainText(text)
                finally:
                    detail_loading["value"] = False
            update_status()
            return True

        def load_detail_for_row(row: int) -> None:
            if row < 0:
                clear_detail()
                return
            payload = current_payload()
            if not isinstance(payload, dict):
                clear_detail()
                return
            _path, entry = entry_from_payload(payload)
            source = str(payload.get("source") or getattr(entry, "msgid", "") or "") if entry is not None else str(payload.get("source") or "")
            translation = getattr(entry, "msgstr", "") if entry is not None else (table.item(row, 3).text() if table.item(row, 3) else "")
            speaker = str(payload.get("speaker") or getattr(entry, "speaker", "") or payload.get("msgctxt") or getattr(entry, "msgctxt", "") or "")
            path_text = ""
            path_obj = _path if isinstance(_path, Path) else None
            if path_obj is not None:
                path_text = file_labels.get(path_obj, path_obj.name)
            line_text = str(payload.get("line") or "")
            label = f"Speaker: {speaker or '—'}"
            if path_text or line_text:
                label += f"  |  File: {path_text}"
                if line_text:
                    label += f":{line_text}"
            detail_loading["value"] = True
            try:
                en_box.setPlainText(source)
                vi_box.setPlainText(translation)
                speaker_label.setText(label)
            finally:
                detail_loading["value"] = False

        def populate(entries: list[ReferenceTranslationConflictEntry]) -> None:
            self._begin_task_progress("Rendering duplicate entries", len(entries))
            self._pump_task_progress()
            loading["value"] = True
            previous_blocked = table.blockSignals(True)
            table.setUpdatesEnabled(False)
            table.clearContents()
            table.setRowCount(len(entries))
            current_group = None
            group_number = 0
            try:
                for row, entry in enumerate(entries):
                    if entry.key != current_group:
                        current_group = entry.key
                        group_number += 1
                    path = resolved(entry.file)
                    file_text = file_labels.get(path, path.name)
                    file_display_text = capped_file_label(file_text)
                    is_hidden = entry.key in hidden_keys
                    hidden_suffix = " HIDDEN" if is_hidden and show_hidden_check.isChecked() else ""
                    hidden_tip = "Hidden duplicate group. It is skipped by default until Hidden is enabled." if is_hidden else ""
                    hidden_fg = HIDDEN_TEXT if is_hidden else None
                    group_item = make_item(
                        f"{group_number}{hidden_suffix}",
                        bg=HIDDEN_GROUP_BG if is_hidden else PANEL_3,
                        fg=hidden_fg,
                        italic=is_hidden,
                    )
                    group_item.setToolTip(hidden_tip)
                    group_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, 0, group_item)
                    speaker_text = entry.speaker or entry.msgctxt or ""
                    table.setItem(row, 1, make_item(speaker_text, bg=HIDDEN_BG_2 if is_hidden else PANEL_2, fg=hidden_fg, italic=is_hidden))
                    en_item = make_item(entry.source, bg=HIDDEN_BG if is_hidden else EN_BG, fg=hidden_fg, italic=is_hidden)
                    en_item.setToolTip(("Hidden duplicate group.\n" if is_hidden else "") + entry.source)
                    set_html(en_item, entry.source, color=HIDDEN_TEXT if is_hidden else TEXT)
                    table.setItem(row, 2, en_item)
                    vi_item = make_item(
                        entry.translation,
                        editable=True,
                        bg=HIDDEN_VI_BG if is_hidden else (VI_BG if entry.translation.strip() else "#4a3828"),
                        fg=hidden_fg,
                        italic=is_hidden,
                    )
                    vi_item.setData(Qt.ItemDataRole.UserRole, ref_payload(entry))
                    vi_item.setToolTip(("Hidden duplicate group.\n" if is_hidden else "") + entry.translation)
                    set_html(vi_item, entry.translation, color=HIDDEN_TEXT if is_hidden else (TEXT if entry.translation.strip() else WARN))
                    table.setItem(row, 3, vi_item)
                    file_item = make_item(file_display_text, bg=HIDDEN_BG_2 if is_hidden else PANEL, fg=hidden_fg, italic=is_hidden)
                    file_item.setToolTip(f"{file_text}\n{path}" if file_text != str(path) else str(path))
                    table.setItem(row, 4, file_item)
                    line_item = make_item(str(entry.line), bg=HIDDEN_BG_2 if is_hidden else PANEL, fg=hidden_fg, italic=is_hidden)
                    line_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, 5, line_item)
                    variants_item = make_item(str(entry.variants), bg=HIDDEN_BG_2 if is_hidden else PANEL, fg=hidden_fg, italic=is_hidden)
                    variants_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, 6, variants_item)
                    update_row_visual(row, entry.translation, refresh_height=False)
                    if (row + 1) % 25 == 0 or row + 1 == len(entries):
                        self._update_task_progress(row + 1, len(entries), "Rendering duplicate entries")
                        self._pump_task_progress()
                refresh_table_row_heights()
            finally:
                table.setUpdatesEnabled(True)
                table.blockSignals(previous_blocked)
                loading["value"] = False
                self._finish_task_progress(f"Rendered {len(entries)} duplicate entries")
            if table.rowCount():
                table.selectRow(0)
                table.setCurrentCell(0, 0)
                load_detail_for_row(0)
            else:
                clear_detail()
            update_status()

        def update_status() -> None:
            update_duplicate_navigation_buttons()
            dirty = len(changed_files)
            prefix = "* " if dirty else ""
            hidden_group_count = len({key for key in entries_by_key if key in hidden_keys})
            shown_entries = table.rowCount()
            shown_group_count = len({
                str((table.item(row, 3).data(Qt.ItemDataRole.UserRole) or {}).get("key") or "")
                for row in range(table.rowCount())
                if table.item(row, 3) is not None
            })
            mode = "hidden shown" if show_hidden_check.isChecked() else "hidden off"
            status.setText(
                f"{prefix}{shown_entries} shown entries | {shown_group_count}/{all_group_count} duplicate source group(s) | "
                f"hidden groups={hidden_group_count} | {result.source_files} working .po file(s) | changed files={dirty} | {mode}"
            )

        def item_changed(item: QTableWidgetItem) -> None:
            if loading["value"] or item.column() != 3:
                return
            row = item.row()
            set_entry_translation(row, item.text(), update_detail=(row == table.currentRow()), undo_label="edit")

        def detail_changed() -> None:
            if detail_loading["value"] or loading["value"]:
                return
            row = table.currentRow()
            if row < 0:
                return
            set_entry_translation(row, vi_box.toPlainText(), undo_label="edit")

        def selection_changed() -> None:
            load_detail_for_row(table.currentRow())

        def selected_rows_or_current() -> list[int]:
            rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
            if not rows and table.currentRow() >= 0:
                rows = [table.currentRow()]
            return rows

        def preset_replace_selected() -> None:
            rows = selected_rows_or_current()
            if not rows:
                QMessageBox.warning(dialog, view_title, "Select duplicate row(s) first.")
                return
            try:
                rules = self._enabled_preset_rules()
            except Exception as exc:
                QMessageBox.warning(dialog, "Preset Replace", str(exc))
                return
            if not rules:
                status.setText("No enabled preset rules.")
                return
            changed = 0
            total_hits = 0
            undo_rows: list[dict[str, object]] = []
            current_row = table.currentRow()
            self._begin_task_progress("Applying preset rules", len(rows))
            self._pump_task_progress()
            for position, row in enumerate(rows, start=1):
                item = table.item(row, 3)
                payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                path, entry = entry_from_payload(payload) if isinstance(payload, dict) else (None, None)
                if path is not None and entry is not None:
                    old_text = entry.msgstr
                    new_text, hits = apply_rules_to_entry(entry, rules)
                    if hits:
                        total_hits += sum(count for _rule, count, _before, _after in hits)
                    if hits and new_text != old_text and set_entry_translation(
                        row, new_text, update_detail=(row == current_row)
                    ):
                        undo_rows.append(
                            {"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": new_text, "label": "preset replace"}
                        )
                        changed += 1
                self._update_task_progress(position, len(rows), "Applying preset rules")
                self._pump_task_progress()
            push_duplicate_undo(undo_rows)
            self._finish_task_progress(f"Preset replaced {changed} duplicate entries")
            update_status()
            status.setText(status.text() + f" | preset hits={total_hits} | changed={changed}")

        def selected_group_keys() -> set[str]:
            keys: set[str] = set()
            for row in selected_rows_or_current():
                item = table.item(row, 3)
                payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if isinstance(payload, dict):
                    key = str(payload.get("key") or "")
                    if key:
                        keys.add(key)
            return keys

        def duplicate_source_group_starts() -> list[int]:
            starts: list[int] = []
            previous_key: str | None = None
            for row in range(table.rowCount()):
                key = payload_key_from_row(row)
                if not key:
                    continue
                if key != previous_key:
                    starts.append(row)
                    previous_key = key
            return starts

        def update_duplicate_navigation_buttons() -> None:
            enabled = len(duplicate_source_group_starts()) > 1
            prev_source_btn.setEnabled(enabled)
            next_source_btn.setEnabled(enabled)

        def jump_duplicate_source(direction: int) -> None:
            starts = duplicate_source_group_starts()
            if not starts:
                status.setText("No duplicate rows loaded.")
                update_duplicate_navigation_buttons()
                return
            if len(starts) == 1:
                target_row = starts[0]
                status.setText("Only one duplicate source group is shown.")
            else:
                current_row = table.currentRow()
                if current_row < 0:
                    target_row = starts[0] if direction >= 0 else starts[-1]
                else:
                    current_index = 0
                    for index, start_row in enumerate(starts):
                        if start_row <= current_row:
                            current_index = index
                        else:
                            break
                    target_index = (current_index + (1 if direction >= 0 else -1)) % len(starts)
                    target_row = starts[target_index]
            table.clearSelection()
            table.selectRow(target_row)
            table.setCurrentCell(target_row, 0)
            item = table.item(target_row, 0) or table.item(target_row, 3)
            if item is not None:
                table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            load_detail_for_row(target_row)

        def remove_displayed_rows_for_keys(keys: set[str]) -> int:
            if not keys:
                return 0
            removed = 0
            previous_row = max(0, table.currentRow())
            loading["value"] = True
            previous_blocked = table.blockSignals(True)
            table.setUpdatesEnabled(False)
            try:
                for row in range(table.rowCount() - 1, -1, -1):
                    if payload_key_from_row(row) in keys:
                        table.removeRow(row)
                        removed += 1
                refresh_table_row_heights()
            finally:
                table.setUpdatesEnabled(True)
                table.blockSignals(previous_blocked)
                loading["value"] = False
            if table.rowCount():
                next_row = min(previous_row, table.rowCount() - 1)
                table.selectRow(next_row)
                table.setCurrentCell(next_row, 0)
                load_detail_for_row(next_row)
            else:
                clear_detail()
            return removed

        def repopulate_after_hidden_change(message: str = "") -> None:
            populate(displayed_conflict_entries())
            if message:
                status.setText(status.text() + f" | {message}")

        def hide_selected_groups() -> None:
            keys = selected_group_keys()
            if not keys:
                QMessageBox.warning(dialog, view_title, "Select duplicate group(s) first.")
                return
            before = len(hidden_keys)
            hidden_keys.update(keys)
            added = len(hidden_keys) - before
            save_hidden_duplicate_keys()
            if show_hidden_check.isChecked():
                restyle_rows_for_keys(keys)
                update_status()
                status.setText(status.text() + f" | hidden added={added}")
                return
            removed = remove_displayed_rows_for_keys(keys)
            update_status()
            status.setText(status.text() + f" | hidden added={added} | rows removed={removed}")

        def unhide_selected_groups() -> None:
            keys = selected_group_keys()
            if not keys:
                QMessageBox.warning(dialog, view_title, "Select hidden duplicate group(s) first.")
                return
            before = len(hidden_keys)
            hidden_keys.difference_update(keys)
            removed = before - len(hidden_keys)
            save_hidden_duplicate_keys()
            if show_hidden_check.isChecked():
                restyle_rows_for_keys(keys)
                update_status()
                status.setText(status.text() + f" | hidden removed={removed}")
                return
            update_status()
            status.setText(status.text() + f" | hidden removed={removed}")

        def toggle_hidden_visibility() -> None:
            repopulate_after_hidden_change()

        def breakline_selected(preset_index: int | None = None) -> None:
            rows = selected_rows_or_current()
            if not rows:
                QMessageBox.warning(dialog, view_title, "Select duplicate row(s) first.")
                return
            changed = 0
            undo_rows: list[dict[str, object]] = []
            current_row = table.currentRow()
            soft_value, hard_value, cuts_value = self._linewrap_settings(preset_index)
            self._begin_task_progress("Wrapping duplicate entries", len(rows))
            self._pump_task_progress()
            for position, row in enumerate(rows, start=1):
                item = table.item(row, 3)
                if item is not None:
                    payload = item.data(Qt.ItemDataRole.UserRole)
                    path, entry = entry_from_payload(payload) if isinstance(payload, dict) else (None, None)
                    if path is not None and entry is not None:
                        old_text = entry.msgstr
                        fixed, did_change = wrap_msgstr(item.text(), soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                        if did_change and set_entry_translation(row, fixed, update_detail=(row == current_row)):
                            undo_rows.append({"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": fixed, "label": "wrap"})
                            changed += 1
                self._update_task_progress(position, len(rows), "Wrapping duplicate entries")
                self._pump_task_progress()
            self._finish_task_progress(f"Wrapped {changed} duplicate entries")
            push_duplicate_undo(undo_rows)
            refresh_table_row_heights()
            update_status()
            status.setText(
                status.text()
                + f" | W{self._active_linewrap_preset_index() + 1} changed={changed} | "
                + f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}"
            )

        def visible_row_for_entry(path: Path, uid: str) -> int | None:
            for row in range(table.rowCount()):
                item = table.item(row, 3)
                payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if not isinstance(payload, dict):
                    continue
                row_path = resolved(Path(str(payload.get("path") or "")))
                if row_path == path and str(payload.get("uid") or "") == uid:
                    return row
            return None

        def wrap_current_duplicate_file() -> None:
            payload = current_payload()
            if not isinstance(payload, dict):
                QMessageBox.warning(dialog, view_title, "Select a duplicate row first.")
                return
            path, _current_entry = entry_from_payload(payload)
            if path is None:
                return
            po_file = po_cache.get(path)
            if po_file is None:
                QMessageBox.warning(dialog, view_title, f"Could not load {path.name}.")
                return
            soft_value, hard_value, cuts_value = self._linewrap_settings()
            changes: list[dict[str, object]] = []
            changed = 0
            entries = list(getattr(po_file, "entries", []))
            self._begin_task_progress(f"Wrapping {path.name}", len(entries))
            self._pump_task_progress()
            loading["value"] = True
            try:
                for position, entry in enumerate(entries, start=1):
                    old_text = entry.msgstr
                    fixed, did_change = wrap_msgstr(
                        old_text,
                        soft=soft_value,
                        hard=hard_value,
                        max_cuts=cuts_value,
                    )
                    if did_change:
                        entry.msgstr = fixed
                        row = visible_row_for_entry(path, entry.uid)
                        changes.append(
                            {
                                "row": row if row is not None else -1,
                                "path": path,
                                "uid": entry.uid,
                                "old": old_text,
                                "new": fixed,
                                "label": "wrap file",
                            }
                        )
                        if row is not None:
                            item = table.item(row, 3)
                            if item is not None:
                                item.setText(fixed)
                                update_row_visual(row, fixed)
                        changed += 1
                    if position % 25 == 0 or position == len(entries):
                        self._update_task_progress(position, len(entries), f"Wrapping {path.name}")
                        self._pump_task_progress()
            finally:
                loading["value"] = False
                self._finish_task_progress(f"Wrapped {changed} entries in {path.name}")
            if changes:
                refresh_changed_file(path)
                push_duplicate_undo(changes)
            load_detail_for_row(table.currentRow())
            refresh_table_row_heights()
            update_status()
            status.setText(
                status.text()
                + f" | W{self._active_linewrap_preset_index() + 1} entire file={path.name} changed={changed} | "
                + f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}"
            )

        def translate_duplicate_with_gemini_api() -> None:
            if self._active_thread is not None and self._active_thread.is_alive():
                QMessageBox.warning(dialog, "Busy", "Another action is already running. Stop it first.")
                return
            rows = selected_rows_or_current()
            if not rows:
                QMessageBox.warning(dialog, view_title, "Select duplicate row(s) first.")
                return
            api_key = str(self.config.get("gemini_api_key", "")).strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                QMessageBox.warning(dialog, view_title, "Enter the Gemini API key in the AI Translation tab, or set GEMINI_API_KEY.")
                return
            work_by_path: dict[Path, list[tuple[int, POEntry]]] = {}
            context_by_path: dict[Path, list[POEntry]] = {}
            seen: set[tuple[Path, str]] = set()
            for row in rows:
                item = table.item(row, 3)
                payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                path, entry = entry_from_payload(payload) if isinstance(payload, dict) else (None, None)
                if path is None or entry is None or (path, entry.uid) in seen:
                    continue
                seen.add((path, entry.uid))
                source = str(payload.get("source") or entry.msgid)
                request_entry = POEntry(
                    index=entry.index,
                    msgctxt=entry.msgctxt,
                    msgid=source,
                    msgstr=entry.msgstr,
                    comments=list(entry.comments),
                    extracted_comments=list(entry.extracted_comments),
                    line=entry.line,
                )
                work_by_path.setdefault(path, []).append((row, request_entry))
                po_file = po_cache.get(path)
                if po_file is not None:
                    context_by_path[path] = copy.deepcopy(list(getattr(po_file, "entries", [])))
            if not work_by_path:
                QMessageBox.warning(dialog, view_title, "No duplicate entries were available for Gemini.")
                return

            model = self._gemini_api_profile_model("single")
            sleep_seconds = self._gemini_api_profile_sleep_seconds("single")
            timeout_seconds = self._gemini_api_timeout_seconds("single")
            thinking_mode = self._gemini_api_profile_thinking_mode("single")
            max_output_tokens = self._gemini_api_profile_max_output_tokens("single")
            total_entries = sum(len(items) for items in work_by_path.values())
            context_limit = self._gemini_api_context_limit("single")
            use_previous_files = self._gemini_api_cross_file_context_enabled("single") and context_limit > 0

            self._begin_task_progress("Gemini duplicate rows", total_entries)
            status.setText(status.text() + f" | Gemini translating {total_entries} row(s)...")
            for widget in (table, vi_box, gemini_btn):
                widget.setEnabled(False)
            self.stop_button.setEnabled(True)
            self._stop_event.clear()

            signals = WorkerSignals()
            self._active_signals.append(signals)
            task_state = {"applied": False, "failed": False, "stopped": False, "cleaned": False}

            def gemini_progress(done: int, total: int, label: str) -> None:
                self._update_task_progress(done, total, label)
                status.setText(status.text().split(" | Gemini translating", 1)[0] + f" | Gemini translating {done}/{total}")

            def apply_gemini_result(payload: object) -> None:
                if not isinstance(payload, dict):
                    return
                translations_by_row = payload.get("translations", {})
                errors = payload.get("errors", [])
                usage = str(payload.get("usage") or "").strip()
                if not isinstance(translations_by_row, dict) or not isinstance(errors, list):
                    return
                task_state["applied"] = True
                changes: list[dict[str, object]] = []
                changed = 0
                current_row = table.currentRow()
                for row, translation in translations_by_row.items():
                    if not isinstance(row, int) or not isinstance(translation, str):
                        continue
                    item = table.item(row, 3)
                    row_payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                    path, entry = entry_from_payload(row_payload) if isinstance(row_payload, dict) else (None, None)
                    if path is None or entry is None or entry.msgstr == translation:
                        continue
                    old_text = entry.msgstr
                    if set_entry_translation(row, translation, update_detail=(row == current_row)):
                        changes.append(
                            {
                                "row": row,
                                "path": path,
                                "uid": entry.uid,
                                "old": old_text,
                                "new": translation,
                                "label": "gemini",
                            }
                        )
                        changed += 1
                push_duplicate_undo(changes)
                self._finish_task_progress(f"Gemini translated {changed} duplicate entries")
                update_status()
                status.setText(status.text() + f" | Gemini changed={changed}" + (f" | tokens: {usage}" if usage else ""))
                if errors:
                    preview = "\n".join(f"{entry.msgctxt or entry.uid}: {entry.reason}" for entry in errors[:8])
                    more = f"\n... {len(errors) - 8} more" if len(errors) > 8 else ""
                    QMessageBox.warning(
                        dialog,
                        "Gemini API",
                        f"Translated {changed} duplicate entries, with {len(errors)} validation issue(s):\n{preview}{more}",
                    )

            def gemini_failed(message: str) -> None:
                stopped = message == "Gemini API translation stopped."
                task_state["stopped"] = stopped
                task_state["failed"] = not stopped
                self._finish_task_progress("Gemini API stopped" if stopped else "Gemini API failed")
                status.setText(message)
                if not stopped:
                    QMessageBox.critical(dialog, "Gemini API", message)

            def finish_gemini_task() -> None:
                if task_state["cleaned"]:
                    return
                task_state["cleaned"] = True
                for widget in (table, vi_box, gemini_btn):
                    try:
                        widget.setEnabled(True)
                    except RuntimeError:
                        pass
                self.stop_button.setEnabled(False)
                self._active_thread = None
                try:
                    self._active_signals.remove(signals)
                except ValueError:
                    pass
                if not task_state["applied"] and not task_state["failed"] and not task_state["stopped"]:
                    self._finish_task_progress("Gemini API stopped")
                    task_state["stopped"] = True
                notification_status = "failed" if task_state["failed"] else ("stopped" if task_state["stopped"] else "success")
                self._notify_task_complete(notification_status)

            signals.progress.connect(gemini_progress)
            signals.result.connect(apply_gemini_result)
            signals.error.connect(gemini_failed)
            signals.done.connect(finish_gemini_task)

            def gemini_worker() -> None:
                try:
                    client = GeminiApiClient(
                        api_key=api_key,
                        model=model,
                        prompt=SYSTEM_INSTRUCTIONS,
                        timeout_seconds=timeout_seconds,
                        thinking_mode=thinking_mode,
                        max_output_tokens=max_output_tokens,
                    )
                    translations_by_row: dict[int, str] = {}
                    errors = []
                    completed = 0
                    previous_file_context: list[POEntry] = []
                    groups = list(work_by_path.items())
                    for group_index, (path, items) in enumerate(groups):
                        self._check_stop()
                        request_entries = [entry for _row, entry in items]
                        row_by_uid = {entry.uid: row for row, entry in items}

                        def report_gemini_progress(done: int, _total: int, *, offset: int = completed) -> None:
                            self._check_stop()
                            signals.progress.emit(offset + done, total_entries, "Gemini duplicate rows")

                        translations, entry_errors = translate_entries_with_client(
                            request_entries,
                            client,
                            batch_size=1,
                            sleep_seconds=sleep_seconds,
                            allow_partial=False,
                            prompt=SYSTEM_INSTRUCTIONS,
                            progress=report_gemini_progress,
                            context_entries=context_by_path.get(path, []),
                            context_limit=context_limit,
                            previous_file_context_entries=previous_file_context if use_previous_files else None,
                            cancel_check=self._check_stop,
                        )
                        errors.extend(entry_errors)
                        for uid, translation in translations.items():
                            row = row_by_uid.get(uid)
                            if row is not None:
                                translations_by_row[row] = translation
                        if use_previous_files:
                            file_context = context_by_path.get(path, [])
                            translated_by_uid = dict(translations)
                            for context_entry in file_context:
                                translated = translated_by_uid.get(context_entry.uid)
                                if translated is not None:
                                    context_entry.msgstr = translated
                            previous_file_context.extend(file_context)
                            if len(previous_file_context) > context_limit:
                                previous_file_context = previous_file_context[-context_limit:]
                        completed += len(items)
                        if sleep_seconds and group_index + 1 < len(groups):
                            deadline = time.monotonic() + sleep_seconds
                            while time.monotonic() < deadline:
                                self._check_stop()
                                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                    self._check_stop()
                    signals.result.emit({"translations": translations_by_row, "errors": errors, "usage": client.total_usage.summary()})
                except OperationCancelled:
                    signals.error.emit("Gemini API translation stopped.")
                except Exception as exc:
                    signals.error.emit(f"Gemini API translation failed:\n{exc}")
                finally:
                    signals.done.emit()

            thread = threading.Thread(target=gemini_worker, daemon=True)
            self._active_thread = thread
            thread.start()

        def switch_duplicate_file(delta: int) -> None:
            payload = current_payload()
            if not isinstance(payload, dict) or table.rowCount() <= 1:
                return
            current_path = resolved(Path(str(payload.get("path") or "")))
            current_row = table.currentRow()
            keep_editor_focus = vi_box.hasFocus()
            for offset in range(1, table.rowCount() + 1):
                row = (current_row + delta * offset) % table.rowCount()
                item = table.item(row, 3)
                row_payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if not isinstance(row_payload, dict):
                    continue
                row_path = resolved(Path(str(row_payload.get("path") or "")))
                if row_path == current_path:
                    continue
                table.clearSelection()
                table.selectRow(row)
                table.setCurrentCell(row, 0)
                table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                load_detail_for_row(row)
                if keep_editor_focus:
                    QTimer.singleShot(0, lambda: vi_box.setFocus(Qt.FocusReason.ShortcutFocusReason))
                status.setText(status.text() + f" | switched to {row_path.name}")
                return
            status.setText(status.text() + " | only one file shown")

        def apply_to_same_source() -> None:
            payload = current_payload()
            if not isinstance(payload, dict):
                QMessageBox.warning(dialog, view_title, "Select a duplicate row first.")
                return
            key = str(payload.get("key") or "")
            if not key:
                return
            new_text = current_translation_text()
            changed = 0
            undo_rows: list[dict[str, object]] = []
            total_rows = table.rowCount()
            self._begin_task_progress("Applying duplicate translation", total_rows)
            self._pump_task_progress()
            loading["value"] = True
            try:
                for row in range(total_rows):
                    item = table.item(row, 3)
                    row_payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                    if isinstance(row_payload, dict) and str(row_payload.get("key") or "") == key:
                        path, entry = entry_from_payload(row_payload)
                        if path is not None and entry is not None:
                            if entry.msgstr == new_text:
                                update_row_visual(row, new_text)
                            else:
                                undo_rows.append({"row": row, "path": path, "uid": entry.uid, "old": entry.msgstr, "new": new_text})
                                entry.msgstr = new_text
                                refresh_changed_file(path)
                                if item is not None:
                                    item.setText(new_text)
                                    update_row_visual(row, new_text)
                                changed += 1
                    if (row + 1) % 25 == 0 or row + 1 == total_rows:
                        self._update_task_progress(row + 1, total_rows, "Applying duplicate translation")
                        self._pump_task_progress()
            finally:
                loading["value"] = False
                self._finish_task_progress(f"Applied to {changed} duplicate rows")
            push_duplicate_undo(undo_rows)
            load_detail_for_row(table.currentRow())
            update_status()
            if changed:
                status.setText(status.text() + f" | mass changed={changed}")
            else:
                status.setText(status.text() + " | no same-source rows needed change")

        def undo_apply_to_same_source() -> None:
            if not apply_undo_stack:
                status.setText("Nothing to undo.")
                undo_apply_btn.setEnabled(False)
                return
            undo_rows = apply_undo_stack.pop()
            undone = 0
            affected_paths: set[Path] = set()
            loading["value"] = True
            undoing["value"] = True
            try:
                for change in reversed(undo_rows):
                    path = change.get("path")
                    uid = str(change.get("uid") or "")
                    old_text = str(change.get("old") or "")
                    if not isinstance(path, Path) or not uid:
                        continue
                    po_file = po_cache.get(path)
                    if po_file is None:
                        continue
                    entry = next((candidate for candidate in getattr(po_file, "entries", []) if candidate.uid == uid), None)
                    if entry is None:
                        continue
                    entry.msgstr = old_text
                    affected_paths.add(path)
                    for row in range(table.rowCount()):
                        item = table.item(row, 3)
                        row_payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                        if not isinstance(row_payload, dict):
                            continue
                        if resolved(Path(str(row_payload.get("path") or ""))) == path and str(row_payload.get("uid") or "") == uid:
                            item.setText(old_text)
                            update_row_visual(row, old_text)
                            break
                    undone += 1
                for path in affected_paths:
                    refresh_changed_file(path)
            finally:
                undoing["value"] = False
                loading["value"] = False
            undo_apply_btn.setEnabled(bool(apply_undo_stack))
            load_detail_for_row(table.currentRow())
            update_status()
            status.setText(status.text() + f" | undo applied={undone}")

        def undo_duplicate_change() -> None:
            focus = QApplication.focusWidget()
            if focus is vi_box or (focus is not None and vi_box.isAncestorOf(focus)):
                undoing["value"] = True
                try:
                    if self._undo_text_editor(vi_box):
                        return
                finally:
                    undoing["value"] = False
            if self._undo_focused_text_editor():
                return
            if focus is table or (focus is not None and table.isAncestorOf(focus)):
                undoing["value"] = True
                try:
                    if self._undo_text_editor(vi_box):
                        return
                finally:
                    undoing["value"] = False
            undo_apply_to_same_source()

        def open_search_replace_dialog() -> None:
            existing = search_replace_state.get("dialog")
            if isinstance(existing, QDialog):
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return

            search_dialog = QDialog(dialog)
            search_dialog.setWindowTitle(f"{view_title} Find / Replace")
            search_dialog.setModal(False)
            search_dialog.resize(560, 220)
            root_layout = QVBoxLayout(search_dialog)
            root_layout.setContentsMargins(10, 10, 10, 10)
            root_layout.setSpacing(8)

            form = QFormLayout()
            find_edit = QLineEdit(str(search_replace_state.get("find", "")))
            find_edit.setPlaceholderText("Find text; another find. Spaces and \\n also match real line breaks. Use \\; for literal ;.")
            replace_edit = QLineEdit(str(search_replace_state.get("replace", "")))
            replace_edit.setPlaceholderText("Replacement; another replacement. Use \\n for a line break and \\; for literal ;.")
            scope_combo = QComboBox()
            scope_combo.addItem("Vietnamese", "vi")
            scope_combo.addItem("English", "en")
            scope_combo.addItem("Both", "both")
            saved_scope = str(search_replace_state.get("scope", "both"))
            scope_index = scope_combo.findData(saved_scope)
            scope_combo.setCurrentIndex(scope_index if scope_index >= 0 else 2)
            form.addRow("Find", find_edit)
            form.addRow("Replace", replace_edit)
            form.addRow("Search in", scope_combo)
            root_layout.addLayout(form)

            option_row = QHBoxLayout()
            case_chk = QCheckBox("Case")
            whole_chk = QCheckBox("Whole word")
            regex_chk = QCheckBox("Regex")
            case_chk.setChecked(bool(search_replace_state.get("case", False)))
            whole_chk.setChecked(bool(search_replace_state.get("whole", False)))
            regex_chk.setChecked(bool(search_replace_state.get("regex", False)))
            for widget in (case_chk, whole_chk, regex_chk):
                option_row.addWidget(widget)
            option_row.addStretch()
            root_layout.addLayout(option_row)

            status_label = QLabel("Use ; for ordered find→replace pairs. Replace edits Vietnamese only. Find can search English, Vietnamese, or both.")
            status_label.setObjectName("muted")
            status_label.setWordWrap(True)
            root_layout.addWidget(status_label)

            button_row = QHBoxLayout()
            prev_btn = self._button("Find Prev", secondary=True)
            next_btn = self._button("Find Next", secondary=True)
            replace_btn = self._button("Replace", secondary=True)
            replace_all_btn = self._button("Replace All")
            close_btn = self._button("Close", secondary=True)
            for widget in (prev_btn, next_btn, replace_btn, replace_all_btn):
                button_row.addWidget(widget)
            button_row.addStretch()
            button_row.addWidget(close_btn)
            root_layout.addLayout(button_row)

            def remember_search_settings() -> None:
                search_replace_state["find"] = find_edit.text()
                search_replace_state["replace"] = replace_edit.text()
                search_replace_state["scope"] = scope_combo.currentData()
                search_replace_state["case"] = case_chk.isChecked()
                search_replace_state["whole"] = whole_chk.isChecked()
                search_replace_state["regex"] = regex_chk.isChecked()

            def compile_patterns() -> list[tuple[re.Pattern[str], object]] | None:
                remember_search_settings()
                try:
                    compiled = compile_search_replace_sequence(
                        find_edit.text(),
                        replace_edit.text(),
                        case_sensitive=case_chk.isChecked(),
                        whole_word=whole_chk.isChecked(),
                        regex=regex_chk.isChecked(),
                    )
                except SearchReplaceCompileError as exc:
                    status_label.setText(f"Invalid regex in item {exc.index}: {exc.error}")
                    return None
                if not compiled:
                    status_label.setText("Enter text to search.")
                    return None
                return compiled

            def row_text(row: int, field: str) -> str:
                if row < 0 or row >= table.rowCount():
                    return ""
                if field == "vi":
                    if row == table.currentRow() and vi_box.hasFocus():
                        return vi_box.toPlainText()
                    item = table.item(row, 3)
                    return item.text() if item is not None else ""
                item = table.item(row, 2)
                return item.text() if item is not None else ""

            def fields_for_row() -> list[str]:
                scope = str(scope_combo.currentData())
                fields: list[str] = []
                if scope in {"vi", "both"}:
                    fields.append("vi")
                if scope in {"en", "both"}:
                    fields.append("en")
                return fields

            def find_in_row(row: int, compiled: list[tuple[re.Pattern[str], object]]) -> tuple[str, re.Match[str]] | None:
                for field in fields_for_row():
                    text_value = row_text(row, field)
                    for pattern, _replacement in compiled:
                        match = pattern.search(text_value)
                        if match:
                            return field, match
                return None

            def select_and_highlight(row: int, field: str, match: re.Match[str]) -> None:
                table.selectRow(row)
                table.setCurrentCell(row, 3 if field == "vi" else 2)
                load_detail_for_row(row)
                box = vi_box if field == "vi" else en_box
                cursor = box.textCursor()
                cursor.setPosition(match.start())
                cursor.setPosition(match.end(), QTextCursor.MoveMode.KeepAnchor)
                box.setTextCursor(cursor)
                box.setFocus(Qt.FocusReason.ShortcutFocusReason)
                table.scrollToItem(table.item(row, 3 if field == "vi" else 2), QAbstractItemView.ScrollHint.PositionAtCenter)

            def find_match(direction: int = 1) -> bool:
                compiled = compile_patterns()
                if compiled is None:
                    return False
                total = table.rowCount()
                if total <= 0:
                    status_label.setText("No duplicate rows loaded.")
                    return False
                current = table.currentRow()
                if current < 0:
                    current = 0 if direction >= 0 else total - 1
                signature = (
                    find_edit.text(),
                    replace_edit.text(),
                    scope_combo.currentData(),
                    case_chk.isChecked(),
                    whole_chk.isChecked(),
                    regex_chk.isChecked(),
                    show_hidden_check.isChecked(),
                    table.rowCount(),
                )
                previous_signature = search_replace_state.get("signature")
                previous_row = search_replace_state.get("last_row")
                start = current
                if previous_signature == signature and isinstance(previous_row, int) and previous_row == current:
                    start = current + direction
                for step in range(total):
                    row = (start + (step * direction)) % total
                    found = find_in_row(row, compiled)
                    if not found:
                        continue
                    field, match = found
                    select_and_highlight(row, field, match)
                    search_replace_state["signature"] = signature
                    search_replace_state["last_row"] = row
                    where = "Vietnamese" if field == "vi" else "English"
                    status_label.setText(f"Found in {where} at row {row + 1}/{total}.")
                    return True
                status_label.setText("No match found.")
                return False

            def change_row_translation(row: int, new_text: str, *, undo_label: str) -> bool:
                item = table.item(row, 3)
                payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if not isinstance(payload, dict):
                    return False
                path, entry = entry_from_payload(payload)
                if path is None or entry is None:
                    return False
                old_text = entry.msgstr
                if old_text == new_text:
                    return False
                if set_entry_translation(row, new_text, update_detail=(row == table.currentRow())):
                    push_duplicate_undo([{"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": new_text, "label": undo_label}])
                    return True
                return False

            def replace_current() -> None:
                if str(scope_combo.currentData()) == "en":
                    status_label.setText("Replace edits Vietnamese only. Switch Search in to Vietnamese or Both.")
                    return
                compiled = compile_patterns()
                if compiled is None:
                    return
                row = table.currentRow()
                if row < 0 or row >= table.rowCount():
                    status_label.setText("Select a duplicate row first.")
                    return
                source_text = row_text(row, "vi")
                new_text, count = apply_search_replace_sequence(source_text, compiled, count_per_pattern=1)
                if count <= 0:
                    status_label.setText("Current row has no Vietnamese match to replace.")
                    return
                if change_row_translation(row, new_text, undo_label="replace"):
                    status_label.setText(f"Replaced {count} match{'es' if count != 1 else ''} in row {row + 1}.")
                    find_match(1)
                else:
                    status_label.setText("Replacement made no change.")

            def replace_all() -> None:
                if str(scope_combo.currentData()) == "en":
                    status_label.setText("Replace edits Vietnamese only. Switch Search in to Vietnamese or Both.")
                    return
                compiled = compile_patterns()
                if compiled is None:
                    return
                undo_rows: list[dict[str, object]] = []
                changed_rows = 0
                total_matches = 0
                total_rows = table.rowCount()
                self._begin_task_progress("Replacing duplicate entries", total_rows)
                self._pump_task_progress()
                loading["value"] = True
                try:
                    for row in range(total_rows):
                        item = table.item(row, 3)
                        payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                        if isinstance(payload, dict):
                            path, entry = entry_from_payload(payload)
                            if path is not None and entry is not None:
                                old_text = entry.msgstr
                                new_text, count = apply_search_replace_sequence(old_text, compiled)
                                if count > 0 and new_text != old_text:
                                    undo_rows.append({"row": row, "path": path, "uid": entry.uid, "old": old_text, "new": new_text, "label": "replace all"})
                                    entry.msgstr = new_text
                                    refresh_changed_file(path)
                                    if item is not None:
                                        item.setText(new_text)
                                        update_row_visual(row, new_text)
                                    if row == table.currentRow():
                                        detail_loading["value"] = True
                                        try:
                                            vi_box.setPlainText(new_text)
                                        finally:
                                            detail_loading["value"] = False
                                    changed_rows += 1
                                    total_matches += count
                        if (row + 1) % 25 == 0 or row + 1 == total_rows:
                            self._update_task_progress(row + 1, total_rows, "Replacing duplicate entries")
                            self._pump_task_progress()
                finally:
                    loading["value"] = False
                    self._finish_task_progress(f"Replaced {total_matches} duplicate matches")
                push_duplicate_undo(undo_rows)
                update_status()
                status_label.setText(f"Replaced {total_matches} match{'es' if total_matches != 1 else ''} in {changed_rows} row{'s' if changed_rows != 1 else ''}.")

            prev_btn.clicked.connect(lambda: find_match(-1))
            next_btn.clicked.connect(lambda: find_match(1))
            replace_btn.clicked.connect(replace_current)
            replace_all_btn.clicked.connect(replace_all)
            close_btn.clicked.connect(search_dialog.close)
            find_edit.returnPressed.connect(lambda: find_match(1))
            find_next_shortcut = QShortcut(QKeySequence("F3"), search_dialog)
            find_next_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            find_next_shortcut.activated.connect(lambda: find_match(1))
            find_prev_shortcut = QShortcut(QKeySequence("Shift+F3"), search_dialog)
            find_prev_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            find_prev_shortcut.activated.connect(lambda: find_match(-1))
            search_dialog.finished.connect(lambda _result: search_replace_state.pop("dialog", None))
            search_replace_state["dialog"] = search_dialog
            search_replace_state["find_next_shortcut"] = find_next_shortcut
            search_replace_state["find_prev_shortcut"] = find_prev_shortcut
            search_dialog.show()
            find_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            find_edit.selectAll()

        def save_one_file(path: Path) -> tuple[bool, str | None]:
            po = po_cache.get(path)
            if po is None:
                return False, f"{path}: not loaded"
            try:
                save_po(po, path)
                for entry in getattr(po, "entries", []):
                    original_translations[(path, entry.uid)] = entry.msgstr
                changed_files.discard(path)
                return True, None
            except Exception as exc:
                return False, f"{path}: {exc}"

        def save_changed() -> None:
            if not changed_files:
                status.setText("No changed files to save.")
                return
            save_paths = sorted(list(changed_files), key=lambda item: str(item).casefold())
            self._begin_task_progress("Saving duplicate PO files", len(save_paths))
            self._pump_task_progress()
            saved = 0
            errors: list[str] = []
            for file_index, path in enumerate(save_paths, start=1):
                ok, error = save_one_file(path)
                if ok:
                    saved += 1
                elif error:
                    errors.append(error)
                self._update_task_progress(file_index, len(save_paths), f"Saving duplicate {path.name}")
                self._pump_task_progress()
            self._finish_task_progress(f"Saved {saved}/{len(save_paths)} duplicate PO files")
            if errors:
                QMessageBox.warning(self, view_title, "Some files could not be saved:\n" + "\n".join(errors[:10]))
            update_status()
            if saved:
                status.setText(status.text() + f" | saved={saved}")

        def open_selected_file_in_po_viewer() -> None:
            payload = current_payload()
            if not isinstance(payload, dict):
                QMessageBox.warning(dialog, view_title, "Select a duplicate row first.")
                return
            path = resolved(Path(str(payload.get("path") or "")))
            if not path.is_file() or path.suffix.lower() != ".po":
                QMessageBox.warning(dialog, view_title, f"Selected row file is not a real .po file:\n{path}")
                return
            if path in changed_files:
                reply = QMessageBox.question(
                    dialog,
                    "Open File",
                    "This file has unsaved edits in the duplicate view. Save this file before opening it in PO Viewer?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    return
                if reply == QMessageBox.StandardButton.Yes:
                    ok, error = save_one_file(path)
                    if not ok:
                        QMessageBox.warning(dialog, "Open File", "Could not save before opening:\n" + (error or str(path)))
                        return
                    update_status()
            opener = getattr(self, "_open_file_in_po_viewer", None)
            if not callable(opener):
                QMessageBox.warning(dialog, view_title, "PO Viewer is not ready yet.")
                return
            line_value: int | None = None
            try:
                raw_line = payload.get("line")
                if raw_line is not None:
                    line_value = int(raw_line)
            except Exception:
                line_value = None
            uid_value = str(payload.get("uid") or "") or None
            # Pass the already-loaded duplicate file list instead of selected root folders.
            # That keeps Open fast in large Working folders while preserving a useful PO Viewer file dropdown.
            source_files = [str(item) for item in sorted(po_cache.keys(), key=lambda value: str(value).casefold())]
            if opener(path, uid=uid_value, line=line_value, source_paths=source_files):
                status.setText(f"Opened {path.name} in PO Viewer.")
            else:
                QMessageBox.warning(dialog, view_title, f"Could not open in PO Viewer:\n{path}")

        def refresh_dialog() -> None:
            nonlocal result, all_group_count
            if changed_files:
                reply = QMessageBox.question(
                    self,
                    view_title,
                    "You have unsaved edits. Refresh and discard them?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            current_references = self._duplicate_scan_paths(tab_key)
            if not current_references:
                QMessageBox.warning(dialog, view_title, "Select checkbox Working folders in this tab first.")
                return
            self._begin_task_progress(f"Rescanning {view_title}")
            self._pump_task_progress()
            try:
                refreshed_entries, refreshed_result = scan_duplicates(current_references)
            except Exception as exc:
                self._finish_task_progress(f"{view_title} rescan failed")
                QMessageBox.critical(dialog, view_title, f"Could not rescan selected Working folders:\n{exc}")
                return

            all_conflict_entries[:] = list(refreshed_entries)
            entries_by_key.clear()
            for conflict_entry in all_conflict_entries:
                entries_by_key[conflict_entry.key].append(conflict_entry)
            result = refreshed_result
            all_group_count = len(entries_by_key)
            po_cache.clear()
            original_translations.clear()
            changed_files.clear()
            apply_undo_stack.clear()
            undo_apply_btn.setEnabled(False)
            current_conflict_files = sorted(
                {resolved(entry.file) for entry in all_conflict_entries},
                key=lambda item: str(item).casefold(),
            )
            self._begin_task_progress("Reloading duplicate PO files", len(current_conflict_files))
            try:
                for file_index, po_path in enumerate(current_conflict_files, start=1):
                    po_cache[po_path] = load_po_clone(po_path)
                    self._update_task_progress(file_index, len(current_conflict_files), f"Reloading duplicate {po_path.name}")
                    self._pump_task_progress()
            except Exception as exc:
                self._finish_task_progress("Duplicate PO reload failed")
                QMessageBox.critical(dialog, view_title, f"Could not reload duplicate files:\n{exc}")
                return
            file_labels.clear()
            file_labels.update(minimal_file_labels(current_conflict_files))
            for po_path, po_file in po_cache.items():
                for entry in getattr(po_file, "entries", []):
                    original_translations[(po_path, entry.uid)] = entry.msgstr
            populate(displayed_conflict_entries())
            status.setText(status.text() + " | refreshed")

        set_duplicate_clt_color_mode(bool(clt_view_state["enabled"]), persist=False, quiet=True)
        table.itemChanged.connect(item_changed)
        table.itemSelectionChanged.connect(selection_changed)
        vi_box.textChanged.connect(detail_changed)
        prev_source_btn.clicked.connect(lambda: jump_duplicate_source(-1))
        next_source_btn.clicked.connect(lambda: jump_duplicate_source(1))
        open_file_btn.clicked.connect(open_selected_file_in_po_viewer)
        search_replace_btn.clicked.connect(open_search_replace_dialog)
        clt_color_btn.clicked.connect(toggle_duplicate_clt_color_mode)
        apply_group_btn.clicked.connect(apply_to_same_source)
        preset_replace_btn.clicked.connect(preset_replace_selected)
        gemini_btn.clicked.connect(translate_duplicate_with_gemini_api)
        undo_apply_btn.clicked.connect(undo_duplicate_change)
        hide_group_btn.clicked.connect(hide_selected_groups)
        unhide_group_btn.clicked.connect(unhide_selected_groups)
        show_hidden_check.stateChanged.connect(lambda _state: toggle_hidden_visibility())
        save_btn.clicked.connect(save_changed)
        refresh_btn.clicked.connect(refresh_dialog)
        close_btn.clicked.connect(dialog.close)
        duplicate_shortcuts: list[QShortcut] = []

        def add_duplicate_shortcut(sequence: str, callback: Callable[[], None]) -> None:
            shortcut = QShortcut(QKeySequence(sequence), dialog)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            duplicate_shortcuts.append(shortcut)

        add_duplicate_shortcut("Ctrl+F", open_search_replace_dialog)
        add_duplicate_shortcut("Ctrl+Z", undo_duplicate_change)
        add_duplicate_shortcut("Ctrl+S", save_changed)
        duplicate_wrap_filter = PersistentWrapShortcutFilter(
            dialog,
            {
                index + 1: (
                    lambda preset_index=index: (
                        self._set_active_linewrap_preset(preset_index),
                        breakline_selected(preset_index),
                    )
                )
                for index in range(4)
            },
            wrap_current_duplicate_file,
            self._custom_shortcut_sequences,
            lambda: switch_duplicate_file(-1),
            lambda: switch_duplicate_file(1),
        )
        add_duplicate_shortcut(PRESET_REPLACE_SHORTCUT, preset_replace_selected)
        add_duplicate_shortcut(GEMINI_TRANSLATE_SHORTCUT, translate_duplicate_with_gemini_api)
        duplicate_undo_filter = RoutedUndoShortcutFilter(dialog, undo_duplicate_change)
        dialog._shortcuts = duplicate_shortcuts  # type: ignore[attr-defined]
        dialog._wrap_shortcut_filter = duplicate_wrap_filter  # type: ignore[attr-defined]
        dialog._undo_shortcut_filter = duplicate_undo_filter  # type: ignore[attr-defined]
        populate(displayed_conflict_entries())
        QTimer.singleShot(0, refresh_table_row_heights)
        update_status()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _build_po_viewer_tab(self) -> None:
        _tab, layout = self._new_tab("PO Viewer")
        self._dr_option_selector(layout, "po_viewer")
        self._po_viewer_tab_widget = _tab

        initial_source = str(self.config.get("po_viewer_source") or self.config.get("po_viewer_file", ""))
        file_combo = QComboBox()
        file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        file_combo.setMinimumContentsLength(24)
        file_combo.setEnabled(False)
        file_combo.addItem("No .po files loaded", None)

        source_row = QHBoxLayout()
        source_row.setSpacing(4)
        file_label = QLabel("File")
        file_label.setStyleSheet("font-weight:700;")
        source_row.addWidget(file_label)
        source_row.addWidget(file_combo, 2)

        source_edit = QLineEdit(initial_source)
        source_edit.setPlaceholderText("Optional extra .po file(s) or folder; ignored unless Extra is checked...")
        source_extra_check = QCheckBox("Use extra")
        source_extra_check.setChecked(bool(self.config.get(self._include_extra_config_key("po_viewer"), False)))
        source_extra_check.setToolTip("When on, the manual PO source is loaded together with selected Working folders.")
        open_po_btn = self._tool_button("", "Open current .po in the system default app", QStyle.StandardPixmap.SP_FileIcon)
        browse_files_btn = self._tool_button("", "Pick extra .po file(s)", QStyle.StandardPixmap.SP_DialogOpenButton)
        browse_folder_btn = self._tool_button("", "Pick extra folder", QStyle.StandardPixmap.SP_DirOpenIcon)
        load_btn = self._tool_button("☑↻", "Load selected checkbox Working folders + enabled Extra source", width=44)
        save_btn = self._tool_button("", "Save current .po", QStyle.StandardPixmap.SP_DialogSaveButton)
        source_row.addWidget(source_extra_check)
        source_row.addWidget(source_edit, 1)
        source_row.addWidget(open_po_btn)
        source_row.addWidget(browse_files_btn)
        source_row.addWidget(browse_folder_btn)
        source_row.addWidget(load_btn)
        source_row.addWidget(save_btn)
        layout.addLayout(source_row)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        wrap_view_btn = self._tool_button("↔ ON", "Toggle visual wrap", width=46)
        clt_color_btn = self._tool_button("CLT", "Toggle CLT tag/color view", width=42)
        wrap_all_btn = self._tool_button("All", "Wrap all translations with the active preset", width=38)
        fill_btn = self._tool_button("TF", "Fill from Translafixer sources", width=38)
        gemini_selected_btn = self._tool_button("AI", "Translate selected rows with Gemini API", width=38)
        undo_edit_btn = self._tool_button("Undo", "Undo the latest PO text change (Ctrl+Z)", width=46)
        undo_edit_btn.setEnabled(False)
        search_replace_btn = self._tool_button("⌕", "Search / replace (Ctrl+F)", width=34)
        preset_replace_btn = self._tool_button("Preset", "Apply enabled ordered replacement rules to selected/current rows", width=58)
        dup_ref_btn = self._tool_button("Dup", "Open duplicate translation view for selected checkbox Working folders", width=42)
        status = QLabel("No file loaded")
        status.setObjectName("muted")
        status.setWordWrap(True)
        view_label = QLabel("View")
        view_label.setObjectName("muted")
        tools.addWidget(view_label)
        tools.addWidget(wrap_view_btn)
        tools.addWidget(clt_color_btn)
        tools.addSpacing(8)
        wrap_label = QLabel("Wrap")
        wrap_label.setObjectName("muted")
        tools.addWidget(wrap_label)
        self._add_linewrap_preset_buttons(
            tools,
            lambda preset_index: wrap_selected(preset_index),
            action="Wrap selected/current PO Viewer rows",
        )
        tools.addWidget(wrap_all_btn)
        tools.addSpacing(8)
        tools.addWidget(fill_btn)
        tools.addWidget(gemini_selected_btn)
        tools.addWidget(undo_edit_btn)
        tools.addWidget(search_replace_btn)
        tools.addWidget(preset_replace_btn)
        tools.addWidget(dup_ref_btn)
        tools.addStretch()
        tools.addWidget(status, 1)
        layout.addLayout(tools)

        split = QSplitter(Qt.Orientation.Vertical)
        table = QTableWidget()
        table.setObjectName("poViewerTable")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["#", "Speaker / Context", "English msgid", "Vietnamese msgstr"])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(36)
        table.verticalHeader().setMinimumSectionSize(36)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        table.setWordWrap(True)
        table.setItemDelegate(NoFocusCellDelegate(table))
        table.setItemDelegateForColumn(2, RichTextCellDelegate(table))
        table.setItemDelegateForColumn(3, RichTextCellDelegate(table))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        split.addWidget(table)

        detail = QSplitter(Qt.Orientation.Horizontal)

        def labeled_box(label: str, box: QPlainTextEdit, *extra_widgets: QWidget) -> QWidget:
            wrap = QWidget()
            box_layout = QVBoxLayout(wrap)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(5)
            lab = QLabel(label)
            lab.setStyleSheet("font-weight:800;")
            box_layout.addWidget(lab)
            for extra_widget in extra_widgets:
                box_layout.addWidget(extra_widget)
            box_layout.addWidget(box, 1)
            return wrap

        en_box = VisibleNewlinePlainTextEdit()
        en_box.setReadOnly(True)
        en_box.setPlaceholderText("English msgid")
        en_box.setFont(QFont("Consolas", 9))
        en_box._clt_highlighter = CltHighlighter(en_box.document())  # keep highlighter alive
        vi_box = VisibleNewlinePlainTextEdit()
        vi_box.setPlaceholderText("Edit Vietnamese msgstr here. English is read-only.")
        vi_box.setFont(QFont("Consolas", 9))
        vi_box._clt_highlighter = CltHighlighter(vi_box.document())  # keep highlighter alive
        self._set_plain_text_visible_rows(en_box, 4)
        self._set_plain_text_visible_rows(vi_box, 4)
        jp_box = VisibleNewlinePlainTextEdit()
        jp_box.setReadOnly(True)
        jp_box.setPlaceholderText("Japanese extracted note")
        jp_box.setFont(QFont("Consolas", 9))
        jp_box._clt_highlighter = CltHighlighter(jp_box.document())  # keep highlighter alive
        en_speaker_label = QLabel("Speaker: —")
        en_speaker_label.setObjectName("muted")
        en_speaker_label.setWordWrap(True)
        en_speaker_label.setStyleSheet(f"font-weight:900; color:{ACCENT_SOFT};")
        vi_speaker_label = QLabel("Speaker: —")
        vi_speaker_label.setObjectName("muted")
        vi_speaker_label.setWordWrap(True)
        vi_speaker_label.setStyleSheet(f"font-weight:900; color:{ACCENT_SOFT};")
        en_character_count_label = QLabel("—")
        en_character_count_label.setObjectName("muted")
        en_character_count_label.setWordWrap(True)
        en_character_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        en_character_count_label.setToolTip("Character count for each English line, from top to bottom. Every character counts except CLT tags.")
        en_character_count_label.setStyleSheet(f"font-weight:800; color:{ACCENT_SOFT};")
        vi_character_count_label = QLabel("—")
        vi_character_count_label.setObjectName("muted")
        vi_character_count_label.setWordWrap(True)
        vi_character_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vi_character_count_label.setToolTip("Character count for each Vietnamese line, from top to bottom. Every character counts except CLT tags.")
        vi_character_count_label.setStyleSheet(f"font-weight:800; color:{TEAL};")

        en_speaker_count_row = QWidget()
        en_speaker_count_layout = QHBoxLayout(en_speaker_count_row)
        en_speaker_count_layout.setContentsMargins(0, 0, 0, 0)
        en_speaker_count_layout.setSpacing(8)
        en_speaker_count_layout.addWidget(en_speaker_label, 1)
        en_speaker_count_layout.addWidget(
            en_character_count_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        vi_speaker_count_row = QWidget()
        vi_speaker_count_layout = QHBoxLayout(vi_speaker_count_row)
        vi_speaker_count_layout.setContentsMargins(0, 0, 0, 0)
        vi_speaker_count_layout.setSpacing(8)
        vi_speaker_count_layout.addWidget(vi_speaker_label, 1)
        vi_speaker_count_layout.addWidget(
            vi_character_count_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        language_split = QSplitter(Qt.Orientation.Vertical)
        language_split.addWidget(labeled_box("English / original — read only", en_box, en_speaker_count_row))
        language_split.addWidget(labeled_box("Vietnamese / translation — editable", vi_box, vi_speaker_count_row))
        language_split.setSizes([145, 145])
        language_split.setStretchFactor(0, 1)
        language_split.setStretchFactor(1, 1)
        detail.addWidget(language_split)
        detail.addWidget(labeled_box("Japanese note — read only / copyable", jp_box))
        detail.setSizes([620, 260])
        detail.setStretchFactor(0, 7)
        detail.setStretchFactor(1, 3)
        split.addWidget(detail)
        split.setSizes([430, 220])

        suggest_group = QGroupBox("Suggestions")
        suggest_layout = QVBoxLayout(suggest_group)
        suggestions_list = QListWidget()
        suggestions_list.setObjectName("suggestionsList")
        suggestions_list.setUniformItemSizes(False)
        suggest_layout.addWidget(suggestions_list, 1)
        suggest_controls = QHBoxLayout()
        suggest_min_score = QSpinBox()
        suggest_min_score.setRange(0, 100)
        suggest_min_score.setSuffix("%")
        suggest_min_score.setValue(max(0, min(100, int(self.config.get("po_viewer_suggest_min_score", 70)))))
        refresh_suggest_btn = self._button("Refresh", secondary=True)
        apply_suggest_btn = self._button("Apply", secondary=True)
        undo_suggest_btn = self._button("Undo", secondary=True)
        undo_suggest_btn.setToolTip("Undo the latest PO text change: typing, table edits, wrapping, suggestions, replacements, Translafixer, or Gemini (Ctrl+Z).")
        undo_suggest_btn.setEnabled(False)
        suggest_controls.addWidget(QLabel("Min match"))
        suggest_controls.addWidget(suggest_min_score)
        suggest_controls.addWidget(refresh_suggest_btn)
        suggest_controls.addWidget(apply_suggest_btn)
        suggest_controls.addWidget(undo_suggest_btn)
        suggest_controls.addStretch()
        suggest_layout.addLayout(suggest_controls)
        suggest_group.setMinimumWidth(240)

        content_split = QSplitter(Qt.Orientation.Horizontal)
        content_split.addWidget(split)
        content_split.addWidget(suggest_group)
        content_split.setSizes([780, 260])
        content_split.setStretchFactor(0, 8)
        content_split.setStretchFactor(1, 2)
        layout.addWidget(content_split, 1)

        state: dict[str, object] = {
            "po": None,
            "path": None,
            "file_paths": [],
            "source_paths": [],
            "source_text": initial_source,
            "dirty": False,
            "loading": False,
            "loading_files": False,
            "detail_loading": False,
            "visual_wrap": True,
            "clt_color_mode": self._initial_clt_color_mode(),
            "suggestion_index": None,
            "suggestion_source_signature": None,
            "suggestion_source_result": None,
            "suggestion_cache": {},
            "live_suggestion_revision": 0,
            "live_suggestion_index_revision": -1,
            "live_suggestion_index_path": None,
            "live_suggestion_index": None,
            "pending_suggestion_row": None,
            "pending_suggestion_force": False,
            "undo_stack": [],
            "undo_batch": None,
            "undoing": False,
            "pending_text_undo": None,
            "po_cache": {},
            "suggestion_building": False,
            "suggestion_build_token": 0,
            "suggestion_build_thread": None,
            "suggestion_build_signals": None,
        }
        suggestion_timer = QTimer(_tab)
        suggestion_timer.setSingleShot(True)
        viewer_config_timer = QTimer(_tab)
        viewer_config_timer.setSingleShot(True)
        viewer_config_timer.timeout.connect(lambda: save_config(self.config))

        def po_file():
            return state["po"]

        def current_path() -> Path | None:
            value = state.get("path")
            return value if isinstance(value, Path) else None

        def current_file_path() -> Path | None:
            data = file_combo.currentData()
            if data:
                return Path(str(data))
            raw = source_edit.text().strip()
            if raw:
                path = Path(raw).expanduser()
                if path.is_file() and path.suffix.lower() == ".po":
                    return path
            return None

        def set_status(text: str) -> None:
            prefix = "* " if state.get("dirty") else ""
            status.setText(prefix + text)

        def characters_per_line_text(text: str) -> str:
            counts = visible_character_counts_by_line(text)
            return "Chars: " + "  |  ".join(str(count) for count in counts)

        def update_character_count_labels(english: str | None = None, vietnamese: str | None = None) -> None:
            en_text = en_box.toPlainText() if english is None else english
            vi_text = vi_box.toPlainText() if vietnamese is None else vietnamese
            en_character_count_label.setText(characters_per_line_text(en_text))
            vi_character_count_label.setText(characters_per_line_text(vi_text))

        def selected_rows() -> list[int]:
            selection = table.selectionModel()
            rows = sorted({idx.row() for idx in selection.selectedRows()}) if selection is not None else []
            return [row for row in rows if 0 <= row < table.rowCount()]

        def make_item(text: str, *, editable: bool = False, bg: str | None = None) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if editable:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            if bg:
                item.setBackground(QColor(bg))
            return item

        def po_viewer_text_height(text: str, column: int) -> int:
            width = max(48, table.columnWidth(column) - 14)
            bounds = table.fontMetrics().boundingRect(
                0,
                0,
                width,
                100000,
                Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs,
                text or " ",
            )
            return max(table.fontMetrics().height(), bounds.height())

        def update_po_viewer_row_height(row: int) -> None:
            if row < 0 or row >= table.rowCount():
                return
            heights = []
            for column in (1, 2, 3):
                item = table.item(row, column)
                heights.append(po_viewer_text_height(item.text() if item is not None else "", column))
            table.setRowHeight(row, max(36, max(heights, default=table.fontMetrics().height()) + 14))

        def update_all_po_viewer_row_heights() -> None:
            table.setUpdatesEnabled(False)
            try:
                for row in range(table.rowCount()):
                    update_po_viewer_row_height(row)
            finally:
                table.setUpdatesEnabled(True)
            table.viewport().update()

        row_height_timer = QTimer(_tab)
        row_height_timer.setSingleShot(True)
        row_height_timer.timeout.connect(update_all_po_viewer_row_heights)
        table.horizontalHeader().sectionResized.connect(lambda *_args: row_height_timer.start(120))

        def set_cell_clt_html(item: QTableWidgetItem | None, text: str, *, color: str = TEXT) -> None:
            if item is None:
                return
            needs_rich = bool(state.get("clt_color_mode")) or "\n" in text or "%TEXT%" in text.upper() or bool(re.search(r"<\s*clt", text, re.IGNORECASE))
            if not needs_rich:
                item.setData(HTML_ROLE, None)
                return
            item.setData(HTML_ROLE, f'<span style="color:{color};">{clt_rich_html(text, color_mode=bool(state.get("clt_color_mode")))}</span>')

        def row_context(entry) -> str:
            return entry.speaker or entry.msgctxt or ""

        def _po_undo_stack() -> list[dict[str, object]]:
            stack = state.get("undo_stack")
            if not isinstance(stack, list):
                stack = []
                state["undo_stack"] = stack
            return stack

        def update_po_undo_controls() -> None:
            native_available = bool(vi_box.document().isUndoAvailable())
            available = bool(_po_undo_stack()) or isinstance(state.get("pending_text_undo"), dict) or native_available
            undo_edit_btn.setEnabled(available)
            undo_suggest_btn.setEnabled(available)

        def trim_po_undo_stack() -> None:
            stack = _po_undo_stack()
            if len(stack) > 500:
                del stack[:-500]

        def push_po_undo_action(label: str, changes: dict[int, dict[str, str]]) -> None:
            po = po_file()
            if po is None:
                return
            action_changes: list[dict[str, object]] = []
            for row, change in sorted(changes.items()):
                if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    continue
                old_text = str(change.get("old", ""))
                new_text = str(change.get("new", ""))
                if old_text == new_text:
                    continue
                entry = po.entries[row]  # type: ignore[union-attr]
                action_changes.append(
                    {
                        "row": row,
                        "uid": entry.uid,
                        "old": old_text,
                        "new": new_text,
                    }
                )
            if not action_changes:
                return
            _po_undo_stack().append({"label": label, "changes": action_changes})
            trim_po_undo_stack()
            update_po_undo_controls()

        def record_po_undo(row: int, old_text: str, new_text: str, label: str = "edit") -> None:
            if old_text == new_text or state.get("undoing"):
                return
            batch = state.get("undo_batch")
            if isinstance(batch, dict):
                changes = batch.setdefault("changes", {})
                if isinstance(changes, dict):
                    existing = changes.get(row)
                    if isinstance(existing, dict):
                        original_old = str(existing.get("old", ""))
                        if original_old == new_text:
                            changes.pop(row, None)
                        else:
                            existing["new"] = new_text
                    else:
                        changes[row] = {"old": old_text, "new": new_text}
                return
            push_po_undo_action(label, {row: {"old": old_text, "new": new_text}})

        def begin_po_undo_batch(label: str) -> None:
            if state.get("undo_batch") is None:
                state["undo_batch"] = {"label": label, "changes": {}}

        def end_po_undo_batch() -> None:
            batch = state.get("undo_batch")
            state["undo_batch"] = None
            if not isinstance(batch, dict):
                return
            changes = batch.get("changes")
            if isinstance(changes, dict):
                push_po_undo_action(str(batch.get("label") or "edit"), changes)

        def clear_pending_text_undo() -> None:
            state["pending_text_undo"] = None
            update_po_undo_controls()

        def commit_pending_text_undo(*, clear_native: bool = True) -> bool:
            pending = state.get("pending_text_undo")
            clear_pending_text_undo()
            if not isinstance(pending, dict):
                return False
            po = po_file()
            if po is None:
                return False
            try:
                row = int(pending.get("row", -1))
            except Exception:
                return False
            if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return False
            entry = po.entries[row]  # type: ignore[union-attr]
            expected_uid = str(pending.get("uid", ""))
            if expected_uid and entry.uid != expected_uid:
                return False
            old_text = str(pending.get("old", ""))
            new_text = entry.msgstr
            if old_text == new_text:
                return False
            push_po_undo_action("text edit", {row: {"old": old_text, "new": new_text}})
            if clear_native and row == table.currentRow():
                self._clear_text_editor_undo(vi_box)
            return True

        def track_pending_text_undo(row: int, old_text: str, new_text: str) -> None:
            if state.get("undoing"):
                return
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            pending = state.get("pending_text_undo")
            if isinstance(pending, dict):
                try:
                    pending_row = int(pending.get("row", -1))
                except Exception:
                    pending_row = -1
                if pending_row != row:
                    commit_pending_text_undo()
                    pending = None
            if not isinstance(pending, dict):
                pending = {
                    "row": row,
                    "uid": po.entries[row].uid,  # type: ignore[union-attr]
                    "old": old_text,
                    "new": new_text,
                }
            else:
                pending["new"] = new_text
            if str(pending.get("old", "")) == new_text:
                clear_pending_text_undo()
            else:
                state["pending_text_undo"] = pending
                update_po_undo_controls()

        def row_for_undo_change(change: dict[str, object]) -> int | None:
            po = po_file()
            if po is None:
                return None
            uid = str(change.get("uid", ""))
            try:
                row = int(change.get("row", -1))
            except Exception:
                row = -1
            if 0 <= row < len(po.entries) and (not uid or po.entries[row].uid == uid):  # type: ignore[union-attr]
                return row
            if uid:
                for idx, entry in enumerate(po.entries):  # type: ignore[union-attr]
                    if entry.uid == uid:
                        return idx
            return None

        def undo_last_po_change() -> None:
            if self._undo_focused_text_editor():
                update_po_undo_controls()
                return
            focus = QApplication.focusWidget()
            if (
                focus is table
                or focus is suggestions_list
                or (focus is not None and (table.isAncestorOf(focus) or suggestions_list.isAncestorOf(focus)))
            ):
                if self._undo_text_editor(vi_box):
                    update_po_undo_controls()
                    return
            commit_pending_text_undo()
            stack = _po_undo_stack()
            if not stack:
                set_status("Nothing to undo.")
                update_po_undo_controls()
                return
            action = stack.pop()
            raw_changes = action.get("changes", [])
            if not isinstance(raw_changes, list):
                set_status("Nothing to undo.")
                update_po_undo_controls()
                return
            restored_rows: list[int] = []
            state["undoing"] = True
            try:
                for change in raw_changes:
                    if not isinstance(change, dict):
                        continue
                    row = row_for_undo_change(change)
                    if row is None:
                        continue
                    old_text = str(change.get("old", ""))
                    if set_entry_translation(row, old_text, record_undo=False):
                        restored_rows.append(row)
            finally:
                state["undoing"] = False
            if not restored_rows:
                set_status("Nothing to undo.")
                update_po_undo_controls()
                return
            try:
                keep_vi_focus = _focus_is_vi_editor()
            except Exception:
                keep_vi_focus = False
            select_entry_row(restored_rows[0], center=True, keep_vi_focus=keep_vi_focus)
            refresh_suggestions_for_row(table.currentRow())
            label = str(action.get("label") or "edit")
            set_status(f"Undid {label}: {len(restored_rows)} PO entr{'ies' if len(restored_rows) != 1 else 'y'}. Save when ready.")
            update_po_undo_controls()

        def _same_file(a: Path | None, b: Path | None) -> bool:
            if a is None or b is None:
                return False
            try:
                return a.resolve(strict=False) == b.resolve(strict=False)
            except OSError:
                return str(a) == str(b)

        def invalidate_live_suggestion_index() -> None:
            state["live_suggestion_revision"] = int(state.get("live_suggestion_revision", 0)) + 1
            state["live_suggestion_index"] = None
            state["live_suggestion_index_revision"] = -1
            state["live_suggestion_index_path"] = None
            cache = state.get("suggestion_cache")
            if isinstance(cache, dict):
                cache.clear()

        def current_live_suggestion_index() -> TranslationSuggestionIndex:
            po = po_file()
            path = current_path()
            revision = int(state.get("live_suggestion_revision", 0))
            cached_revision = int(state.get("live_suggestion_index_revision", -1))
            cached_path = state.get("live_suggestion_index_path")
            cached_index = state.get("live_suggestion_index")
            path_key = self._path_key(path) if path is not None else ""
            if (
                isinstance(cached_index, TranslationSuggestionIndex)
                and cached_revision == revision
                and cached_path == path_key
            ):
                return cached_index
            index = TranslationSuggestionIndex()
            if po is not None:
                index.add_po_file(po, path)
            state["live_suggestion_index"] = index
            state["live_suggestion_index_revision"] = revision
            state["live_suggestion_index_path"] = path_key
            return index

        def _suggestion_source_signature() -> tuple[tuple[str, int, int], ...]:
            signature_items: list[tuple[str, int, int]] = []
            for raw_path in self._all_configured_working_paths():
                try:
                    po_path = Path(str(raw_path)).expanduser()
                    resolved = str(po_path.resolve(strict=False))
                    stat = po_path.stat() if po_path.exists() else None
                    signature_items.append((resolved, int(stat.st_mtime_ns if stat else 0), int(stat.st_size if stat else 0)))
                except Exception:
                    signature_items.append((str(raw_path), 0, 0))
            return tuple(signature_items)

        def rebuild_suggestion_candidates(*, quiet: bool = False) -> None:
            sources = self._all_configured_working_paths()
            signature = _suggestion_source_signature()
            state["suggestion_build_token"] = int(state.get("suggestion_build_token", 0)) + 1
            token = int(state["suggestion_build_token"])
            state["suggestion_building"] = True
            state["suggestion_source_signature"] = signature
            cache = state.get("suggestion_cache")
            if isinstance(cache, dict):
                cache.clear()
            if not sources:
                state["suggestion_index"] = TranslationSuggestionIndex()
                state["suggestion_source_result"] = None
                state["suggestion_building"] = False
                if not quiet:
                    set_status("No Settings Working folders found. Set Working folders in Settings for suggestions.")
                return
            if not quiet:
                set_status("Building suggestion index in background…")

            signals = WorkerSignals()

            def build_worker() -> None:
                try:
                    index, result = TranslationSuggestionIndex.from_translafixer_sources(sources)
                    signals.result.emit((token, signature, index, result, None, quiet))
                except Exception as exc:
                    signals.result.emit((token, signature, None, None, str(exc), quiet))
                finally:
                    signals.done.emit()

            def apply_result(payload: object) -> None:
                if not isinstance(payload, tuple) or len(payload) != 6:
                    return
                result_token, result_signature, index, result, error, was_quiet = payload
                if result_token != state.get("suggestion_build_token"):
                    return
                state["suggestion_building"] = False
                state["suggestion_build_thread"] = None
                state["suggestion_build_signals"] = None
                if error:
                    state["suggestion_index"] = TranslationSuggestionIndex()
                    state["suggestion_source_result"] = None
                    if not was_quiet:
                        QMessageBox.warning(self, "PO Viewer", f"Could not build suggestion index from Settings Working folders:\n{error}")
                    return
                state["suggestion_source_signature"] = result_signature
                state["suggestion_index"] = index
                state["suggestion_source_result"] = result
                cache = state.get("suggestion_cache")
                if isinstance(cache, dict):
                    cache.clear()
                if not was_quiet and result is not None:
                    set_status(f"Suggestion index: {result.usable_translations} translated entries from {result.source_files} Settings Working .po file(s).")
                refresh_suggestions_for_row(table.currentRow(), immediate=True)

            signals.result.connect(apply_result)
            thread = threading.Thread(target=build_worker, daemon=True)
            state["suggestion_build_signals"] = signals
            state["suggestion_build_thread"] = thread
            thread.start()

        def ensure_suggestion_index(*, force: bool = False, quiet: bool = True) -> TranslationSuggestionIndex | None:
            signature = _suggestion_source_signature()
            index = state.get("suggestion_index")
            needs_build = force or index is None or state.get("suggestion_source_signature") != signature
            if needs_build:
                if not state.get("suggestion_building") or force:
                    rebuild_suggestion_candidates(quiet=quiet)
                return None
            return index if isinstance(index, TranslationSuggestionIndex) else None

        def _suggestion_cache_key(source_text: str, min_score: float, uid: str) -> tuple[str, float, str, str]:
            path = current_path()
            path_key = self._path_key(path) if path is not None else ""
            return (suggestion_match_key(source_text), round(min_score, 3), path_key, uid)

        def _render_suggestions(row: int, suggestions) -> None:
            suggestions_list.clear()

            def _one_line(text: str, *, max_len: int = 190) -> str:
                value = re.sub(r"\s+", " ", text or " ").strip()
                return value if len(value) <= max_len else value[: max_len - 1] + "…"

            for number, suggestion in enumerate(suggestions, start=1):
                percent = int(round(suggestion.score * 100))
                translation = _one_line(suggestion.translation)
                percent_color = GOOD if suggestion.score > 0.95 else ACCENT_SOFT
                meta = f"<b style='color:{WHITE};'>{number}.</b> <span style='color:{percent_color}; font-weight:900;'>{percent}%</span>"

                item = QListWidgetItem()
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "translation": suggestion.translation,
                        "source": suggestion.source,
                        "speaker": suggestion.speaker,
                        "file": str(suggestion.file),
                        "row": suggestion.row,
                        "uid": suggestion.uid,
                        "score": suggestion.score,
                        "target_row": row,
                    },
                )
                item.setToolTip(f"{percent}% match")

                widget = QWidget()
                widget.setObjectName("compactSuggestion")
                row_layout = QVBoxLayout(widget)
                row_layout.setContentsMargins(5, 3, 5, 4)
                row_layout.setSpacing(1)
                meta_label = QLabel(meta)
                meta_label.setTextFormat(Qt.TextFormat.RichText)
                meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                vi_label = QLabel(f"<span style='color:{WHITE};'>{clt_rich_html(translation, color_mode=bool(state.get('clt_color_mode')))}</span>")
                vi_label.setTextFormat(Qt.TextFormat.RichText)
                vi_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                vi_label.setWordWrap(True)
                vi_label.setStyleSheet(f"color: {WHITE};")
                row_layout.addWidget(meta_label)
                row_layout.addWidget(vi_label)

                item.setSizeHint(widget.sizeHint())
                suggestions_list.addItem(item)
                suggestions_list.setItemWidget(item, widget)
            if suggestions_list.count():
                suggestions_list.setCurrentRow(0)

        def _refresh_suggestions_now() -> None:
            row = state.get("pending_suggestion_row")
            force_rebuild = bool(state.get("pending_suggestion_force"))
            state["pending_suggestion_force"] = False
            suggestions_list.clear()
            po = po_file()
            if po is None:
                return
            if not isinstance(row, int):
                row = table.currentRow()
            if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            # Ignore stale delayed refreshes after the user has already moved again.
            if row != table.currentRow() and not force_rebuild:
                return
            index = ensure_suggestion_index(force=force_rebuild, quiet=not force_rebuild)
            live_index = current_live_suggestion_index()
            target = po.entries[row]  # type: ignore[union-attr]
            min_score = suggest_min_score.value() / 100.0
            key = _suggestion_cache_key(target.msgid, min_score, target.uid)
            cache = state.get("suggestion_cache")
            if not isinstance(cache, dict):
                cache = {}
                state["suggestion_cache"] = cache
            suggestions = None if force_rebuild else cache.get(key)
            if suggestions is None:
                current = current_path()
                disk_candidates = index.suggest(target.msgid, min_score=min_score, limit=15) if index is not None else []
                # Ignore every disk candidate from the current file; the live
                # in-memory overlay below is authoritative for that file.
                disk_candidates = [item for item in disk_candidates if not _same_file(item.file, current)]
                live_candidates = [
                    item
                    for item in live_index.suggest(target.msgid, min_score=min_score, limit=15)
                    if item.uid != target.uid
                ]
                combined = sorted(live_candidates + disk_candidates, key=lambda item: item.score, reverse=True)
                suggestions = []
                seen_translations: set[str] = set()
                for candidate in combined:
                    translation_key = re.sub(r"\s+", " ", candidate.translation).strip().casefold()
                    if not translation_key or translation_key in seen_translations:
                        continue
                    seen_translations.add(translation_key)
                    suggestions.append(candidate)
                    if len(suggestions) >= 5:
                        break
                if len(cache) > 512:
                    cache.clear()
                cache[key] = suggestions
            _render_suggestions(row, suggestions)

        def refresh_suggestions_for_row(row: int | None = None, *, force_rebuild: bool = False, immediate: bool = False) -> None:
            if row is None:
                row = table.currentRow()
            state["pending_suggestion_row"] = row
            state["pending_suggestion_force"] = bool(force_rebuild)
            if force_rebuild:
                cache = state.get("suggestion_cache")
                if isinstance(cache, dict):
                    cache.clear()
            suggestions_list.clear()
            suggestion_timer.stop()
            suggestion_timer.start(0 if (immediate or force_rebuild) else 100)

        suggestion_timer.timeout.connect(_refresh_suggestions_now)

        def apply_selected_suggestion() -> None:
            row = table.currentRow()
            item = suggestions_list.currentItem()
            if row < 0 or item is None:
                QMessageBox.warning(self, "PO Viewer", "Select a row and a suggestion first.")
                return
            data = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                return
            target_row = data.get("target_row")
            if isinstance(target_row, int) and target_row != row:
                refresh_suggestions_for_row(row, immediate=True)
                return
            translation = str(data.get("translation") or "")
            if not translation.strip():
                return
            if set_entry_translation(row, translation, undo_label="suggestion"):
                set_status(f"Applied suggestion to entry {row + 1}. Use Undo or Ctrl+Z to reverse it.")

        def apply_suggestion_number(number: int) -> None:
            index = number - 1
            if index < 0 or index >= suggestions_list.count():
                return
            suggestions_list.setCurrentRow(index)
            apply_selected_suggestion()

        def refresh_row_style(row: int) -> None:
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            entry = po.entries[row]  # type: ignore[union-attr]
            for col, color in [(0, PANEL_3), (1, PANEL_2), (2, EN_BG)]:
                item = table.item(row, col)
                if item is not None:
                    item.setBackground(QColor(color))
            en_item = table.item(row, 2)
            set_cell_clt_html(en_item, entry.msgid, color=TEXT)
            vi_item = table.item(row, 3)
            if vi_item is not None:
                vi_color = TEXT if entry.msgstr.strip() else WARN
                vi_item.setBackground(QColor(VI_BG if entry.msgstr.strip() else "#4a3828"))
                vi_item.setForeground(QBrush(QColor(vi_color)))
                set_cell_clt_html(vi_item, entry.msgstr, color=vi_color)

        def load_detail(row: int) -> None:
            po = po_file()
            state["detail_loading"] = True
            try:
                if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    en_box.clear()
                    vi_box.clear()
                    jp_box.clear()
                    en_speaker_label.setText("Speaker: —")
                    en_speaker_label.setToolTip("")
                    vi_speaker_label.setText("Speaker: —")
                    vi_speaker_label.setToolTip("")
                    en_character_count_label.setText("—")
                    vi_character_count_label.setText("—")
                    return
                entry = po.entries[row]  # type: ignore[union-attr]
                speaker = entry.speaker.strip() or "—"
                speaker_text = f"Speaker: {speaker}"
                speaker_tooltip = entry.msgctxt or ""
                en_speaker_label.setText(speaker_text)
                en_speaker_label.setToolTip(speaker_tooltip)
                vi_speaker_label.setText(speaker_text)
                vi_speaker_label.setToolTip(speaker_tooltip)
                if en_box.toPlainText() != entry.msgid:
                    en_box.setPlainText(entry.msgid)
                if vi_box.toPlainText() != entry.msgstr:
                    vi_box.setPlainText(entry.msgstr)
                    self._clear_text_editor_undo(vi_box)
                if jp_box.toPlainText() != entry.japanese_context:
                    jp_box.setPlainText(entry.japanese_context)
                update_character_count_labels(entry.msgid, entry.msgstr)
                set_status(f"Entry {row + 1}/{len(po.entries)} | line {entry.line}")  # type: ignore[union-attr]
                refresh_suggestions_for_row(row)
            finally:
                state["detail_loading"] = False

        def set_entry_translation(row: int, text: str, *, dirty: bool = True, record_undo: bool = True, undo_label: str = "edit") -> bool:
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return False
            entry = po.entries[row]  # type: ignore[union-attr]
            if entry.msgstr == text:
                return False
            if record_undo:
                commit_pending_text_undo()
            old_text = entry.msgstr
            if record_undo:
                record_po_undo(row, old_text, text, undo_label)
            invalidate_po_cache()
            entry.msgstr = text
            invalidate_live_suggestion_index()
            state["loading"] = True
            try:
                item = table.item(row, 3)
                if item is not None:
                    item.setText(text)
            finally:
                state["loading"] = False
            if row == table.currentRow():
                state["detail_loading"] = True
                try:
                    if vi_box.toPlainText() != text:
                        vi_box.setPlainText(text)
                        self._clear_text_editor_undo(vi_box)
                finally:
                    state["detail_loading"] = False
                update_character_count_labels(entry.msgid, text)
            refresh_row_style(row)
            update_po_viewer_row_height(row)
            if dirty:
                state["dirty"] = True
                set_status(f"Edited entry {row + 1}. Save when ready.")
            return True

        def populate_table() -> None:
            po = po_file()
            total_entries = len(po.entries) if po is not None else 0  # type: ignore[union-attr]
            self._begin_task_progress("Loading PO entries", total_entries)
            self._pump_task_progress()
            state["loading"] = True
            table.setUpdatesEnabled(False)
            table.blockSignals(True)
            try:
                table.clearContents()
                if po is None:
                    table.setRowCount(0)
                    return
                table.setRowCount(total_entries)
                for row, entry in enumerate(po.entries):  # type: ignore[union-attr]
                    number = make_item(str(row + 1), bg=PANEL_3)
                    number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, 0, number)
                    table.setItem(row, 1, make_item(row_context(entry), bg=PANEL_2))
                    table.setItem(row, 2, make_item(entry.msgid, bg=EN_BG))
                    table.setItem(row, 3, make_item(entry.msgstr, editable=True, bg=VI_BG if entry.msgstr.strip() else "#4a3828"))
                    refresh_row_style(row)
                    update_po_viewer_row_height(row)
                    if (row + 1) % 50 == 0 or row + 1 == total_entries:
                        label = f"Loading {current_path().name}" if current_path() is not None else "Loading PO entries"
                        self._update_task_progress(row + 1, total_entries, label)
                        self._pump_task_progress()
                if table.rowCount():
                    table.setCurrentCell(0, 0)
                    table.selectRow(0)
            finally:
                table.blockSignals(False)
                table.setUpdatesEnabled(True)
                state["loading"] = False
                self._finish_task_progress(f"Loaded {total_entries} PO entries")
            table.viewport().update()
            row_height_timer.start(0)
            if table.rowCount():
                load_detail(0)

        def discover_po_files(paths: list[str | Path]) -> list[Path]:
            found: list[Path] = []
            seen: set[str] = set()
            for raw in paths:
                text = str(raw).strip()
                if not text:
                    continue
                base = Path(text).expanduser()
                for po_path in iter_po_files(base, include_copy=False):
                    try:
                        key = str(po_path.resolve())
                    except OSError:
                        key = str(po_path)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(po_path)
            return sorted(found, key=lambda item: str(item).lower())

        def file_label(path: Path) -> str:
            sources = state.get("source_paths", [])
            if isinstance(sources, list):
                dirs = [item for item in sources if isinstance(item, Path) and item.is_dir()]
                if len(dirs) == 1:
                    try:
                        return str(path.relative_to(dirs[0]))
                    except ValueError:
                        pass
            paths = state.get("file_paths", [])
            if isinstance(paths, list) and sum(1 for item in paths if isinstance(item, Path) and item.name == path.name) > 1:
                return str(path)
            return path.name

        def select_combo_path(path: Path) -> None:
            target = str(path)
            file_combo.blockSignals(True)
            try:
                for idx in range(file_combo.count()):
                    if file_combo.itemData(idx) == target:
                        file_combo.setCurrentIndex(idx)
                        break
            finally:
                file_combo.blockSignals(False)

        def set_file_list(
            paths: list[str | Path],
            source_text: str,
            *,
            auto_load: bool = True,
            quiet: bool = False,
            update_source_edit: bool = True,
        ) -> None:
            source_paths = [Path(str(item)).expanduser() for item in paths if str(item).strip()]
            self._begin_task_progress("Discovering PO files")
            self._pump_task_progress()
            files = discover_po_files(source_paths)
            self._begin_task_progress("Building PO file list", len(files))
            state["loading_files"] = True
            file_combo.blockSignals(True)
            try:
                file_combo.clear()
                if not files:
                    file_combo.setEnabled(False)
                    file_combo.addItem("No .po files found", None)
                else:
                    file_combo.setEnabled(True)
                    state["file_paths"] = files
                    state["source_paths"] = source_paths
                    for file_index, po_path in enumerate(files, start=1):
                        file_combo.addItem(file_label(po_path), str(po_path))
                        if file_index % 50 == 0 or file_index == len(files):
                            self._update_task_progress(file_index, len(files), "Building PO file list")
                            self._pump_task_progress()
                    preferred = str(current_path() or self.config.get("po_viewer_file", ""))
                    if preferred:
                        for idx, po_path in enumerate(files):
                            if str(po_path) == preferred:
                                file_combo.setCurrentIndex(idx)
                                break
            finally:
                file_combo.blockSignals(False)
                state["loading_files"] = False
                self._finish_task_progress(f"Found {len(files)} PO file(s)")

            if not files:
                state["file_paths"] = []
                state["source_paths"] = source_paths
                if not quiet:
                    set_status("No usable .po files found. Copy files are skipped.")
                return

            state["source_text"] = source_text
            if update_source_edit:
                source_edit.setText(source_text)
                self.config["po_viewer_source"] = source_text
            else:
                self.config["po_viewer_source"] = source_edit.text().strip()
            self.config["po_viewer_files"] = [str(item) for item in files]
            save_config(self.config)
            if not quiet:
                set_status(f"Found {len(files)} .po file{'s' if len(files) != 1 else ''}. Copy files skipped.")
            if auto_load:
                load_file()

        def load_source_from_text() -> None:
            paths = self._processing_paths("po_viewer", extra_edit=source_edit, include_extra=source_extra_check, require_any=False)
            if not paths:
                QMessageBox.warning(self, "PO Viewer", "No input paths. Select file groups with Working folders in Settings, or enable Extra source.")
                return
            source_text = "; ".join(str(path) for path in paths)
            set_file_list(paths, source_text, auto_load=True, update_source_edit=False)

        def invalidate_po_cache(path: Path | None = None) -> None:
            target = path or current_path()
            cache = state.get("po_cache")
            if target is not None and isinstance(cache, dict):
                cache.pop(self._path_key(target), None)

        def load_po_cached(path: Path):
            cache = state.get("po_cache")
            if not isinstance(cache, dict):
                cache = {}
                state["po_cache"] = cache
            stat = path.stat()
            key = self._path_key(path)
            cached = cache.get(key)
            if isinstance(cached, dict) and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
                cached_po = cached.get("po")
                if cached_po is not None:
                    return cached_po, True
            loaded = load_po_clone(path)
            if len(cache) >= 12:
                cache.pop(next(iter(cache)), None)
            cache[key] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "po": loaded}
            return loaded, False

        def cache_current_po() -> None:
            po = po_file()
            path = current_path()
            cache = state.get("po_cache")
            if po is None or path is None or not isinstance(cache, dict):
                return
            try:
                stat = path.stat()
            except OSError:
                return
            cache[self._path_key(path)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "po": po}

        def load_file(path: Path | None = None) -> None:
            if path is None:
                path = current_file_path()
            if path is None:
                paths = self._processing_paths("po_viewer", extra_edit=source_edit, include_extra=source_extra_check, require_any=False)
                if paths:
                    set_file_list(paths, "; ".join(str(path) for path in paths), auto_load=True, update_source_edit=False)
                    return
                QMessageBox.warning(self, "PO Viewer", "No input paths. Select file groups with Working folders in Settings, or enable Extra source.")
                return
            path = Path(path).expanduser()
            if not path.is_file() or path.suffix.lower() != ".po":
                QMessageBox.warning(self, "PO Viewer", "Choose a real .po file.")
                return
            try:
                po, from_cache = load_po_cached(path)
            except Exception as exc:
                QMessageBox.critical(self, "PO Viewer", f"Could not load file:\n{exc}")
                return
            state["po"] = po
            state["path"] = path
            state["dirty"] = False
            invalidate_live_suggestion_index()
            clear_pending_text_undo()
            _po_undo_stack().clear()
            self._clear_text_editor_undo(vi_box)
            update_po_undo_controls()
            select_combo_path(path)
            self.config["po_viewer_file"] = str(path)
            if not self.config.get("po_viewer_source"):
                self.config["po_viewer_source"] = str(path)
            viewer_config_timer.start(350)
            populate_table()
            issue_count = len(po.issues)
            extra = f" | issues={issue_count}" if issue_count else ""
            cache_note = " | cached" if from_cache else ""
            set_status(f"Loaded {len(po.entries)} entries from {path.name}{extra}{cache_note}")

        def save_file() -> None:
            po = po_file()
            path = current_path()
            if po is None or path is None:
                QMessageBox.warning(self, "PO Viewer", "No file loaded.")
                return
            self._begin_task_progress(f"Saving {path.name}", 1)
            self._pump_task_progress()
            commit_pending_text_undo()
            try:
                save_po(po, path)
            except Exception as exc:
                self._finish_task_progress(f"Save failed: {path.name}")
                QMessageBox.critical(self, "PO Viewer", f"Could not save file:\n{exc}")
                return
            self._update_task_progress(1, 1, f"Saving {path.name}")
            state["dirty"] = False
            cache_current_po()
            state["suggestion_index"] = None
            state["suggestion_source_signature"] = None
            cache = state.get("suggestion_cache")
            if isinstance(cache, dict):
                cache.clear()
            refresh_suggestions_for_row(table.currentRow(), force_rebuild=True, immediate=True)
            set_status(f"Saved {path.name}")
            self._finish_task_progress(f"Saved {path.name}")

        def select_entry_row(row: int, *, center: bool = False, keep_vi_focus: bool = False) -> None:
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            selection = table.selectionModel()
            table.setUpdatesEnabled(False)
            try:
                if selection is not None:
                    selection.clearSelection()
                table.setCurrentCell(row, 0)
                table.selectRow(row)
                item = table.item(row, 0)
                if item is not None:
                    table.scrollToItem(
                        item,
                        QAbstractItemView.ScrollHint.PositionAtCenter if center else QAbstractItemView.ScrollHint.EnsureVisible,
                    )
            finally:
                table.setUpdatesEnabled(True)
            table.viewport().update()
            if row == table.currentRow():
                load_detail(row)
            if keep_vi_focus:
                vi_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
                table.viewport().update()

        def select_entry_uid(uid: str | None = None, *, context: str | None = None, line: int | None = None, center: bool = True, keep_vi_focus: bool = False) -> bool:
            po = po_file()
            if po is None:
                return False
            target = -1
            if uid:
                for row, entry in enumerate(po.entries):  # type: ignore[union-attr]
                    if entry.uid == uid:
                        target = row
                        break
            if target < 0 and context:
                context_rows = [
                    (row, entry) for row, entry in enumerate(po.entries)  # type: ignore[union-attr]
                    if (entry.msgctxt or "") == context
                ]
                if context_rows:
                    if line is not None:
                        target = min(context_rows, key=lambda item: abs(int(item[1].line or 0) - line))[0]
                    else:
                        target = context_rows[0][0]
            if target < 0 and line is not None:
                for row, entry in enumerate(po.entries):  # type: ignore[union-attr]
                    if int(entry.line or 0) >= line:
                        target = row
                        break
            if target < 0 and table.rowCount():
                target = 0
            if target < 0:
                return False
            select_entry_row(target, center=center, keep_vi_focus=keep_vi_focus)
            return True

        def open_search_replace_dialog() -> None:
            existing = state.get("search_replace_dialog")
            if isinstance(existing, QDialog):
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("PO Viewer Search / Replace")
            dialog.setModal(False)
            dialog.resize(520, 210)
            root = QVBoxLayout(dialog)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            form = QFormLayout()
            find_edit = QLineEdit(str(state.get("search_replace_find", "")))
            find_edit.setPlaceholderText("Find text; another find. Spaces and \\n also match real line breaks. Use \\; for literal ;.")
            replace_edit = QLineEdit(str(state.get("search_replace_replace", "")))
            replace_edit.setPlaceholderText("Replacement; another replacement. Use \\n for a line break and \\; for literal ;.")
            scope_combo = QComboBox()
            scope_combo.addItem("Vietnamese msgstr", "vi")
            scope_combo.addItem("English msgid", "en")
            scope_combo.addItem("Both", "both")
            form.addRow("Find", find_edit)
            form.addRow("Replace", replace_edit)
            form.addRow("Search in", scope_combo)
            root.addLayout(form)

            option_row = QHBoxLayout()
            case_chk = QCheckBox("Case sensitive")
            whole_chk = QCheckBox("Whole word")
            regex_chk = QCheckBox("Regex")
            for widget in (case_chk, whole_chk, regex_chk):
                option_row.addWidget(widget)
            option_row.addStretch()
            root.addLayout(option_row)

            status_label = QLabel("Use ; for ordered find→replace pairs. Replace edits Vietnamese only.")
            status_label.setObjectName("muted")
            status_label.setWordWrap(True)
            root.addWidget(status_label)

            button_row = QHBoxLayout()
            prev_btn = self._button("Find Prev", secondary=True)
            next_btn = self._button("Find Next", secondary=True)
            replace_btn = self._button("Replace", secondary=True)
            replace_all_btn = self._button("Replace All")
            close_btn = self._button("Close", secondary=True)
            for widget in (prev_btn, next_btn, replace_btn, replace_all_btn):
                button_row.addWidget(widget)
            button_row.addStretch()
            button_row.addWidget(close_btn)
            root.addLayout(button_row)

            def compile_patterns() -> list[tuple[re.Pattern[str], object]] | None:
                needle_text = find_edit.text()
                state["search_replace_find"] = needle_text
                state["search_replace_replace"] = replace_edit.text()
                try:
                    compiled = compile_search_replace_sequence(
                        needle_text,
                        replace_edit.text(),
                        case_sensitive=case_chk.isChecked(),
                        whole_word=whole_chk.isChecked(),
                        regex=regex_chk.isChecked(),
                    )
                except SearchReplaceCompileError as exc:
                    status_label.setText(f"Invalid regex in item {exc.index}: {exc.error}")
                    return None
                if not compiled:
                    status_label.setText("Enter text to search.")
                    return None
                return compiled

            def fields_for_entry(entry) -> list[tuple[str, str]]:
                scope = str(scope_combo.currentData())
                fields: list[tuple[str, str]] = []
                if scope in {"vi", "both"}:
                    fields.append(("vi", entry.msgstr))
                if scope in {"en", "both"}:
                    fields.append(("en", entry.msgid))
                return fields

            def find_in_row(row: int, compiled: list[tuple[re.Pattern[str], object]]) -> tuple[str, re.Match[str]] | None:
                po = po_file()
                if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    return None
                entry = po.entries[row]  # type: ignore[union-attr]
                for field, text_value in fields_for_entry(entry):
                    for pattern, _replacement in compiled:
                        match = pattern.search(text_value)
                        if match:
                            return field, match
                return None

            def highlight_match(field: str, match: re.Match[str]) -> None:
                box = vi_box if field == "vi" else en_box
                cursor = box.textCursor()
                cursor.setPosition(match.start())
                cursor.setPosition(match.end(), QTextCursor.MoveMode.KeepAnchor)
                box.setTextCursor(cursor)
                box.setFocus(Qt.FocusReason.ShortcutFocusReason)

            def find_match(direction: int = 1) -> bool:
                po = po_file()
                compiled = compile_patterns()
                if po is None:
                    status_label.setText("Load a .po file first.")
                    return False
                if compiled is None:
                    return False
                total = len(po.entries)  # type: ignore[union-attr]
                if total <= 0:
                    status_label.setText("No entries loaded.")
                    return False
                current = table.currentRow()
                if current < 0:
                    current = 0
                signature = (find_edit.text(), replace_edit.text(), scope_combo.currentData(), case_chk.isChecked(), whole_chk.isChecked(), regex_chk.isChecked())
                previous_signature = state.get("search_replace_signature")
                previous_row = state.get("search_replace_last_row")
                start = current
                if previous_signature == signature and isinstance(previous_row, int) and previous_row == current:
                    start = current + direction
                for step in range(total):
                    row = (start + (step * direction)) % total
                    found = find_in_row(row, compiled)
                    if not found:
                        continue
                    field, match = found
                    select_entry_row(row, center=True)
                    highlight_match(field, match)
                    state["search_replace_signature"] = signature
                    state["search_replace_last_row"] = row
                    status_label.setText(f"Found in {'Vietnamese' if field == 'vi' else 'English'} at entry {row + 1}/{total}.")
                    return True
                status_label.setText("No match found.")
                return False

            def replace_current() -> None:
                po = po_file()
                compiled = compile_patterns()
                row = table.currentRow()
                if po is None or compiled is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    status_label.setText("Find a loaded entry first.")
                    return
                entry = po.entries[row]  # type: ignore[union-attr]
                new_text, count = apply_search_replace_sequence(entry.msgstr, compiled, count_per_pattern=1)
                if count <= 0:
                    status_label.setText("Current row has no Vietnamese match to replace.")
                    return
                set_entry_translation(row, new_text, undo_label="replace")
                status_label.setText(f"Replaced {count} match{'es' if count != 1 else ''} in entry {row + 1}.")
                find_match(1)

            def replace_all() -> None:
                po = po_file()
                compiled = compile_patterns()
                if po is None:
                    status_label.setText("Load a .po file first.")
                    return
                if compiled is None:
                    return
                total_entries = len(po.entries)  # type: ignore[union-attr]
                changed_rows = 0
                total_matches = 0
                self._begin_task_progress("Replacing PO entries", total_entries)
                self._pump_task_progress()
                begin_po_undo_batch("replace all")
                try:
                    for row, entry in enumerate(po.entries):  # type: ignore[union-attr]
                        new_text, count = apply_search_replace_sequence(entry.msgstr, compiled)
                        if count > 0:
                            total_matches += count
                            if set_entry_translation(row, new_text, undo_label="replace all"):
                                changed_rows += 1
                        if (row + 1) % 25 == 0 or row + 1 == total_entries:
                            self._update_task_progress(row + 1, total_entries, "Replacing PO entries")
                            self._pump_task_progress()
                finally:
                    end_po_undo_batch()
                    self._finish_task_progress(f"Replaced {total_matches} PO matches")
                refresh_suggestions_for_row(table.currentRow())
                status_label.setText(f"Replaced {total_matches} match{'es' if total_matches != 1 else ''} in {changed_rows} row{'s' if changed_rows != 1 else ''}.")

            prev_btn.clicked.connect(lambda: find_match(-1))
            next_btn.clicked.connect(lambda: find_match(1))
            replace_btn.clicked.connect(replace_current)
            replace_all_btn.clicked.connect(replace_all)
            close_btn.clicked.connect(dialog.close)
            find_edit.returnPressed.connect(lambda: find_match(1))
            dialog_undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), dialog)
            dialog_undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            dialog_undo_shortcut.activated.connect(undo_last_po_change)
            dialog.finished.connect(lambda _result: state.pop("search_replace_dialog", None))
            state["search_replace_dialog"] = dialog
            state["search_replace_dialog_undo_shortcut"] = dialog_undo_shortcut
            dialog.show()
            find_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            find_edit.selectAll()

        def open_file_in_po_viewer(path: str | Path, *, uid: str | None = None, context: str | None = None, line: int | None = None, source_paths: list[str] | None = None) -> bool:
            target = Path(str(path)).expanduser()
            if not target.is_file() or target.suffix.lower() != ".po":
                QMessageBox.warning(self, "PO Viewer", f"Could not open in PO Viewer:\n{target}")
                return False
            roots = [item for item in (source_paths or []) if str(item).strip()]
            files = state.get("file_paths")
            in_current_list = False
            if isinstance(files, list):
                in_current_list = any(_same_file(item if isinstance(item, Path) else Path(str(item)), target) for item in files)
            if roots:
                root_paths = [Path(str(item)).expanduser() for item in roots]
                current_sources = state.get("source_paths")
                current_signature = tuple(sorted(self._path_key(item if isinstance(item, Path) else Path(str(item))) for item in current_sources)) if isinstance(current_sources, list) else ()
                requested_signature = tuple(sorted(self._path_key(item) for item in root_paths))
                if requested_signature != current_signature or not in_current_list:
                    set_file_list(root_paths, "; ".join(str(item) for item in root_paths), auto_load=False, quiet=True)
            elif not in_current_list:
                set_file_list([target], str(target), auto_load=False, quiet=True)
            load_file(target)
            select_entry_uid(uid, context=context, line=line, center=True)
            tab_widget = getattr(self, "_po_viewer_tab_widget", None)
            if tab_widget is not None:
                self.tabs.setCurrentWidget(tab_widget)
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(0, focus_vi_editor)
            return True

        self._open_file_in_po_viewer = open_file_in_po_viewer

        def open_current_po_external() -> None:
            path = current_path() or current_file_path()
            if path is None:
                QMessageBox.warning(self, "PO Viewer", "Load or select a .po file first.")
                return
            path = Path(path).expanduser()
            if not path.is_file() or path.suffix.lower() != ".po":
                QMessageBox.warning(self, "PO Viewer", "Current selection is not a real .po file.")
                return
            if state.get("dirty"):
                reply = QMessageBox.question(
                    self,
                    "PO Viewer",
                    "This file has unsaved edits. Open the saved file on disk anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, "PO Viewer", f"Could not open file in default app:\n{path}")
                return
            set_status(f"Opened {path.name} in default app.")

        def browse_files() -> None:
            current = current_path()
            if current is not None:
                start = str(current.parent)
            else:
                source = Path(source_edit.text().strip()).expanduser() if source_edit.text().strip() else Path.cwd()
                start = str(source if source.is_dir() else source.parent)
            paths, _ = QFileDialog.getOpenFileNames(self, "Open .po file(s)", start, "PO files (*.po);;All files (*.*)")
            if paths:
                source_edit.setText("; ".join(paths))
                source_extra_check.setChecked(True)
                self.config["po_viewer_source"] = source_edit.text().strip()
                self.config[self._include_extra_config_key("po_viewer")] = True
                save_config(self.config)
                load_source_from_text()

        def browse_folder() -> None:
            current = current_path()
            if current is not None:
                start = str(current.parent)
            else:
                source = Path(source_edit.text().strip()).expanduser() if source_edit.text().strip() else Path.cwd()
                start = str(source if source.is_dir() else source.parent)
            folder = QFileDialog.getExistingDirectory(self, "Open folder with .po files", start)
            if folder:
                source_edit.setText(folder)
                source_extra_check.setChecked(True)
                self.config["po_viewer_source"] = source_edit.text().strip()
                self.config[self._include_extra_config_key("po_viewer")] = True
                save_config(self.config)
                load_source_from_text()

        def table_item_changed(item: QTableWidgetItem) -> None:
            if state.get("loading") or item.column() != 3:
                return
            set_entry_translation(item.row(), item.text(), undo_label="table edit")

        def vi_text_changed() -> None:
            if state.get("detail_loading"):
                return
            row = table.currentRow()
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            new_text = vi_box.toPlainText()
            old_text = po.entries[row].msgstr  # type: ignore[union-attr]
            update_character_count_labels()
            track_pending_text_undo(row, old_text, new_text)
            set_entry_translation(row, new_text, record_undo=False)

        def wrap_rows(rows: list[int], preset_index: int | None = None) -> None:
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            valid_rows = [row for row in rows if 0 <= row < len(po.entries)]  # type: ignore[union-attr]
            soft_value, hard_value, cuts_value = self._linewrap_settings(preset_index)
            changed = 0
            self._begin_task_progress("Wrapping PO entries", len(valid_rows))
            self._pump_task_progress()
            begin_po_undo_batch("wrap")
            try:
                for position, row in enumerate(valid_rows, start=1):
                    entry = po.entries[row]  # type: ignore[union-attr]
                    fixed, did_change = wrap_msgstr(entry.msgstr, soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                    if did_change and set_entry_translation(row, fixed, undo_label="wrap"):
                        changed += 1
                    if position % 25 == 0 or position == len(valid_rows):
                        self._update_task_progress(position, len(valid_rows), "Wrapping PO entries")
                        self._pump_task_progress()
            finally:
                end_po_undo_batch()
                self._finish_task_progress(f"Wrapped {changed} PO entr{'y' if changed == 1 else 'ies'}")
            set_status(
                f"W{self._active_linewrap_preset_index() + 1}: wrapped {changed} translation entr{'y' if changed == 1 else 'ies'} "
                f"using Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}."
            )

        def wrap_selected(preset_index: int | None = None) -> None:
            rows = selected_rows()
            if not rows and table.currentRow() >= 0:
                rows = [table.currentRow()]
            if not rows:
                QMessageBox.warning(self, "PO Viewer", "Select entries to wrap.")
                return
            wrap_rows(rows, preset_index)

        def wrap_all() -> None:
            wrap_rows(list(range(table.rowCount())), self._active_linewrap_preset_index())

        def preset_replace_selected() -> None:
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            rows = selected_rows()
            if not rows and table.currentRow() >= 0:
                rows = [table.currentRow()]
            if not rows:
                QMessageBox.warning(self, "PO Viewer", "Select one or more entries first.")
                return
            try:
                rules = self._enabled_preset_rules()
            except Exception as exc:
                QMessageBox.warning(self, "Preset Replace", str(exc))
                return
            if not rules:
                set_status("No enabled preset rules.")
                return
            changed = 0
            total_hits = 0
            self._begin_task_progress("Applying preset rules", len(rows))
            self._pump_task_progress()
            begin_po_undo_batch("preset replace")
            try:
                for position, row in enumerate(rows, start=1):
                    if 0 <= row < len(po.entries):  # type: ignore[union-attr]
                        entry = po.entries[row]  # type: ignore[union-attr]
                        new_text, hits = apply_rules_to_entry(entry, rules)
                        if hits:
                            total_hits += sum(count for _rule, count, _before, _after in hits)
                        if hits and set_entry_translation(row, new_text, undo_label="preset replace"):
                            changed += 1
                    self._update_task_progress(position, len(rows), "Applying preset rules")
                    self._pump_task_progress()
            finally:
                end_po_undo_batch()
                self._finish_task_progress(f"Preset replaced {changed} PO entries")
            refresh_suggestions_for_row(table.currentRow())
            set_status(f"Preset rules replaced {total_hits} hit(s) in {changed} selected entr{'y' if changed == 1 else 'ies'}. Save when ready.")

        def toggle_visual_wrap() -> None:
            state["visual_wrap"] = not bool(state.get("visual_wrap"))
            enabled = bool(state["visual_wrap"])
            table.setWordWrap(enabled)
            mode = QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap
            en_box.setLineWrapMode(mode)
            vi_box.setLineWrapMode(mode)
            wrap_view_btn.setText(f"↔ {'ON' if enabled else 'OFF'}")
            wrap_view_btn.setToolTip(f"Visual wrap: {'ON' if enabled else 'OFF'}")
            row_height_timer.start(0)
            set_status("Visual line wrap enabled." if enabled else "Visual line wrap disabled.")

        def set_clt_color_mode(enabled: bool, *, persist: bool = True, quiet: bool = False) -> None:
            state["clt_color_mode"] = enabled
            clt_color_btn.setText(f"CLT {'C' if enabled else 'T'}")
            clt_color_btn.setToolTip(f"CLT view: {'Color' if enabled else 'Tags'}")
            en_box._clt_highlighter.set_color_spans(enabled)
            vi_box._clt_highlighter.set_color_spans(enabled)
            for row in range(table.rowCount()):
                refresh_row_style(row)
            row_height_timer.start(0)
            refresh_suggestions_for_row(table.currentRow())
            if persist:
                self._save_clt_color_mode(enabled)
            if not quiet:
                set_status("CLT color view enabled. Tags are hidden in table/suggestions; text uses in-game colors." if enabled else "CLT tag view enabled. Raw CLT tags are visible in the PO table.")

        def toggle_clt_color_mode() -> None:
            set_clt_color_mode(not bool(state.get("clt_color_mode")))

        def focus_vi_editor() -> None:
            if table.rowCount():
                row = table.currentRow() if table.currentRow() >= 0 else 0
                select_entry_row(row)
            vi_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
            cursor = vi_box.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            vi_box.setTextCursor(cursor)
            table.viewport().update()

        def fill_from_translafixer_sources() -> None:
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            sources = self._translafixer_source_paths()
            if not sources:
                QMessageBox.warning(self, "PO Viewer", "Add source files/folders in the Translafixer tab first.")
                return
            self._begin_task_progress("Reading Translafixer sources")
            self._pump_task_progress()
            try:
                translations, result = build_translation_map(sources)
            except Exception as exc:
                self._finish_task_progress("Translafixer source read failed")
                QMessageBox.critical(self, "PO Viewer", f"Could not read Translafixer sources:\n{exc}")
                return
            rows = selected_rows()
            mode = "selected"
            if not rows:
                rows = [i for i, entry in enumerate(po.entries) if not entry.msgstr.strip()]  # type: ignore[union-attr]
                mode = "empty"
            if not rows:
                self._finish_task_progress("No PO entries to fill")
                set_status("No selected rows and no empty translations to fill.")
                return
            matched = 0
            changed = 0
            unchanged = 0
            self._begin_task_progress("Filling PO entries", len(rows))
            begin_po_undo_batch("translafix")
            try:
                for position, row in enumerate(rows, start=1):
                    if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                        continue
                    entry = po.entries[row]  # type: ignore[union-attr]
                    replacement = translations.get(msgid_match_key(entry.msgid))
                    if replacement is not None:
                        matched += 1
                        if entry.msgstr == replacement:
                            unchanged += 1
                        elif set_entry_translation(row, replacement, undo_label="translafix"):
                            changed += 1
                    if position % 25 == 0 or position == len(rows):
                        self._update_task_progress(position, len(rows), "Filling PO entries")
                        self._pump_task_progress()
            finally:
                end_po_undo_batch()
                self._finish_task_progress(f"Filled {changed} PO entr{'y' if changed == 1 else 'ies'}")
            conflict_note = f" | conflicts skipped={result.ambiguous_msgids}" if result.ambiguous_msgids else ""
            set_status(
                f"Translafix {mode}: source files={result.source_files}, usable={result.usable_translations}, "
                f"matched={matched}, changed={changed}, unchanged={unchanged}{conflict_note}"
            )

        def previous_po_viewer_file_context(limit: int) -> list[POEntry]:
            if limit <= 0 or not self._gemini_api_cross_file_context_enabled():
                return []
            files = state.get("file_paths")
            active_path = current_path()
            if not isinstance(files, list) or active_path is None:
                return []
            ordered_paths = [item for item in files if isinstance(item, Path)]
            active_index = next((index for index, item in enumerate(ordered_paths) if _same_file(item, active_path)), None)
            if active_index is None or active_index <= 0:
                return []
            chunks: list[list[POEntry]] = []
            remaining = limit
            po_cache = state.get("po_cache")
            for previous_path in reversed(ordered_paths[:active_index]):
                if remaining <= 0:
                    break
                cached_po = po_cache.get(previous_path) if isinstance(po_cache, dict) else None
                try:
                    previous_po = cached_po if cached_po is not None else get_cached_po(previous_path)
                except Exception:
                    continue
                entries = list(getattr(previous_po, "entries", []))
                if not entries:
                    continue
                take = entries[-remaining:]
                chunks.insert(0, take)
                remaining -= len(take)
            return [entry for chunk in chunks for entry in chunk]

        def translate_selected_with_gemini_api() -> None:
            if self._active_thread is not None and self._active_thread.is_alive():
                QMessageBox.warning(self, "Busy", "Another action is already running. Stop it first.")
                return
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            rows = selected_rows()
            if not rows and table.currentRow() >= 0:
                rows = [table.currentRow()]
            if not rows:
                QMessageBox.warning(self, "PO Viewer", "Select one or more entries first.")
                return
            api_key = str(self.config.get("gemini_api_key", "")).strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                QMessageBox.warning(self, "PO Viewer", "Enter the Gemini API key in the AI Translation tab, or set GEMINI_API_KEY.")
                return

            prompt = SYSTEM_INSTRUCTIONS
            model = self._gemini_api_profile_model("single")
            batch_size = 1
            sleep_seconds = self._gemini_api_profile_sleep_seconds("single")
            timeout_seconds = self._gemini_api_timeout_seconds("single")
            thinking_mode = self._gemini_api_profile_thinking_mode("single")
            max_output_tokens = self._gemini_api_profile_max_output_tokens("single")
            context_limit = self._gemini_api_context_limit("single")
            source_po = po
            source_path = current_path()
            original_entries = [po.entries[row] for row in rows if 0 <= row < len(po.entries)]  # type: ignore[union-attr]
            request_entries = copy.deepcopy(original_entries)
            context_entries = copy.deepcopy(list(po.entries))  # type: ignore[union-attr]
            previous_file_context = copy.deepcopy(previous_po_viewer_file_context(context_limit))
            by_uid = {entry.uid: row for row, entry in zip(rows, request_entries)}

            self._begin_task_progress("Gemini API PO entries", len(request_entries))
            set_status(
                f"Gemini API translating {len(request_entries)} selected entr{'y' if len(request_entries) == 1 else 'ies'}..."
            )
            _tab.setEnabled(False)
            self.stop_button.setEnabled(True)
            self._stop_event.clear()

            signals = WorkerSignals()
            self._active_signals.append(signals)
            task_state = {"applied": False, "failed": False, "stopped": False, "cleaned": False}

            def gemini_progress(done: int, total: int, label: str) -> None:
                self._update_task_progress(done, total, label)
                set_status(f"Gemini API translating selected entries: {done}/{total}")

            def apply_gemini_result(payload: object) -> None:
                if not isinstance(payload, dict):
                    return
                translations = payload.get("translations", {})
                errors = payload.get("errors", [])
                usage = str(payload.get("usage") or "").strip()
                if not isinstance(translations, dict) or not isinstance(errors, list):
                    return
                task_state["applied"] = True
                if po_file() is not source_po or current_path() != source_path:
                    self._finish_task_progress("Gemini result not applied")
                    QMessageBox.warning(
                        self,
                        "Gemini API",
                        "The loaded PO file changed while Gemini was working, so the returned translation was not applied.",
                    )
                    return
                changed = 0
                begin_po_undo_batch("gemini")
                try:
                    for uid, translation in translations.items():
                        row = by_uid.get(uid)
                        if row is not None and isinstance(translation, str) and set_entry_translation(row, translation, undo_label="gemini"):
                            changed += 1
                finally:
                    end_po_undo_batch()
                refresh_suggestions_for_row(table.currentRow())
                self._finish_task_progress(f"Gemini translated {changed} PO entr{'y' if changed == 1 else 'ies'}")
                if errors:
                    preview = "\n".join(f"{e.msgctxt or e.uid}: {e.reason}" for e in errors[:8])
                    more = f"\n... {len(errors) - 8} more" if len(errors) > 8 else ""
                    QMessageBox.warning(
                        self,
                        "Gemini API",
                        f"Translated {changed} entr{'y' if changed == 1 else 'ies'}, with {len(errors)} validation issue(s):\n{preview}{more}",
                    )
                else:
                    set_status(
                        f"Gemini API translated {changed} selected entr{'y' if changed == 1 else 'ies'}. Save when ready."
                        + (f" Tokens: {usage}." if usage else "")
                    )

            def gemini_failed(message: str) -> None:
                stopped = message == "Gemini API translation stopped."
                task_state["stopped"] = stopped
                task_state["failed"] = not stopped
                self._finish_task_progress("Gemini API stopped" if stopped else "Gemini API failed")
                set_status(message)
                if not stopped:
                    QMessageBox.critical(self, "Gemini API", message)

            def finish_gemini_task() -> None:
                if task_state["cleaned"]:
                    return
                task_state["cleaned"] = True
                _tab.setEnabled(True)
                self.stop_button.setEnabled(False)
                self._active_thread = None
                try:
                    self._active_signals.remove(signals)
                except ValueError:
                    pass
                if not task_state["applied"] and not task_state["failed"] and not task_state["stopped"]:
                    self._finish_task_progress("Gemini API stopped")
                    set_status("Gemini API translation stopped.")
                    task_state["stopped"] = True
                notification_status = "failed" if task_state["failed"] else ("stopped" if task_state["stopped"] else "success")
                self._notify_task_complete(notification_status)

            signals.progress.connect(gemini_progress)
            signals.result.connect(apply_gemini_result)
            signals.error.connect(gemini_failed)
            signals.done.connect(finish_gemini_task)

            def gemini_worker() -> None:
                try:
                    client = GeminiApiClient(
                        api_key=api_key,
                        model=model,
                        prompt=prompt,
                        timeout_seconds=timeout_seconds,
                        thinking_mode=thinking_mode,
                        max_output_tokens=max_output_tokens,
                    )

                    def report_gemini_progress(done: int, total: int) -> None:
                        self._check_stop()
                        signals.progress.emit(done, total, "Gemini API PO entries")

                    translations, errors = translate_entries_with_client(
                        request_entries,
                        client,
                        batch_size=batch_size,
                        sleep_seconds=sleep_seconds,
                        allow_partial=False,
                        prompt=prompt,
                        progress=report_gemini_progress,
                        context_entries=context_entries,
                        context_limit=context_limit,
                        previous_file_context_entries=previous_file_context or None,
                        cancel_check=self._check_stop,
                    )
                    self._check_stop()
                    signals.result.emit({"translations": translations, "errors": errors, "usage": client.total_usage.summary()})
                except OperationCancelled:
                    signals.error.emit("Gemini API translation stopped.")
                except Exception as exc:
                    signals.error.emit(f"Gemini API translation failed:\n{exc}")
                finally:
                    signals.done.emit()

            thread = threading.Thread(target=gemini_worker, daemon=True)
            self._active_thread = thread
            thread.start()

        def current_changed(row: int, _col: int, previous_row: int, _prev_col: int) -> None:
            if state.get("loading"):
                return
            if row != previous_row:
                commit_pending_text_undo()
                # Never carry the previous entry's native editor history into a
                # different row, even when both translations happen to match.
                self._clear_text_editor_undo(vi_box)
            load_detail(row)

        def _focus_is_vi_editor() -> bool:
            widget = QApplication.focusWidget()
            return widget is vi_box or (widget is not None and vi_box.isAncestorOf(widget))

        def switch_entry(delta: int) -> None:
            if table.rowCount() <= 0:
                return
            current = table.currentRow()
            row = 0 if current < 0 else max(0, min(table.rowCount() - 1, current + delta))
            keep_vi_focus = _focus_is_vi_editor()
            if not keep_vi_focus:
                table.setFocus(Qt.FocusReason.ShortcutFocusReason)
            select_entry_row(row, keep_vi_focus=keep_vi_focus)

        def switch_file(delta: int) -> None:
            if file_combo.count() <= 1:
                return
            keep_vi_focus = _focus_is_vi_editor()
            idx = file_combo.currentIndex()
            next_idx = (idx + delta) % file_combo.count()
            if file_combo.itemData(next_idx) is None:
                return
            file_combo.setCurrentIndex(next_idx)
            if keep_vi_focus:
                QTimer.singleShot(0, focus_vi_editor)

        table.currentCellChanged.connect(current_changed)
        table.itemChanged.connect(table_item_changed)
        vi_box.textChanged.connect(vi_text_changed)
        open_po_btn.clicked.connect(open_current_po_external)
        browse_files_btn.clicked.connect(browse_files)
        browse_folder_btn.clicked.connect(browse_folder)
        load_btn.clicked.connect(load_source_from_text)
        save_btn.clicked.connect(save_file)
        file_combo.currentIndexChanged.connect(lambda _idx: None if state.get("loading_files") else load_file())
        source_edit.returnPressed.connect(load_source_from_text)
        source_edit.editingFinished.connect(lambda: (self.config.__setitem__("po_viewer_source", source_edit.text().strip()), save_config(self.config)))
        source_extra_check.stateChanged.connect(lambda _state: (self.config.__setitem__(self._include_extra_config_key("po_viewer"), source_extra_check.isChecked()), save_config(self.config)))
        wrap_view_btn.clicked.connect(toggle_visual_wrap)
        clt_color_btn.clicked.connect(toggle_clt_color_mode)
        wrap_all_btn.clicked.connect(wrap_all)
        fill_btn.clicked.connect(fill_from_translafixer_sources)
        gemini_selected_btn.clicked.connect(translate_selected_with_gemini_api)
        undo_edit_btn.clicked.connect(undo_last_po_change)
        search_replace_btn.clicked.connect(open_search_replace_dialog)
        preset_replace_btn.clicked.connect(preset_replace_selected)
        dup_ref_btn.clicked.connect(lambda: self._open_reference_duplicates_dialog(tab_key="po_viewer"))
        refresh_suggest_btn.clicked.connect(lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        apply_suggest_btn.clicked.connect(apply_selected_suggestion)
        undo_suggest_btn.clicked.connect(undo_last_po_change)
        suggestions_list.itemDoubleClicked.connect(lambda _item: apply_selected_suggestion())
        vi_box.undoAvailable.connect(lambda _available: update_po_undo_controls())
        suggest_min_score.valueChanged.connect(lambda _value: (self.config.__setitem__("po_viewer_suggest_min_score", suggest_min_score.value()), save_config(self.config), refresh_suggestions_for_row(table.currentRow())))

        shortcuts: list[QShortcut] = []

        def add_shortcut(
            sequence: str,
            callback: Callable[[], None],
            *,
            parent: QWidget | None = None,
            context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
        ) -> None:
            shortcut = QShortcut(QKeySequence(sequence), parent or _tab)
            shortcut.setContext(context)
            shortcut.activated.connect(callback)
            shortcuts.append(shortcut)

        for nav_parent in (table, vi_box):
            add_shortcut("Ctrl+Up", lambda: switch_entry(-1), parent=nav_parent)
            add_shortcut("Ctrl+Down", lambda: switch_entry(1), parent=nav_parent)
        add_shortcut("Ctrl+E", focus_vi_editor)
        add_shortcut("F2", focus_vi_editor)
        add_shortcut("Ctrl+S", save_file)
        add_shortcut("Ctrl+Z", undo_last_po_change)
        add_shortcut("Ctrl+F", open_search_replace_dialog)
        po_viewer_wrap_filter = PersistentWrapShortcutFilter(
            _tab,
            {
                index + 1: (
                    lambda preset_index=index: (
                        self._set_active_linewrap_preset(preset_index),
                        wrap_selected(preset_index),
                    )
                )
                for index in range(4)
            },
            wrap_all,
            self._custom_shortcut_sequences,
            lambda: switch_file(-1),
            lambda: switch_file(1),
        )
        suggestion_shortcut_filter = RepeatedSuggestionShortcutFilter(
            _tab,
            {number: (lambda suggestion_number=number: apply_suggestion_number(suggestion_number)) for number in range(1, 4)},
        )
        po_viewer_undo_filter = RoutedUndoShortcutFilter(_tab, undo_last_po_change)
        add_shortcut(PRESET_REPLACE_SHORTCUT, preset_replace_selected)
        add_shortcut(GEMINI_TRANSLATE_SHORTCUT, translate_selected_with_gemini_api)
        add_shortcut(SUGGESTION_REFRESH_SHORTCUT, lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        self._po_viewer_shortcuts = shortcuts
        self._po_viewer_wrap_filter = po_viewer_wrap_filter
        self._po_viewer_suggestion_filter = suggestion_shortcut_filter
        self._po_viewer_undo_filter = po_viewer_undo_filter

        set_clt_color_mode(bool(state.get("clt_color_mode")), persist=False, quiet=True)
        update_po_undo_controls()

        initial_paths = self._processing_paths("po_viewer", extra_edit=source_edit, include_extra=source_extra_check, require_any=False)
        if initial_paths:
            set_file_list(initial_paths, "; ".join(str(path) for path in initial_paths), auto_load=False, quiet=True, update_source_edit=False)

    # ---------------- Web translation / Gemini API ----------------
    def _build_translate_tab(self) -> None:
        _tab, layout = self._new_tab("AI Translation")
        self._dr_option_selector(layout, "gemini_web")
        path_edit, include_extra = self._extra_path_row(layout, "gemini_web", "Extra folder", "last_path")

        form = QFormLayout()
        mode_combo = QComboBox()
        mode_combo.addItem("Web tab", "web")
        mode_combo.addItem("Gemini API key", "api")
        saved_mode = str(self.config.get("gemini_translate_mode", "web"))
        mode_combo.setCurrentIndex(1 if saved_mode == "api" else 0)
        form.addRow("Mode", mode_combo)

        cdp_edit = QLineEdit(str(self.config.get("gemini_web_cdp_url", "http://localhost:9222")))
        form.addRow("Chrome CDP", cdp_edit)

        chatgpt_web_toggle = QCheckBox("Use ChatGPT Web instead of Gemini")
        chatgpt_web_toggle.setChecked(bool(self.config.get("gemini_web_use_chatgpt", False)))
        chatgpt_web_toggle.setToolTip("Affects Web tab mode only. Gemini API mode always uses Gemini.")
        form.addRow("Web provider", chatgpt_web_toggle)

        api_key_edit = QLineEdit(str(self.config.get("gemini_api_key", "")))
        api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_edit.setPlaceholderText("Shared by single-entry and mass Gemini API translation, or set GEMINI_API_KEY")
        api_key_edit.setToolTip(
            "This key is shared. The settings below are separate: Single entry is used by Search, duplicate views, and PO Viewer; Mass translation is used by this tab."
        )
        form.addRow("Gemini API key", api_key_edit)
        layout.addLayout(form)

        def make_thinking_combo(saved: str) -> QComboBox:
            combo = QComboBox()
            for label, value in (
                ("Off / lowest cost", "off"),
                ("Minimal", "minimal"),
                ("Low", "low"),
                ("Medium", "medium"),
                ("High", "high"),
                ("Dynamic / model default", "dynamic"),
            ):
                combo.addItem(label, value)
            index = combo.findData(saved)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.setToolTip("Gemini 2.5 uses a thinking-token budget. Gemini 3 Flash/Lite maps Off to Minimal; Gemini 3 Pro maps Off or Minimal to Low.")
            return combo

        api_profiles = QTabWidget()

        single_page = QWidget()
        single_form = QFormLayout(single_page)
        single_note = QLabel("Used by AI buttons in Search, duplicate/diff views, and PO Viewer. Each selected entry is a separate request.")
        single_note.setWordWrap(True)
        single_form.addRow(single_note)
        api_single_model_edit = QLineEdit(self._gemini_api_profile_model("single"))
        single_form.addRow("Model", api_single_model_edit)
        api_single_timeout_seconds = QSpinBox()
        api_single_timeout_seconds.setRange(5, 3600)
        api_single_timeout_seconds.setValue(int(self._gemini_api_timeout_seconds("single")))
        api_single_timeout_seconds.setSuffix(" s")
        single_form.addRow("Request timeout", api_single_timeout_seconds)
        api_single_context_wrap = QWidget()
        api_single_context_row = QHBoxLayout(api_single_context_wrap)
        api_single_context_row.setContentsMargins(0, 0, 0, 0)
        api_single_context_entries = QSpinBox()
        api_single_context_entries.setRange(0, 200)
        api_single_context_entries.setValue(self._gemini_api_context_limit("single"))
        api_single_context_entries.setFixedWidth(72)
        api_single_context_entries.setToolTip("Previous English/Vietnamese entries sent only for continuity. Recommended: 3 to 5.")
        api_single_context_across_files = QCheckBox("Include previous files")
        api_single_context_across_files.setChecked(self._gemini_api_cross_file_context_enabled("single"))
        api_single_context_row.addWidget(api_single_context_entries)
        api_single_context_row.addWidget(api_single_context_across_files)
        api_single_context_row.addStretch()
        single_form.addRow("Previous context", api_single_context_wrap)
        api_single_wait_seconds = QDoubleSpinBox()
        api_single_wait_seconds.setRange(0.0, 300.0)
        api_single_wait_seconds.setDecimals(1)
        api_single_wait_seconds.setSingleStep(0.1)
        api_single_wait_seconds.setValue(self._gemini_api_profile_sleep_seconds("single"))
        api_single_wait_seconds.setSuffix(" s")
        single_form.addRow("Delay between requests", api_single_wait_seconds)
        api_single_thinking = make_thinking_combo(self._gemini_api_profile_thinking_mode("single"))
        single_form.addRow("Thinking", api_single_thinking)
        api_single_max_output = QSpinBox()
        api_single_max_output.setRange(0, 65536)
        api_single_max_output.setValue(self._gemini_api_profile_max_output_tokens("single"))
        api_single_max_output.setSpecialValueText("Auto")
        api_single_max_output.setToolTip("Auto sizes a safe cap from the English source. A cap prevents runaway output; unused capacity is not billed.")
        single_form.addRow("Max output tokens", api_single_max_output)
        api_profiles.addTab(single_page, "Single-entry API")

        mass_page = QWidget()
        mass_form = QFormLayout(mass_page)
        mass_note = QLabel("Used only by Run Gemini API below. Current entries are batched while previous context is sent once per batch.")
        mass_note.setWordWrap(True)
        mass_form.addRow(mass_note)
        api_mass_model_edit = QLineEdit(self._gemini_api_profile_model("mass"))
        mass_form.addRow("Model", api_mass_model_edit)
        api_mass_timeout_seconds = QSpinBox()
        api_mass_timeout_seconds.setRange(5, 3600)
        api_mass_timeout_seconds.setValue(int(self._gemini_api_timeout_seconds("mass")))
        api_mass_timeout_seconds.setSuffix(" s")
        mass_form.addRow("Request timeout", api_mass_timeout_seconds)
        api_mass_max_files = QSpinBox()
        api_mass_max_files.setRange(0, 9999)
        api_mass_max_files.setValue(int(self.config.get("gemini_api_mass_max_files", 59)))
        api_mass_max_files.setSpecialValueText("All")
        mass_form.addRow("Max files", api_mass_max_files)
        api_mass_batch_entries = QSpinBox()
        api_mass_batch_entries.setRange(1, 200)
        api_mass_batch_entries.setValue(int(self.config.get("gemini_api_mass_batch_entries", 40)))
        api_mass_batch_entries.setToolTip("Number of untranslated current entries per API request. Recommended: 20 to 40.")
        mass_form.addRow("Entries per batch", api_mass_batch_entries)
        api_mass_context_wrap = QWidget()
        api_mass_context_row = QHBoxLayout(api_mass_context_wrap)
        api_mass_context_row.setContentsMargins(0, 0, 0, 0)
        api_mass_context_entries = QSpinBox()
        api_mass_context_entries.setRange(0, 200)
        api_mass_context_entries.setValue(self._gemini_api_context_limit("mass"))
        api_mass_context_entries.setFixedWidth(72)
        api_mass_context_entries.setToolTip("Previous translated entries sent once before each batch. Recommended: 3 to 5.")
        api_mass_context_across_files = QCheckBox("Include previous files")
        api_mass_context_across_files.setChecked(self._gemini_api_cross_file_context_enabled("mass"))
        api_mass_context_row.addWidget(api_mass_context_entries)
        api_mass_context_row.addWidget(api_mass_context_across_files)
        api_mass_context_row.addStretch()
        mass_form.addRow("Previous context", api_mass_context_wrap)
        api_mass_wait_seconds = QDoubleSpinBox()
        api_mass_wait_seconds.setRange(0.0, 300.0)
        api_mass_wait_seconds.setDecimals(1)
        api_mass_wait_seconds.setSingleStep(0.1)
        api_mass_wait_seconds.setValue(self._gemini_api_profile_sleep_seconds("mass"))
        api_mass_wait_seconds.setSuffix(" s")
        mass_form.addRow("Delay between batches", api_mass_wait_seconds)
        api_mass_thinking = make_thinking_combo(self._gemini_api_profile_thinking_mode("mass"))
        mass_form.addRow("Thinking", api_mass_thinking)
        api_mass_max_output = QSpinBox()
        api_mass_max_output.setRange(0, 65536)
        api_mass_max_output.setValue(self._gemini_api_profile_max_output_tokens("mass"))
        api_mass_max_output.setSpecialValueText("Auto")
        api_mass_max_output.setToolTip("Auto sizes a safe cap for each batch. Unused output capacity is not billed.")
        mass_form.addRow("Max output tokens", api_mass_max_output)
        api_profiles.addTab(mass_page, "Mass-translation API")
        layout.addWidget(api_profiles)

        web_group = QGroupBox("Web translation settings")
        grid = QGridLayout(web_group)
        web_max_files = QSpinBox(); web_max_files.setRange(0, 9999); web_max_files.setValue(int(self.config.get("gemini_web_max_files", 59)))
        max_lines = QSpinBox(); max_lines.setRange(1, 9999); max_lines.setValue(int(self.config.get("gemini_web_max_lines", 600)))
        max_entries = QSpinBox(); max_entries.setRange(1, 999); max_entries.setValue(int(self.config.get("gemini_web_max_entries", DEFAULT_MAX_ENTRIES_PER_BATCH)))
        wait_seconds = QDoubleSpinBox(); wait_seconds.setRange(2.5, 999.0); wait_seconds.setDecimals(1); wait_seconds.setSingleStep(0.5); wait_seconds.setValue(max(2.5, float(self.config.get("gemini_web_wait_seconds", 2.5))))
        timeout_seconds = QSpinBox(); timeout_seconds.setRange(1, 9999); timeout_seconds.setValue(int(self.config.get("gemini_web_timeout_seconds", 180)))
        retries = QSpinBox(); retries.setRange(0, 99); retries.setValue(int(self.config.get("gemini_web_retries", DEFAULT_BATCH_RETRIES)))
        controls = [("Max files", web_max_files), ("Max lines", max_lines), ("Max entries", max_entries), ("Post-save wait", wait_seconds), ("Timeout", timeout_seconds), ("Retries", retries)]
        for i, (label, widget) in enumerate(controls):
            grid.addWidget(QLabel(label), 0, i)
            grid.addWidget(widget, 1, i)
        layout.addWidget(web_group)

        flags = QHBoxLayout()
        rename_dupes = QCheckBox("Rename (1)"); rename_dupes.setChecked(True)
        backup_missing = QCheckBox("Create Copy.po if missing"); backup_missing.setChecked(True)
        rename_folders = QCheckBox("Rename segment folders"); rename_folders.setChecked(True)
        allow_invalid = QCheckBox("Allow invalid")
        allow_state = {"value": False}
        allow_invalid.stateChanged.connect(lambda _state: allow_state.__setitem__("value", allow_invalid.isChecked()))
        for chk in [rename_dupes, backup_missing, rename_folders, allow_invalid]:
            flags.addWidget(chk)
        flags.addStretch()
        layout.addLayout(flags)

        row = QHBoxLayout()
        row.addStretch()
        run_btn = self._button("Run Translation")
        chrome_btn = self._button("Open Chrome", secondary=True)
        row.addWidget(chrome_btn)
        row.addWidget(run_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def api_mode_enabled() -> bool:
            return str(mode_combo.currentData()) == "api"

        def web_provider_name() -> str:
            return "ChatGPT" if chatgpt_web_toggle.isChecked() else "Gemini"

        def sync_mode_ui() -> None:
            is_api = api_mode_enabled()
            cdp_edit.setEnabled(not is_api)
            chatgpt_web_toggle.setEnabled(not is_api)
            timeout_seconds.setEnabled(not is_api)
            retries.setEnabled(not is_api)
            max_lines.setEnabled(not is_api)
            rename_dupes.setEnabled(not is_api)
            backup_missing.setEnabled(not is_api)
            rename_folders.setEnabled(not is_api)
            web_group.setEnabled(not is_api)
            api_single_context_across_files.setEnabled(api_single_context_entries.value() > 0)
            api_mass_context_across_files.setEnabled(api_mass_context_entries.value() > 0)
            chrome_btn.setEnabled(not is_api)
            run_btn.setText("Run Gemini API" if is_api else f"Run {web_provider_name()} Web")

        def save_web_config() -> None:
            self.config["gemini_translate_mode"] = "api" if api_mode_enabled() else "web"
            self.config["gemini_web_cdp_url"] = cdp_edit.text().strip() or "http://localhost:9222"
            self.config["gemini_web_use_chatgpt"] = chatgpt_web_toggle.isChecked()
            self.config["gemini_web_max_files"] = web_max_files.value()
            self.config["gemini_web_max_lines"] = max_lines.value()
            self.config["gemini_web_max_entries"] = max_entries.value()
            self.config["gemini_web_wait_seconds"] = wait_seconds.value()
            self.config["gemini_web_timeout_seconds"] = timeout_seconds.value()
            self.config["gemini_web_retries"] = retries.value()
            self.config["gemini_api_key"] = api_key_edit.text().strip()
            self.config["gemini_api_single_model"] = api_single_model_edit.text().strip() or "gemini-2.5-flash"
            self.config["gemini_api_single_timeout_seconds"] = api_single_timeout_seconds.value()
            self.config["gemini_api_single_context_entries"] = api_single_context_entries.value()
            self.config["gemini_api_single_context_across_files"] = api_single_context_across_files.isChecked()
            self.config["gemini_api_single_sleep_seconds"] = api_single_wait_seconds.value()
            self.config["gemini_api_single_thinking_mode"] = str(api_single_thinking.currentData())
            self.config["gemini_api_single_max_output_tokens"] = api_single_max_output.value()
            self.config["gemini_api_mass_model"] = api_mass_model_edit.text().strip() or "gemini-2.5-flash"
            self.config["gemini_api_mass_timeout_seconds"] = api_mass_timeout_seconds.value()
            self.config["gemini_api_mass_max_files"] = api_mass_max_files.value()
            self.config["gemini_api_mass_batch_entries"] = api_mass_batch_entries.value()
            self.config["gemini_api_mass_context_entries"] = api_mass_context_entries.value()
            self.config["gemini_api_mass_context_across_files"] = api_mass_context_across_files.isChecked()
            self.config["gemini_api_mass_sleep_seconds"] = api_mass_wait_seconds.value()
            self.config["gemini_api_mass_thinking_mode"] = str(api_mass_thinking.currentData())
            self.config["gemini_api_mass_max_output_tokens"] = api_mass_max_output.value()
            save_config(self.config)

        def run_web(logwrite, progresswrite):
            save_web_config()
            paths = self._processing_paths("gemini_web", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            provider = web_provider_name()
            runner = run_chatgpt_web_path if provider == "ChatGPT" else run_gemini_web_path
            limit = web_max_files.value()
            remaining = None if limit <= 0 else limit
            total_files = 0
            total_translated = 0
            total_errors = 0
            for input_path in paths:
                self._check_stop()
                if remaining is not None and remaining <= 0:
                    break
                logwrite(f"{provider} Web input: {input_path}")
                result = runner(
                    str(input_path),
                    max_files=remaining,
                    max_lines_per_batch=max_lines.value(),
                    max_entries_per_batch=max_entries.value(),
                    wait_between_batches=wait_seconds.value(),
                    cdp_url=cdp_edit.text().strip() or "http://localhost:9222",
                    allow_invalid=lambda: bool(allow_state["value"]),
                    rename_duplicates=rename_dupes.isChecked(),
                    create_missing_backups=backup_missing.isChecked(),
                    rename_folders=rename_folders.isChecked(),
                    response_timeout_seconds=timeout_seconds.value(),
                    retry_count=retries.value(),
                    log=lambda msg: logwrite(msg),
                    stop_requested=self._stop_event.is_set,
                    progress=lambda done, total, path: progresswrite(done, total, f"{provider} Web {path.name}"),
                )
                if not result.files:
                    logwrite(f"No untranslated PO files found in {input_path}.", "warn")
                    continue
                total_files += len(result.files)
                total_translated += result.total_translated
                total_errors += result.total_errors
                if remaining is not None:
                    remaining = max(0, remaining - len(result.files))
                for item in result.files:
                    self._check_stop()
                    tag = "good" if not item.errors else "warn"
                    logwrite(f"{item.file} | missing={item.missing_before} | applied={item.translated} | errors={len(item.errors)}", tag)
                    if item.debug_log:
                        logwrite(f"  debug: {item.debug_log}")
                    if item.backup_created:
                        logwrite("  backup: created missing Copy.po only; existing Copy.po was not touched", "good")
                    if item.folder_renamed_to:
                        logwrite(f"  folder: {item.folder_renamed_from} -> {item.folder_renamed_to}", "good")
                    elif item.folder_rename_skipped_reason:
                        logwrite(f"  folder: skipped ({item.folder_rename_skipped_reason})", "warn")
                    for e in item.errors[:40]:
                        logwrite(f"  {e.uid} | {e.msgctxt} | {e.reason}", "bad")
                    if len(item.errors) > 40:
                        logwrite(f"  ... {len(item.errors) - 40} more errors", "warn")
            if not total_files:
                logwrite("No untranslated PO files found.", "warn")
                return
            logwrite(f"Total translated: {total_translated}", "good")
            if total_errors:
                logwrite(f"Total errors: {total_errors}", "bad")

        def run_api(logwrite, progresswrite):
            save_web_config()
            api_key = api_key_edit.text().strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("Gemini API key missing. Paste it into the API key field or set GEMINI_API_KEY.")
            paths = self._processing_paths("gemini_web", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            prompt = SYSTEM_INSTRUCTIONS
            client = GeminiApiClient(
                api_key=api_key,
                model=api_mass_model_edit.text().strip() or "gemini-2.5-flash",
                prompt=prompt,
                timeout_seconds=api_mass_timeout_seconds.value(),
                thinking_mode=str(api_mass_thinking.currentData()),
                max_output_tokens=api_mass_max_output.value(),
            )
            limit = api_mass_max_files.value()
            po_files: list[Path] = []
            seen: set[str] = set()
            for base in paths:
                self._check_stop()
                if limit > 0 and len(po_files) >= limit:
                    break
                if base.is_file():
                    candidates = [base] if base.suffix.lower() == ".po" else []
                else:
                    candidates = discover_untranslated_po_files(base, max_files=None)
                for candidate in candidates:
                    key = self._path_key(candidate)
                    if key in seen:
                        continue
                    seen.add(key)
                    po_files.append(candidate)
                    if limit > 0 and len(po_files) >= limit:
                        break
            if not po_files:
                logwrite("No untranslated PO files found.", "warn")
                return
            total_changed = 0
            total_errors = 0
            context_limit = api_mass_context_entries.value()
            use_previous_files = api_mass_context_across_files.isChecked() and context_limit > 0
            previous_file_context: list[POEntry] = []
            progresswrite(0, len(po_files), "Gemini API files")
            for idx, po_path in enumerate(po_files, start=1):
                self._check_stop()
                progresswrite(idx - 1, len(po_files), f"Gemini API {po_path.name}")
                logwrite(f"[{idx}/{len(po_files)}] Gemini API translating {po_path}")
                changed, errors = translate_file_with_client(
                    po_path,
                    client,
                    batch_size=api_mass_batch_entries.value(),
                    sleep_seconds=api_mass_wait_seconds.value(),
                    allow_partial=bool(allow_state["value"]),
                    prompt=prompt,
                    context_limit=context_limit,
                    previous_file_context_entries=previous_file_context if use_previous_files else None,
                    cancel_check=self._check_stop,
                )
                total_changed += changed
                total_errors += len(errors)
                if use_previous_files:
                    try:
                        translated_po = get_cached_po(po_path)
                        previous_file_context.extend(translated_po.entries)
                        if len(previous_file_context) > context_limit:
                            previous_file_context = previous_file_context[-context_limit:]
                    except Exception as exc:
                        logwrite(f"  context: could not reload {po_path.name}: {exc}", "warn")
                tag = "good" if not errors else "warn"
                logwrite(f"  applied={changed} | errors={len(errors)}", tag)
                for e in errors[:40]:
                    logwrite(f"  {e.uid} | {e.msgctxt} | {e.reason}", "bad")
                if len(errors) > 40:
                    logwrite(f"  ... {len(errors) - 40} more errors", "warn")
                progresswrite(idx, len(po_files), f"Gemini API {po_path.name}")
            logwrite(f"Total translated: {total_changed}", "good")
            logwrite(f"Token usage: {client.total_usage.summary()}", "good")
            if total_errors:
                logwrite(f"Total errors: {total_errors}", "bad")

        def run_selected_mode(logwrite, progresswrite):
            if api_mode_enabled():
                run_api(logwrite, progresswrite)
            else:
                run_web(logwrite, progresswrite)

        def open_chrome(logwrite, progresswrite):
            progresswrite(0, 0, "Opening Chrome")
            save_web_config()
            provider = web_provider_name()
            start_url = DEFAULT_CHATGPT_URL if provider == "ChatGPT" else DEFAULT_GEMINI_URL
            cmd = open_chrome_debug(
                cdp_url=cdp_edit.text().strip() or "http://localhost:9222",
                user_data_dir=DEFAULT_CHROME_USER_DATA_DIR,
                url=start_url,
            )
            logwrite("Chrome opened with remote debugging.", "good")
            logwrite(f"Login to {provider} in that Chrome window, then click Run {provider} Web.")
            logwrite("Command: " + " ".join(str(x) for x in cmd))
            progresswrite(1, 1, "Chrome opened")

        mode_combo.currentIndexChanged.connect(lambda _idx: (sync_mode_ui(), save_web_config()))
        chatgpt_web_toggle.stateChanged.connect(lambda _state: (sync_mode_ui(), save_web_config()))
        api_key_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_key", text.strip()))
        api_single_model_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_single_model", text.strip()))
        api_mass_model_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_mass_model", text.strip()))
        api_single_context_entries.valueChanged.connect(
            lambda value: (
                self.config.__setitem__("gemini_api_single_context_entries", int(value)),
                api_single_context_across_files.setEnabled(int(value) > 0),
                save_web_config(),
            )
        )
        api_mass_context_entries.valueChanged.connect(
            lambda value: (
                self.config.__setitem__("gemini_api_mass_context_entries", int(value)),
                api_mass_context_across_files.setEnabled(int(value) > 0),
                save_web_config(),
            )
        )
        api_single_context_across_files.stateChanged.connect(
            lambda _state: save_web_config()
        )
        api_mass_context_across_files.stateChanged.connect(
            lambda _state: save_web_config()
        )
        for widget in (
            api_single_timeout_seconds,
            api_single_wait_seconds,
            api_single_max_output,
            api_mass_timeout_seconds,
            api_mass_max_files,
            api_mass_batch_entries,
            api_mass_wait_seconds,
            api_mass_max_output,
            web_max_files,
            max_lines,
            max_entries,
            wait_seconds,
            timeout_seconds,
            retries,
        ):
            widget.valueChanged.connect(lambda _value: save_web_config())
        api_single_thinking.currentIndexChanged.connect(lambda _index: save_web_config())
        api_mass_thinking.currentIndexChanged.connect(lambda _index: save_web_config())
        for widget in [cdp_edit, api_key_edit, api_single_model_edit, api_mass_model_edit]:
            widget.editingFinished.connect(save_web_config)
        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run_selected_mode))
        chrome_btn.clicked.connect(lambda: self._run_threaded(chrome_btn, log, open_chrome))
        sync_mode_ui()

    # ---------------- Repack ----------------
    def _build_repack_tab(self) -> None:
        _tab, layout = self._new_tab("Repack")
        self._dr_option_selector(layout, "repack")
        source_edit = self._path_row(layout, "Manual sync source", "sync_source")
        target_edit = self._path_row(layout, "Manual sync target", "sync_target")

        row = QHBoxLayout()
        row.addStretch()
        repack_btn = self._button("Repack")
        sync_btn = self._button("Sync by Filename", secondary=True)
        row.addWidget(repack_btn)
        row.addWidget(sync_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def log_paths(title: str, paths: list[Path], logwrite, level: str = "warn", max_items: int = 80) -> None:
            if not paths:
                return
            logwrite(f"{title}: {len(paths)}", level)
            for path in paths[:max_items]:
                logwrite(f"  - {path}", level)
            if len(paths) > max_items:
                logwrite(f"  ... {len(paths) - max_items} more", level)

        def log_items(title, items, formatter, logwrite, level: str = "info", max_items: int = 80) -> None:
            if not items:
                return
            logwrite(f"{title}: {len(items)}", level)
            for item in items[:max_items]:
                logwrite(f"  - {formatter(item)}", level)
            if len(items) > max_items:
                logwrite(f"  ... {len(items) - max_items} more", level)

        def sync(logwrite, progresswrite):
            self._check_stop()
            source = source_edit.text().strip()
            target = target_edit.text().strip()
            if not source or not target:
                raise DratRepackError("Choose both Manual sync source and Manual sync target.")
            progresswrite(0, 0, "Scanning sync folders")
            result = sync_by_filename_report(
                source,
                target,
                progress=lambda done, total, path: progresswrite(done, total, f"Sync {path.name}"),
            )
            logwrite(f"Source PO files: {result.source_files}")
            logwrite(f"Target PO files: {result.target_files}")
            logwrite(f"Files copied: {result.copied}", "good" if result.copied else "warn")
            logwrite(f"Identical files skipped: {result.skipped_identical}")
            if result.duplicate_source_names:
                logwrite(f"Duplicate source filenames skipped: {result.duplicate_source_names}", "bad")
                log_paths("Duplicate source files", result.duplicate_source_files, logwrite, "bad")
            log_paths("Source files without a target filename match", result.source_without_target, logwrite)
            log_paths("Target files without a source filename match", result.target_without_source, logwrite, "info")

        def repack(logwrite, progresswrite):
            selected = self._selected_dr_options("repack")
            if not selected:
                raise DratRepackError("Select at least one Danganronpa file group.")

            drat_folder = str(self.config.get("drat_folder_path", "")).strip()
            script_raw = str(self.config.get("script_path", "")).strip()
            game_raw = str(self.config.get("game_folder_path", "")).strip()
            if not drat_folder or not script_raw or not game_raw:
                raise DratRepackError("Set Settings > DRAT Folder, Script, and Game Folder first.")

            workspace = resolve_drat_workspace(drat_folder)
            script_folder = Path(script_raw).expanduser()
            game_folder = Path(game_raw).expanduser()
            if not script_folder.exists() or not script_folder.is_dir():
                raise DratRepackError(f"Script folder does not exist: {script_folder}")
            if not game_folder.exists() or not game_folder.is_dir():
                raise DratRepackError(f"Game Folder does not exist: {game_folder}")

            try:
                script_resolved = script_folder.resolve(strict=False)
                wad_root_resolved = workspace.wad_extracted_root.resolve(strict=False)
                script_inside_wad = script_resolved == wad_root_resolved or script_resolved.is_relative_to(wad_root_resolved)
            except OSError:
                script_inside_wad = False
            if not script_inside_wad:
                raise DratRepackError(
                    f"Script must be the extracted WAD root or a folder inside DRAT EXTRACTED/WAD: {workspace.wad_extracted_root}"
                )

            logwrite(f"DRAT workspace: {workspace.manual_root}", "good")
            logwrite(f"Game profile: {workspace.profile.name}")
            logwrite(f"Script target: {script_folder}")
            logwrite(f"Game target: {game_folder}")

            # 1. Sync selected Working folders into DRAT EXTRACTED by filename.
            working: list[tuple[str, Path]] = []
            invalid: list[str] = []
            for option_key in selected:
                label = option_name(option_key)
                raw = str(self.config.get(f"working_{option_key}_path", "")).strip()
                path = Path(raw).expanduser() if raw else None
                if path is None or not path.exists() or not path.is_dir():
                    invalid.append(f"{label}: {raw or 'not set'}")
                else:
                    working.append((label, path))
            if invalid:
                raise DratRepackError("Invalid selected Working folders: " + "; ".join(invalid))

            sync_failures = 0
            total_synced = total_identical = 0
            progresswrite(0, 0, "1/5 Indexing DRAT EXTRACTED PO files")
            target_index, target_file_count = index_po_files_by_name(
                workspace.extracted_root,
                progress=lambda done, total, path: progresswrite(
                    done,
                    total,
                    f"1/5 Index DRAT EXTRACTED: {path.name}",
                ),
            )
            logwrite(f"1/5 Target PO index ready: {target_file_count} file(s)")
            progresswrite(0, len(working), "1/5 Sync selected Working folders")
            for option_index, (label, working_folder) in enumerate(working, start=1):
                self._check_stop()
                result = sync_by_filename_report(
                    working_folder,
                    workspace.extracted_root,
                    progress=lambda done, total, path, label=label: progresswrite(
                        done, total, f"1/5 Sync {label}: {path.name}"
                    ),
                    target_index=target_index,
                    target_file_count=target_file_count,
                    collect_target_without_source=False,
                )
                total_synced += result.copied
                total_identical += result.skipped_identical
                failures = result.duplicate_source_names + len(result.source_without_target)
                if result.source_files == 0:
                    failures += 1
                    logwrite(f"{label}: no working PO files found.", "bad")
                sync_failures += failures
                logwrite(
                    f"{label}: source={result.source_files}, copied={result.copied}, "
                    f"identical={result.skipped_identical}, missing targets={len(result.source_without_target)}, "
                    f"duplicate source names={result.duplicate_source_names}",
                    "good" if failures == 0 else "bad",
                )
                log_paths(f"{label} files not found in DRAT EXTRACTED", result.source_without_target, logwrite, "bad")
                log_paths(f"{label} duplicate source files", result.duplicate_source_files, logwrite, "bad")
                progresswrite(option_index, len(working), f"1/5 Synced {label}")
            if sync_failures:
                raise DratRepackError(f"Sync stage has {sync_failures} unresolved file problem(s); repack stopped.")
            logwrite(f"1/5 Sync complete: copied={total_synced}, identical={total_identical}", "good")

            # 2. Repack every supported DRAT text/container format.
            progresswrite(0, 0, "2/5 Repacking LIN and PAK files")
            format_result = repack_all_formats(
                workspace,
                progress=lambda done, total, path: progresswrite(done, total, f"2/5 Repack {path.name}"),
                cancel=self._check_stop,
            )
            for category in format_result.categories_missing:
                logwrite(f"Category not present, skipped: {category}", "info")
            log_items(
                "Skipped format jobs",
                format_result.skipped,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "info",
            )
            log_items(
                "Format errors",
                format_result.errors,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "bad",
            )
            log_paths("Built LIN/PAK outputs", format_result.built_outputs, logwrite, "good")
            log_paths("Unchanged LIN/PAK outputs", format_result.unchanged_outputs, logwrite, "info")
            if format_result.errors:
                raise DratRepackError(f"Format repack failed for {len(format_result.errors)} source folder(s).")
            if not format_result.outputs:
                raise DratRepackError("No LIN or PAK files were available from DRAT EXTRACTED.")
            logwrite(
                f"2/5 Repack complete: built={len(format_result.built_outputs)}, "
                f"unchanged={len(format_result.unchanged_outputs)}",
                "good",
            )

            # 3. Resolve generated files to Script targets, but do not copy yet.
            # WAD creation consumes these files virtually, keeping the extracted
            # WAD tree untouched until every repack has completed successfully.
            progresswrite(0, 0, "3/5 Preparing virtual Script overlay")
            script_plan = plan_files_by_filename(
                format_result.outputs,
                script_folder,
                progress=lambda done, total, path: progresswrite(
                    done,
                    total,
                    f"3/5 Scan Script: {path.name}",
                ),
                cancel=self._check_stop,
            )
            log_items(
                "Script mapping errors",
                script_plan.errors,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "bad",
            )
            if script_plan.errors:
                raise DratRepackError(f"Script mapping failed for {len(script_plan.errors)} generated file(s).")
            log_items(
                "Planned Script replacements",
                script_plan.matches,
                lambda item: f"{item[0]} -> {item[1]}",
                logwrite,
                "info",
            )
            script_overrides = {target: source for source, target in script_plan.matches}
            logwrite(f"3/5 Script overlay ready: matched={len(script_plan.matches)}", "good")

            # 4. Repack all WADs using virtual generated-file replacements.
            progresswrite(0, 0, "4/5 Repacking WAD")
            wad_result = repack_all_wads(
                workspace,
                file_overrides=script_overrides,
                progress=lambda done, total, path: progresswrite(done, total, f"4/5 WAD {path.name}"),
                cancel=self._check_stop,
            )
            log_items(
                "Skipped WAD jobs",
                wad_result.skipped,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "info",
            )
            log_items(
                "WAD errors",
                wad_result.errors,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "bad",
            )
            log_paths("Built WAD outputs", wad_result.built_outputs, logwrite, "good")
            log_paths("Unchanged WAD outputs", wad_result.unchanged_outputs, logwrite, "info")
            if wad_result.errors:
                raise DratRepackError(f"WAD repack failed for {len(wad_result.errors)} folder(s).")
            if not wad_result.outputs:
                raise DratRepackError("No WAD files were available.")
            logwrite(
                f"4/5 WAD repack complete: built={len(wad_result.built_outputs)}, "
                f"unchanged={len(wad_result.unchanged_outputs)}",
                "good",
            )

            # 5. Validate both destinations first, then deploy Script and WAD files
            # in one transaction. No target file is changed before all repacks and
            # all filename matching have succeeded.
            progresswrite(0, 0, "5/5 Preparing transactional deployment")
            game_plan = plan_files_by_filename(
                wad_result.outputs,
                game_folder,
                progress=lambda done, total, path: progresswrite(
                    done,
                    total,
                    f"5/5 Scan Game: {path.name}",
                ),
                cancel=self._check_stop,
            )
            log_items(
                "Game mapping errors",
                game_plan.errors,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "bad",
            )
            if game_plan.errors:
                raise DratRepackError(f"Game mapping failed for {len(game_plan.errors)} WAD file(s).")

            script_targets = {target for _source, target in script_plan.matches}
            game_targets = {target for _source, target in game_plan.matches}
            deploy_result = deploy_filename_plans(
                [script_plan, game_plan],
                progress=lambda done, total, path: progresswrite(done, total, f"5/5 Deploy {path.name}"),
                cancel=self._check_stop,
            )
            log_items(
                "Deployed files",
                deploy_result.copied_files,
                lambda item: (
                    f"{'SCRIPT' if item[1] in script_targets else 'GAME'} {item[0]} -> {item[1]}"
                ),
                logwrite,
                "good",
            )
            log_items(
                "Unchanged deployment targets",
                deploy_result.skipped_identical_files,
                lambda item: f"{'SCRIPT' if item[1] in script_targets else 'GAME'} {item[1]}",
                logwrite,
                "info",
            )
            log_items(
                "Deployment errors",
                deploy_result.errors,
                lambda item: f"{item[0]}: {item[1]}",
                logwrite,
                "bad",
            )
            if deploy_result.errors:
                raise DratRepackError(f"Transactional deployment failed for {len(deploy_result.errors)} file(s).")

            script_copied = sum(1 for _source, target in deploy_result.copied_files if target in script_targets)
            game_copied = sum(1 for _source, target in deploy_result.copied_files if target in game_targets)
            script_unchanged = sum(
                1 for _source, target in deploy_result.skipped_identical_files if target in script_targets
            )
            game_unchanged = sum(
                1 for _source, target in deploy_result.skipped_identical_files if target in game_targets
            )
            logwrite(
                f"5/5 Deployment complete: Script copied={script_copied}, unchanged={script_unchanged}; "
                f"Game copied={game_copied}, unchanged={game_unchanged}",
                "good",
            )
            logwrite(
                f"Repack finished. Synced {total_synced} PO file(s); "
                f"LIN/PAK built={len(format_result.built_outputs)}, unchanged={len(format_result.unchanged_outputs)}; "
                f"WAD built={len(wad_result.built_outputs)}, unchanged={len(wad_result.unchanged_outputs)}; "
                f"deployed={deploy_result.copied}, unchanged targets={deploy_result.skipped_identical}.",
                "good",
            )

        repack_btn.clicked.connect(lambda: self._run_threaded(repack_btn, log, repack))
        sync_btn.clicked.connect(lambda: self._run_threaded(sync_btn, log, sync))


def _send_app_link_to_running_instance(value: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(APP_LINK_SERVER_NAME)
    if not socket.waitForConnected(250):
        return False
    socket.write(value.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def _start_app_link_server(window: ToolkitGUI) -> QLocalServer | None:
    # Do not steal the endpoint from another live window. Remove it only when
    # the previous process crashed and left a stale local-server name behind.
    probe = QLocalSocket()
    probe.connectToServer(APP_LINK_SERVER_NAME)
    if probe.waitForConnected(100):
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(APP_LINK_SERVER_NAME)
    server = QLocalServer(window)
    if not server.listen(APP_LINK_SERVER_NAME):
        return None

    sockets: set[QLocalSocket] = set()

    def accept_connections() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                continue
            sockets.add(socket)

            def read_link(sock: QLocalSocket = socket) -> None:
                raw = bytes(sock.readAll()).decode("utf-8", errors="replace").strip()
                if raw:
                    window.open_app_link(raw)

            def drop_socket(sock: QLocalSocket = socket) -> None:
                read_link(sock)
                sockets.discard(sock)
                sock.deleteLater()

            socket.readyRead.connect(read_link)
            socket.disconnected.connect(drop_socket)
            QTimer.singleShot(0, read_link)

    server.newConnection.connect(accept_connections)
    window._app_link_server = server
    window._app_link_sockets = sockets
    return server


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    initial_link = next((arg for arg in sys.argv[1:] if is_entry_url(arg)), "")
    if initial_link and _send_app_link_to_running_instance(initial_link):
        return

    register_url_protocol()
    window = ToolkitGUI()
    _start_app_link_server(window)
    window.show()
    QTimer.singleShot(0, window.start_initial_indexing)
    if initial_link:
        QTimer.singleShot(0, lambda value=initial_link: window.open_app_link(value))
    app.exec()


if __name__ == "__main__":
    main()
