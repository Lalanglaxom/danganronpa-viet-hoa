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
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QBrush, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
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

from .backup import make_backups, restore_working_po_from_copies, sync_by_filename
from .cancel import OperationCancelled
from .config import load_config, save_config
from .gemini_web import (
    DEFAULT_BATCH_RETRIES,
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_MAX_ENTRIES_PER_BATCH,
    open_chrome_debug,
    run_gemini_web_path,
)
from .linewrap import wrap_msgstr, wrap_path
from .po_io import load_po, patch_msgstr_by_uid, save_po
from .rules import apply_rules_to_path, load_rules, rule_to_dict
from .search import SearchResult, search_path
from .validation import format_text_report, validate_path, write_reports
from .text_utils import clt_tags, generic_tags, has_bad_unicode, nfc, order_number, placeholders_by_type, visible_len, visible_text

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
    done = pyqtSignal()


class LogBox(QTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
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
        self.resize(1220, 780)
        self._apply_style()
        self._build()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: Segoe UI, Arial;
                font-size: 10pt;
            }}
            QTabWidget::pane {{
                border: 1px solid #3a4058;
                border-radius: 12px;
                background: {BG};
            }}
            QTabBar::tab {{
                background: {PANEL};
                color: {MUTED};
                padding: 10px 18px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
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
                border-radius: 9px;
                padding: 7px;
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
                padding: 8px;
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
                padding: 8px;
                border: 0;
                border-right: 1px solid #3a4058;
                font-weight: 800;
            }}
            QPushButton {{
                background: {ACCENT};
                color: #28131e;
                border: 0;
                border-radius: 9px;
                padding: 9px 13px;
                font-weight: 800;
            }}
            QPushButton:hover {{ background: {ACCENT_SOFT}; }}
            QPushButton:pressed {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QPushButton:disabled {{ background: #465066; color: #968da6; }}
            QPushButton#dangerButton {{ background: {BAD}; color: #241018; }}
            QPushButton#secondaryButton {{ background: {PANEL_3}; color: {TEXT}; border: 1px solid #46506a; }}
            QPushButton#secondaryButton:hover {{ background: #46506a; color: {ACCENT_SOFT}; }}
            QCheckBox {{ spacing: 7px; color: {TEXT}; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; }}
            QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT_SOFT}; border-radius: 4px; }}
            QCheckBox::indicator:unchecked {{ background: {PANEL}; border: 1px solid #657089; border-radius: 4px; }}
            QGroupBox {{
                border: 1px solid #3a4058;
                border-radius: 12px;
                margin-top: 12px;
                padding: 14px;
                font-weight: 800;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {ACCENT};
            }}
            QLabel#title {{
                font-size: 19pt;
                font-weight: 900;
                color: {ACCENT};
                letter-spacing: 0.5px;
            }}
            QLabel#muted {{ color: {MUTED}; }}
            QTextEdit#logBox {{ background: #101521; border: 1px solid #3a4058; }}
            QListWidget#pathList::item {{ padding: 7px; border-radius: 6px; }}
            QListWidget#pathList::item:selected {{ background: {ACCENT_DARK}; color: {WHITE}; }}
            QSplitter::handle {{ background: #2a3144; }}
            """
        )

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

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
        self._build_translate_tab()
        self._build_backup_tab()

    def _new_tab(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
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
            lengths = [visible_len(line) for line in fixed.splitlines()] or [0]
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
        wrap_btn = self._button("Wrap Line", secondary=True)
        save_btn = self._button("Save msgstr")
        edit_buttons.addWidget(open_btn)
        edit_buttons.addWidget(wrap_btn)
        edit_buttons.addWidget(save_btn)
        right_layout.addLayout(edit_buttons)

        replace_group = QGroupBox("Find / Replace in Results")
        repl_layout = QGridLayout(replace_group)
        find_edit = QLineEdit()
        repl_edit = QLineEdit()
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

        def fill_table() -> None:
            table.setRowCount(0)
            for idx, result in enumerate(self.search_results, start=1):
                row = table.rowCount()
                table.insertRow(row)
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
                    item.setData(Qt.ItemDataRole.UserRole, idx - 1)
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
            table.resizeRowsToContents()
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

        def wrap_current_msgstr() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            soft_spin = getattr(self, "linewrap_soft_spin", None)
            hard_spin = getattr(self, "linewrap_hard_spin", None)
            cuts_spin = getattr(self, "linewrap_cuts_spin", None)
            soft_value = int(soft_spin.value()) if soft_spin is not None else int(self.config.get("soft_limit", 58))
            hard_value = int(hard_spin.value()) if hard_spin is not None else int(self.config.get("hard_limit", 64))
            cuts_value = int(cuts_spin.value()) if cuts_spin is not None else int(self.config.get("max_cuts", 2))
            fixed, changed = wrap_msgstr(
                msgstr_box.toPlainText(),
                soft=soft_value,
                hard=hard_value,
                max_cuts=cuts_value,
            )
            msgstr_box.setPlainText(fixed)
            if changed:
                status.setText(f"Wrapped line in editor. Click Save msgstr to write. Soft={soft_value}, Hard={hard_value}, Cuts={cuts_value}.")
            else:
                status.setText("Line wrap made no change.")

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
            if not find_edit.text().strip():
                find_edit.setText(text)

        def open_selected_file() -> None:
            idx = current_result_index()
            if idx is None:
                status.setText("Select a result first.")
                return
            if not self._open_external(self.search_results[idx].file):
                QMessageBox.warning(self, "Open file", f"Could not open file:\n{self.search_results[idx].file}")

        def compile_find() -> re.Pattern[str] | None:
            needle = find_edit.text()
            if not needle:
                status.setText("Find is empty.")
                return None
            pattern = re.escape(needle)
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
            repl = repl_edit.text()
            updates: dict[int, str] = {}
            total_hits = 0
            for idx in indices:
                before = self.search_results[idx].msgstr
                after, hits = pattern.subn(repl, before)
                if hits:
                    updates[idx] = after
                    total_hits += hits
            changed = save_updates(updates)
            status.setText(f"Replaced {total_hits} hit(s) in {changed} result(s).")

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
        wrap_btn.clicked.connect(wrap_current_msgstr)
        save_btn.clicked.connect(save_current)
        msgstr_box.installEventFilter(self)
        prev_btn.clicked.connect(lambda: find_step(-1))
        next_btn.clicked.connect(lambda: find_step(1))
        current_btn.clicked.connect(replace_current)
        selected_btn.clicked.connect(lambda: replace_indices(selected_result_indices()))
        all_btn.clicked.connect(lambda: replace_indices(list(range(len(self.search_results)))))

    # ---------------- Gemini Web ----------------
    def _build_translate_tab(self) -> None:
        _tab, layout = self._new_tab("Gemini Web")
        path_edit = self._path_row(layout, "Folder", "last_path")
        form = QFormLayout()
        cdp_edit = QLineEdit(str(self.config.get("gemini_web_cdp_url", "http://localhost:9222")))
        form.addRow("Chrome CDP", cdp_edit)
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
        run_btn = self._button("Run Gemini Web")
        chrome_btn = self._button("Open Chrome", secondary=True)
        row.addWidget(run_btn)
        row.addWidget(chrome_btn)
        layout.addLayout(row)
        log = self._make_log()
        layout.addWidget(log, 1)

        def save_web_config() -> None:
            self.config["gemini_web_cdp_url"] = cdp_edit.text().strip() or "http://localhost:9222"
            self.config["gemini_web_max_files"] = max_files.value()
            self.config["gemini_web_max_lines"] = max_lines.value()
            self.config["gemini_web_max_entries"] = max_entries.value()
            self.config["gemini_web_wait_seconds"] = wait_seconds.value()
            self.config["gemini_web_timeout_seconds"] = timeout_seconds.value()
            self.config["gemini_web_retries"] = retries.value()
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

        def open_chrome(logwrite):
            save_web_config()
            cmd = open_chrome_debug(cdp_url=cdp_edit.text().strip() or "http://localhost:9222", user_data_dir=DEFAULT_CHROME_USER_DATA_DIR)
            logwrite("Chrome opened with remote debugging.", "good")
            logwrite("Login to Gemini in that Chrome window, then click Run Gemini Web.")
            logwrite("Command: " + " ".join(str(x) for x in cmd))

        run_btn.clicked.connect(lambda: self._run_threaded(run_btn, log, run_web))
        chrome_btn.clicked.connect(lambda: self._run_threaded(chrome_btn, log, open_chrome))

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
            n = sync_by_filename(source_edit.text().strip(), target_edit.text().strip())
            logwrite(f"Files synced: {n}", "good")

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
