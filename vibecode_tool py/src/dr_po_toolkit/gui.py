from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, QRectF, QSize
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QBrush, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

from .backup import make_backups, restore_working_po_from_copies, sync_by_filename_report
from .cancel import OperationCancelled
from .config import load_config, save_config
from .discovery import iter_po_files
from .gemini_web import (
    DEFAULT_BATCH_RETRIES,
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_MAX_ENTRIES_PER_BATCH,
    discover_untranslated_po_files,
    open_chrome_debug,
    run_gemini_web_path,
)
from .linewrap import wrap_msgstr, wrap_path
from .po_io import load_po, patch_msgstr_by_uid, save_po
from .rules import apply_rules_to_path, load_rules, rule_to_dict
from .search import SearchResult, search_path
from .translator import GeminiApiClient, SYSTEM_INSTRUCTIONS, translate_entries_with_client, translate_file_with_client
from .translafixer import (
    TranslationSuggestionIndex,
    apply_translafix,
    build_translation_map,
    collect_source_po_files,
    msgid_match_key,
)
from .validation import format_text_report, validate_path, write_reports

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
PURPLE = "#c7a7ee"
# Search result colors. EN is no longer dark yellow: it is now a dusty
# periwinkle-lavender that matches the Chiaki-inspired app theme.
EN_BG = "#3b3458"
VI_BG = "#1f3b42"
EN_HIT_BG = "#67508e"
VI_HIT_BG = "#2f5d66"
CONTEXT_BG = "#1b2030"
HTML_ROLE = 0x0100 + 91


class WorkerSignals(QObject):
    log = pyqtSignal(str, str)
    result = pyqtSignal(object)
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
        self.format = QTextCharFormat()
        self.format.setForeground(QColor(BLUE))
        self.format.setFontWeight(700)
        self.pattern = re.compile(r"<\s*clt(?:[\s_]*(?:\d+|n))?\s*>", re.IGNORECASE)

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        for match in self.pattern.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.format)


CLT_TAG_RE = re.compile(r"<\s*clt(?:[\s_]*(?:\d+|n))?\s*>", re.IGNORECASE)


def clt_rich_html(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in CLT_TAG_RE.finditer(text or ""):
        parts.append(html.escape((text or "")[last:match.start()]).replace("\n", "<br>"))
        parts.append(f'<span style="color:{BLUE}; font-weight:800;">{html.escape(match.group(0))}</span>')
        last = match.end()
    parts.append(html.escape((text or "")[last:]).replace("\n", "<br>"))
    return "".join(parts)


class NoFocusCellDelegate(QStyledItemDelegate):
    """Hide the dotted/current-cell focus rectangle while keeping row selection color.

    Selection is still visible with the original purple highlight background and still works
    for Replace Selected / Open File. Only the focus outline is removed.
    """

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, opt, index)


class RichTextCellDelegate(NoFocusCellDelegate):
    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        html_text = index.data(HTML_ROLE)
        if not html_text:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
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
        self.search_results: list[SearchResult] = []
        self.search_last_index: int = -1
        self.rule_list_data: list[dict] = []
        self.rule_loading_fields = False
        self.rule_auto_timer: QTimer | None = None

        self.setWindowTitle("Chiaki PO Toolkit — PyQt")
        self.resize(1180, 720)
        self._apply_style()
        self._build()

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
                padding: 5px 9px;
                font-weight: 800;
            }}
            QPushButton:hover {{ background: {ACCENT_SOFT}; }}
            QPushButton:pressed {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QPushButton:disabled {{ background: #465066; color: #968da6; }}
            QPushButton#dangerButton {{ background: {BAD}; color: #241018; }}
            QPushButton#secondaryButton {{ background: {PANEL_3}; color: {TEXT}; border: 1px solid #46506a; }}
            QPushButton#secondaryButton:hover {{ background: #46506a; color: {ACCENT_SOFT}; }}
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
        top.addStretch()
        self.stop_button = QPushButton("Stop Current Action")
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

    def _button(self, text: str, *, secondary: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        if danger:
            btn.setObjectName("dangerButton")
        elif secondary:
            btn.setObjectName("secondaryButton")
        return btn

    def _path_row(self, layout: QVBoxLayout | QFormLayout, label: str, key: str, *, file: bool = False) -> QLineEdit:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        edit = QLineEdit(str(self.config.get(key, "")))
        edit.setPlaceholderText("Choose file/folder or paste path...")
        browse = self._button("Browse", secondary=True)

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

        browse.clicked.connect(browse_path)
        edit.editingFinished.connect(save_path)
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
        return edit

    def _request_stop(self) -> None:
        self._stop_event.set()
        if self._active_log is not None:
            self._active_log.append_log("Stop requested. Waiting for current safe checkpoint...", "warn")

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise OperationCancelled("Stopped by user.")

    def _run_threaded(self, button: QPushButton, log: LogBox, fn: Callable[[Callable[[str, str], None]], None]) -> None:
        if self._active_thread is not None and self._active_thread.is_alive():
            QMessageBox.warning(self, "Busy", "Another action is running. Press Stop Current Action first.")
            return

        signals = WorkerSignals()
        self._active_signals.append(signals)
        signals.log.connect(log.append_log)

        def done() -> None:
            button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self._active_log = None
            try:
                self._active_signals.remove(signals)
            except ValueError:
                pass

        signals.done.connect(done)

        def logwrite(text: str, tag: str = "") -> None:
            signals.log.emit(str(text), str(tag or ""))

        def worker() -> None:
            self._stop_event.clear()
            self._active_log = log
            try:
                self._check_stop()
                fn(logwrite)
            except OperationCancelled:
                logwrite("Stopped by user.", "warn")
            except Exception as exc:
                logwrite(f"ERROR: {exc}", "bad")
            finally:
                signals.done.emit()

        log.clear()
        button.setEnabled(False)
        self.stop_button.setEnabled(True)
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

    # ---------------- Validate ----------------
    def _build_validate_tab(self) -> None:
        _tab, layout = self._new_tab("Validate")
        path_edit = self._path_row(layout, "Folder/File", "last_path")
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

        def run(logwrite):
            self._check_stop()
            path = path_edit.text().strip()
            if not path:
                logwrite("Set path first.", "warn")
                return
            results = validate_path(path)
            logwrite(format_text_report(results, path))
            if save_reports.isChecked():
                out_dir = Path(path) if Path(path).is_dir() else Path(path).parent
                txt, html_path = write_reports(results, out_dir, path)
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
        path_edit = self._path_row(layout, "Folder/File", "last_path")
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

        def run(logwrite):
            self._check_stop()
            rules = load_rules(rules_edit.text().strip())
            changes = apply_rules_to_path(path_edit.text().strip(), rules, dry_run=dry_run.isChecked())
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
        rule_buttons.addWidget(delete_btn, 0, 1)
        rule_buttons.addWidget(enable_btn, 1, 0)
        rule_buttons.addWidget(disable_btn, 1, 1)
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
        path_edit = self._path_row(layout, "Folder/File", "last_path")

        controls = QHBoxLayout()
        dry = QCheckBox("Dry run")
        dry.setChecked(True)
        controls.addWidget(dry)
        soft = QSpinBox(); soft.setRange(1, 999); soft.setValue(int(self.config.get("soft_limit", 58)))
        hard = QSpinBox(); hard.setRange(1, 999); hard.setValue(int(self.config.get("hard_limit", 64)))
        cuts = QSpinBox(); cuts.setRange(1, 20); cuts.setValue(int(self.config.get("max_cuts", 2)))
        self.linewrap_soft_spin = soft
        self.linewrap_hard_spin = hard
        self.linewrap_cuts_spin = cuts
        for label, spin in [("Soft", soft), ("Hard", hard), ("Max cuts", cuts)]:
            controls.addWidget(QLabel(label))
            controls.addWidget(spin)
        controls.addStretch()
        layout.addLayout(controls)

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
            fixed, changed = wrap_msgstr(test_input.toPlainText(), soft=soft.value(), hard=hard.value(), max_cuts=cuts.value())
            test_input.setPlainText(fixed)
            lengths = [len(line) for line in fixed.splitlines()] or [0]
            test_status.setText(f"Applied: {'changed' if changed else 'unchanged'} | Lines: {lengths}")

        def run(logwrite):
            self._check_stop()
            results = wrap_path(path_edit.text().strip(), soft=soft.value(), hard=hard.value(), max_cuts=cuts.value(), dry_run=dry.isChecked())
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
        theme_note = QLabel("☾ Sleepy gamer theme: EN results use dusty lavender, VI results use muted teal.")
        theme_note.setObjectName("muted")
        theme_note.setWordWrap(True)
        layout.addWidget(theme_note)
        path_edit = self._path_row(layout, "Folder/File", "last_path")

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Phrase"))
        phrase = QLineEdit()
        search_row.addWidget(phrase, 1)
        search_msgid = QCheckBox("EN")
        search_msgid.setChecked(True)
        search_msgstr = QCheckBox("VI")
        search_msgstr.setChecked(True)
        search_case = QCheckBox("Case")
        search_whole = QCheckBox("Whole word")
        search_btn = self._button("Search")
        for w in [search_msgid, search_msgstr, search_case, search_whole, search_btn]:
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
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        open_btn = self._button("Open File", secondary=True)
        wrap_btn = self._button("Wrap Selected", secondary=True)
        save_btn = self._button("Save msgstr")
        edit_buttons.addWidget(open_btn)
        edit_buttons.addWidget(wrap_btn)
        edit_buttons.addWidget(save_btn)
        right_layout.addLayout(edit_buttons)

        replace_group = QGroupBox("Find / Replace in Results")
        repl_layout = QGridLayout(replace_group)
        find_edit = QPlainTextEdit()
        find_edit.setFixedHeight(58)
        find_edit.setPlaceholderText("Find text. Spaces or pasted line breaks will also match line breaks in msgstr.")
        repl_edit = QPlainTextEdit()
        repl_edit.setFixedHeight(58)
        repl_edit.setPlaceholderText("Replacement text. Use pasted line breaks or \\n for a line break.")
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
        splitter.addWidget(right)
        splitter.setSizes([740, 430])

        def compact(text: str, limit: int = 1000) -> str:
            text = text.replace("\\n", "\n")
            return text if len(text) <= limit else text[: limit - 1] + "…"

        def multiline_text(editor: QPlainTextEdit) -> str:
            # Let users type/paste real line breaks, or type \n as a shortcut.
            return editor.toPlainText().replace("\\n", "\n")

        def flexible_whitespace_pattern(text: str) -> str:
            # Search text often comes from visible PO text where line breaks were collapsed.
            # Treat any typed/pasted whitespace as flexible whitespace so "hello world"
            # can replace "hello\nworld", literal "\\n", and other wrapped msgstr forms.
            pieces: list[str] = []
            pos = 0
            for match in re.finditer(r"\s+", text):
                if match.start() > pos:
                    pieces.append(re.escape(text[pos:match.start()]))
                pieces.append(r"(?:\s+|\\n)+")
                pos = match.end()
            if pos < len(text):
                pieces.append(re.escape(text[pos:]))
            return "".join(pieces)

        def wrap_settings() -> tuple[int, int, int]:
            soft_spin = getattr(self, "linewrap_soft_spin", None)
            hard_spin = getattr(self, "linewrap_hard_spin", None)
            cuts_spin = getattr(self, "linewrap_cuts_spin", None)
            soft_value = int(soft_spin.value()) if soft_spin is not None else int(self.config.get("soft_limit", 58))
            hard_value = int(hard_spin.value()) if hard_spin is not None else int(self.config.get("hard_limit", 64))
            cuts_value = int(cuts_spin.value()) if cuts_spin is not None else int(self.config.get("max_cuts", 2))
            return soft_value, hard_value, cuts_value

        def fill_table() -> None:
            table.setUpdatesEnabled(False)
            table.setRowCount(0)
            table.setRowCount(len(self.search_results))
            try:
                for row, result in enumerate(self.search_results):
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
                        elif col == 1:
                            bg_color = EN_HIT_BG if result.hit_msgid else EN_BG
                            item.setForeground(QBrush(QColor(WHITE)))
                            item.setBackground(QBrush(QColor(bg_color)))
                            item.setData(HTML_ROLE, f'<span style="color:{WHITE};">{clt_rich_html(value)}</span>')
                            if result.hit_msgid:
                                font = item.font()
                                font.setBold(True)
                                item.setFont(font)
                        elif col == 2:
                            bg_color = VI_HIT_BG if result.hit_msgstr else VI_BG
                            item.setForeground(QBrush(QColor(WHITE)))
                            item.setBackground(QBrush(QColor(bg_color)))
                            item.setData(HTML_ROLE, f'<span style="color:{WHITE};">{clt_rich_html(value)}</span>')
                            if result.hit_msgstr:
                                font = item.font()
                                font.setBold(True)
                                item.setFont(font)
                        table.setItem(row, col, item)
            finally:
                table.setUpdatesEnabled(True)

            # resizeRowsToContents is very expensive with thousands of hits.
            if len(self.search_results) <= 1000:
                table.resizeRowsToContents()
            else:
                table.verticalHeader().setDefaultSectionSize(54)
            status.setText(f"Results: {len(self.search_results)}")

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

        def save_updates(updates: dict[int, str]) -> int:
            if not updates:
                return 0
            grouped: dict[Path, dict[str, str]] = defaultdict(dict)
            changed_indices: list[int] = []
            for idx, new_text in updates.items():
                if idx < 0 or idx >= len(self.search_results):
                    continue
                result = self.search_results[idx]
                if result.msgstr == new_text:
                    continue
                grouped[result.file][result.uid] = new_text
                result.msgstr = new_text
                changed_indices.append(idx)
            changed_files = 0
            for path, translations in grouped.items():
                po = load_po(path)
                n = patch_msgstr_by_uid(po, translations)
                if n:
                    save_po(po, path)
                    changed_files += 1
            for idx in changed_indices:
                for row in range(table.rowCount()):
                    if row_result_index(row) == idx:
                        item = table.item(row, 2)
                        if item:
                            value = compact(self.search_results[idx].msgstr)
                            bg_color = VI_HIT_BG if self.search_results[idx].hit_msgstr else VI_BG
                            item.setText(value)
                            item.setForeground(QBrush(QColor(WHITE)))
                            item.setBackground(QBrush(QColor(bg_color)))
                            item.setData(HTML_ROLE, f'<span style="color:{WHITE};">{clt_rich_html(value)}</span>')
                        break
            table.resizeRowsToContents()
            load_selected()
            return len(changed_indices)

        def save_current() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            changed = save_updates({idx: msgstr_box.toPlainText()})
            status.setText("Saved msgstr." if changed else "No change.")

        def wrap_selected_msgstrs() -> None:
            indices = selected_result_indices()
            if not indices:
                current = current_result_index()
                indices = [current] if current is not None else []
            if not indices:
                status.setText("Select one or more results first.")
                return

            current_idx = current_result_index()
            soft_value, hard_value, cuts_value = wrap_settings()
            updates: dict[int, str] = {}
            wrapped_count = 0
            for idx in sorted(set(indices)):
                if idx < 0 or idx >= len(self.search_results):
                    continue
                source = msgstr_box.toPlainText() if idx == current_idx else self.search_results[idx].msgstr
                fixed, changed = wrap_msgstr(source, soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                if changed:
                    wrapped_count += 1
                if changed or source != self.search_results[idx].msgstr:
                    updates[idx] = fixed

            changed = save_updates(updates)
            if current_idx is not None and 0 <= current_idx < len(self.search_results):
                msgstr_box.setPlainText(self.search_results[current_idx].msgstr)
            status.setText(
                f"Wrapped {wrapped_count} selected result(s), saved {changed}. "
                f"Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}."
            )

        def run_search() -> None:
            path = path_edit.text().strip()
            text = phrase.text()
            if not path or not text:
                status.setText("Set path and phrase first.")
                return
            self.search_results = search_path(
                path,
                text,
                search_msgid=search_msgid.isChecked(),
                search_msgstr=search_msgstr.isChecked(),
                case_sensitive=search_case.isChecked(),
                whole_word=search_whole.isChecked(),
            )
            self.search_last_index = -1
            fill_table()
            if self.search_results:
                table.selectRow(0)
                load_selected()
            if not multiline_text(find_edit).strip():
                find_edit.setPlainText(text)

        def open_selected_file() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            if not self._open_external(self.search_results[idx].file):
                QMessageBox.warning(self, "Open file", f"Could not open file:\n{self.search_results[idx].file}")

        def compile_find() -> re.Pattern[str] | None:
            needle = multiline_text(find_edit)
            if not needle.strip():
                status.setText("Find is empty.")
                return None
            pattern = flexible_whitespace_pattern(needle)
            if replace_whole.isChecked():
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            flags = 0 if replace_case.isChecked() else re.IGNORECASE
            return re.compile(pattern, flags)

        def result_matches(idx: int, pattern: re.Pattern[str]) -> bool:
            return bool(pattern.search(self.search_results[idx].msgstr))

        def select_result(idx: int) -> None:
            for row in range(table.rowCount()):
                if row_result_index(row) == idx:
                    table.selectRow(row)
                    table.setCurrentCell(row, 0)
                    table.scrollToItem(table.item(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                    load_selected()
                    self.search_last_index = idx
                    return

        def find_step(direction: int) -> None:
            pattern = compile_find()
            if pattern is None or not self.search_results:
                return
            current = current_result_index()
            start = current if current is not None else self.search_last_index
            n = len(self.search_results)
            for offset in range(1, n + 1):
                idx = (start + direction * offset) % n
                if result_matches(idx, pattern):
                    select_result(idx)
                    status.setText(f"Found result {idx + 1}/{n}.")
                    return
            status.setText("No match in current results.")

        def replace_indices(indices: list[int]) -> None:
            pattern = compile_find()
            if pattern is None:
                return
            repl = multiline_text(repl_edit)
            updates: dict[int, str] = {}
            total_hits = 0
            wrapped_count = 0
            soft_value, hard_value, cuts_value = wrap_settings()
            for idx in sorted(set(indices)):
                if idx < 0 or idx >= len(self.search_results):
                    continue
                before = self.search_results[idx].msgstr
                after, hits = pattern.subn(repl, before)
                if hits:
                    after, wrapped = wrap_msgstr(after, soft=soft_value, hard=hard_value, max_cuts=cuts_value)
                    updates[idx] = after
                    total_hits += hits
                    if wrapped:
                        wrapped_count += 1
            changed = save_updates(updates)
            status.setText(
                f"Replaced {total_hits} hit(s) in {changed} result(s). "
                f"Auto-wrapped {wrapped_count}."
            )

        def replace_current() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            replace_indices([idx])

        table.itemSelectionChanged.connect(load_selected)
        table.itemDoubleClicked.connect(lambda _item: open_selected_file())
        search_btn.clicked.connect(run_search)
        phrase.returnPressed.connect(run_search)
        open_btn.clicked.connect(open_selected_file)
        wrap_btn.clicked.connect(wrap_selected_msgstrs)
        save_btn.clicked.connect(save_current)
        msgstr_box.installEventFilter(self)
        prev_btn.clicked.connect(lambda: find_step(-1))
        next_btn.clicked.connect(lambda: find_step(1))
        current_btn.clicked.connect(replace_current)
        selected_btn.clicked.connect(lambda: replace_indices(selected_result_indices()))
        all_btn.clicked.connect(lambda: replace_indices(list(range(len(self.search_results)))))


    # ---------------- Translafixer ----------------
    def _build_translafixer_tab(self) -> None:
        _tab, layout = self._new_tab("Translafixer")
        note = QLabel(
            "Drag or add known-good source .po files, then pick the target folder to fix. "
            "Target .po files are rewritten when original text / msgid matches. "
            "Copy.po target files are skipped. Selected source files are never rewritten, "
            "even if they are inside the target folder. Conflicting source translations are skipped."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        source_box = QGroupBox("Correct source translation files")
        source_layout = QVBoxLayout(source_box)
        source_hint = QLabel("Drag .po files or folders here. Dropped folders are expanded recursively into source .po files.")
        source_hint.setObjectName("muted")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)

        source_list = PathDropList()
        self.translafixer_source_list_widget = source_list
        source_list.setMinimumHeight(145)
        source_list.setToolTip("Drop known-good .po files or folders here.")
        source_layout.addWidget(source_list)

        source_buttons = QHBoxLayout()
        add_files_btn = self._button("Add .po files", secondary=True)
        add_folder_btn = self._button("Add folder", secondary=True)
        remove_files_btn = self._button("Remove selected", secondary=True)
        clear_files_btn = self._button("Clear", secondary=True)
        source_status = QLabel("0 source files")
        source_status.setObjectName("muted")
        source_buttons.addWidget(add_files_btn)
        source_buttons.addWidget(add_folder_btn)
        source_buttons.addWidget(remove_files_btn)
        source_buttons.addWidget(clear_files_btn)
        source_buttons.addStretch()
        source_buttons.addWidget(source_status)
        source_layout.addLayout(source_buttons)
        layout.addWidget(source_box)

        target_edit = self._path_row(layout, "Target folder", "translafixer_target_folder")

        def source_files() -> list[str]:
            return [source_list.item(i).text() for i in range(source_list.count())]

        def refresh_source_status() -> None:
            count = source_list.count()
            source_status.setText(f"{count} source file{'s' if count != 1 else ''}")

        def save_sources() -> None:
            self.config["translafixer_source_files"] = source_files()
            self.config["translafixer_target_folder"] = target_edit.text().strip()
            save_config(self.config)
            refresh_source_status()

        def add_source_paths(paths: list[str]) -> None:
            existing = {str(Path(source_list.item(i).text()).expanduser().resolve(strict=False)) for i in range(source_list.count())}
            added = 0
            for candidate in collect_source_po_files(paths):
                resolved = str(Path(candidate).expanduser().resolve(strict=False))
                if resolved in existing:
                    continue
                existing.add(resolved)
                source_list.addItem(QListWidgetItem(str(candidate)))
                added += 1

            if added:
                save_sources()
            else:
                refresh_source_status()

        stored_sources = self.config.get("translafixer_source_files", [])
        if isinstance(stored_sources, list):
            add_source_paths([str(item) for item in stored_sources])

        def browse_source_files() -> None:
            start_dir = str(Path.cwd())
            if source_list.count():
                start_dir = str(Path(source_list.item(source_list.count() - 1).text()).expanduser().parent)
            elif target_edit.text().strip():
                start_dir = target_edit.text().strip()
            paths, _ = QFileDialog.getOpenFileNames(self, "Add correct source .po files", start_dir, "PO files (*.po);;All files (*.*)")
            if paths:
                add_source_paths(paths)

        def browse_source_folder() -> None:
            start_dir = target_edit.text().strip() or str(Path.cwd())
            folder = QFileDialog.getExistingDirectory(self, "Add folder containing correct source .po files", start_dir)
            if folder:
                add_source_paths([folder])

        def remove_selected_sources() -> None:
            for item in source_list.selectedItems():
                row = source_list.row(item)
                source_list.takeItem(row)
            save_sources()

        def clear_sources() -> None:
            source_list.clear()
            save_sources()

        source_list.pathsDropped.connect(add_source_paths)
        add_files_btn.clicked.connect(browse_source_files)
        add_folder_btn.clicked.connect(browse_source_folder)
        remove_files_btn.clicked.connect(remove_selected_sources)
        clear_files_btn.clicked.connect(clear_sources)

        options = QHBoxLayout()
        dry_run = QCheckBox("Dry run")
        dry_run.setChecked(True)
        backup = QCheckBox("Create .translafixer.bak before write")
        backup.setChecked(True)
        include_empty = QCheckBox("Allow empty source msgstr")
        include_empty.setChecked(False)
        for widget in [dry_run, backup, include_empty]:
            options.addWidget(widget)
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

        def run(logwrite):
            save_paths()
            sources = source_files()
            target = target_edit.text().strip()
            if not sources or not target:
                logwrite("Add source .po files/folders and pick target folder first.", "warn")
                return
            self._check_stop()
            result = apply_translafix(
                sources,
                target,
                dry_run=dry_run.isChecked(),
                create_backup=backup.isChecked(),
                include_empty=include_empty.isChecked(),
                log=lambda msg: logwrite(msg),
                stop_requested=self._stop_event.is_set,
            )
            logwrite(f"Source files scanned: {result.source_files}")
            logwrite(f"Source entries: {result.source_entries}")
            logwrite(f"Usable msgid translations: {result.usable_translations}", "good" if result.usable_translations else "warn")
            if result.skipped_source_targets:
                logwrite(f"Skipped selected source files inside target folder: {result.skipped_source_targets}", "warn")
            if result.empty_source_entries and not include_empty.isChecked():
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
            logwrite(f"Target files scanned: {result.target_files}")
            logwrite(f"Matched entries: {result.total_matched}")
            logwrite(f"Changed entries: {result.total_changed}", "good" if result.total_changed else "warn")
            if result.total_errors:
                logwrite(f"Errors: {result.total_errors}", "bad")
            if dry_run.isChecked():
                logwrite("Dry run only. Uncheck Dry run to write files.", "warn")

        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run))

    # ---------------- PO Viewer ----------------
    def _translafixer_source_paths(self) -> list[str]:
        widget = getattr(self, "translafixer_source_list_widget", None)
        if widget is not None:
            return [widget.item(i).text() for i in range(widget.count())]
        stored = self.config.get("translafixer_source_files", [])
        if isinstance(stored, list):
            return [str(item) for item in stored]
        return []

    def _build_po_viewer_tab(self) -> None:
        _tab, layout = self._new_tab("PO Viewer")
        note = QLabel(
            "Pick .po files/folder and choose a non-copy .po from the dropdown. Use Open PO to launch the currently viewed file in its default app. "
            "View English + Vietnamese side by side, edit only Vietnamese, wrap msgstr lines, then fill translations from the Translafixer source list. "
            "Shortcuts: Ctrl+Up/Down = entry, Ctrl+Enter = wrap selected/current, "
            "Ctrl+Shift+Up/Down = file, Ctrl+1..9 = apply suggestion, Ctrl+0 = refresh suggestions. Translafixer matching ignores CLT tags."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        source_row = QHBoxLayout()
        source_label = QLabel("PO source")
        source_label.setMinimumWidth(80)
        source_label.setStyleSheet("font-weight:700;")
        initial_source = str(self.config.get("po_viewer_source") or self.config.get("po_viewer_file", ""))
        source_edit = QLineEdit(initial_source)
        source_edit.setPlaceholderText("Choose .po file(s) or a folder...")
        open_po_btn = self._button("Open PO", secondary=True)
        open_po_btn.setToolTip("Open the currently viewed .po file in the system default app")
        browse_files_btn = self._button("Pick files", secondary=True)
        browse_folder_btn = self._button("Pick folder", secondary=True)
        load_btn = self._button("Load")
        save_btn = self._button("Save", secondary=True)
        source_row.addWidget(source_label)
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
        wrap_view_btn = self._button("Visual wrap: ON", secondary=True)
        wrap_selected_btn = self._button("Wrap selected", secondary=True)
        wrap_all_btn = self._button("Wrap all", secondary=True)
        fill_btn = self._button("Translafix from sources")
        gemini_selected_btn = self._button("Gemini API selected", secondary=True)
        gemini_selected_btn.setToolTip("Translate the selected PO Viewer rows with the Gemini API. Configure key/model/prompt in the Gemini Web tab.")
        status = QLabel("No file loaded")
        status.setObjectName("muted")
        status.setWordWrap(True)
        tools.addWidget(wrap_view_btn)
        tools.addWidget(wrap_selected_btn)
        tools.addWidget(wrap_all_btn)
        tools.addWidget(fill_btn)
        tools.addWidget(gemini_selected_btn)
        tools.addStretch()
        tools.addWidget(status, 1)
        layout.addLayout(tools)

        split = QSplitter(Qt.Orientation.Vertical)
        table = QTableWidget()
        table.setObjectName("poViewerTable")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["#", "Speaker / Context", "English msgid", "Vietnamese msgstr"])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
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

        def labeled_box(label: str, box: QPlainTextEdit) -> QWidget:
            wrap = QWidget()
            box_layout = QVBoxLayout(wrap)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(5)
            lab = QLabel(label)
            lab.setStyleSheet("font-weight:800;")
            box_layout.addWidget(lab)
            box_layout.addWidget(box, 1)
            return wrap

        en_box = QPlainTextEdit()
        en_box.setReadOnly(True)
        en_box.setPlaceholderText("English msgid")
        en_box.setFont(QFont("Consolas", 9))
        en_box._clt_highlighter = CltHighlighter(en_box.document())  # keep highlighter alive
        vi_box = QPlainTextEdit()
        vi_box.setPlaceholderText("Edit Vietnamese msgstr here. English is read-only.")
        vi_box.setFont(QFont("Consolas", 9))
        vi_box._clt_highlighter = CltHighlighter(vi_box.document())  # keep highlighter alive
        detail.addWidget(labeled_box("English / original — read only", en_box))
        detail.addWidget(labeled_box("Vietnamese / translation — editable", vi_box))
        detail.setSizes([1, 1])
        split.addWidget(detail)
        split.setSizes([430, 155])

        suggest_group = QGroupBox("Suggestions")
        suggest_layout = QVBoxLayout(suggest_group)
        suggest_note = QLabel("From Translafixer sources. Shows distinct Vietnamese translations only. >95% percentage is green. Ctrl+1..9 apply, Ctrl+0 refresh.")
        suggest_note.setObjectName("muted")
        suggest_note.setWordWrap(True)
        suggest_layout.addWidget(suggest_note)
        suggestions_list = QListWidget()
        suggestions_list.setObjectName("suggestionsList")
        suggestions_list.setUniformItemSizes(False)
        suggest_layout.addWidget(suggestions_list, 1)
        suggest_controls = QHBoxLayout()
        suggest_min_score = QSpinBox()
        suggest_min_score.setRange(70, 100)
        suggest_min_score.setSuffix("%")
        suggest_min_score.setValue(max(70, int(self.config.get("po_viewer_suggest_min_score", 70))))
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
            "suggestion_index": None,
            "suggestion_source_signature": None,
            "suggestion_source_result": None,
        }

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
            if item is not None:
                item.setData(HTML_ROLE, f'<span style="color:{color};">{clt_rich_html(text)}</span>')

        def row_context(entry) -> str:
            return entry.speaker or entry.msgctxt or ""

        def _same_file(a: Path | None, b: Path | None) -> bool:
            if a is None or b is None:
                return False
            try:
                return a.resolve(strict=False) == b.resolve(strict=False)
            except OSError:
                return str(a) == str(b)

        def _suggestion_source_signature() -> tuple[tuple[str, int, int], str]:
            current = current_path()
            current_key = str(current.expanduser().resolve(strict=False)) if current is not None else ""
            signature_items: list[tuple[str, int, int]] = []
            for raw_path in self._translafixer_source_paths():
                try:
                    po_path = Path(str(raw_path)).expanduser()
                    resolved = str(po_path.resolve(strict=False))
                    stat = po_path.stat() if po_path.exists() else None
                    signature_items.append((resolved, int(stat.st_mtime_ns if stat else 0), int(stat.st_size if stat else 0)))
                except Exception:
                    signature_items.append((str(raw_path), 0, 0))
            return tuple(signature_items), current_key

        def rebuild_suggestion_candidates(*, quiet: bool = False) -> None:
            sources = self._translafixer_source_paths()
            current = current_path()
            state["suggestion_source_signature"] = _suggestion_source_signature()
            state["suggestion_index"] = TranslationSuggestionIndex()
            state["suggestion_source_result"] = None
            if not sources:
                if not quiet:
                    set_status("No Translafixer sources. Add files/folders in Translafixer for suggestions.")
                return
            try:
                index, result = TranslationSuggestionIndex.from_translafixer_sources(
                    sources,
                    exclude_files=[current] if current is not None else [],
                )
            except Exception as exc:
                if not quiet:
                    QMessageBox.warning(self, "PO Viewer", f"Could not build suggestion index from Translafixer sources:\n{exc}")
                return
            state["suggestion_index"] = index
            state["suggestion_source_result"] = result
            if not quiet:
                skipped = f", skipped current={result.skipped_source_targets}" if result.skipped_source_targets else ""
                set_status(f"Suggestion index: {result.usable_translations} translated entries from {result.source_files} Translafixer source file(s){skipped}.")

        def ensure_suggestion_index(*, force: bool = False, quiet: bool = True) -> TranslationSuggestionIndex | None:
            signature = _suggestion_source_signature()
            index = state.get("suggestion_index")
            if force or index is None or state.get("suggestion_source_signature") != signature:
                rebuild_suggestion_candidates(quiet=quiet)
                index = state.get("suggestion_index")
            return index if isinstance(index, TranslationSuggestionIndex) else None

        def refresh_suggestions_for_row(row: int | None = None, *, force_rebuild: bool = False) -> None:
            suggestions_list.clear()
            po = po_file()
            if po is None:
                return
            if row is None:
                row = table.currentRow()
            if row is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return
            index = ensure_suggestion_index(force=force_rebuild, quiet=not force_rebuild)
            if index is None:
                return
            target = po.entries[row]  # type: ignore[union-attr]
            min_score = max(70, suggest_min_score.value()) / 100.0
            suggestions = index.suggest(target.msgid, min_score=min_score, limit=5)

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
                vi_label = QLabel(f"<span style='color:{WHITE}; font-weight:700;'>{clt_rich_html(translation)}</span>")
                vi_label.setTextFormat(Qt.TextFormat.RichText)
                vi_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                vi_label.setWordWrap(True)
                vi_label.setStyleSheet(f"color: {WHITE}; font-weight:700;")
                row_layout.addWidget(meta_label)
                row_layout.addWidget(vi_label)

                item.setSizeHint(widget.sizeHint())
                suggestions_list.addItem(item)
                suggestions_list.setItemWidget(item, widget)
            if suggestions_list.count():
                suggestions_list.setCurrentRow(0)

        def apply_selected_suggestion() -> None:
            row = table.currentRow()
            item = suggestions_list.currentItem()
            if row < 0 or item is None:
                QMessageBox.warning(self, "PO Viewer", "Select a row and a suggestion first.")
                return
            data = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                return
            translation = str(data.get("translation") or "")
            if not translation.strip():
                return
            set_entry_translation(row, translation)

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
                    return
                entry = po.entries[row]  # type: ignore[union-attr]
                en_box.setPlainText(entry.msgid)
                vi_box.setPlainText(entry.msgstr)
                set_status(f"Entry {row + 1}/{len(po.entries)} | line {entry.line}")  # type: ignore[union-attr]
                refresh_suggestions_for_row(row)
            finally:
                state["detail_loading"] = False

        def set_entry_translation(row: int, text: str, *, dirty: bool = True) -> bool:
            po = po_file()
            if po is None or row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                return False
            entry = po.entries[row]  # type: ignore[union-attr]
            if entry.msgstr == text:
                return False
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
                finally:
                    state["detail_loading"] = False
            refresh_row_style(row)
            if dirty:
                state["dirty"] = True
                set_status(f"Edited entry {row + 1}. Save when ready.")
            return True

        def populate_table() -> None:
            po = po_file()
            state["loading"] = True
            table.clearContents()
            try:
                if po is None:
                    table.setRowCount(0)
                    return
                table.setRowCount(len(po.entries))  # type: ignore[union-attr]
                for row, entry in enumerate(po.entries):  # type: ignore[union-attr]
                    number = make_item(str(row + 1), bg=PANEL_3)
                    number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, 0, number)
                    table.setItem(row, 1, make_item(row_context(entry), bg=PANEL_2))
                    table.setItem(row, 2, make_item(entry.msgid, bg=EN_BG))
                    table.setItem(row, 3, make_item(entry.msgstr, editable=True, bg=VI_BG if entry.msgstr.strip() else "#4a3828"))
                    refresh_row_style(row)
                    table.setRowHeight(row, 44)
            finally:
                state["loading"] = False
            if table.rowCount():
                table.selectRow(0)
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

        def set_file_list(paths: list[str | Path], source_text: str, *, auto_load: bool = True, quiet: bool = False) -> None:
            source_paths = [Path(str(item)).expanduser() for item in paths if str(item).strip()]
            files = discover_po_files(source_paths)
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
                    for po_path in files:
                        file_combo.addItem(file_label(po_path), str(po_path))
                    preferred = str(current_path() or self.config.get("po_viewer_file", ""))
                    if preferred:
                        for idx, po_path in enumerate(files):
                            if str(po_path) == preferred:
                                file_combo.setCurrentIndex(idx)
                                break
            finally:
                file_combo.blockSignals(False)
                state["loading_files"] = False

            if not files:
                state["file_paths"] = []
                state["source_paths"] = source_paths
                if not quiet:
                    set_status("No usable .po files found. Copy files are skipped.")
                return

            state["source_text"] = source_text
            source_edit.setText(source_text)
            self.config["po_viewer_source"] = source_text
            self.config["po_viewer_files"] = [str(item) for item in files]
            save_config(self.config)
            if not quiet:
                set_status(f"Found {len(files)} .po file{'s' if len(files) != 1 else ''}. Copy files skipped.")
            if auto_load:
                load_file()

        def load_source_from_text() -> None:
            raw = source_edit.text().strip()
            if not raw:
                QMessageBox.warning(self, "PO Viewer", "Choose .po file(s) or a folder first.")
                return
            parts = [part.strip() for part in raw.split(";") if part.strip()]
            set_file_list(parts or [raw], raw, auto_load=True)

        def load_file(path: Path | None = None) -> None:
            if path is None:
                path = current_file_path()
            if path is None:
                raw = source_edit.text().strip()
                if raw:
                    set_file_list([raw], raw, auto_load=True)
                    return
                QMessageBox.warning(self, "PO Viewer", "Choose .po file(s) or a folder first.")
                return
            path = Path(path).expanduser()
            if not path.is_file() or path.suffix.lower() != ".po":
                QMessageBox.warning(self, "PO Viewer", "Choose a real .po file.")
                return
            try:
                po = load_po(path)
            except Exception as exc:
                QMessageBox.critical(self, "PO Viewer", f"Could not load file:\n{exc}")
                return
            state["po"] = po
            state["path"] = path
            state["dirty"] = False
            select_combo_path(path)
            self.config["po_viewer_file"] = str(path)
            if not self.config.get("po_viewer_source"):
                self.config["po_viewer_source"] = str(path)
            save_config(self.config)
            rebuild_suggestion_candidates(quiet=True)
            populate_table()
            issue_count = len(po.issues)
            extra = f" | issues={issue_count}" if issue_count else ""
            set_status(f"Loaded {len(po.entries)} entries from {path.name}{extra}")

        def save_file() -> None:
            po = po_file()
            path = current_path()
            if po is None or path is None:
                QMessageBox.warning(self, "PO Viewer", "No file loaded.")
                return
            try:
                save_po(po, path)
            except Exception as exc:
                QMessageBox.critical(self, "PO Viewer", f"Could not save file:\n{exc}")
                return
            state["dirty"] = False
            set_status(f"Saved {path.name}")

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
                set_file_list(paths, "; ".join(paths), auto_load=True)

        def browse_folder() -> None:
            current = current_path()
            if current is not None:
                start = str(current.parent)
            else:
                source = Path(source_edit.text().strip()).expanduser() if source_edit.text().strip() else Path.cwd()
                start = str(source if source.is_dir() else source.parent)
            folder = QFileDialog.getExistingDirectory(self, "Open folder with .po files", start)
            if folder:
                set_file_list([folder], folder, auto_load=True)

        def table_item_changed(item: QTableWidgetItem) -> None:
            if state.get("loading") or item.column() != 3:
                return
            set_entry_translation(item.row(), item.text())

        def vi_text_changed() -> None:
            if state.get("detail_loading"):
                return
            row = table.currentRow()
            if row < 0:
                return
            set_entry_translation(row, vi_box.toPlainText())

        def wrap_rows(rows: list[int]) -> None:
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            changed = 0
            for row in rows:
                if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    continue
                entry = po.entries[row]  # type: ignore[union-attr]
                fixed, did_change = wrap_msgstr(entry.msgstr)
                if did_change and set_entry_translation(row, fixed):
                    changed += 1
            set_status(f"Wrapped {changed} translation entr{'y' if changed == 1 else 'ies'}.")

        def wrap_selected() -> None:
            rows = selected_rows()
            if not rows and table.currentRow() >= 0:
                rows = [table.currentRow()]
            if not rows:
                QMessageBox.warning(self, "PO Viewer", "Select entries to wrap.")
                return
            wrap_rows(rows)

        def wrap_all() -> None:
            wrap_rows(list(range(table.rowCount())))

        def toggle_visual_wrap() -> None:
            state["visual_wrap"] = not bool(state.get("visual_wrap"))
            enabled = bool(state["visual_wrap"])
            table.setWordWrap(enabled)
            mode = QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap
            en_box.setLineWrapMode(mode)
            vi_box.setLineWrapMode(mode)
            wrap_view_btn.setText(f"Visual wrap: {'ON' if enabled else 'OFF'}")
            set_status("Visual line wrap enabled." if enabled else "Visual line wrap disabled.")

        def fill_from_translafixer_sources() -> None:
            po = po_file()
            if po is None:
                QMessageBox.warning(self, "PO Viewer", "Load a file first.")
                return
            sources = self._translafixer_source_paths()
            if not sources:
                QMessageBox.warning(self, "PO Viewer", "Add source files/folders in the Translafixer tab first.")
                return
            try:
                translations, result = build_translation_map(sources)
            except Exception as exc:
                QMessageBox.critical(self, "PO Viewer", f"Could not read Translafixer sources:\n{exc}")
                return
            rows = selected_rows()
            mode = "selected"
            if not rows:
                rows = [i for i, entry in enumerate(po.entries) if not entry.msgstr.strip()]  # type: ignore[union-attr]
                mode = "empty"
            if not rows:
                set_status("No selected rows and no empty translations to fill.")
                return
            matched = 0
            changed = 0
            unchanged = 0
            for row in rows:
                if row < 0 or row >= len(po.entries):  # type: ignore[union-attr]
                    continue
                entry = po.entries[row]  # type: ignore[union-attr]
                replacement = translations.get(msgid_match_key(entry.msgid))
                if replacement is None:
                    continue
                matched += 1
                if entry.msgstr == replacement:
                    unchanged += 1
                    continue
                if set_entry_translation(row, replacement):
                    changed += 1
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
            prompt = str(self.config.get("gemini_api_prompt", "")).strip() or SYSTEM_INSTRUCTIONS
            model = str(self.config.get("gemini_api_model", "gemini-2.5-flash")).strip() or "gemini-2.5-flash"
            batch_size = max(1, int(self.config.get("gemini_web_max_entries", DEFAULT_MAX_ENTRIES_PER_BATCH)))
            sleep_seconds = float(self.config.get("gemini_api_sleep_seconds", 1.0))
            entries = [po.entries[row] for row in rows if 0 <= row < len(po.entries)]  # type: ignore[union-attr]
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                client = GeminiApiClient(api_key=api_key, model=model, prompt=prompt)
                translations, errors = translate_entries_with_client(
                    entries,
                    client,
                    batch_size=batch_size,
                    sleep_seconds=sleep_seconds,
                    allow_partial=False,
                    prompt=prompt,
                )
                changed = 0
                by_uid = {entry.uid: row for row, entry in zip(rows, entries)}
                for uid, translation in translations.items():
                    row = by_uid.get(uid)
                    if row is not None and set_entry_translation(row, translation):
                        changed += 1
                refresh_suggestions_for_row(table.currentRow())
            except Exception as exc:
                QMessageBox.critical(self, "Gemini API", f"Gemini API translation failed:\n{exc}")
                return
            finally:
                QApplication.restoreOverrideCursor()
            if errors:
                preview = "\n".join(f"{e.msgctxt or e.uid}: {e.reason}" for e in errors[:8])
                more = f"\n... {len(errors) - 8} more" if len(errors) > 8 else ""
                QMessageBox.warning(self, "Gemini API", f"Translated {changed} entr{'y' if changed == 1 else 'ies'}, with {len(errors)} validation issue(s):\n{preview}{more}")
            else:
                set_status(f"Gemini API translated {changed} selected entr{'y' if changed == 1 else 'ies'}. Save when ready.")

        def current_changed(row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
            load_detail(row)

        def switch_entry(delta: int) -> None:
            if table.rowCount() <= 0:
                return
            row = table.currentRow()
            if row < 0:
                row = 0
            else:
                row = max(0, min(table.rowCount() - 1, row + delta))
            table.setFocus()
            table.selectRow(row)
            item = table.item(row, 0)
            if item is not None:
                table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            load_detail(row)

        def switch_file(delta: int) -> None:
            if file_combo.count() <= 1:
                return
            idx = file_combo.currentIndex()
            next_idx = (idx + delta) % file_combo.count()
            if file_combo.itemData(next_idx) is None:
                return
            file_combo.setCurrentIndex(next_idx)

        table.currentCellChanged.connect(current_changed)
        table.itemChanged.connect(table_item_changed)
        vi_box.textChanged.connect(vi_text_changed)
        open_po_btn.clicked.connect(open_current_po_external)
        browse_files_btn.clicked.connect(browse_files)
        browse_folder_btn.clicked.connect(browse_folder)
        load_btn.clicked.connect(lambda: load_file() if source_edit.text().strip() == state.get("source_text") else load_source_from_text())
        save_btn.clicked.connect(save_file)
        file_combo.currentIndexChanged.connect(lambda _idx: None if state.get("loading_files") else load_file())
        source_edit.returnPressed.connect(load_source_from_text)
        source_edit.editingFinished.connect(lambda: (self.config.__setitem__("po_viewer_source", source_edit.text().strip()), save_config(self.config)))
        wrap_view_btn.clicked.connect(toggle_visual_wrap)
        wrap_selected_btn.clicked.connect(wrap_selected)
        wrap_all_btn.clicked.connect(wrap_all)
        fill_btn.clicked.connect(fill_from_translafixer_sources)
        gemini_selected_btn.clicked.connect(translate_selected_with_gemini_api)
        refresh_suggest_btn.clicked.connect(lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        apply_suggest_btn.clicked.connect(apply_selected_suggestion)
        suggestions_list.itemDoubleClicked.connect(lambda _item: apply_selected_suggestion())
        suggest_min_score.valueChanged.connect(lambda _value: (self.config.__setitem__("po_viewer_suggest_min_score", suggest_min_score.value()), save_config(self.config), refresh_suggestions_for_row(table.currentRow())))

        shortcuts: list[QShortcut] = []

        def add_shortcut(sequence: str, callback: Callable[[], None]) -> None:
            shortcut = QShortcut(QKeySequence(sequence), _tab)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            shortcuts.append(shortcut)

        add_shortcut("Ctrl+Up", lambda: switch_entry(-1))
        add_shortcut("Ctrl+Down", lambda: switch_entry(1))
        add_shortcut("Ctrl+Return", wrap_selected)
        add_shortcut("Ctrl+Enter", wrap_selected)
        add_shortcut("Ctrl+Shift+Up", lambda: switch_file(-1))
        add_shortcut("Ctrl+Shift+Down", lambda: switch_file(1))
        for suggestion_number in range(1, 10):
            add_shortcut(f"Ctrl+{suggestion_number}", lambda n=suggestion_number: apply_suggestion_number(n))
        add_shortcut("Ctrl+0", lambda: refresh_suggestions_for_row(table.currentRow(), force_rebuild=True))
        self._po_viewer_shortcuts = shortcuts

        if initial_source:
            set_file_list([initial_source], initial_source, auto_load=False, quiet=True)

    # ---------------- Gemini Web / API ----------------
    def _build_translate_tab(self) -> None:
        _tab, layout = self._new_tab("Gemini Web")
        path_edit = self._path_row(layout, "Folder", "last_path")

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

        api_prompt_edit = QPlainTextEdit()
        api_prompt_edit.setPlainText(str(self.config.get("gemini_api_prompt", "")).strip() or SYSTEM_INSTRUCTIONS)
        api_prompt_edit.setMinimumHeight(125)
        api_prompt_edit.setPlaceholderText("Prompt used by Gemini API translation calls.")
        form.addRow("API prompt", api_prompt_edit)
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
        row.addWidget(run_btn)
        row.addWidget(chrome_btn)
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
            api_prompt_edit.setEnabled(is_api)
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
            self.config["gemini_api_prompt"] = api_prompt_edit.toPlainText().strip() or SYSTEM_INSTRUCTIONS
            self.config["gemini_api_sleep_seconds"] = wait_seconds.value()
            save_config(self.config)

        def run_web(logwrite):
            save_web_config()
            limit = max_files.value()
            result = run_gemini_web_path(
                path_edit.text().strip(),
                max_files=None if limit <= 0 else limit,
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
            )
            if not result.files:
                logwrite("No untranslated PO files found.", "warn")
                return
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
            logwrite(f"Total translated: {result.total_translated}", "good")
            if result.total_errors:
                logwrite(f"Total errors: {result.total_errors}", "bad")

        def run_api(logwrite):
            save_web_config()
            api_key = api_key_edit.text().strip() or os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("Gemini API key missing. Paste it into the API key field or set GEMINI_API_KEY.")
            base_text = path_edit.text().strip()
            if not base_text:
                raise RuntimeError("Choose a .po file or folder first.")
            base = Path(base_text).expanduser()
            if not base.exists():
                raise RuntimeError(f"Path not found: {base}")
            prompt = api_prompt_edit.toPlainText().strip() or SYSTEM_INSTRUCTIONS
            client = GeminiApiClient(api_key=api_key, model=api_model_edit.text().strip() or "gemini-2.5-flash", prompt=prompt)
            limit = max_files.value()
            if base.is_file():
                po_files = [base] if base.suffix.lower() == ".po" else []
            else:
                po_files = discover_untranslated_po_files(base, max_files=None if limit <= 0 else limit)
            if not po_files:
                logwrite("No untranslated PO files found.", "warn")
                return
            total_changed = 0
            total_errors = 0
            for idx, po_path in enumerate(po_files, start=1):
                self._check_stop()
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
            logwrite(f"Total translated: {total_changed}", "good")
            if total_errors:
                logwrite(f"Total errors: {total_errors}", "bad")

        def run_selected_mode(logwrite):
            if api_mode_enabled():
                run_api(logwrite)
            else:
                run_web(logwrite)

        def open_chrome(logwrite):
            save_web_config()
            cmd = open_chrome_debug(cdp_url=cdp_edit.text().strip() or "http://localhost:9222", user_data_dir=DEFAULT_CHROME_USER_DATA_DIR)
            logwrite("Chrome opened with remote debugging.", "good")
            logwrite("Login to Gemini in that Chrome window, then click Run Gemini Web.")
            logwrite("Command: " + " ".join(str(x) for x in cmd))

        mode_combo.currentIndexChanged.connect(lambda _idx: (api_key_toggle.setChecked(str(mode_combo.currentData()) == "api"), sync_mode_ui()))
        api_key_toggle.stateChanged.connect(lambda _state: sync_mode_ui())
        api_key_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_key", text.strip()))
        api_model_edit.textChanged.connect(lambda text: self.config.__setitem__("gemini_api_model", text.strip()))
        api_prompt_edit.textChanged.connect(lambda: self.config.__setitem__("gemini_api_prompt", api_prompt_edit.toPlainText().strip()))
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
        backup_edit = self._path_row(layout, "Backup path", "last_path")
        source_edit = self._path_row(layout, "Sync source", "sync_source")
        target_edit = self._path_row(layout, "Sync target", "sync_target")

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
        sync_btn = self._button("Sync by Filename", secondary=True)
        restore_btn = self._button("Restore Working PO from Copy.po")
        row.addWidget(backup_btn)
        row.addWidget(sync_btn)
        row.addWidget(restore_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def backup(logwrite):
            self._check_stop()
            n = make_backups(backup_edit.text().strip(), overwrite=False)
            logwrite(f"Missing Copy.po backups written: {n}", "good")
            logwrite("Existing Copy.po files were not touched.", "warn")

        def sync(logwrite):
            self._check_stop()
            result = sync_by_filename_report(source_edit.text().strip(), target_edit.text().strip())
            logwrite(f"Source files scanned: {result.source_files}")
            logwrite(f"Target files scanned: {result.target_files}")
            if result.duplicate_source_names:
                logwrite(f"Duplicate source filenames skipped: {result.duplicate_source_names}", "warn")
            if result.skipped_identical:
                logwrite(f"Identical files skipped: {result.skipped_identical}")
            if result.skipped_self:
                logwrite(f"Self-copy skipped: {result.skipped_self}", "warn")
            logwrite(f"Files synced: {result.copied}", "good" if result.copied else "warn")

        def restore_from_copy(logwrite):
            if not restore_paths:
                logwrite("Add or drag one or more restore folders / - Copy.po files first.", "warn")
                return
            self._check_stop()
            results = restore_working_po_from_copies(list(restore_paths))
            ok = failed = 0
            for result in results:
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
        sync_btn.clicked.connect(lambda: self._run_threaded(sync_btn, log, sync))
        restore_btn.clicked.connect(start_restore)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = ToolkitGUI()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
