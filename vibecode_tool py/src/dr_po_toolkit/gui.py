from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, QRectF, QSize, QEventLoop
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
from .backup import copy_wad_repack_to_game, make_backups, move_repack_to_script, restore_working_po_from_copies, sync_by_filename_report, sync_option_from_working_folder
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
    DEFAULT_MAX_ENTRIES_PER_BATCH,
    discover_untranslated_po_files,
    open_chrome_debug,
    run_gemini_web_path,
)
from .linewrap import normalize_wrap_presets, wrap_msgstr, wrap_po_file
from .po_io import load_po, save_po
from .rules import apply_rules_to_file, load_rules, rule_to_dict
from .search import SearchResult, search_files
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


class ToolkitGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._stop_event = threading.Event()
        self._active_thread: threading.Thread | None = None
        self._active_signals: list[WorkerSignals] = []
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
                background: {PANEL};
                color: {WHITE};
                border: 1px solid #3a4058;
                border-radius: 7px;
                text-align: center;
                font-weight: 800;
            }}
            QProgressBar::chunk {{ background: {TEAL}; border-radius: 6px; }}
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
        self._build_rule_editor_tab()
        self._build_linewrap_tab()
        self._build_search_tab()
        self._build_translafixer_tab()
        self._build_po_viewer_tab()
        self._build_translate_tab()
        self._build_backup_tab()

    def _new_tab(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        self.tabs.addTab(tab, title)
        return tab, layout

    def _make_log(self) -> LogBox:
        log = LogBox()
        log.setMinimumHeight(210)
        return log

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
        base_note = " Editable in the Line Wrap tab."
        return f"{action} with preset W{preset_index + 1}: Soft={soft}, Hard={hard}, Cuts={cuts}.{base_note}"

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
            "Create Missing Copy.po Backups": "Create missing Copy.po backups without overwriting existing backups.",
            "Sync Selected Options": "Copy selected Working folders to the shared Extracted destination.",
            "Sync by Filename": "Sync files by matching filename from source to target.",
            "Move Compile": "Copy compiled files from Repack to Script. WAD Repack is skipped.",
            "Move Repack": "Copy all Repack files to Script, excluding WAD Repack files.",
            "Move to Game": "Copy WAD Repack files into the configured Game Folder.",
            "Restore Working PO from Copy.po": "Restore Working PO files from matching Copy.po backups.",
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
        if clean in {"Clear", "Disable Selected", "Hide", "Restore Working PO from Copy.po"}:
            return "warnButton"
        if clean in {"Move Compile", "Move Repack", "Move to Game", "Sync Selected Options", "Sync by Filename"}:
            return "deployButton"
        if clean in {"Save", "Save msgstr", "Apply", "Apply Wrap", "Create Missing Copy.po Backups"}:
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
        box = QGroupBox("Danganronpa file groups")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(5)

        top = QHBoxLayout()
        hint = QLabel("Choose which chapters/file groups this tab should target.")
        hint.setObjectName("muted")
        top.addWidget(hint)
        top.addStretch()
        all_btn = self._button("All", secondary=True)
        none_btn = self._button("None", secondary=True)
        top.addWidget(all_btn)
        top.addWidget(none_btn)
        outer.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        selected = set(self._initial_dr_options(tab_key))
        checks: dict[str, QCheckBox] = {}
        for index, option in enumerate(DR_FILE_OPTIONS):
            checkbox = QCheckBox(option.name)
            checkbox.setChecked(option.key in selected)
            checkbox.setToolTip(option.description or option.name)
            row, col = divmod(index, 4)
            grid.addWidget(checkbox, row, col)
            checks[option.key] = checkbox
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

        note = QLabel(
            "Set one Working folder for each Danganronpa file group. "
            "Tabs process the selected checkbox groups from their Working folders. "
            "Optional Extra paths on each tab are added only when their Extra toggle is on. "
            "Backup/Sync sends every selected Working folder to the shared Extracted destination."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

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
        self._path_row(general_form, "Extracted", "extracted_path")
        self._path_row(general_form, "Repack", "repack_path")
        self._path_row(general_form, "WAD Repack", "wad_repack_path")
        self._path_row(general_form, "Script", "script_path")
        self._path_row(general_form, "Game Folder", "game_folder_path")
        content_layout.addWidget(general_box)

        git_box = QGroupBox("Danganronpa Việt Hóa Git")
        git_form = QFormLayout(git_box)
        git_form.setSpacing(8)
        git_form.setContentsMargins(8, 8, 8, 8)
        self._git_path_row(git_form)
        git_note = QLabel(
            f"Repository: {DANGANVIETHOA_REPOSITORY_URL}\n"
            "Git Pull preserves local edits with rebase + autostash. "
            "Git Push opens a real CMD window, stages all repository files with git add ., "
            "creates a commit when needed, and pushes main to origin. "
            "The CMD window shows each step and stays open for inspection."
        )
        git_note.setObjectName("muted")
        git_note.setWordWrap(True)
        git_form.addRow("", git_note)
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
        outcome = {"label": f"{action_label} complete"}

        def done() -> None:
            button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self._active_log = None
            self._active_thread = None
            self._finish_task_progress(str(outcome["label"]))
            try:
                self._active_signals.remove(signals)
            except ValueError:
                pass

        signals.done.connect(done)

        def logwrite(text: str, tag: str = "") -> None:
            signals.log.emit(str(text), str(tag or ""))

        def progresswrite(done_count: int, total_count: int = 0, label: str = "") -> None:
            self._check_stop()
            signals.progress.emit(int(done_count), int(total_count), str(label or action_label))

        def worker() -> None:
            self._stop_event.clear()
            self._active_log = log
            try:
                self._check_stop()
                fn(logwrite, progresswrite)
                self._check_stop()
            except OperationCancelled:
                outcome["label"] = f"{action_label} stopped"
                logwrite("Stopped by user.", "warn")
            except Exception as exc:
                outcome["label"] = f"{action_label} failed"
                logwrite(f"ERROR: {exc}", "bad")
            finally:
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

    # ---------------- Mass Replace ----------------
    def _build_replace_tab(self) -> None:
        _tab, layout = self._new_tab("Mass Replace")
        self._dr_option_selector(layout, "replace")
        path_edit, include_extra = self._extra_path_row(layout, "replace", "Extra Folder/File", "last_path")
        rules_edit = self._path_row(layout, "Rules JSON", "rules_file", file=True)
        row = QHBoxLayout()
        dry_run = QCheckBox("Dry run")
        dry_run.setChecked(True)
        row.addWidget(dry_run)
        row.addStretch()
        run_btn = self._button("Run Replace")
        row.addWidget(run_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def run(logwrite, progresswrite):
            self._check_stop()
            paths = self._processing_paths("replace", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            rules = load_rules(rules_edit.text().strip())
            po_files = self._iter_unique_po_paths(paths)
            changes = []
            progresswrite(0, len(po_files), "Replacing PO files")
            for file_index, po_path in enumerate(po_files, start=1):
                self._check_stop()
                changes.extend(apply_rules_to_file(po_path, rules, dry_run=dry_run.isChecked()))
                progresswrite(file_index, len(po_files), f"Replace {po_path.name}")
            logwrite(f"Inputs: {len(paths)} | PO files: {len(po_files)}")
            logwrite(f"Rules loaded: {len(rules)}")
            logwrite(f"Changes: {len(changes)}", "good" if changes else "")
            for ch in changes[:300]:
                self._check_stop()
                logwrite(f"{ch.file.name} | {ch.msgctxt} | {ch.rule_id} | {ch.count}")
                logwrite(f"- {ch.before}", "warn")
                logwrite(f"+ {ch.after}", "good")
            if len(changes) > 300:
                logwrite(f"... {len(changes) - 300} more changes", "warn")
            if dry_run.isChecked():
                logwrite("Dry run only. Uncheck Dry run to write files.", "warn")

        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run))

    # ---------------- Rule Editor ----------------
    def _normalize_rule_dict(self, raw: dict | None = None) -> dict:
        raw = dict(raw or {})
        return {
            "id": str(raw.get("id") or raw.get("label") or ""),
            "enabled": bool(raw.get("enabled", True)),
            "priority": int(raw.get("priority") or 100),
            "speaker": raw.get("speaker", raw.get("character")) or "",
            "scope": raw.get("scope") or "",
            "find": str(raw.get("find", "")),
            "replace": str(raw.get("replace", "")),
            "whole_word": bool(raw.get("whole_word", False)),
            "case_sensitive": bool(raw.get("case_sensitive", True)),
            "stop_after": bool(raw.get("stop_after", False)),
            "notes": str(raw.get("notes", "")),
        }

    def _build_rule_editor_tab(self) -> None:
        _tab, layout = self._new_tab("Rule Editor")
        top = QHBoxLayout()
        rules_file = QLineEdit(str(self.config.get("rules_file", "rules/mass_replace_rules.json")))
        load_btn = self._button("Load", secondary=True)
        save_btn = self._button("Save", secondary=True)
        top.addWidget(rules_file, 1)
        top.addWidget(load_btn)
        top.addWidget(save_btn)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        rule_list = QListWidget()
        rule_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        form.setContentsMargins(8, 0, 0, 0)
        form.setSpacing(8)
        fields: dict[str, QWidget] = {}
        for name in ["id", "priority", "speaker", "scope", "find", "replace", "notes"]:
            edit = QLineEdit()
            fields[name] = edit
            form.addRow(name, edit)
        for name in ["enabled", "whole_word", "case_sensitive", "stop_after"]:
            chk = QCheckBox(name)
            fields[name] = chk
            form.addRow("", chk)
        splitter.addWidget(right)
        splitter.setSizes([470, 650])

        def label_for_rule(rule: dict) -> str:
            state = "ON " if rule.get("enabled", True) else "OFF"
            speaker = rule.get("speaker") or "GLOBAL"
            find = rule.get("find") or "<empty>"
            replace = rule.get("replace") or ""
            return f"{state} | {int(rule.get('priority') or 100):>4} | {speaker:<10} | {find} → {replace}"

        def selected_indices() -> list[int]:
            return sorted({rule_list.row(item) for item in rule_list.selectedItems()})

        def selected_index() -> int | None:
            indices = selected_indices()
            return indices[0] if indices else None

        def refresh_list(keep: list[int] | None = None) -> None:
            keep = selected_indices() if keep is None else keep
            rule_list.blockSignals(True)
            rule_list.clear()
            for rule in self.rule_list_data:
                item = QListWidgetItem(label_for_rule(rule))
                item.setForeground(QColor(GOOD if rule.get("enabled", True) else BAD))
                item.setBackground(QColor(TEAL_DARK if rule.get("enabled", True) else "#4a2938"))
                rule_list.addItem(item)
            for i in keep:
                if 0 <= i < rule_list.count():
                    rule_list.item(i).setSelected(True)
            rule_list.blockSignals(False)

        def write_rules_file(show_message: bool = False) -> None:
            path = Path(rules_file.text().strip())
            if not path:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            normalized = [self._normalize_rule_dict(r) for r in self.rule_list_data]
            path.write_text(json.dumps({"version": 2, "rules": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
            self.config["rules_file"] = str(path)
            save_config(self.config)
            if show_message:
                QMessageBox.information(self, "Rules", "Rules saved.")

        def collect_form() -> dict:
            data: dict = {}
            for name, widget in fields.items():
                if isinstance(widget, QCheckBox):
                    data[name] = widget.isChecked()
                elif isinstance(widget, QLineEdit):
                    data[name] = widget.text()
            try:
                data["priority"] = int(data.get("priority") or 100)
            except Exception:
                data["priority"] = 100
            return self._normalize_rule_dict(data)

        def load_selected() -> None:
            idx = selected_index()
            if idx is None or idx >= len(self.rule_list_data):
                return
            self.rule_loading_fields = True
            try:
                rule = self.rule_list_data[idx]
                for name, widget in fields.items():
                    if isinstance(widget, QCheckBox):
                        default = True if name in ("enabled", "case_sensitive") else False
                        widget.setChecked(bool(rule.get(name, default)))
                    elif isinstance(widget, QLineEdit):
                        widget.setText(str(rule.get(name, "")))
            finally:
                self.rule_loading_fields = False

        def apply_form(auto_save: bool = True) -> None:
            idx = selected_index()
            if idx is None or idx >= len(self.rule_list_data) or self.rule_loading_fields:
                return
            self.rule_list_data[idx] = collect_form()
            keep = selected_indices()
            refresh_list(keep)
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
            self.rule_auto_timer.start(140)

        def load_file() -> None:
            path = Path(rules_file.text().strip())
            if not path.exists():
                QMessageBox.warning(self, "Rules", "Rules file not found.")
                return
            loaded: list[dict] = []
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("rules", raw) if isinstance(raw, dict) else raw
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and isinstance(item.get("replace"), list):
                            loaded = [rule_to_dict(rule) for rule in load_rules(path)]
                            break
                        if isinstance(item, dict):
                            loaded.append(self._normalize_rule_dict(item))
            except Exception:
                loaded = [rule_to_dict(rule) for rule in load_rules(path)]
            self.rule_list_data = loaded
            refresh_list([0] if loaded else [])
            load_selected()
            self.config["rules_file"] = str(path)
            save_config(self.config)

        def add_rule() -> None:
            self.rule_list_data.append(self._normalize_rule_dict({"enabled": True, "priority": 100, "case_sensitive": True}))
            refresh_list([len(self.rule_list_data) - 1])
            load_selected()
            write_rules_file(False)

        def delete_rules() -> None:
            indices = selected_indices()
            if not indices:
                return
            for i in reversed(indices):
                if 0 <= i < len(self.rule_list_data):
                    self.rule_list_data.pop(i)
            refresh_list([min(indices[0], len(self.rule_list_data) - 1)] if self.rule_list_data else [])
            load_selected()
            write_rules_file(False)

        def set_enabled_for_selected(value: bool) -> None:
            indices = selected_indices()
            for i in indices:
                if 0 <= i < len(self.rule_list_data):
                    self.rule_list_data[i]["enabled"] = value
            refresh_list(indices)
            load_selected()
            write_rules_file(False)

        rule_list.itemSelectionChanged.connect(load_selected)
        for widget in fields.values():
            if isinstance(widget, QCheckBox):
                widget.stateChanged.connect(schedule_auto_update)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(schedule_auto_update)
        load_btn.clicked.connect(load_file)
        save_btn.clicked.connect(lambda: (apply_form(False), write_rules_file(True)))
        add_btn.clicked.connect(add_rule)
        delete_btn.clicked.connect(delete_rules)
        enable_btn.clicked.connect(lambda: set_enabled_for_selected(True))
        disable_btn.clicked.connect(lambda: set_enabled_for_selected(False))

        QTimer.singleShot(0, load_file)

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

        preset_info = QLabel("")
        preset_info.setObjectName("muted")
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
            preset_info.setText(
                f"Editing W{index + 1}; changes save automatically."
            )

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
        controls.addWidget(preset_info)
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
        theme_note = QLabel("☾ Sleepy gamer theme: EN results use dusty lavender, VI results use muted teal. Up/Down navigates the focused results table. Ctrl+S saves the current result; Ctrl+Z undoes editor text or the last saved Search edit.")
        theme_note.setObjectName("muted")
        theme_note.setWordWrap(True)
        layout.addWidget(theme_note)
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
        en_label = QLabel("English / msgid")
        en_label.setStyleSheet(f"color: {ACCENT_SOFT}; font-weight: 900;")
        right_layout.addWidget(en_label)
        msgid_box = QPlainTextEdit(); msgid_box.setReadOnly(True); msgid_box.setMinimumHeight(90)
        msgid_box.setStyleSheet(
            f"QPlainTextEdit {{ color: {WHITE}; background: {EN_BG}; border: 1px solid {PURPLE}; border-radius: 9px; }}"
        )
        msgid_box._clt_highlighter = CltHighlighter(msgid_box.document())  # keep highlighter alive
        right_layout.addWidget(msgid_box)
        vi_label = QLabel("Vietnamese / msgstr")
        vi_label.setStyleSheet(f"color: {TEAL}; font-weight: 900;")
        right_layout.addWidget(vi_label)
        msgstr_box = QPlainTextEdit(); msgstr_box.setMinimumHeight(150)
        msgstr_box.setStyleSheet(
            f"QPlainTextEdit {{ color: {WHITE}; background: {VI_BG}; border: 1px solid {TEAL}; border-radius: 9px; }}"
        )
        msgstr_box._clt_highlighter = CltHighlighter(msgstr_box.document())  # keep highlighter alive
        right_layout.addWidget(msgstr_box, 1)

        edit_buttons = QHBoxLayout()
        edit_buttons.setSpacing(4)
        open_btn = self._button("Open File", secondary=True)
        save_btn = self._button("Save msgstr")
        edit_buttons.addWidget(save_btn)
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
            if len(search_undo_stack) > 100:
                del search_undo_stack[:-100]

        def compact(text: str, limit: int = 1000) -> str:
            text = user_multiline_text(text)
            return text if len(text) <= limit else text[: limit - 1] + "…"

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

        def save_updates(
            updates: dict[int, str],
            *,
            record_undo: bool = True,
            progress_label: str = "Saving search changes",
        ) -> int:
            save_state["errors"] = []
            if not updates:
                return 0
            grouped: dict[Path, list[dict[str, object]]] = defaultdict(list)
            for idx, new_text in updates.items():
                if idx < 0 or idx >= len(self.search_results):
                    continue
                result = self.search_results[idx]
                old_text = result.msgstr
                normalized_new = unicodedata.normalize("NFC", new_text)
                if old_text == normalized_new:
                    continue
                grouped[result.file].append(
                    {
                        "index": idx,
                        "file": result.file,
                        "uid": result.uid,
                        "old": old_text,
                        "new": normalized_new,
                    }
                )
            if not grouped:
                return 0

            begin_progress(progress_label, len(grouped))
            successful_changes: list[dict[str, object]] = []
            for file_number, (path, changes) in enumerate(grouped.items(), start=1):
                try:
                    po = load_search_po(path)
                    by_uid = po.by_uid()
                    translations = {
                        str(change["uid"]): str(change["new"])
                        for change in changes
                        if str(change["uid"]) in by_uid
                    }
                    missing = len(changes) - len(translations)
                    changed_on_disk = 0
                    for uid, new_text in translations.items():
                        normalized = unicodedata.normalize("NFC", new_text)
                        entry = by_uid[uid]
                        if entry.msgstr != normalized:
                            entry.msgstr = normalized
                            changed_on_disk += 1
                    if changed_on_disk:
                        save_po(po, path)
                    cache_saved_po(path, po)
                    successful_changes.extend(
                        change
                        for change in changes
                        if str(change["uid"]) in by_uid
                        and by_uid[str(change["uid"])].msgstr == str(change["new"])
                    )
                    if missing:
                        save_state["errors"].append(
                            f"{path.name}: {missing} result entr{'y was' if missing == 1 else 'ies were'} not found"
                        )
                except Exception as exc:
                    search_save_cache.pop(self._path_key(path), None)
                    save_state["errors"].append(f"{path.name}: {exc}")
                update_progress(file_number, len(grouped), progress_label)

            changed_indices: list[int] = []
            for change in successful_changes:
                idx = int(change["index"])
                if 0 <= idx < len(self.search_results):
                    self.search_results[idx].msgstr = str(change["new"])
                    changed_indices.append(idx)

            if record_undo and successful_changes:
                search_undo_stack.append(successful_changes)
                trim_search_undo_stack()

            changed_rows: list[int] = []
            table.setUpdatesEnabled(False)
            try:
                for idx in sorted(set(changed_indices)):
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
            finish_progress(f"Saved {len(changed_indices)} result(s)")
            return len(changed_indices)

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
            updates: dict[int, str] = {}
            restored_indices: list[int] = []
            for change in action:
                idx = int(change.get("index", -1))
                if idx < 0 or idx >= len(self.search_results):
                    continue
                result = self.search_results[idx]
                expected_file = change.get("file")
                expected_uid = str(change.get("uid", ""))
                if expected_uid and result.uid != expected_uid:
                    continue
                if isinstance(expected_file, Path) and self._path_key(result.file) != self._path_key(expected_file):
                    continue
                updates[idx] = str(change.get("old", ""))
                restored_indices.append(idx)
            changed = save_updates(
                updates,
                record_undo=False,
                progress_label="Restoring previous text",
            )
            if restored_indices:
                select_result(restored_indices[0])
            status.setText(
                f"Undid {changed} saved Search change{'s' if changed != 1 else ''}."
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
        msgstr_box.installEventFilter(self)
        prev_btn.clicked.connect(lambda: find_step(-1))
        next_btn.clicked.connect(lambda: find_step(1))
        current_btn.clicked.connect(replace_current)
        selected_btn.clicked.connect(lambda: replace_indices(selected_result_indices()))
        all_btn.clicked.connect(lambda: replace_indices(list(range(len(self.search_results)))))

        undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), _tab)
        undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo_shortcut.activated.connect(undo_last_search_change)
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), _tab)
        save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_shortcut.activated.connect(save_current)
        self._search_shortcuts = [undo_shortcut, save_shortcut]


    # ---------------- Translafixer ----------------
    def _build_translafixer_tab(self) -> None:
        _tab, layout = self._new_tab("Translafixer")
        self._dr_option_selector(layout, "translafixer")
        note = QLabel(
            "Translafixer Source is used only for fixing/filling translations. "
            "Duplicate views scan only selected checkbox Working folders. "
            "PO Viewer suggestions scan all configured Working folders from Settings. "
            "Target .po files are rewritten when original text / msgid matches. Copy.po target files are skipped. "
            "Selected source files are never rewritten, even if they are inside the target folder."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        def build_path_picker(
            title: str,
            hint_text: str,
            config_key: str,
            attr_name: str,
            tooltip: str,
        ) -> tuple[QGroupBox, PathDropList, Callable[[], list[str]], Callable[[], None]]:
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            hint = QLabel(hint_text)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            box_layout.addWidget(hint)

            list_widget = PathDropList()
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
                po_cache[po_path] = load_po(po_path)
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
            if len(apply_undo_stack) > 100:
                del apply_undo_stack[:-100]
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
                    po_cache[po_path] = load_po(po_path)
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
        undo_apply_btn.clicked.connect(undo_duplicate_change)
        hide_group_btn.clicked.connect(hide_selected_groups)
        unhide_group_btn.clicked.connect(unhide_selected_groups)
        show_hidden_check.stateChanged.connect(lambda _state: toggle_hidden_visibility())
        save_btn.clicked.connect(save_changed)
        refresh_btn.clicked.connect(refresh_dialog)
        close_btn.clicked.connect(dialog.close)
        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), dialog)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(open_search_replace_dialog)
        undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), dialog)
        undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo_shortcut.activated.connect(undo_duplicate_change)
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), dialog)
        save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_shortcut.activated.connect(save_changed)
        wrap_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), dialog)
        wrap_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        wrap_shortcut.activated.connect(lambda: breakline_selected())
        dialog._find_shortcut = find_shortcut  # type: ignore[attr-defined]
        dialog._undo_shortcut = undo_shortcut  # type: ignore[attr-defined]
        dialog._save_shortcut = save_shortcut  # type: ignore[attr-defined]
        dialog._wrap_shortcut = wrap_shortcut  # type: ignore[attr-defined]
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
        note = QLabel(
            "Use ☑↻ to load .po files from selected checkbox Working folders. The Extra source is optional and only loads when Extra is checked. "
            "Choose a non-copy .po from the dropdown. Use Open PO to launch the currently viewed file in its default app. "
            "View English + Vietnamese side by side, edit only Vietnamese, wrap msgstr lines. TF fill uses Translafixer Source; suggestions use all Settings Working folders. "
            "Shortcuts: Ctrl+E/F2 = focus Vietnamese editor, Ctrl+S = save, Ctrl+Z repeatedly undoes current and earlier PO text edits, Ctrl+Up/Down = entry, Ctrl+Enter = wrap selected/current, "
            "Shift+Up/Down = file, Ctrl+1..9 = apply suggestion, Ctrl+0 = refresh suggestions. These work while editing Vietnamese too. "
            "Visible character counts are shown per real line above each language field; spaces and punctuation count, while CLT/control tags and placeholders are ignored. Translafixer matching ignores CLT tags."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        source_row = QHBoxLayout()
        source_label = QLabel("Extra source")
        source_label.setMinimumWidth(92)
        source_label.setStyleSheet("font-weight:700;")
        initial_source = str(self.config.get("po_viewer_source") or self.config.get("po_viewer_file", ""))
        source_edit = QLineEdit(initial_source)
        source_edit.setPlaceholderText("Optional extra .po file(s) or folder; ignored unless Extra is checked...")
        source_extra_check = QCheckBox("Extra")
        source_extra_check.setChecked(bool(self.config.get(self._include_extra_config_key("po_viewer"), False)))
        source_extra_check.setToolTip("When on, the manual PO source is loaded together with selected Working folders.")
        open_po_btn = self._tool_button("", "Open current .po in the system default app", QStyle.StandardPixmap.SP_FileIcon)
        browse_files_btn = self._tool_button("", "Pick extra .po file(s)", QStyle.StandardPixmap.SP_DialogOpenButton)
        browse_folder_btn = self._tool_button("", "Pick extra folder", QStyle.StandardPixmap.SP_DirOpenIcon)
        load_btn = self._tool_button("☑↻", "Load selected checkbox Working folders + enabled Extra source", width=44)
        save_btn = self._tool_button("", "Save current .po", QStyle.StandardPixmap.SP_DialogSaveButton)
        source_row.addWidget(source_label)
        source_row.addWidget(source_extra_check)
        source_row.addWidget(source_edit, 1)
        source_row.addWidget(open_po_btn)
        source_row.addWidget(browse_files_btn)
        source_row.addWidget(browse_folder_btn)
        source_row.addWidget(load_btn)
        source_row.addWidget(save_btn)
        layout.addLayout(source_row)

        file_row = QHBoxLayout()
        file_label = QLabel("File")
        file_label.setMinimumWidth(80)
        file_label.setStyleSheet("font-weight:700;")
        file_combo = QComboBox()
        file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        file_combo.setMinimumContentsLength(32)
        file_combo.setEnabled(False)
        file_combo.addItem("No .po files loaded", None)
        file_row.addWidget(file_label)
        file_row.addWidget(file_combo, 1)
        layout.addLayout(file_row)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        wrap_view_btn = self._tool_button("↔ ON", "Toggle visual wrap", width=46)
        clt_color_btn = self._tool_button("CLT", "Toggle CLT tag/color view", width=42)
        wrap_all_btn = self._tool_button("All", "Wrap all translations with the active preset", width=38)
        fill_btn = self._tool_button("TF", "Fill from Translafixer sources", width=38)
        gemini_selected_btn = self._tool_button("AI", "Translate selected rows with Gemini API", width=38)
        search_replace_btn = self._tool_button("⌕", "Search / replace (Ctrl+F)", width=34)
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
        tools.addWidget(search_replace_btn)
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
        table.verticalHeader().setDefaultSectionSize(44)
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
        speaker_label = QLabel("Speaker: —")
        speaker_label.setObjectName("muted")
        speaker_label.setWordWrap(True)
        speaker_label.setStyleSheet(f"font-weight:900; color:{ACCENT_SOFT};")
        en_character_count_label = QLabel("—")
        en_character_count_label.setObjectName("muted")
        en_character_count_label.setWordWrap(True)
        en_character_count_label.setToolTip("Visible character count for each English line, from top to bottom. Spaces and punctuation count; CLT/control tags and placeholders are ignored.")
        en_character_count_label.setStyleSheet(f"font-weight:800; color:{ACCENT_SOFT};")
        vi_character_count_label = QLabel("—")
        vi_character_count_label.setObjectName("muted")
        vi_character_count_label.setWordWrap(True)
        vi_character_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vi_character_count_label.setToolTip("Visible character count for each Vietnamese line, from top to bottom. Spaces and punctuation count; CLT/control tags and placeholders are ignored.")
        vi_character_count_label.setStyleSheet(f"font-weight:800; color:{TEAL};")

        speaker_count_row = QWidget()
        speaker_count_layout = QHBoxLayout(speaker_count_row)
        speaker_count_layout.setContentsMargins(0, 0, 0, 0)
        speaker_count_layout.setSpacing(8)
        speaker_count_layout.addWidget(speaker_label, 1)
        speaker_count_layout.addWidget(
            vi_character_count_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        detail.addWidget(labeled_box("English / original — read only", en_box, en_character_count_label))
        detail.addWidget(labeled_box("Vietnamese / translation — editable", vi_box, speaker_count_row))
        detail.setSizes([1, 1])
        split.addWidget(detail)
        split.setSizes([430, 155])

        suggest_group = QGroupBox("Suggestions")
        suggest_layout = QVBoxLayout(suggest_group)
        suggest_note = QLabel("From all configured Settings Working folders. Match percentage is raw: CLT tags, line breaks, spacing, punctuation, and case all count. >95% is green. Ctrl+1..9 apply, Ctrl+0 refresh.")
        suggest_note.setObjectName("muted")
        suggest_note.setWordWrap(True)
        suggest_layout.addWidget(suggest_note)
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
        suggest_controls.addWidget(QLabel("Min match"))
        suggest_controls.addWidget(suggest_min_score)
        suggest_controls.addWidget(refresh_suggest_btn)
        suggest_controls.addWidget(apply_suggest_btn)
        suggest_controls.addStretch()
        suggest_layout.addLayout(suggest_controls)
        suggest_group.setMinimumWidth(280)

        content_split = QSplitter(Qt.Orientation.Horizontal)
        content_split.addWidget(split)
        content_split.addWidget(suggest_group)
        content_split.setSizes([700, 300])
        content_split.setStretchFactor(0, 7)
        content_split.setStretchFactor(1, 3)
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
            return "  |  ".join(str(count) for count in counts)

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

        def trim_po_undo_stack() -> None:
            stack = _po_undo_stack()
            if len(stack) > 100:
                del stack[:-100]

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
                return
            focus = QApplication.focusWidget()
            if (
                focus is table
                or focus is suggestions_list
                or (focus is not None and (table.isAncestorOf(focus) or suggestions_list.isAncestorOf(focus)))
            ):
                if self._undo_text_editor(vi_box):
                    return
            commit_pending_text_undo()
            stack = _po_undo_stack()
            if not stack:
                set_status("Nothing to undo.")
                return
            action = stack.pop()
            raw_changes = action.get("changes", [])
            if not isinstance(raw_changes, list):
                set_status("Nothing to undo.")
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
                return
            try:
                keep_vi_focus = _focus_is_vi_editor()
            except Exception:
                keep_vi_focus = False
            select_entry_row(restored_rows[0], center=True, keep_vi_focus=keep_vi_focus)
            refresh_suggestions_for_row(table.currentRow())
            set_status(f"Undid {len(restored_rows)} PO edit{'s' if len(restored_rows) != 1 else ''}. Save when ready.")

        def _same_file(a: Path | None, b: Path | None) -> bool:
            if a is None or b is None:
                return False
            try:
                return a.resolve(strict=False) == b.resolve(strict=False)
            except OSError:
                return str(a) == str(b)

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
            if index is None:
                return
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
                candidates = index.suggest(target.msgid, min_score=min_score, limit=10)
                suggestions = [
                    item for item in candidates
                    if not (_same_file(item.file, current) and item.uid == target.uid)
                ][:5]
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
            set_entry_translation(row, translation, undo_label="suggestion")

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
                    speaker_label.setText("Speaker: —")
                    speaker_label.setToolTip("")
                    en_character_count_label.setText("—")
                    vi_character_count_label.setText("—")
                    return
                entry = po.entries[row]  # type: ignore[union-attr]
                speaker = entry.speaker.strip() or "—"
                speaker_label.setText(f"Speaker: {speaker}")
                speaker_label.setToolTip(entry.msgctxt or "")
                if en_box.toPlainText() != entry.msgid:
                    en_box.setPlainText(entry.msgid)
                if vi_box.toPlainText() != entry.msgstr:
                    vi_box.setPlainText(entry.msgstr)
                    self._clear_text_editor_undo(vi_box)
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
            loaded = load_po(path)
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
            clear_pending_text_undo()
            _po_undo_stack().clear()
            self._clear_text_editor_undo(vi_box)
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

        def toggle_visual_wrap() -> None:
            state["visual_wrap"] = not bool(state.get("visual_wrap"))
            enabled = bool(state["visual_wrap"])
            table.setWordWrap(enabled)
            mode = QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap
            en_box.setLineWrapMode(mode)
            vi_box.setLineWrapMode(mode)
            wrap_view_btn.setText(f"↔ {'ON' if enabled else 'OFF'}")
            wrap_view_btn.setToolTip(f"Visual wrap: {'ON' if enabled else 'OFF'}")
            set_status("Visual line wrap enabled." if enabled else "Visual line wrap disabled.")

        def set_clt_color_mode(enabled: bool, *, persist: bool = True, quiet: bool = False) -> None:
            state["clt_color_mode"] = enabled
            clt_color_btn.setText(f"CLT {'C' if enabled else 'T'}")
            clt_color_btn.setToolTip(f"CLT view: {'Color' if enabled else 'Tags'}")
            en_box._clt_highlighter.set_color_spans(enabled)
            vi_box._clt_highlighter.set_color_spans(enabled)
            for row in range(table.rowCount()):
                refresh_row_style(row)
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

        def translate_selected_with_gemini_api() -> None:
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
                QMessageBox.warning(self, "PO Viewer", "Enter the Gemini API key in the Gemini Web tab, or set GEMINI_API_KEY.")
                return
            prompt = SYSTEM_INSTRUCTIONS
            model = str(self.config.get("gemini_api_model", "gemini-2.5-flash")).strip() or "gemini-2.5-flash"
            batch_size = max(1, int(self.config.get("gemini_web_max_entries", DEFAULT_MAX_ENTRIES_PER_BATCH)))
            sleep_seconds = float(self.config.get("gemini_api_sleep_seconds", 1.0))
            entries = [po.entries[row] for row in rows if 0 <= row < len(po.entries)]  # type: ignore[union-attr]
            self._begin_task_progress("Gemini API PO entries", len(entries))
            self._pump_task_progress()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                client = GeminiApiClient(api_key=api_key, model=model, prompt=prompt)

                def report_gemini_progress(done: int, total: int) -> None:
                    self._update_task_progress(done, total, "Gemini API PO entries")
                    self._pump_task_progress()

                translations, errors = translate_entries_with_client(
                    entries,
                    client,
                    batch_size=batch_size,
                    sleep_seconds=sleep_seconds,
                    allow_partial=False,
                    prompt=prompt,
                    progress=report_gemini_progress,
                )
                changed = 0
                by_uid = {entry.uid: row for row, entry in zip(rows, entries)}
                begin_po_undo_batch("gemini")
                try:
                    for uid, translation in translations.items():
                        row = by_uid.get(uid)
                        if row is not None and set_entry_translation(row, translation, undo_label="gemini"):
                            changed += 1
                finally:
                    end_po_undo_batch()
                refresh_suggestions_for_row(table.currentRow())
            except Exception as exc:
                self._finish_task_progress("Gemini API failed")
                QMessageBox.critical(self, "Gemini API", f"Gemini API translation failed:\n{exc}")
                return
            finally:
                QApplication.restoreOverrideCursor()
            self._finish_task_progress(f"Gemini translated {changed} PO entr{'y' if changed == 1 else 'ies'}")
            if errors:
                preview = "\n".join(f"{e.msgctxt or e.uid}: {e.reason}" for e in errors[:8])
                more = f"\n... {len(errors) - 8} more" if len(errors) > 8 else ""
                QMessageBox.warning(self, "Gemini API", f"Translated {changed} entr{'y' if changed == 1 else 'ies'}, with {len(errors)} validation issue(s):\n{preview}{more}")
            else:
                set_status(f"Gemini API translated {changed} selected entr{'y' if changed == 1 else 'ies'}. Save when ready.")

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
        search_replace_btn.clicked.connect(open_search_replace_dialog)
        dup_ref_btn.clicked.connect(lambda: self._open_reference_duplicates_dialog(tab_key="po_viewer"))
        refresh_suggest_btn.clicked.connect(lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        apply_suggest_btn.clicked.connect(apply_selected_suggestion)
        suggestions_list.itemDoubleClicked.connect(lambda _item: apply_selected_suggestion())
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
            add_shortcut("Shift+Up", lambda: switch_file(-1), parent=nav_parent)
            add_shortcut("Shift+Down", lambda: switch_file(1), parent=nav_parent)
        add_shortcut("Ctrl+E", focus_vi_editor)
        add_shortcut("F2", focus_vi_editor)
        add_shortcut("Ctrl+S", save_file)
        add_shortcut("Ctrl+Z", undo_last_po_change)
        add_shortcut("Ctrl+F", open_search_replace_dialog)
        add_shortcut("Ctrl+Return", wrap_selected)
        add_shortcut("Ctrl+Enter", wrap_selected)
        for suggestion_number in range(1, 10):
            add_shortcut(f"Ctrl+{suggestion_number}", lambda n=suggestion_number: apply_suggestion_number(n))
        add_shortcut("Ctrl+0", lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        self._po_viewer_shortcuts = shortcuts

        set_clt_color_mode(bool(state.get("clt_color_mode")), persist=False, quiet=True)

        initial_paths = self._processing_paths("po_viewer", extra_edit=source_edit, include_extra=source_extra_check, require_any=False)
        if initial_paths:
            set_file_list(initial_paths, "; ".join(str(path) for path in initial_paths), auto_load=False, quiet=True, update_source_edit=False)

    # ---------------- Gemini Web / API ----------------
    def _build_translate_tab(self) -> None:
        _tab, layout = self._new_tab("Gemini Web")
        self._dr_option_selector(layout, "gemini_web")
        path_edit, include_extra = self._extra_path_row(layout, "gemini_web", "Extra folder", "last_path")

        form = QFormLayout()
        mode_combo = QComboBox()
        mode_combo.addItem("Gemini Web tab", "web")
        mode_combo.addItem("Gemini API key", "api")
        saved_mode = str(self.config.get("gemini_translate_mode", "web"))
        mode_combo.setCurrentIndex(1 if saved_mode == "api" else 0)
        form.addRow("Mode", mode_combo)

        cdp_edit = QLineEdit(str(self.config.get("gemini_web_cdp_url", "http://localhost:9222")))
        form.addRow("Chrome CDP", cdp_edit)

        api_key_wrap = QWidget()
        api_key_row = QHBoxLayout(api_key_wrap)
        api_key_row.setContentsMargins(0, 0, 0, 0)
        api_key_toggle = QCheckBox("Use Gemini API key")
        api_key_toggle.setChecked(bool(self.config.get("gemini_api_use_key", False)) or saved_mode == "api")
        api_key_edit = QLineEdit(str(self.config.get("gemini_api_key", "")))
        api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_edit.setPlaceholderText("Paste API key here, or leave blank and set GEMINI_API_KEY")
        api_key_row.addWidget(api_key_toggle)
        api_key_row.addWidget(api_key_edit, 1)
        form.addRow("Gemini API", api_key_wrap)

        api_model_edit = QLineEdit(str(self.config.get("gemini_api_model", self.config.get("gemini_model", "gemini-2.5-flash"))))
        form.addRow("API model", api_model_edit)

        layout.addLayout(form)

        grid = QGridLayout()
        max_files = QSpinBox(); max_files.setRange(0, 9999); max_files.setValue(int(self.config.get("gemini_web_max_files", 59)))
        max_lines = QSpinBox(); max_lines.setRange(1, 9999); max_lines.setValue(int(self.config.get("gemini_web_max_lines", 600)))
        max_entries = QSpinBox(); max_entries.setRange(1, 999); max_entries.setValue(int(self.config.get("gemini_web_max_entries", DEFAULT_MAX_ENTRIES_PER_BATCH)))
        wait_seconds = QDoubleSpinBox(); wait_seconds.setRange(0.0, 999.0); wait_seconds.setDecimals(1); wait_seconds.setValue(float(self.config.get("gemini_web_wait_seconds", 8.0)))
        timeout_seconds = QSpinBox(); timeout_seconds.setRange(1, 9999); timeout_seconds.setValue(int(self.config.get("gemini_web_timeout_seconds", 180)))
        retries = QSpinBox(); retries.setRange(0, 99); retries.setValue(int(self.config.get("gemini_web_retries", DEFAULT_BATCH_RETRIES)))
        controls = [("Max files", max_files), ("Max lines", max_lines), ("Max entries", max_entries), ("Wait", wait_seconds), ("Timeout", timeout_seconds), ("Retries", retries)]
        for i, (label, widget) in enumerate(controls):
            grid.addWidget(QLabel(label), 0, i)
            grid.addWidget(widget, 1, i)
        layout.addLayout(grid)

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
            return str(mode_combo.currentData()) == "api" or api_key_toggle.isChecked()

        def sync_mode_ui() -> None:
            is_api = api_mode_enabled()
            cdp_edit.setEnabled(not is_api)
            timeout_seconds.setEnabled(not is_api)
            retries.setEnabled(not is_api)
            max_lines.setEnabled(not is_api)
            rename_dupes.setEnabled(not is_api)
            backup_missing.setEnabled(not is_api)
            rename_folders.setEnabled(not is_api)
            api_key_edit.setEnabled(is_api)
            api_model_edit.setEnabled(is_api)
            chrome_btn.setEnabled(not is_api)
            run_btn.setText("Run Gemini API" if is_api else "Run Gemini Web")

        def save_web_config() -> None:
            self.config["gemini_translate_mode"] = "api" if api_mode_enabled() else "web"
            self.config["gemini_web_cdp_url"] = cdp_edit.text().strip() or "http://localhost:9222"
            self.config["gemini_web_max_files"] = max_files.value()
            self.config["gemini_web_max_lines"] = max_lines.value()
            self.config["gemini_web_max_entries"] = max_entries.value()
            self.config["gemini_web_wait_seconds"] = wait_seconds.value()
            self.config["gemini_web_timeout_seconds"] = timeout_seconds.value()
            self.config["gemini_web_retries"] = retries.value()
            self.config["gemini_api_use_key"] = api_key_toggle.isChecked()
            self.config["gemini_api_key"] = api_key_edit.text().strip()
            self.config["gemini_api_model"] = api_model_edit.text().strip() or "gemini-2.5-flash"
            self.config["gemini_api_sleep_seconds"] = wait_seconds.value()
            save_config(self.config)

        def run_web(logwrite, progresswrite):
            save_web_config()
            paths = self._processing_paths("gemini_web", extra_edit=path_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            limit = max_files.value()
            remaining = None if limit <= 0 else limit
            total_files = 0
            total_translated = 0
            total_errors = 0
            for input_path in paths:
                self._check_stop()
                if remaining is not None and remaining <= 0:
                    break
                logwrite(f"Gemini Web input: {input_path}")
                result = run_gemini_web_path(
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
                    progress=lambda done, total, path: progresswrite(done, total, f"Gemini Web {path.name}"),
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
            client = GeminiApiClient(api_key=api_key, model=api_model_edit.text().strip() or "gemini-2.5-flash", prompt=prompt)
            limit = max_files.value()
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
            progresswrite(0, len(po_files), "Gemini API files")
            for idx, po_path in enumerate(po_files, start=1):
                self._check_stop()
                progresswrite(idx - 1, len(po_files), f"Gemini API {po_path.name}")
                logwrite(f"[{idx}/{len(po_files)}] Gemini API translating {po_path}")
                changed, errors = translate_file_with_client(
                    po_path,
                    client,
                    batch_size=max_entries.value(),
                    sleep_seconds=wait_seconds.value(),
                    allow_partial=bool(allow_state["value"]),
                    prompt=prompt,
                )
                total_changed += changed
                total_errors += len(errors)
                tag = "good" if not errors else "warn"
                logwrite(f"  applied={changed} | errors={len(errors)}", tag)
                for e in errors[:40]:
                    logwrite(f"  {e.uid} | {e.msgctxt} | {e.reason}", "bad")
                if len(errors) > 40:
                    logwrite(f"  ... {len(errors) - 40} more errors", "warn")
                progresswrite(idx, len(po_files), f"Gemini API {po_path.name}")
            logwrite(f"Total translated: {total_changed}", "good")
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
            cmd = open_chrome_debug(cdp_url=cdp_edit.text().strip() or "http://localhost:9222", user_data_dir=DEFAULT_CHROME_USER_DATA_DIR)
            logwrite("Chrome opened with remote debugging.", "good")
            logwrite("Login to Gemini in that Chrome window, then click Run Gemini Web.")
            logwrite("Command: " + " ".join(str(x) for x in cmd))
            progresswrite(1, 1, "Chrome opened")

        mode_combo.currentIndexChanged.connect(lambda _idx: (api_key_toggle.setChecked(str(mode_combo.currentData()) == "api"), sync_mode_ui()))
        api_key_toggle.stateChanged.connect(lambda _state: sync_mode_ui())
        api_key_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_key", text.strip()))
        api_model_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_model", text.strip()))
        for widget in [cdp_edit, api_key_edit, api_model_edit]:
            widget.editingFinished.connect(save_web_config)
        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run_selected_mode))
        chrome_btn.clicked.connect(lambda: self._run_threaded(chrome_btn, log, open_chrome))
        sync_mode_ui()

    # ---------------- Backup / Sync ----------------
    def _choose_multiple_folders(self, title: str) -> list[str]:
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        # QFileDialog internals use QListView/QTreeView; set selection mode dynamically.
        for view in dialog.findChildren(QAbstractItemView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        return dialog.selectedFiles() if dialog.exec() else []

    def _build_backup_tab(self) -> None:
        _tab, layout = self._new_tab("Backup / Sync")
        self._dr_option_selector(layout, "backup_sync")
        backup_edit, include_extra = self._extra_path_row(layout, "backup_sync", "Extra backup path", "last_path")
        source_edit = self._path_row(layout, "Manual sync source", "sync_source")
        target_edit = self._path_row(layout, "Manual sync target", "sync_target")

        sync_hint = QLabel("Backup and restore use selected Settings > Working folders. Extra backup path is included only when its toggle is on. Selected option sync copies every selected Working folder to the shared Settings > Extracted destination. Manual sync by filename still uses its explicit source/target pair.")
        sync_hint.setObjectName("muted")
        sync_hint.setWordWrap(True)
        layout.addWidget(sync_hint)

        restore_group = QGroupBox("Restore paths")
        restore_layout = QHBoxLayout(restore_group)
        restore_list = PathDropList()
        restore_layout.addWidget(restore_list, 1)
        restore_buttons = QVBoxLayout()
        add_btn = self._button("Add Folders", secondary=True)
        remove_btn = self._button("Remove", secondary=True)
        clear_btn = self._button("Clear", secondary=True)
        restore_buttons.addWidget(add_btn)
        restore_buttons.addWidget(remove_btn)
        restore_buttons.addWidget(clear_btn)
        restore_buttons.addStretch()
        restore_layout.addLayout(restore_buttons)
        layout.addWidget(restore_group)

        hint = QLabel("Drag folders or - Copy.po files into the restore list. Restore overwrites working .po from matching - Copy.po. Copy.po files are never changed.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        restore_paths: list[str] = list(self.config.get("restore_copy_paths", []))

        def refresh_restore() -> None:
            restore_list.clear()
            for path in restore_paths:
                restore_list.addItem(path)

        def save_restore() -> None:
            self.config["restore_copy_paths"] = restore_paths
            save_config(self.config)

        def add_restore_paths(paths: list[str]) -> None:
            added = 0
            for raw in paths:
                try:
                    p = Path(str(raw)).expanduser()
                except Exception:
                    continue
                if not p.exists():
                    continue
                if p.is_file() and p.suffix.lower() != ".po":
                    continue
                text = str(p.resolve()) if p.exists() else str(p)
                if text not in restore_paths:
                    restore_paths.append(text)
                    added += 1
            if added:
                refresh_restore()
                save_restore()

        def remove_restore() -> None:
            rows = sorted({restore_list.row(item) for item in restore_list.selectedItems()}, reverse=True)
            for row in rows:
                if 0 <= row < len(restore_paths):
                    restore_paths.pop(row)
            refresh_restore()
            save_restore()

        add_btn.clicked.connect(lambda: add_restore_paths(self._choose_multiple_folders("Select restore folders")))
        remove_btn.clicked.connect(remove_restore)
        clear_btn.clicked.connect(lambda: (restore_paths.clear(), refresh_restore(), save_restore()))
        restore_list.pathsDropped.connect(add_restore_paths)
        refresh_restore()

        row = QHBoxLayout()
        row.addStretch()
        backup_btn = self._button("Create Missing Copy.po Backups")
        option_sync_btn = self._button("Sync Selected Options")
        sync_btn = self._button("Sync by Filename", secondary=True)
        move_compile_btn = self._button("Move Repack")
        move_game_btn = self._button("Move to Game")
        restore_btn = self._button("Restore Working PO from Copy.po")
        row.addWidget(backup_btn)
        row.addWidget(option_sync_btn)
        row.addWidget(sync_btn)
        row.addWidget(move_compile_btn)
        row.addWidget(move_game_btn)
        row.addWidget(restore_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def backup(logwrite, progresswrite):
            self._check_stop()
            paths = self._processing_paths("backup_sync", extra_edit=backup_edit, include_extra=include_extra, logwrite=logwrite)
            if not paths:
                return
            total = 0
            progresswrite(0, 0, "Discovering PO files for backup")
            for input_path in paths:
                self._check_stop()
                written = make_backups(
                    input_path,
                    overwrite=False,
                    progress=lambda done, total_files, path: progresswrite(done, total_files, f"Backup {path.name}"),
                )
                total += written
                logwrite(f"{input_path}: wrote {written} missing Copy.po backup(s)", "good" if written else "warn")
            logwrite(f"Missing Copy.po backups written: {total}", "good")
            logwrite("Existing Copy.po files were not touched.", "warn")

        def sync(logwrite, progresswrite):
            self._check_stop()
            progresswrite(0, 0, "Scanning sync folders")
            result = sync_by_filename_report(
                source_edit.text().strip(),
                target_edit.text().strip(),
                progress=lambda done, total, path: progresswrite(done, total, f"Manual sync {path.name}"),
            )

            def log_paths(title: str, paths: list[Path], level: str = "warn", *, max_items: int = 200) -> None:
                if not paths:
                    return
                logwrite(f"{title}: {len(paths)}", level)
                for path in paths[:max_items]:
                    logwrite(f"  - {path}", level)
                if len(paths) > max_items:
                    logwrite(f"  ... {len(paths) - max_items} more", level)

            def log_pairs(title: str, pairs: list[tuple[Path, Path]], level: str = "info", *, max_items: int = 200) -> None:
                if not pairs:
                    return
                logwrite(f"{title}: {len(pairs)}", level)
                for src, target in pairs[:max_items]:
                    logwrite(f"  - {src} -> {target}", level)
                if len(pairs) > max_items:
                    logwrite(f"  ... {len(pairs) - max_items} more", level)

            logwrite(f"Source files scanned: {result.source_files}")
            logwrite(f"Target files scanned: {result.target_files}")
            if result.duplicate_source_names:
                logwrite(f"Duplicate source filenames skipped: {result.duplicate_source_names}", "warn")
                log_paths("Duplicate source files not pasted", result.duplicate_source_files, "warn")
            if result.skipped_identical:
                logwrite(f"Identical files skipped: {result.skipped_identical}")
                log_pairs("Identical pairs skipped", result.skipped_identical_files, "info")
            if result.skipped_self:
                logwrite(f"Self-copy skipped: {result.skipped_self}", "warn")
                log_pairs("Self-copy pairs skipped", result.skipped_self_files, "warn")
            log_pairs("Copied source -> target", result.copied_files, "good")
            log_paths("Source files with no target filename match (not pasted)", result.source_without_target, "warn")
            log_paths("Target files with no source filename match (not found in source)", result.target_without_source, "warn")
            logwrite(f"Files synced: {result.copied}", "good" if result.copied else "warn")

        def sync_selected_options(logwrite, progresswrite):
            selected = self._selected_dr_options("backup_sync")
            if not selected:
                logwrite("No Danganronpa file groups selected.", "warn")
                return
            extracted_folder = str(self.config.get("extracted_path", "")).strip()
            if not extracted_folder:
                logwrite("Set Settings > Extracted first.", "warn")
                return
            total_matched = total_copied = total_errors = 0
            progresswrite(0, len(selected), "Syncing selected groups")
            for option_index, option_key in enumerate(selected, start=1):
                self._check_stop()
                label = option_name(option_key)
                progresswrite(option_index - 1, len(selected), f"Sync {label}")
                working_folder = str(self.config.get(f"working_{option_key}_path", "")).strip()
                filter_by_option = False

                # Backward-compatible fallback for older configs. Dedicated Working
                # paths are preferred because Extracted is the shared destination.
                if not working_folder:
                    legacy_root = str(self.config.get("game_folder_path", "")).strip()
                    if legacy_root:
                        working_folder = legacy_root
                        filter_by_option = True
                        logwrite(f"{label}: using legacy Settings > Game Folder fallback. Set Working {label} for dedicated sync source.", "warn")

                if not working_folder:
                    logwrite(f"Skip {label}: set Settings > Working {label} first.", "warn")
                    progresswrite(option_index, len(selected), f"Skipped {label}")
                    continue
                try:
                    result = sync_option_from_working_folder(
                        working_folder,
                        extracted_folder,
                        option_key,
                        filter_by_option=filter_by_option,
                        progress=lambda done, total, path, label=label: progresswrite(done, total, f"Sync {label}: {path.name}"),
                    )
                except Exception as exc:
                    logwrite(f"ERR {label}: {exc}", "bad")
                    total_errors += 1
                    progresswrite(option_index, len(selected), f"Failed {label}")
                    continue
                total_matched += result.matched
                total_copied += result.copied
                total_errors += len(result.errors)
                tag = "good" if result.copied else "warn"
                logwrite(
                    f"{label}: source={result.source_root}, extracted={result.target_root}, matched={result.matched}, copied={result.copied}, identical={result.skipped_identical}, self={result.skipped_self}, errors={len(result.errors)}",
                    tag,
                )
                for src, dest in result.copied_files[:100]:
                    logwrite(f"  copy: {src} -> {dest}", "good")
                if len(result.copied_files) > 100:
                    logwrite(f"  ... {len(result.copied_files) - 100} more copied", "good")
                for src, err in result.errors[:50]:
                    logwrite(f"  ERR {src}: {err}", "bad")
                if len(result.errors) > 50:
                    logwrite(f"  ... {len(result.errors) - 50} more errors", "bad")
                progresswrite(option_index, len(selected), f"Synced {label}")
            logwrite(f"Selected option sync done. matched={total_matched}, copied={total_copied}, errors={total_errors}", "good" if total_errors == 0 else "warn")

        def move_compile(logwrite, progresswrite):
            repack = str(self.config.get("repack_path", "")).strip()
            script = str(self.config.get("script_path", "")).strip()
            wad_repack = str(self.config.get("wad_repack_path", "")).strip()
            if not repack or not script:
                logwrite("Set Settings > Repack and Settings > Script first.", "warn")
                return
            self._check_stop()
            progresswrite(0, 0, "Scanning Repack files")
            result = move_repack_to_script(
                repack,
                script,
                wad_repack_folder=wad_repack or None,
                progress=lambda done, total, path: progresswrite(done, total, f"Repack → Script {path.name}"),
            )
            logwrite(f"Repack files scanned: {result.scanned}")
            if result.skipped_wad_repack:
                logwrite(f"WAD Repack files skipped: {result.skipped_wad_repack}", "warn")
            if result.skipped_identical:
                logwrite(f"Identical Script files skipped: {result.skipped_identical}", "info")
            for src, dest in result.moved_files[:200]:
                self._check_stop()
                logwrite(f"  copy: {src} -> {dest}", "good")
            if len(result.moved_files) > 200:
                logwrite(f"  ... {len(result.moved_files) - 200} more copied", "good")
            for src, err in result.errors[:80]:
                logwrite(f"  ERR {src}: {err}", "bad")
            if len(result.errors) > 80:
                logwrite(f"  ... {len(result.errors) - 80} more errors", "bad")
            logwrite(f"Copied Repack files to Script: {result.moved}", "good" if result.moved else "warn")
            if result.overwritten:
                logwrite(f"Overwritten existing Script files: {result.overwritten}", "warn")
            if result.errors:
                logwrite(f"Move Repack errors: {len(result.errors)}", "bad")

        def move_to_game(logwrite, progresswrite):
            wad_repack = str(self.config.get("wad_repack_path", "")).strip()
            game_folder = str(self.config.get("game_folder_path", "")).strip()
            if not wad_repack or not game_folder:
                logwrite("Set Settings > WAD Repack and Settings > Game Folder first.", "warn")
                return
            self._check_stop()
            progresswrite(0, 0, "Scanning WAD Repack files")
            result = copy_wad_repack_to_game(
                wad_repack,
                game_folder,
                progress=lambda done, total, path: progresswrite(done, total, f"WAD → Game {path.name}"),
            )
            logwrite(f"WAD Repack files scanned: {result.scanned}")
            if result.skipped_identical:
                logwrite(f"Identical Game Folder files skipped: {result.skipped_identical}", "info")
            for src, dest in result.moved_files[:200]:
                self._check_stop()
                logwrite(f"  copy: {src} -> {dest}", "good")
            if len(result.moved_files) > 200:
                logwrite(f"  ... {len(result.moved_files) - 200} more copied", "good")
            for src, err in result.errors[:80]:
                logwrite(f"  ERR {src}: {err}", "bad")
            if len(result.errors) > 80:
                logwrite(f"  ... {len(result.errors) - 80} more errors", "bad")
            logwrite(f"Copied WAD Repack files to Game Folder: {result.moved}", "good" if result.moved else "warn")
            if result.overwritten:
                logwrite(f"Overwritten existing Game Folder files: {result.overwritten}", "warn")
            if result.errors:
                logwrite(f"Move to Game errors: {len(result.errors)}", "bad")

        def restore_from_copy(logwrite, progresswrite):
            working_paths = self._selected_working_paths("backup_sync", logwrite=logwrite)
            paths = [str(path) for path in working_paths] + list(restore_paths)
            if not paths:
                logwrite("Select Working folders or add restore folders / - Copy.po files first.", "warn")
                return
            self._check_stop()
            progresswrite(0, 0, "Scanning Copy.po files")
            results = restore_working_po_from_copies(
                paths,
                progress=lambda done, total, path: progresswrite(done, total, f"Restore {path.name}"),
            )
            ok = failed = 0
            for result_index, result in enumerate(results, start=1):
                self._check_stop()
                if result.ok:
                    ok += 1
                    logwrite(f"OK {result.action}: {result.copy_po} -> {result.work_po}", "good")
                else:
                    failed += 1
                    logwrite(f"ERR {result.action}: {result.copy_po} -> {result.work_po} | {result.error}", "bad")
            logwrite(f"Restored working PO files: {ok}", "good")
            if failed:
                logwrite(f"Failed/skipped: {failed}", "bad")
            if not results:
                logwrite("No Copy.po files found in selected folders.", "warn")

        def start_restore() -> None:
            answer = QMessageBox.question(
                self,
                "Restore working PO",
                "This will overwrite working .po files with clean content copied from matching - Copy.po files.\n\nCopy.po files will NOT be modified. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                log.append_log("Restore cancelled before start.", "warn")
                return
            self._run_threaded(restore_btn, log, restore_from_copy)

        backup_btn.clicked.connect(lambda: self._run_threaded(backup_btn, log, backup))
        option_sync_btn.clicked.connect(lambda: self._run_threaded(option_sync_btn, log, sync_selected_options))
        sync_btn.clicked.connect(lambda: self._run_threaded(sync_btn, log, sync))
        move_compile_btn.clicked.connect(lambda: self._run_threaded(move_compile_btn, log, move_compile))
        move_game_btn.clicked.connect(lambda: self._run_threaded(move_game_btn, log, move_to_game))
        restore_btn.clicked.connect(start_restore)


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
    if initial_link:
        QTimer.singleShot(0, lambda value=initial_link: window.open_app_link(value))
    app.exec()


if __name__ == "__main__":
    main()
