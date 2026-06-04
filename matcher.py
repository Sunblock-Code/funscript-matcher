#!/usr/bin/env python3
"""Funscript Matcher — local desktop GUI (PySide6 / Qt).

Native Qt application with hardware-accelerated rendering. Pick a folder,
scan to auto-pair each funscript with its closest video by filename
similarity, review or override matches, then move or copy them into an
output folder. Each matched pair lands in its own subfolder named after
the funscript's base name. Multi-axis scripts (.roll, .pitch, …) travel
together. The per-row "Find" button searches the funscript's name on
PornHub, Eporner, FapTap, Eroscripts, etc.

Run:  python matcher.py     (or double-click matcher.bat)
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
import traceback
import urllib.parse
import webbrowser
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from difflib import SequenceMatcher
from pathlib import Path

try:
    import rarfile  # type: ignore
    _HAS_RAR = True
except ImportError:
    rarfile = None  # type: ignore
    _HAS_RAR = False

ARCHIVE_EXTS = {".zip", ".rar"}

CONFIG_FILE = Path(__file__).parent / "matcher_config.json"


def _find_7z() -> Path | None:
    """Locate a 7-Zip CLI executable. Used as a fallback when Python's
    zipfile chokes on malformed/repacked archives (mismatched headers,
    bad CRC, etc.)."""
    p = shutil.which("7z")
    if p:
        return Path(p)
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ):
            cp = Path(cand)
            if cp.exists():
                return cp
    return None


_SEVENZ_PATH: Path | None = _find_7z()


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
except ImportError:
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk(); _r.withdraw()
        _mb.showerror(
            "Setup needed",
            "This app needs PySide6.\n\n"
            "Run:\n  pip install PySide6\n\n"
            "(matcher.bat installs it automatically on first run.)"
        )
        _r.destroy()
    except Exception:
        print("Install with:  pip install PySide6")
    sys.exit(1)

VIDEO_EXTS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv",
    ".m4v", ".flv", ".mpg", ".mpeg", ".ts", ".m2ts",
}
AXIS_SUFFIXES = (".roll", ".pitch", ".surge", ".sway", ".twist", ".yaw")

# Search sites for the per-row Find button. Edit freely.
# {q} is replaced with the URL-encoded funscript base name.
SEARCH_SITES = {
    "video": [
        ("PornHub", "https://www.pornhub.com/video/search?search={q}"),
        ("Eporner", "https://www.eporner.com/search/{q}/"),
        ("xHamster", "https://xhamster.com/search/{q}"),
        ("SpankBang", "https://spankbang.com/s/{q}/"),
    ],
    "script": [
        ("FapTap", "https://faptap.net/search?q={q}"),
        ("Eroscripts", "https://discuss.eroscripts.com/search?q={q}"),
    ],
}


def open_search(template: str, query: str) -> None:
    if not query:
        return
    url = template.replace("{q}", urllib.parse.quote(query))
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def normalize(s: str) -> str:
    return "".join(c.lower() for c in s if c.isalnum())


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def split_axis(stem: str):
    low = stem.lower()
    for sfx in AXIS_SUFFIXES:
        if low.endswith(sfx):
            return stem[: -len(sfx)], stem[-len(sfx):]
    return stem, ""


STYLE = """
* { font-family: "Segoe UI", -apple-system, sans-serif; }
QMainWindow, QWidget { background: #0d1117; color: #f0f6fc; font-size: 14px; }

QFrame#card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
}

QLabel#title { font-size: 22px; font-weight: 700; color: #f0f6fc; letter-spacing: -0.01em; }
QLabel#subtitle { color: #a4abb5; font-size: 13px; }
QLabel#field-label { color: #f0f6fc; font-size: 17px; font-weight: 700; letter-spacing: 0.2px; background: transparent; }
QLabel#status { color: #a4abb5; font-size: 13px; }
QLabel#min-score-label {
    color: #a4abb5;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.2px;
}
QLabel#min-score-chip {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 3px 10px;
    color: #79c0ff;
    font-family: Consolas, "SF Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
QLabel#empty { color: #a4abb5; padding: 80px; font-size: 15px; }
QLabel#fs-name { color: #f0f6fc; font-family: Consolas, "SF Mono", monospace; font-size: 14px; background: transparent; }
QLabel#fs-axes { color: #a4abb5; font-family: Consolas, monospace; font-size: 12px; background: transparent; }

QLineEdit {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    color: #f0f6fc;
    selection-background-color: #2f81f7;
    font-family: Consolas, monospace;
    font-size: 14px;
}
QLineEdit:hover { border-color: #484f58; }
QLineEdit:focus { border: 1px solid #2f81f7; }

QPushButton {
    background: #22272e;
    border: 1px solid #30363d;
    border-radius: 7px;
    padding: 8px 18px;
    color: #f0f6fc;
    font-weight: 500;
    font-size: 14px;
}
QPushButton:hover { background: #2d333b; border-color: #484f58; }
QPushButton:pressed { background: #1c2128; }
QPushButton:disabled { color: #6e7681; background: #1c2128; }

QPushButton#primary {
    background: #2f81f7;
    border: 1px solid #2f81f7;
    color: white;
}
QPushButton#primary:hover { background: #4493f8; border-color: #4493f8; }
QPushButton#primary:pressed { background: #1f6feb; border-color: #1f6feb; }
QPushButton#primary:disabled { background: #1c2128; border-color: #30363d; color: #6e7681; }

QPushButton#find {
    background: #22272e;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 10px;
    color: #d8dee4;
    font-size: 13px;
}
QPushButton#find:hover { background: #2d333b; border-color: #484f58; }

QPushButton#skip-btn {
    background: #22272e;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 12px;
    color: #d8dee4;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#skip-btn:hover { background: #2d333b; border-color: #484f58; }
QPushButton#skip-btn:checked {
    background: #3a1416;
    color: #ff7b72;
    border-color: #f85149;
}
QPushButton#skip-btn:checked:hover {
    background: #4a1c1f;
    border-color: #ff7b72;
}

QPushButton#apply-single {
    background: #0d2030;
    border: 1px solid #2f81f7;
    border-radius: 6px;
    padding: 4px 12px;
    color: #79c0ff;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#apply-single:hover {
    background: #14385a;
    color: #ffffff;
    border-color: #58a6ff;
}
QPushButton#apply-single:disabled {
    background: #161b22;
    color: #6e7681;
    border-color: #30363d;
}

QCheckBox { color: #d8dee4; spacing: 8px; font-size: 14px; }
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #484f58;
    border-radius: 4px;
    background: #1c2128;
}
QCheckBox::indicator:hover { border-color: #6e7681; }
QCheckBox::indicator:checked {
    background: #2f81f7;
    border-color: #2f81f7;
}

QComboBox {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 12px;
    color: #f0f6fc;
    font-size: 13px;
    min-height: 24px;
}
QComboBox:hover { border-color: #484f58; }
QComboBox:focus { border: 1px solid #2f81f7; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView {
    background: #1c2128;
    border: 1px solid #30363d;
    selection-background-color: #2f81f7;
    selection-color: white;
    color: #f0f6fc;
    padding: 4px;
    outline: 0;
    font-size: 13px;
}

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: transparent;
    width: 14px;
    border: none;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #30363d;
    border-radius: 5px;
    min-height: 30px;
    margin: 3px;
}
QScrollBar::handle:vertical:hover { background: #484f58; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QSlider::groove:horizontal {
    background: #1c2128;
    border: 1px solid #30363d;
    height: 6px;
    border-radius: 4px;
}
QSlider::sub-page:horizontal {
    background: #2f81f7;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: #f0f6fc;
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    border: 1px solid #30363d;
}
QSlider::handle:horizontal:hover {
    background: #ffffff;
    border-color: #2f81f7;
}

QScrollBar:horizontal {
    background: transparent;
    height: 14px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #30363d;
    border-radius: 5px;
    min-width: 30px;
    margin: 3px;
}
QScrollBar::handle:horizontal:hover { background: #484f58; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; background: none; }

QTableWidget {
    background: #161b22;
    border: none;
    color: #f0f6fc;
    gridline-color: transparent;
    selection-background-color: transparent;
    outline: 0;
}
QTableWidget::item {
    padding: 0;
    border-bottom: 1px solid #21262d;
    background: #161b22;
}
QTableWidget::item:hover {
    background: #1c2128;
}

QHeaderView { background: #161b22; }
QHeaderView::section {
    background: #161b22;
    color: #f0f6fc;
    padding: 14px 18px;
    border: none;
    border-bottom: 1px solid #30363d;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
QHeaderView::section:first {
    border-top-left-radius: 11px;
}
QHeaderView::section:last {
    border-top-right-radius: 11px;
}
QHeaderView::section:hover {
    background: #1c2128;
    color: #ffffff;
}
QTableCornerButton::section {
    background: #161b22;
    border: none;
    border-bottom: 1px solid #30363d;
}

QToolTip {
    background: #1c2128;
    color: #f0f6fc;
    border: 1px solid #30363d;
    padding: 6px 10px;
    border-radius: 5px;
    font-size: 13px;
}

QMessageBox { background: #161b22; }
QMessageBox QLabel { color: #f0f6fc; background: transparent; font-size: 14px; }
QMessageBox QPushButton { min-width: 90px; min-height: 32px; }

QMenu {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 7px;
    padding: 5px;
    color: #f0f6fc;
    font-size: 14px;
}
QMenu::item {
    padding: 8px 22px;
    border-radius: 4px;
    color: #f0f6fc;
}
QMenu::item:selected {
    background: #2f81f7;
    color: white;
}
QMenu::item:disabled {
    color: #6e7681;
    font-size: 12px;
    font-weight: 600;
    padding: 8px 16px 4px 16px;
}
QMenu::separator {
    height: 1px;
    background: #30363d;
    margin: 5px 8px;
}

QFrame#log-panel {
    background: #0d1117;
}
QSplitter#main-splitter::handle:horizontal {
    background: #30363d;
    width: 4px;
}
QSplitter#main-splitter::handle:horizontal:hover {
    background: #2f81f7;
}
QLabel#log-title {
    color: #f0f6fc;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1.4px;
}
QPushButton#log-action {
    background: transparent;
    border: 1px solid #30363d;
    border-radius: 5px;
    padding: 3px 10px;
    color: #a4abb5;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#log-action:hover {
    background: #1c2128;
    color: #f0f6fc;
    border-color: #484f58;
}
QPushButton#log-close {
    background: transparent;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #d8dee4;
    font-size: 22px;
    font-weight: 500;
    padding: 0 0 4px 0;
    text-align: center;
}
QPushButton#log-close:hover {
    background: #1c2128;
    color: #ffffff;
    border-color: #2f81f7;
}
QPushButton#log-toggle {
    background: #22272e;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 14px;
    color: #d8dee4;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#log-toggle:hover { background: #2d333b; border-color: #484f58; }
QPushButton#log-toggle:checked {
    background: #0d2030;
    border-color: #2f81f7;
    color: #79c0ff;
}
QPushButton#donate-btn {
    background: #22272e;
    border: 1px solid #e0709a;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e0709a;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#donate-btn:hover { background: #2a1c25; border-color: #f49ac0; color: #f49ac0; }
QPushButton#donate-btn:pressed { background: #1c2128; }
QTextEdit#log-view {
    background: transparent;
    border: none;
    padding: 4px 2px;
    color: #d8dee4;
    font-family: Consolas, "SF Mono", monospace;
    font-size: 13px;
    selection-background-color: #2f81f7;
}

QPushButton#add-source {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 18px;
    color: #d8dee4;
    font-size: 14px;
    font-weight: 500;
}
QPushButton#add-source:hover {
    background: #22272e;
    border-color: #2f81f7;
    color: #2f81f7;
}
QPushButton#add-source:pressed {
    background: #161b22;
}
QPushButton#source-remove {
    background: transparent;
    border: 1px solid #5a2528;
    border-radius: 7px;
    color: #ff7b72;
    font-size: 22px;
    font-weight: 700;
}
QPushButton#source-remove:hover {
    background: #3a1416;
    color: #ff7b72;
    border-color: #f85149;
}
QPushButton#source-remove:disabled {
    background: transparent;
    color: transparent;
    border: 1px solid transparent;
}
QLineEdit#source-title {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 8px;
    color: #f0f6fc;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.2px;
    selection-background-color: #2f81f7;
}
QLineEdit#source-title:hover {
    background: #1c2128;
    border: 1px solid #30363d;
}
QLineEdit#source-title:focus {
    background: #1c2128;
    border: 1px solid #2f81f7;
}
QFrame#section-divider {
    background: #21262d;
    border: none;
}
QLineEdit#combo-search {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 5px;
    padding: 4px 8px;
    color: #f0f6fc;
    font-size: 13px;
    selection-background-color: #2f81f7;
}
QLineEdit#combo-search:focus {
    border: 1px solid #2f81f7;
}
"""


class Chip(QtWidgets.QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(64, 28)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.set_score(None)

    def set_score(self, score):
        if score is None:
            bg, fg, text = "#30363d", "#a4abb5", "—"
        elif score >= 0.7:
            bg, fg, text = "#1a4327", "#56d364", f"{score:.2f}"
        elif score >= 0.4:
            bg, fg, text = "#3a2e0a", "#e3b341", f"{score:.2f}"
        else:
            bg, fg, text = "#3a1416", "#ff7b72", f"{score:.2f}"
        self.setText(text)
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; "
            f"border-radius: 14px; font-weight: 700; font-size: 13px;"
        )


class ScoreItemDelegate(QtWidgets.QStyledItemDelegate):
    """Combo dropdown delegate: paints the filename on the left and the
    similarity score on the right. The score lives in Qt.UserRole so the
    button display (combo.currentText) stays as just the filename."""

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""

        widget = opt.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)

        score = index.data(QtCore.Qt.UserRole)
        if not isinstance(score, (int, float)):
            score = None

        rect = option.rect.adjusted(12, 0, -12, 0)
        fm = QtGui.QFontMetrics(option.font)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)

        painter.save()
        score_text = "" if score is None else f"{score:.2f}"
        score_w = fm.horizontalAdvance(score_text) + 14 if score_text else 0
        text_rect = QtCore.QRect(rect.left(), rect.top(),
                                 max(0, rect.width() - score_w), rect.height())

        painter.setPen(QtGui.QColor("#ffffff" if selected else "#f0f6fc"))
        elided = fm.elidedText(text, QtCore.Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, elided)

        if score_text:
            if score >= 0.7:
                color = "#56d364"
            elif score >= 0.4:
                color = "#e3b341"
            else:
                color = "#7d8590"
            painter.setPen(QtGui.QColor("#cce0ff" if selected else color))
            painter.drawText(rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, score_text)
        painter.restore()


def _centered(widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Wrap a widget so it sits centered inside a table cell."""
    holder = QtWidgets.QWidget()
    holder.setObjectName("cell-wrap")
    layout = QtWidgets.QHBoxLayout(holder)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setAlignment(QtCore.Qt.AlignCenter)
    layout.addWidget(widget)
    return holder


class _NoWheelComboBox(QtWidgets.QComboBox):
    """Combo that ignores wheel events so scrolling the matches list
    doesn't accidentally change the matched video."""

    def wheelEvent(self, event):
        event.ignore()


class _SearchableComboBox(_NoWheelComboBox):
    """Combo with a search bar at the top of the dropdown popup. Typing
    substring-filters the items (case-insensitive) by hiding rows. The
    first item is treated as 'always visible' so the '(skip)' entry
    stays selectable. Arrow keys / Enter / Esc work from the search box."""

    def __init__(self):
        super().__init__()
        self._search: QtWidgets.QLineEdit | None = None

    def showPopup(self):
        super().showPopup()
        view = self.view()
        if view is None:
            return
        container = view.parent()
        if container is None:
            return
        if self._search is None or self._search.parent() is not container:
            if self._search is not None:
                self._search.deleteLater()
            self._search = QtWidgets.QLineEdit(container)
            self._search.setObjectName("combo-search")
            self._search.setPlaceholderText("Search…")
            self._search.setClearButtonEnabled(True)
            self._search.textChanged.connect(self._filter_items)
            self._search.installEventFilter(self)
            container.installEventFilter(self)
        # Reset any previous filter state
        for i in range(self.count()):
            view.setRowHidden(i, False)
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._reflow_popup()
        self._search.show()
        self._search.raise_()
        self._search.setFocus(QtCore.Qt.PopupFocusReason)

    def hidePopup(self):
        view = self.view()
        if view is not None:
            for i in range(self.count()):
                view.setRowHidden(i, False)
        if self._search is not None:
            self._search.clear()
        super().hidePopup()

    def _reflow_popup(self):
        view = self.view()
        if view is None or self._search is None:
            return
        container = view.parent()
        if container is None:
            return
        margin = 4
        search_h = 28
        cw = container.width()
        ch = container.height()
        self._search.setGeometry(margin, margin, cw - 2 * margin, search_h)
        view_y = search_h + margin * 2
        view.setGeometry(0, view_y, cw, max(0, ch - view_y))

    def eventFilter(self, watched, event):
        view = self.view()
        if (view is not None
                and watched is view.parent()
                and event.type() == QtCore.QEvent.Resize):
            self._reflow_popup()
            return False
        if self._search is not None and watched is self._search:
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                if key in (
                    QtCore.Qt.Key_Down, QtCore.Qt.Key_Up,
                    QtCore.Qt.Key_PageDown, QtCore.Qt.Key_PageUp,
                    QtCore.Qt.Key_Home, QtCore.Qt.Key_End,
                ):
                    if view is not None:
                        QtWidgets.QApplication.sendEvent(view, event)
                    return True
                if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    if view is not None and view.currentIndex().isValid():
                        idx = view.currentIndex().row()
                        self.setCurrentIndex(idx)
                        # Treat search-Enter as a real user activation so
                        # the row gets flagged as manually picked.
                        self.activated.emit(idx)
                    self.hidePopup()
                    return True
                if key == QtCore.Qt.Key_Escape:
                    self.hidePopup()
                    return True
        return super().eventFilter(watched, event)

    def _filter_items(self, text: str):
        query = text.strip().lower()
        view = self.view()
        if view is None:
            return
        first_match = -1   # First real (non-skip) item matching the query.
        for i in range(self.count()):
            if i == 0:
                # "(skip)" is always visible but never auto-highlighted by search.
                view.setRowHidden(i, False)
                continue
            item_text = self.itemText(i).lower()
            visible = (not query) or (query in item_text)
            view.setRowHidden(i, not visible)
            if visible and query and first_match < 0:
                first_match = i
        # Only move the highlight when there's a query AND a real match.
        # If the query has no matches, leave the popup's highlight on the
        # combo's current selection so pressing Enter (or closing the popup)
        # never drops the user's existing pick onto "(skip)".
        if query and first_match >= 0:
            model = view.model()
            if model is not None:
                view.setCurrentIndex(model.index(first_match, 0))


class _SourceTitle(QtWidgets.QLineEdit):
    """Folder-name display for a source row. Acts as a read-only label
    until double-clicked, then enters rename mode. On commit, renames the
    folder on disk and emits the new path."""

    rename_done = QtCore.Signal(str)

    def __init__(self, path: str = "", placeholder: str = ""):
        super().__init__()
        self.setObjectName("source-title")
        self.setMinimumWidth(180)
        self.setMinimumHeight(44)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setReadOnly(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._placeholder = placeholder
        self._path = ""
        self._committed = ""
        self._editing = False
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.set_path(path)
        self.editingFinished.connect(self._commit)

    def set_path(self, path: str):
        self._path = path or ""
        base = Path(self._path).name if self._path else ""
        if not self._editing:
            self._committed = base
            self.setText(base)
        if base:
            self.setToolTip(f"{base}\n\nDouble-click to rename folder")
        elif self._placeholder:
            self.setToolTip(f"Pick a {self._placeholder.lower()} folder first")
        else:
            self.setToolTip("Pick a folder to enable rename")

    def _enter_edit_mode(self):
        if not self._path:
            return
        self._editing = True
        self.setReadOnly(False)
        self.setCursor(QtCore.Qt.IBeamCursor)
        self.setAlignment(QtCore.Qt.AlignLeft)
        self.selectAll()
        self.setFocus(QtCore.Qt.OtherFocusReason)

    def _exit_edit_mode(self):
        self._editing = False
        self.setReadOnly(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAlignment(QtCore.Qt.AlignCenter)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if not self._editing:
            self._enter_edit_mode()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self._editing:
            self._commit()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape and self._editing:
            self._exit_edit_mode()
            self.setText(self._committed)
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def _commit(self):
        if not self._editing:
            return
        new_name = self.text().strip()
        self._exit_edit_mode()
        if not new_name or not self._path:
            self.setText(self._committed)
            return
        old_path = Path(self._path)
        if old_path.name == new_name:
            self.setText(self._committed)
            return
        if not old_path.exists():
            QtWidgets.QMessageBox.warning(
                self, "Rename failed",
                f"The folder no longer exists at:\n{self._path}",
            )
            self.setText(self._committed)
            return
        if any(c in new_name for c in '/\\:*?"<>|'):
            QtWidgets.QMessageBox.warning(
                self, "Invalid name",
                'Folder names cannot contain: / \\ : * ? " < > |',
            )
            self.setText(self._committed)
            return
        new_path = old_path.parent / new_name
        if new_path.exists():
            QtWidgets.QMessageBox.warning(
                self, "Rename failed",
                f"A folder named '{new_name}' already exists here.",
            )
            self.setText(self._committed)
            return
        try:
            old_path.rename(new_path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, "Rename failed", f"Could not rename folder:\n{exc}"
            )
            self.setText(self._committed)
            return
        self._path = str(new_path)
        self._committed = new_name
        self.setText(new_name)
        self.setToolTip(f"{new_name}\n\nDouble-click to rename folder")
        self.rename_done.emit(self._path)


class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Funscript Matcher")
        self.resize(1640, 820)
        self.setMinimumSize(1000, 540)

        self.scan_src: Path | None = None
        self.row_widgets: list[dict] = []
        self._score_delegate = ScoreItemDelegate(self)

        self._build_ui()
        self._load_state()

    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        main_h = QtWidgets.QHBoxLayout(cw)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        self._main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._main_splitter.setObjectName("main-splitter")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(4)
        main_h.addWidget(self._main_splitter)

        left = QtWidgets.QWidget()
        self._main_splitter.addWidget(left)
        L = QtWidgets.QVBoxLayout(left)
        L.setContentsMargins(24, 22, 24, 22)
        L.setSpacing(14)

        # Title row with log toggle on the right (wrapped in a widget so it
        # can be collapsed in compact mode)
        self._title_widget = QtWidgets.QWidget()
        title_row = QtWidgets.QHBoxLayout(self._title_widget)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title_box.setContentsMargins(0, 0, 0, 6)
        t = QtWidgets.QLabel("Funscript Matcher"); t.setObjectName("title")
        s = QtWidgets.QLabel("Pair scripts with videos and rename in one click")
        s.setObjectName("subtitle")
        title_box.addWidget(t)
        title_box.addWidget(s)
        title_row.addLayout(title_box)
        title_row.addStretch()

        self.donate_btn = QtWidgets.QPushButton("♥  Donate")
        self.donate_btn.setObjectName("donate-btn")
        self.donate_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.donate_btn.setMinimumHeight(34)
        self.donate_btn.clicked.connect(self._show_donate)
        title_row.addWidget(self.donate_btn, 0, QtCore.Qt.AlignTop)

        self.log_toggle_btn = QtWidgets.QPushButton("Log  ▸")
        self.log_toggle_btn.setObjectName("log-toggle")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.log_toggle_btn.setMinimumHeight(34)
        self.log_toggle_btn.setMinimumWidth(82)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        title_row.addWidget(self.log_toggle_btn, 0, QtCore.Qt.AlignTop)

        L.addWidget(self._title_widget)

        # Splitter `self._main_splitter` is used later to attach the log panel.

        # Paths card with dynamic sources + single output
        self._folder_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon)
        self.source_rows: list[dict] = []

        paths_card = QtWidgets.QFrame(); paths_card.setObjectName("card")
        pcv = QtWidgets.QVBoxLayout(paths_card)
        pcv.setContentsMargins(24, 22, 24, 22)
        pcv.setSpacing(12)

        # Sources stack (rows are appended by _add_source_row)
        self.sources_container = QtWidgets.QVBoxLayout()
        self.sources_container.setSpacing(10)
        pcv.addLayout(self.sources_container)
        self._add_source_row(initial=True)

        # "+ Add another source" button — aligned under the entries
        add_src_row = QtWidgets.QHBoxLayout()
        add_src_row.setContentsMargins(180 + 14, 0, 0, 0)
        self.add_source_btn = QtWidgets.QPushButton("+ Add another source")
        self.add_source_btn.setObjectName("add-source")
        self.add_source_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.add_source_btn.setMinimumHeight(44)
        self.add_source_btn.clicked.connect(lambda: self._add_source_row())
        add_src_row.addWidget(self.add_source_btn, 0, QtCore.Qt.AlignLeft)
        add_src_row.addStretch()
        pcv.addLayout(add_src_row)

        # Divider between sources block and output
        pcv.addSpacing(6)
        divider = QtWidgets.QFrame()
        divider.setObjectName("section-divider")
        divider.setFixedHeight(1)
        pcv.addWidget(divider)
        pcv.addSpacing(6)

        # Output row
        out_row = QtWidgets.QHBoxLayout()
        out_row.setSpacing(14)
        self.output_title = _SourceTitle("", placeholder="Output")
        out_row.addWidget(self.output_title, 0, QtCore.Qt.AlignVCenter)
        self.output_entry = QtWidgets.QLineEdit()
        self.output_entry.setPlaceholderText("Leave blank to use the first source folder")
        self.output_entry.setMinimumHeight(44)
        self.output_entry.addAction(self._folder_icon, QtWidgets.QLineEdit.LeadingPosition)
        out_row.addWidget(self.output_entry, 1)

        self.output_entry.textChanged.connect(
            lambda t, w=self.output_title: w.set_path(t.strip())
        )

        def _on_output_rename(new_path: str):
            if sys.platform == "win32":
                new_path = new_path.replace("/", "\\")
            self.output_entry.setText(new_path)

        self.output_title.rename_done.connect(_on_output_rename)
        out_browse = QtWidgets.QPushButton("Browse…")
        out_browse.setMinimumHeight(44)
        out_browse.setMinimumWidth(118)
        out_browse.setCursor(QtCore.Qt.PointingHandCursor)
        out_browse.clicked.connect(lambda: self._pick_for(self.output_entry, "output"))
        out_row.addWidget(out_browse)
        out_placeholder = QtWidgets.QPushButton("✕")
        out_placeholder.setObjectName("source-remove")
        out_placeholder.setFixedSize(48, 44)
        out_placeholder.setEnabled(False)
        out_placeholder.setFocusPolicy(QtCore.Qt.NoFocus)
        out_row.addWidget(out_placeholder)
        pcv.addLayout(out_row)

        L.addWidget(paths_card)
        self._paths_widget = paths_card

        # Options grid — checkboxes align vertically in columns across both
        # rows. Wrapped in a widget so we can collapse it in compact mode.
        self._opts_widget = QtWidgets.QWidget()
        opts = QtWidgets.QGridLayout(self._opts_widget)
        opts.setHorizontalSpacing(22)
        opts.setVerticalSpacing(10)
        opts.setContentsMargins(0, 0, 0, 0)

        self.move_check = QtWidgets.QCheckBox("Move (instead of copy)")
        self.move_check.setChecked(True)
        opts.addWidget(self.move_check, 0, 0)

        self.auto_check = QtWidgets.QCheckBox("Auto-pair 100% matches")
        self.auto_check.setToolTip("On scan, pair anything with a perfect score automatically.")
        opts.addWidget(self.auto_check, 0, 1)

        self.hide_skipped_check = QtWidgets.QCheckBox("Hide skipped")
        self.hide_skipped_check.setToolTip("Hide rows currently set to (skip).")
        self.hide_skipped_check.toggled.connect(self._apply_skip_filter)
        opts.addWidget(self.hide_skipped_check, 0, 2)

        self.alt_colors_check = QtWidgets.QCheckBox("Alternate row colors")
        self.alt_colors_check.setToolTip("Tint every other row for easier reading.")
        self.alt_colors_check.toggled.connect(lambda _: self._apply_alt_colors())
        opts.addWidget(self.alt_colors_check, 0, 3)

        self.recursive_check = QtWidgets.QCheckBox("Search subfolders")
        self.recursive_check.setToolTip(
            "Recurse into subdirectories. Folders that already look paired are left alone."
        )
        opts.addWidget(self.recursive_check, 1, 0)

        self.cross_source_check = QtWidgets.QCheckBox("Match across source folders")
        self.cross_source_check.setToolTip(
            "Allow scripts in one source folder to pair with videos in another."
        )
        opts.addWidget(self.cross_source_check, 1, 1)

        self.move_existing_check = QtWidgets.QCheckBox("Move existing paired subfolders")
        self.move_existing_check.setToolTip(
            "Also relocate already-paired subfolders inside the source(s) into the output folder."
        )
        opts.addWidget(self.move_existing_check, 1, 2)

        self.extract_check = QtWidgets.QCheckBox("Extract archives")
        rar_note = "" if _HAS_RAR else "  (RAR needs: pip install rarfile)"
        self.extract_check.setToolTip(
            "Auto-extract .zip / .rar files in the source(s) into a folder named after the "
            "archive, then delete the original archive." + rar_note
        )
        self.extract_check.setChecked(True)
        opts.addWidget(self.extract_check, 1, 3)

        # Trailing column eats remaining space so columns stay tight on the left
        opts.setColumnStretch(4, 1)
        L.addWidget(self._opts_widget)

        # Action row: min-match slider + Scan
        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(14)

        min_match_text = QtWidgets.QLabel("Min match")
        min_match_text.setObjectName("min-score-label")
        action_row.addWidget(min_match_text)

        self.min_score_label = QtWidgets.QLabel("1.00")
        self.min_score_label.setObjectName("min-score-chip")
        self.min_score_label.setAlignment(QtCore.Qt.AlignCenter)
        self.min_score_label.setMinimumWidth(58)
        action_row.addWidget(self.min_score_label)

        self.min_score_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.min_score_slider.setRange(0, 100)
        self.min_score_slider.setValue(100)
        self.min_score_slider.setFixedWidth(220)
        self.min_score_slider.setCursor(QtCore.Qt.PointingHandCursor)
        self.min_score_slider.valueChanged.connect(self._on_min_score_changed)
        action_row.addWidget(self.min_score_slider)

        action_row.addStretch()

        self.compact_btn = QtWidgets.QPushButton("Compact  ▴")
        self.compact_btn.setObjectName("log-toggle")
        self.compact_btn.setCheckable(True)
        self.compact_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.compact_btn.setMinimumHeight(30)
        self.compact_btn.setToolTip(
            "Hide the title, paths, and options to give the matches list more room"
        )
        self.compact_btn.clicked.connect(
            lambda checked: self._set_compact_mode(checked)
        )
        action_row.addWidget(self.compact_btn)

        L.addLayout(action_row)

        # Debounced auto-scan: source / options changes restart this timer,
        # and after 600ms of quiet it fires _scan once.
        self._scan_timer = QtCore.QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(600)
        self._scan_timer.timeout.connect(self._scan)

        # Matches card with resizable table
        matches_card = QtWidgets.QFrame(); matches_card.setObjectName("card")
        ml = QtWidgets.QVBoxLayout(matches_card)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "📄  Funscript",
            "🎬  Matched video",
            "⭐  Score",
            "⏭  Skip",
            "✅  Apply",
            "🔍  Find",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setAlternatingRowColors(False)
        self.table.setTextElideMode(QtCore.Qt.ElideRight)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QtWidgets.QHeaderView.Interactive)
        hh.resizeSection(0, 340)
        hh.resizeSection(2, 140)
        hh.resizeSection(3, 130)
        hh.resizeSection(4, 130)
        hh.resizeSection(5, 140)
        hh.setMinimumSectionSize(110)
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        hh.setSectionsClickable(True)
        hh.setCursor(QtCore.Qt.PointingHandCursor)
        hh.sectionClicked.connect(self._on_header_clicked)
        self._score_sort: str | None = None  # None | "desc" | "asc"
        sc_item = self.table.horizontalHeaderItem(2)
        if sc_item is not None:
            sc_item.setToolTip("Click to sort by score (toggles high→low / low→high)")

        ml.addWidget(self.table)
        L.addWidget(matches_card, 1)

        # Empty-state overlay (shown when table has no rows)
        self.empty_label = QtWidgets.QLabel("📁  Pick a source folder above and click Scan.")
        self.empty_label.setObjectName("empty")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setParent(self.table.viewport())
        self._reposition_empty()
        self.table.viewport().installEventFilter(self)

        # Footer
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(12)
        self.status_label = QtWidgets.QLabel("Pick a source folder to begin.")
        self.status_label.setObjectName("status")
        footer.addWidget(self.status_label)
        footer.addStretch()
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setMinimumHeight(40)
        self.apply_btn.setMinimumWidth(150)
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)
        footer.addWidget(self.apply_btn)
        L.addLayout(footer)

        # Right: collapsible log panel — full window height.
        self.log_panel = self._build_log_panel()
        self._main_splitter.addWidget(self.log_panel)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 0)
        self.log_panel.hide()

    # ----- log panel -----
    def _build_log_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("log-panel")
        panel.setMinimumWidth(280)

        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(14, 18, 14, 18)
        v.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        close_btn = QtWidgets.QPushButton("‹")
        close_btn.setObjectName("log-close")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setToolTip("Hide activity log")
        close_btn.clicked.connect(self._toggle_log_panel)
        header.addWidget(close_btn)

        title = QtWidgets.QLabel("ACTIVITY LOG")
        title.setObjectName("log-title")
        header.addWidget(title)
        header.addStretch()

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setObjectName("log-action")
        clear_btn.setCursor(QtCore.Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_log)
        header.addWidget(clear_btn)

        v.addLayout(header)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setObjectName("log-view")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        v.addWidget(self.log_view, 1)

        return panel

    def _toggle_log_panel(self):
        visible = not self.log_panel.isVisible()
        self.log_panel.setVisible(visible)
        self.log_toggle_btn.setChecked(visible)
        self.log_toggle_btn.setText("Log  ▾" if visible else "Log  ▸")
        if visible:
            # Defer the splitter sizing until after the event loop has had a
            # chance to paint — at startup the splitter has no real width yet
            # (the window hasn't been shown), so setSizes() right now would
            # be a no-op and the log panel could collapse to its minimum.
            QtCore.QTimer.singleShot(0, self._ensure_log_panel_width)
        if hasattr(self, "_save_state"):
            self._save_state()

    def _show_donate(self):
        """Donation popup — Ko-fi link + Monero (XMR) address with QR."""
        KOFI = "https://ko-fi.com/sunblockbukkake"
        XMR = ("8AKehPGkA4UTw92xa4xXp8Qa99ZfrUUHsE21Hi9bVz4d8j5aEVg"
               "UEPSgR69j7XMXTYYNhArcsjCivAfVZyJmRaNX9wBzLLk")

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Support development")
        dlg.setStyleSheet("QDialog { background: #0d1117; }")
        dlg.setMinimumWidth(420)

        v = QtWidgets.QVBoxLayout(dlg)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(8)

        title = QtWidgets.QLabel("Support development ♥")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f0f6fc;")
        v.addWidget(title)
        sub = QtWidgets.QLabel("If this tool saved you time, a tip is hugely appreciated.")
        sub.setStyleSheet("color: #a4abb5; font-size: 12px;")
        v.addWidget(sub)
        v.addSpacing(8)

        kofi = QtWidgets.QPushButton("☕   Support me on Ko-fi")
        kofi.setCursor(QtCore.Qt.PointingHandCursor)
        kofi.setMinimumHeight(46)
        kofi.setStyleSheet(
            "QPushButton { background: #ff5e5b; border: none; border-radius: 8px;"
            " color: #ffffff; font-size: 15px; font-weight: 700; }"
            " QPushButton:hover { background: #ff7472; }")
        kofi.clicked.connect(lambda: webbrowser.open(KOFI, new=2))
        v.addWidget(kofi)
        link = QtWidgets.QLabel("ko-fi.com/sunblockbukkake")
        link.setStyleSheet("color: #8b90a0; font-size: 11px;")
        v.addWidget(link)
        v.addSpacing(8)

        # ── Monero card ──
        card = QtWidgets.QFrame()
        card.setObjectName("xmr-card")
        # Scope to #xmr-card so the border/background does not cascade to child
        # QLabels (QLabel subclasses QFrame, so a bare "QFrame {…}" would match them).
        card.setStyleSheet(
            "QFrame#xmr-card { background: #161b22; border: 1px solid #30363d;"
            " border-radius: 12px; }")
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(16, 16, 16, 16)
        cv.setSpacing(10)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        badge = QtWidgets.QLabel("ɱ")
        badge.setFixedSize(34, 34)
        badge.setAlignment(QtCore.Qt.AlignCenter)
        badge.setStyleSheet(
            "background: #f26822; border-radius: 17px; color: #ffffff;"
            " font-size: 18px; font-weight: 700;")
        head.addWidget(badge, 0, QtCore.Qt.AlignTop)
        htxt = QtWidgets.QVBoxLayout()
        htxt.setSpacing(1)
        mt = QtWidgets.QLabel("Monero (XMR)")
        mt.setStyleSheet("color: #f0f6fc; font-size: 14px; font-weight: 700;")
        ms = QtWidgets.QLabel("Private crypto • send any amount")
        ms.setStyleSheet("color: #8b90a0; font-size: 10px;")
        htxt.addWidget(mt)
        htxt.addWidget(ms)
        head.addLayout(htxt)
        head.addStretch()
        copy_btn = QtWidgets.QPushButton("Copy")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.setStyleSheet(
            "QPushButton { background: #22272e; border: 1px solid #e0709a;"
            " border-radius: 8px; color: #e0709a; padding: 6px 12px;"
            " font-weight: 700; } QPushButton:hover { background: #2a1c25; }")

        def _copy():
            QtWidgets.QApplication.clipboard().setText(XMR)
            copy_btn.setText("✓ Copied")
            copy_btn.setStyleSheet(
                "QPushButton { background: #22272e; border: 1px solid #3fb950;"
                " border-radius: 8px; color: #3fb950; padding: 6px 12px;"
                " font-weight: 700; }")
            QtCore.QTimer.singleShot(1400, lambda: (
                copy_btn.setText("Copy"),
                copy_btn.setStyleSheet(
                    "QPushButton { background: #22272e; border: 1px solid #e0709a;"
                    " border-radius: 8px; color: #e0709a; padding: 6px 12px;"
                    " font-weight: 700; } QPushButton:hover { background: #2a1c25; }")))
        copy_btn.clicked.connect(_copy)
        head.addWidget(copy_btn, 0, QtCore.Qt.AlignTop)
        cv.addLayout(head)

        addr = QtWidgets.QLabel(XMR)
        addr.setWordWrap(True)
        addr.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        addr.setStyleSheet(
            "background: #0d1117; border: 1px solid #30363d; border-radius: 8px;"
            " color: #c9d1d9; font-family: Consolas, monospace; font-size: 11px;"
            " padding: 8px;")
        cv.addWidget(addr)

        try:
            qr_path = Path(__file__).parent / "monero_qr.png"
            if qr_path.exists():
                pix = QtGui.QPixmap(str(qr_path)).scaled(
                    200, 200, QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation)
                qlabel = QtWidgets.QLabel()
                qlabel.setPixmap(pix)
                qlabel.setAlignment(QtCore.Qt.AlignCenter)
                qlabel.setStyleSheet(
                    "background: #ffffff; border-radius: 10px; padding: 12px;")
                cv.addWidget(qlabel, 0, QtCore.Qt.AlignCenter)
                cap = QtWidgets.QLabel("Scan with a Monero wallet")
                cap.setAlignment(QtCore.Qt.AlignCenter)
                cap.setStyleSheet("color: #8b90a0; font-size: 11px;")
                cv.addWidget(cap)
        except Exception:
            pass

        v.addWidget(card)
        dlg.exec()

    def _ensure_log_panel_width(self):
        if not hasattr(self, "_main_splitter") or not self.log_panel.isVisible():
            return
        total = self._main_splitter.width()
        if total <= 0:
            # Splitter still hasn't been laid out — try again shortly.
            QtCore.QTimer.singleShot(50, self._ensure_log_panel_width)
            return
        sizes = self._main_splitter.sizes()
        log_w = sizes[1] if len(sizes) > 1 else 0
        if log_w < 280:
            default_log = max(360, total // 4)
            self._main_splitter.setSizes([total - default_log, default_log])

    def _set_compact_mode(self, compact: bool):
        """Hide the title, paths card, and options when compact=True so only
        the min-match slider, the matches list, and the footer remain."""
        visible = not compact
        if hasattr(self, "_title_widget"):
            self._title_widget.setVisible(visible)
        if hasattr(self, "_paths_widget"):
            self._paths_widget.setVisible(visible)
        if hasattr(self, "_opts_widget"):
            self._opts_widget.setVisible(visible)
        self.compact_btn.setChecked(compact)
        self.compact_btn.setText("Expand  ▾" if compact else "Compact  ▴")
        if hasattr(self, "_save_state"):
            self._save_state()

    def _clear_log(self):
        if hasattr(self, "log_view"):
            self.log_view.clear()

    def _log(self, msg: str):
        if not hasattr(self, "log_view"):
            return
        ts = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")

        # Classify by content. "Skipped"/"collision" beat the "!" prefix
        # so notices about pre-existing folders aren't mislabeled as errors.
        stripped = msg.lstrip()
        # Drop a leading "!" + whitespace for keyword detection
        body = stripped[1:].lstrip() if stripped.startswith("!") else stripped
        lower = body.lower()
        if any(k in lower for k in ("skipped", "collision", "skipping")):
            color = "#e3b341"   # amber
            tag = "WARN "
        elif any(k in lower for k in ("failed", "error", "couldn't", "could not")):
            color = "#ff7b72"   # red
            tag = "ERROR"
        elif stripped.startswith("!"):
            # Fallback for "!" lines without a clearer keyword
            color = "#e3b341"
            tag = "WARN "
        elif any(body.startswith(p) for p in (
            "Auto-paired", "Moved", "Copied", "Merged", "Renamed", "Extracted",
        )) or "extracted" in lower:
            color = "#56d364"   # green
            tag = "OK   "
        else:
            color = "#d8dee4"   # default
            tag = "INFO "

        safe = html.escape(msg).replace("  ", "&nbsp;&nbsp;")
        line = (
            f'<div style="margin: 2px 0;">'
            f'<span style="color: #6e7681;">{ts}</span> '
            f'<span style="color: {color}; font-weight: 600;">{tag}</span> '
            f'<span style="color: {color};">{safe}</span>'
            f'</div>'
        )
        self.log_view.append(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ----- persistence -----
    def _load_state(self):
        cfg = load_config()

        sources = cfg.get("sources")
        if not sources and cfg.get("source"):
            sources = [cfg["source"]]
        if sources:
            if sources[0]:
                self.source_rows[0]["entry"].setText(sources[0])
            for extra in sources[1:]:
                self._add_source_row(initial=False, path=extra)

        if cfg.get("output"):
            self.output_entry.setText(cfg["output"])
        if "move" in cfg:
            self.move_check.setChecked(bool(cfg["move"]))
        if "auto" in cfg:
            self.auto_check.setChecked(bool(cfg["auto"]))
        if "hide_skipped" in cfg:
            self.hide_skipped_check.setChecked(bool(cfg["hide_skipped"]))
        if "recursive" in cfg:
            self.recursive_check.setChecked(bool(cfg["recursive"]))
        if "cross_source" in cfg:
            self.cross_source_check.setChecked(bool(cfg["cross_source"]))
        if "move_existing" in cfg:
            self.move_existing_check.setChecked(bool(cfg["move_existing"]))
        if "extract" in cfg:
            self.extract_check.setChecked(bool(cfg["extract"]))
        if "alt_colors" in cfg:
            self.alt_colors_check.setChecked(bool(cfg["alt_colors"]))
        if cfg.get("log_visible"):
            self._toggle_log_panel()
        if cfg.get("compact"):
            self._set_compact_mode(True)
        # min_score intentionally not loaded from config — it always resets
        # to 1.00 on launch so each session starts at perfect-match only.

        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_state)
        for row in self.source_rows:
            row["entry"].textChanged.connect(self._save_timer.start)
            row["entry"].textChanged.connect(self._scan_timer.start)
        self.output_entry.textChanged.connect(self._save_timer.start)
        self.move_check.toggled.connect(self._save_state)
        self.auto_check.toggled.connect(self._save_state)
        self.hide_skipped_check.toggled.connect(self._save_state)
        self.recursive_check.toggled.connect(self._save_state)
        self.cross_source_check.toggled.connect(self._save_state)
        self.move_existing_check.toggled.connect(self._save_state)
        self.extract_check.toggled.connect(self._save_state)
        self.alt_colors_check.toggled.connect(self._save_state)
        # Not saving min_score — it's a per-session setting (see _load_state).
        # Options that affect which pairs are produced auto-rescan immediately.
        self.recursive_check.toggled.connect(lambda _: self._scan_timer.start())
        self.cross_source_check.toggled.connect(lambda _: self._scan_timer.start())
        self.move_existing_check.toggled.connect(lambda _: self._scan_timer.start())
        self.extract_check.toggled.connect(lambda _: self._scan_timer.start())

        # Kick off the first scan after a short delay so the UI is fully
        # painted before scan starts. Uses the same debounced timer as the
        # source/option triggers so it shares the re-entrance guard.
        if any(Path(r["entry"].text().strip()).is_dir()
               for r in self.source_rows if r["entry"].text().strip()):
            self._scan_timer.setInterval(1500)
            self._scan_timer.start()
            # Restore the normal debounce for subsequent edits.
            QtCore.QTimer.singleShot(
                1600, lambda: self._scan_timer.setInterval(600)
            )

    def _save_state(self):
        sources = [row["entry"].text() for row in self.source_rows]
        save_config({
            "sources": sources,
            "source": sources[0] if sources else "",  # back-compat with older readers
            "output": self.output_entry.text(),
            "move": self.move_check.isChecked(),
            "auto": self.auto_check.isChecked(),
            "hide_skipped": self.hide_skipped_check.isChecked(),
            "recursive": self.recursive_check.isChecked(),
            "cross_source": self.cross_source_check.isChecked(),
            "move_existing": self.move_existing_check.isChecked(),
            "extract": self.extract_check.isChecked(),
            "alt_colors": self.alt_colors_check.isChecked() if hasattr(self, "alt_colors_check") else False,
            "log_visible": self.log_panel.isVisible() if hasattr(self, "log_panel") else False,
            "compact": self.compact_btn.isChecked() if hasattr(self, "compact_btn") else False,
        })

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    # ----- helpers -----
    def eventFilter(self, watched, event):
        if watched is self.table.viewport() and event.type() == QtCore.QEvent.Resize:
            self._reposition_empty()
        return super().eventFilter(watched, event)

    def _reposition_empty(self):
        if hasattr(self, "empty_label") and self.empty_label is not None:
            vp = self.table.viewport()
            self.empty_label.setGeometry(0, 0, vp.width(), vp.height())

    def _toggle_empty(self, text: str | None):
        if text is None:
            self.empty_label.hide()
        else:
            self.empty_label.setText(text)
            self.empty_label.show()
            self._reposition_empty()

    def _pick_for(self, entry: QtWidgets.QLineEdit, label: str):
        cur = entry.text()
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, f"Choose {label} folder", cur or "",
        )
        if not path:
            return
        if sys.platform == "win32":
            path = path.replace("/", "\\")
        entry.setText(path)
        self._save_state()

    def _add_source_row(self, initial: bool = False, path: str = ""):
        """Append a source folder row. The first (initial=True) one cannot be removed."""
        row_layout = QtWidgets.QHBoxLayout()
        row_layout.setSpacing(14)

        title_widget = _SourceTitle(path, placeholder="Source")
        row_layout.addWidget(title_widget, 0, QtCore.Qt.AlignVCenter)

        entry = QtWidgets.QLineEdit()
        entry.setText(path)
        entry.setPlaceholderText(
            "Folder containing your funscripts and videos"
            if initial else "Additional source folder"
        )
        entry.setMinimumHeight(44)
        entry.addAction(self._folder_icon, QtWidgets.QLineEdit.LeadingPosition)
        row_layout.addWidget(entry, 1)

        entry.textChanged.connect(
            lambda t, w=title_widget: w.set_path(t.strip())
        )

        def _on_rename(new_path: str, _e=entry):
            if sys.platform == "win32":
                new_path = new_path.replace("/", "\\")
            _e.setText(new_path)

        title_widget.rename_done.connect(_on_rename)

        browse = QtWidgets.QPushButton("Browse…")
        browse.setMinimumHeight(44)
        browse.setMinimumWidth(118)
        browse.setCursor(QtCore.Qt.PointingHandCursor)
        browse.clicked.connect(lambda _=False, e=entry: self._pick_for(e, "source"))
        row_layout.addWidget(browse)

        if initial:
            placeholder = QtWidgets.QPushButton("✕")
            placeholder.setObjectName("source-remove")
            placeholder.setFixedSize(48, 44)
            placeholder.setEnabled(False)
            placeholder.setFocusPolicy(QtCore.Qt.NoFocus)
            row_layout.addWidget(placeholder)
            remove_btn = None
        else:
            remove_btn = QtWidgets.QPushButton("✕")
            remove_btn.setObjectName("source-remove")
            remove_btn.setFixedSize(48, 44)
            remove_btn.setCursor(QtCore.Qt.PointingHandCursor)
            remove_btn.setToolTip("Remove this source folder")
            remove_btn.clicked.connect(lambda _=False, e=entry: self._remove_source_row(e))
            row_layout.addWidget(remove_btn)

        self.sources_container.addLayout(row_layout)
        self.source_rows.append({
            "layout": row_layout,
            "entry": entry,
            "browse": browse,
            "remove": remove_btn,
            "title": title_widget,
        })
        # Keep alias to first source for any legacy reads
        if initial:
            self.source_entry = entry
        # Live save when path is edited
        if hasattr(self, "_save_timer"):
            entry.textChanged.connect(self._save_timer.start)
        # Live auto-scan when path is edited
        if hasattr(self, "_scan_timer"):
            entry.textChanged.connect(self._scan_timer.start)

        return entry

    def _remove_source_row(self, entry: QtWidgets.QLineEdit):
        for i, row in enumerate(self.source_rows):
            if row["entry"] is not entry:
                continue
            path = entry.text().strip()
            name = Path(path).name if path else ""
            msg = (
                f"Remove source folder '{name}' from the list?"
                if name else "Remove this source row?"
            )
            reply = QtWidgets.QMessageBox.question(
                self,
                "Remove source",
                msg,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
            while row["layout"].count():
                item = row["layout"].takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            self.sources_container.removeItem(row["layout"])
            row["layout"].deleteLater()
            self.source_rows.pop(i)
            self._save_state()
            if hasattr(self, "_scan_timer"):
                self._scan_timer.start()
            return

    # ----- find button -----
    def _make_find_button(self, query: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton("Find  ▾")
        btn.setObjectName("find")
        btn.setFixedHeight(30)
        btn.setMinimumWidth(82)
        btn.setToolTip(f"Search the web for: {query}")
        btn.setCursor(QtCore.Qt.PointingHandCursor)

        menu = QtWidgets.QMenu(btn)
        hdr_v = menu.addAction("Find video on")
        hdr_v.setEnabled(False)
        for name, tmpl in SEARCH_SITES["video"]:
            a = menu.addAction(f"  {name}")
            a.triggered.connect(
                lambda checked=False, t=tmpl, q=query: open_search(t, q)
            )
        menu.addSeparator()
        hdr_s = menu.addAction("Find script on")
        hdr_s.setEnabled(False)
        for name, tmpl in SEARCH_SITES["script"]:
            a = menu.addAction(f"  {name}")
            a.triggered.connect(
                lambda checked=False, t=tmpl, q=query: open_search(t, q)
            )

        def show_menu():
            pt = btn.mapToGlobal(QtCore.QPoint(0, btn.height() + 2))
            menu.exec(pt)
        btn.clicked.connect(show_menu)
        return btn

    # ----- archive extraction -----
    def _flatten_single_subfolder(self, target: Path) -> None:
        """If target contains a single subfolder and nothing else, promote its contents up."""
        try:
            items = list(target.iterdir())
        except OSError:
            return
        if len(items) != 1 or not items[0].is_dir():
            return
        inner = items[0]
        try:
            for child in list(inner.iterdir()):
                dst = target / child.name
                if dst.exists():
                    return  # collision — leave nesting alone
                shutil.move(str(child), str(dst))
            inner.rmdir()
        except OSError:
            pass

    def _extract_archive(self, archive: Path) -> tuple[bool, list[str]]:
        """Extract `archive` into `archive.parent / archive.stem`. Delete archive on success.
        Thread-safe — does not touch the UI. Returns (success, log_messages)."""
        msgs: list[str] = []
        sfx = archive.suffix.lower()
        target = archive.parent / archive.stem

        if target.exists():
            msgs.append(f"  ! Skipped extract (folder exists): {archive.name}")
            return False, msgs

        if sfx == ".rar" and not _HAS_RAR:
            msgs.append(f"  ! Can't extract {archive.name}: install rarfile  (pip install rarfile)")
            return False, msgs

        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as e:
            msgs.append(f"  ! Couldn't create {target.name}/: {e}")
            return False, msgs

        try:
            if sfx == ".zip":
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(target)
            elif sfx == ".rar":
                with rarfile.RarFile(archive) as rf:  # type: ignore[union-attr]
                    rf.extractall(target)
            else:
                target.rmdir()
                return False, msgs
        except Exception as e:  # noqa: BLE001 — bubble any extractor error up to the log
            # If the built-in library refused the archive, try 7-Zip — it
            # handles malformed/repacked zips and is more lenient about CRC
            # mismatches than Python's zipfile.
            ok_7z, retry_msgs = self._try_7z_extract(archive, target, str(e))
            msgs.extend(retry_msgs)
            if ok_7z:
                # 7-Zip wrote into `target`; fall through to flatten + unlink.
                pass
            else:
                try:
                    shutil.rmtree(target)
                except OSError:
                    pass
                return False, msgs

        self._flatten_single_subfolder(target)

        try:
            archive.unlink()
            msgs.append(f"Extracted: {archive.name} → {target.name}/")
        except OSError as e:
            msgs.append(f"Extracted {archive.name} (couldn't delete archive: {e})")
        return True, msgs

    def _try_7z_extract(
        self, archive: Path, target: Path, primary_err: str
    ) -> tuple[bool, list[str]]:
        """Fallback path: invoke the 7-Zip CLI to extract `archive` into
        `target`. Used when Python's zipfile/rarfile rejected the file.
        Returns (success, log_messages)."""
        msgs: list[str] = []
        if _SEVENZ_PATH is None:
            msgs.append(
                f"  ! Extract failed: {archive.name} — {primary_err} "
                f"(install 7-Zip to enable fallback for malformed archives)"
            )
            return False, msgs

        # Wipe the target so 7-Zip starts clean.
        try:
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=False)
        except OSError as e:
            msgs.append(f"  ! 7-Zip retry skipped — couldn't reset {target.name}/: {e}")
            return False, msgs

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            cp = subprocess.run(
                [
                    str(_SEVENZ_PATH), "x",
                    str(archive),
                    f"-o{target}",
                    "-y",        # assume Yes on prompts
                    "-bso0",     # silence stdout
                    "-bsp0",     # silence progress
                ],
                capture_output=True, text=True, timeout=900,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            msgs.append(f"  ! 7-Zip extract timed out: {archive.name}")
            return False, msgs
        except OSError as e:
            msgs.append(f"  ! 7-Zip extract failed to launch: {archive.name} — {e}")
            return False, msgs

        if cp.returncode == 0:
            msgs.append(f"Extracted (7-Zip fallback): {archive.name} → {target.name}/")
            return True, msgs

        # 7-Zip returns 1 for warnings (e.g. partial extraction on bad CRC).
        # Treat that as success only if some files actually landed in target.
        if cp.returncode == 1 and any(target.iterdir()):
            msgs.append(
                f"Extracted with warnings (7-Zip): {archive.name} → {target.name}/ "
                f"— some files may be corrupt"
            )
            return True, msgs

        stderr_tail = (cp.stderr or "").strip().splitlines()[-1:] or [""]
        msgs.append(
            f"  ! Extract failed: {archive.name} — {primary_err} "
            f"(7-Zip exit {cp.returncode}: {stderr_tail[0][:160]})"
        )
        return False, msgs

    def _extract_archives_in(self, src: Path, recursive: bool) -> int:
        """Walk `src`, extract any .zip/.rar found in parallel. Skips folders that already look
        like a paired subfolder so we don't smear archives next to organized content.
        Returns the count of archives successfully extracted."""
        archives: list[Path] = []

        def visit(d: Path):
            try:
                entries = list(d.iterdir())
            except OSError:
                return
            for e in entries:
                if e.is_file() and e.suffix.lower() in ARCHIVE_EXTS:
                    archives.append(e)
            if recursive:
                for e in entries:
                    if not e.is_dir():
                        continue
                    if self._is_existing_pair_dir(e):
                        continue
                    visit(e)

        visit(src)
        if not archives:
            return 0

        workers = min(len(archives), 4)
        count = 0
        completed = 0
        total = len(archives)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self._extract_archive, a) for a in archives}
            # Pump Qt events every 50ms instead of blocking. `wait(timeout=…)`
            # returns as soon as anything finishes (or the timeout elapses),
            # so the main thread keeps the window responsive even while a
            # single huge archive is still being extracted in a worker.
            while futures:
                done, futures = wait(
                    futures, timeout=0.05, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    completed += 1
                    try:
                        success, msgs = fut.result()
                    except Exception as e:  # noqa: BLE001
                        success, msgs = False, [f"  ! Extract worker crashed: {e}"]
                    for m in msgs:
                        self._log(m)
                    if success:
                        count += 1
                if hasattr(self, "status_label") and total > 1:
                    self.status_label.setText(
                        f"Extracting archives… {completed}/{total}"
                    )
                QtCore.QCoreApplication.processEvents()
        return count

    # ----- file gathering -----
    def _is_existing_pair_dir(self, d: Path):
        """Detect if `d` looks like a pair subfolder produced by this app.

        A folder qualifies if its (normalized) name matches a (script base,
        video stem) pair found inside it. Returns (base, [fs_paths], video_path)
        on hit, else None.
        """
        try:
            files = [f for f in d.iterdir() if f.is_file()]
        except OSError:
            return None
        scripts = [f for f in files if f.suffix.lower() == ".funscript"]
        vids = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
        if not scripts or not vids:
            return None
        folder_norm = normalize(d.name)
        if not folder_norm:
            return None
        groups: dict[str, list[Path]] = {}
        for s in scripts:
            base, _ = split_axis(s.stem)
            groups.setdefault(base, []).append(s)
        for base, fs_list in groups.items():
            if normalize(base) != folder_norm:
                continue
            for v in vids:
                if normalize(v.stem) == folder_norm:
                    return base, fs_list, v
        return None

    def _gather_files(self, src: Path, recursive: bool, find_existing: bool):
        """Walk `src` and return (funscripts, videos, existing_pairs).

        - `funscripts`/`videos`: lists of Path for loose files.
        - `existing_pairs`: list of dicts for already-organized pair subfolders
          (only when `find_existing` is True).
        - Subfolders that look like existing pairs are NEVER recursed into,
          regardless of `recursive`, so we don't scrape their contents and
          break the pair.
        """
        funscripts: list[Path] = []
        videos: list[Path] = []
        existing: list[dict] = []

        def visit(d: Path):
            try:
                entries = list(d.iterdir())
            except OSError:
                return
            for e in entries:
                if e.is_file():
                    sfx = e.suffix.lower()
                    if sfx == ".funscript":
                        funscripts.append(e)
                    elif sfx in VIDEO_EXTS:
                        videos.append(e)
            for e in entries:
                if not e.is_dir():
                    continue
                if not (recursive or find_existing):
                    continue
                pair = self._is_existing_pair_dir(e)
                if pair:
                    if find_existing:
                        base, fs_list, vid = pair
                        existing.append({
                            "id": base,
                            "fs_paths": fs_list,
                            "video_path": vid,
                            "subfolder": e,
                            "source": src,
                        })
                    # never descend into an existing pair folder
                elif recursive:
                    visit(e)

        visit(src)
        funscripts.sort()
        videos.sort()
        return funscripts, videos, existing

    def _build_pool_rows(self, fs_list: list, vd_list: list) -> list[dict]:
        """Group scripts by base and greedy-match each base to a video. Returns row dicts."""
        grouped: dict[str, list[Path]] = {}
        for fs in fs_list:
            base, _ = split_axis(fs.stem)
            grouped.setdefault(base, []).append(fs)

        bases = list(grouped.keys())
        triples = []
        for gi, base in enumerate(bases):
            for v in vd_list:
                triples.append((similarity(base, v.stem), gi, v))
        triples.sort(key=lambda t: -t[0])

        auto: dict[int, tuple[Path, float]] = {}
        used_g: set = set()
        used_v: set = set()
        for sc, gi, v in triples:
            vk = str(v)
            if gi in used_g or vk in used_v or sc <= 0:
                continue
            auto[gi] = (v, round(sc, 4))
            used_g.add(gi); used_v.add(vk)

        rows: list[dict] = []
        for gi, base in enumerate(bases):
            fs_paths = grouped[base]
            axes = [split_axis(fs.stem)[1] for fs in fs_paths if split_axis(fs.stem)[1]]
            scored = sorted(
                ((similarity(base, v.stem), v) for v in vd_list),
                key=lambda t: -t[0],
            )
            auto_v, auto_sc = auto.get(gi, (None, 0.0))
            rows.append({
                "id": base,
                "fs_paths": fs_paths,
                "axes": axes,
                "videos_pool": [v for _, v in scored],
                "score_map": {v: s for s, v in scored},
                "auto_video": auto_v,
                "auto_score": auto_sc,
                "is_existing": False,
                "subfolder": None,
            })
        return rows

    def _scan(self):
        # Re-entrance guard: processEvents() inside the scan can fire
        # _scan_timer (queued by a checkbox toggle, a path edit, etc.) and
        # re-enter _scan, which corrupts self.row_widgets / self.table as
        # they're being rebuilt. The guard makes overlapping triggers wait.
        if getattr(self, "_scanning", False):
            return
        self._scanning = True
        try:
            self._do_scan()
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ! Scan crashed: {exc}")
            traceback.print_exc()
            self.status_label.setText("Scan failed — see activity log.")
        finally:
            self._scanning = False

    def _do_scan(self):
        sources: list[Path] = []
        for row in self.source_rows:
            s = row["entry"].text().strip()
            if not s:
                continue
            p = Path(s)
            if p.is_dir():
                sources.append(p)
            else:
                self._log(f"Source not a directory (skipped): {s}")

        if not sources:
            # On auto-scan we don't want a modal pop-up — just bail silently
            # and let the user fix paths.
            return

        recursive = self.recursive_check.isChecked()
        cross_source = self.cross_source_check.isChecked()
        move_existing = self.move_existing_check.isChecked()
        extract = self.extract_check.isChecked()

        self.status_label.setText("Scanning…")
        self._log(
            f"Scanning {len(sources)} source(s)"
            + (" recursively" if recursive else "")
            + (" with cross-source matching" if cross_source and len(sources) > 1 else "")
            + (" + existing pairs" if move_existing else "")
            + (" + extract archives" if extract else "")
        )
        QtCore.QCoreApplication.processEvents()

        if extract:
            for src in sources:
                self.status_label.setText(f"Extracting archives in {src.name}…")
                QtCore.QCoreApplication.processEvents()
                try:
                    n = self._extract_archives_in(src, recursive)
                except OSError as e:
                    self._log(f"  ! couldn't scan archives in {src}: {e}")
                    continue
                if n:
                    self._log(f"  {src.name}: extracted {n} archive(s)")
                QtCore.QCoreApplication.processEvents()

        per_source: list = []  # (src, funscripts, videos)
        all_existing: list[dict] = []
        for src in sources:
            try:
                fs, vd, existing = self._gather_files(src, recursive, move_existing)
            except OSError as e:
                self._log(f"  ! couldn't read {src}: {e}")
                continue
            per_source.append((src, fs, vd))
            all_existing.extend(existing)
            self._log(
                f"  {src.name}: {len(fs)} script(s), {len(vd)} video(s)"
                + (f", {len(existing)} existing pair(s)" if existing else "")
            )
            QtCore.QCoreApplication.processEvents()

        # Build matching pools (cross-source merges everything; otherwise per-source)
        if cross_source:
            all_fs, all_vd = [], []
            for _src, fs, vd in per_source:
                all_fs.extend(fs); all_vd.extend(vd)
            rows = self._build_pool_rows(all_fs, all_vd)
        else:
            rows = []
            for _src, fs, vd in per_source:
                rows.extend(self._build_pool_rows(fs, vd))

        # Existing-pair rows (one per discovered subfolder)
        for ex in all_existing:
            axes = [split_axis(fs.stem)[1] for fs in ex["fs_paths"] if split_axis(fs.stem)[1]]
            rows.append({
                "id": ex["id"],
                "fs_paths": ex["fs_paths"],
                "axes": axes,
                "videos_pool": [ex["video_path"]],
                "score_map": {ex["video_path"]: 1.0},
                "auto_video": ex["video_path"],
                "auto_score": 1.0,
                "is_existing": True,
                "subfolder": ex["subfolder"],
            })

        # Auto-pair perfect matches (≥0.999 score). Existing pairs always qualify.
        out_str = self.output_entry.text().strip()
        out = Path(out_str) if out_str else sources[0]
        move = self.move_check.isChecked()

        auto_paired = 0
        if self.auto_check.isChecked():
            perfect = []
            for r in rows:
                if r["auto_video"] and r["auto_score"] >= 0.999:
                    perfect.append({
                        "id": r["id"],
                        "fs_paths": r["fs_paths"],
                        "video_path": r["auto_video"],
                        "subfolder": r["subfolder"] if r["is_existing"] else None,
                    })
            if perfect:
                self.status_label.setText(f"Auto-pairing {len(perfect)} perfect match(es)…")
                QtCore.QCoreApplication.processEvents()
                paired_ids, errs = self._do_pair(out, move, perfect)
                auto_paired = len(paired_ids)
                self._log(f"Auto-paired {auto_paired}/{len(perfect)} perfect match(es).")
                for e in errs:
                    self._log(f"  ! {e}")
                if paired_ids:
                    rows = [r for r in rows if r["id"] not in paired_ids]

        self.scan_src_paths = sources
        self.table.setRowCount(0)
        self.row_widgets = []

        if not rows:
            self._toggle_empty(
                "🎉  All paired."
                if auto_paired else
                "🔍  No .funscript files found."
            )
            self.apply_btn.setEnabled(False)
            note = f" Auto-paired {auto_paired}." if auto_paired else ""
            self.status_label.setText(("No funscripts found." if not auto_paired else "Done.") + note)
            return

        self._toggle_empty(None)
        threshold = self.min_score_slider.value() / 100.0
        # Build rows incrementally so Qt can repaint and stay responsive
        # — for large source folders this would otherwise lock the UI long
        # enough that Windows flags the app as "Not Responding".
        total_rows = len(rows)
        for idx, r in enumerate(rows):
            self._add_row(r, threshold)
            if (idx % 8) == 0 or idx == total_rows - 1:
                self.status_label.setText(
                    f"Building list… {idx + 1}/{total_rows}"
                )
                QtCore.QCoreApplication.processEvents()

        total_videos = sum(len(vd) for _src, _fs, vd in per_source)
        matched = sum(
            1 for w in self.row_widgets
            if w["combo"].currentData(QtCore.Qt.UserRole + 1) is not None
        )
        note = f" Auto-paired {auto_paired}." if auto_paired else ""
        self.status_label.setText(
            f"Found {len(self.row_widgets)} script(s), "
            f"{total_videos} video(s) — {matched} matched.{note}"
        )
        self.apply_btn.setEnabled(True)
        self._apply_skip_filter()

    def _add_row(self, r: dict, threshold: float):
        canonical = r["id"]
        fs_paths: list[Path] = r["fs_paths"]
        axes: list[str] = r["axes"]
        is_existing: bool = r["is_existing"]

        ri = self.table.rowCount()
        self.table.insertRow(ri)
        self.table.setRowHeight(ri, 64 if (axes or is_existing) else 56)

        # FUNSCRIPT cell — primary script name + sub-info, vertically and
        # horizontally centred in the row.
        primary_fs = next((fs for fs in fs_paths if not split_axis(fs.stem)[1]), fs_paths[0])
        name_cell = QtWidgets.QWidget()
        name_cell.setObjectName("cell-wrap")
        nl = QtWidgets.QVBoxLayout(name_cell)
        nl.setContentsMargins(18, 6, 18, 6)
        nl.setSpacing(2)
        nl.addStretch()
        name_label = QtWidgets.QLabel(primary_fs.name)
        name_label.setObjectName("fs-name")
        name_label.setAlignment(QtCore.Qt.AlignCenter)
        name_label.setWordWrap(False)
        name_label.setToolTip(str(primary_fs))
        name_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        nl.addWidget(name_label)
        sub_parts: list[str] = []
        if axes:
            sub_parts.append(f"+ {len(axes)} axis ({', '.join(axes)})")
        if is_existing and r["subfolder"] is not None:
            sub_parts.append(f"existing pair · {r['subfolder'].name}/")
        if sub_parts:
            ax_label = QtWidgets.QLabel(" · ".join(sub_parts))
            ax_label.setObjectName("fs-axes")
            ax_label.setAlignment(QtCore.Qt.AlignCenter)
            nl.addWidget(ax_label)
        nl.addStretch()
        self.table.setCellWidget(ri, 0, name_cell)

        # MATCHED VIDEO cell — combo where each entry stores the full path string
        combo_cell = QtWidgets.QWidget()
        combo_cell.setObjectName("cell-wrap")
        cl = QtWidgets.QHBoxLayout(combo_cell)
        cl.setContentsMargins(8, 8, 8, 8)
        combo = _SearchableComboBox()
        combo.setFocusPolicy(QtCore.Qt.StrongFocus)
        combo.setItemDelegate(self._score_delegate)
        combo.addItem("(skip)")
        combo.setItemData(0, None, QtCore.Qt.UserRole + 1)
        seen_names: dict[str, int] = {}
        for vp in r["videos_pool"]:
            display = vp.name if seen_names.get(vp.name, 0) == 0 else f"{vp.name}  ·  {vp.parent.name}"
            seen_names[vp.name] = seen_names.get(vp.name, 0) + 1
            combo.addItem(display)
            idx = combo.count() - 1
            sc = r["score_map"].get(vp, 0.0)
            combo.setItemData(idx, float(sc), QtCore.Qt.UserRole)
            combo.setItemData(idx, str(vp), QtCore.Qt.UserRole + 1)
            combo.setItemData(idx, str(vp), QtCore.Qt.ToolTipRole)

        # Initial selection
        auto_v = r["auto_video"]
        auto_sc = r["auto_score"]
        show_auto = bool(auto_v) and (is_existing or auto_sc >= threshold)
        if show_auto:
            target_str = str(auto_v)
            for i in range(combo.count()):
                if combo.itemData(i, QtCore.Qt.UserRole + 1) == target_str:
                    combo.setCurrentIndex(i)
                    break
        if is_existing:
            combo.setEnabled(False)
            combo.setToolTip("Existing pair — video is locked")
        combo.setMinimumHeight(34)
        cl.addWidget(combo)
        self.table.setCellWidget(ri, 1, combo_cell)

        # SCORE cell
        chip = Chip()
        if is_existing:
            chip.set_score(1.0)
        else:
            chip.set_score(auto_sc if show_auto else None)
        chip_cell = _centered(chip)
        self.table.setCellWidget(ri, 2, chip_cell)

        # SKIP cell
        skip_btn = QtWidgets.QPushButton("Skip")
        skip_btn.setObjectName("skip-btn")
        skip_btn.setCheckable(True)
        skip_btn.setFixedSize(72, 30)
        skip_btn.setCursor(QtCore.Qt.PointingHandCursor)
        skip_cell = _centered(skip_btn)
        self.table.setCellWidget(ri, 3, skip_cell)

        # APPLY cell — applies just this single pair without affecting others
        apply_btn = QtWidgets.QPushButton("Apply")
        apply_btn.setObjectName("apply-single")
        apply_btn.setFixedSize(82, 30)
        apply_btn.setCursor(QtCore.Qt.PointingHandCursor)
        apply_btn.setToolTip("Move/copy just this pair (uses the global Move toggle)")
        apply_cell = _centered(apply_btn)
        self.table.setCellWidget(ri, 4, apply_cell)

        # FIND cell
        find_btn = self._make_find_button(canonical)
        find_cell = _centered(find_btn)
        self.table.setCellWidget(ri, 5, find_cell)

        row = {
            "id": canonical,
            "fs_paths": fs_paths,
            "videos_pool": r["videos_pool"],
            "score_map": r["score_map"],
            "auto_video": auto_v,
            "auto_score": auto_sc,
            "combo": combo,
            "chip": chip,
            "skip_btn": skip_btn,
            "apply_btn": apply_btn,
            "is_existing": is_existing,
            "subfolder": r["subfolder"],
            "pre_skip_index": combo.currentIndex(),
            # The 6 cell-container widgets, in column order — used to reorder
            # the table when sorting by score and to tint alternating rows.
            "cells": [name_cell, combo_cell, chip_cell,
                      skip_cell, apply_cell, find_cell],
            "row_height": 64 if (axes or is_existing) else 56,
            # Becomes True the first time the user activates an item in the
            # dropdown (click or Enter from the search box). Once set, the
            # min-match slider no longer auto-hides this row — only a fresh
            # scan resets the flag (rows are rebuilt from scratch on _scan).
            "user_picked": False,
        }
        self.row_widgets.append(row)

        apply_btn.clicked.connect(lambda _=False, _r=row: self._apply_single(_r))

        def on_change(_text, _r=row):
            sel = _r["combo"].currentData(QtCore.Qt.UserRole + 1)
            if sel is None:
                _r["chip"].set_score(None)
            else:
                for v in _r["videos_pool"]:
                    if str(v) == sel:
                        _r["chip"].set_score(_r["score_map"].get(v))
                        break
            if not _r["skip_btn"].isChecked():
                _r["pre_skip_index"] = _r["combo"].currentIndex()
            self._apply_skip_filter()
        combo.currentTextChanged.connect(on_change)

        def on_activated(_idx, _r=row):
            _r["user_picked"] = True
            self._reapply_filters()
        combo.activated.connect(on_activated)

        skip_btn.toggled.connect(lambda checked, _r=row: self._on_skip_toggled(_r, checked))

    def _on_skip_toggled(self, row: dict, checked: bool):
        if checked:
            row["pre_skip_index"] = row["combo"].currentIndex()
            row["combo"].blockSignals(True)
            row["combo"].setCurrentIndex(0)  # "(skip)"
            row["combo"].blockSignals(False)
            row["combo"].setEnabled(False)
            row["chip"].set_score(None)
        else:
            # Existing pairs stay locked even when un-skipped
            row["combo"].setEnabled(not row["is_existing"])
            target_idx = row.get("pre_skip_index", 0)
            if not (0 <= target_idx < row["combo"].count()):
                target_idx = 0
            row["combo"].setCurrentIndex(target_idx)
        self._apply_skip_filter()

    def _apply_skip_filter(self):
        # Backwards-compat shim — visibility is now computed by
        # _reapply_filters which handles skip + threshold + user-picked.
        self._reapply_filters()

    def _reapply_filters(self):
        """Decide row visibility from the score threshold, hide-skipped
        toggle, and the per-row user_picked flag (set when the user picks
        from the dropdown or via the search box). Existing pairs and any
        user-picked row are always shown (subject to hide-skipped)."""
        if not hasattr(self, "min_score_slider"):
            return
        threshold = self.min_score_slider.value() / 100.0
        hide_skipped = self.hide_skipped_check.isChecked()
        for i, row in enumerate(self.row_widgets):
            is_skipped = row["combo"].currentIndex() == 0
            if hide_skipped and is_skipped:
                visible = False
            elif row["is_existing"]:
                visible = True
            elif row.get("user_picked", False):
                visible = True
            else:
                auto_score = row["auto_score"] or 0.0
                visible = auto_score >= threshold
            self.table.setRowHidden(i, not visible)
        self._apply_alt_colors()

    # ----- score sorting + alternating colors -----
    def _row_score(self, row: dict) -> float:
        """Score currently shown in the row's chip: the selected video's
        score, or -1 for (skip) so skipped rows sink to the bottom when
        sorting high→low (and rise to the top low→high)."""
        sc = row["combo"].currentData(QtCore.Qt.UserRole)
        if isinstance(sc, (int, float)):
            return float(sc)
        if row["is_existing"]:
            return 1.0
        return -1.0

    def _on_header_clicked(self, section: int):
        if section == 2:  # Score column
            self._sort_by_score()

    def _sort_by_score(self):
        if not self.row_widgets:
            return
        # Toggle: none → desc → asc → desc → …
        self._score_sort = "asc" if self._score_sort == "desc" else "desc"
        reverse = self._score_sort == "desc"
        # QTableWidget owns (and deletes) cell widgets when rows are cleared
        # or replaced. PySide6 has no takeCellWidget, so park every widget on
        # an off-screen stash first; that detaches them from the table
        # without destroying them. Then rebuild rows in the sorted order.
        if not hasattr(self, "_widget_stash"):
            self._widget_stash = QtWidgets.QWidget()
            self._widget_stash.hide()
        for row in self.row_widgets:
            for cell in row["cells"]:
                cell.setParent(self._widget_stash)
        self.table.setRowCount(0)
        self.row_widgets.sort(key=self._row_score, reverse=reverse)
        for i, row in enumerate(self.row_widgets):
            self.table.insertRow(i)
            self.table.setRowHeight(i, row["row_height"])
            for c, cell in enumerate(row["cells"]):
                self.table.setCellWidget(i, c, cell)
        self._update_score_header()
        self._reapply_filters()  # also re-applies alt colors

    def _update_score_header(self):
        item = self.table.horizontalHeaderItem(2)
        if item is None:
            return
        arrow = ""
        if self._score_sort == "desc":
            arrow = "  ▼"
        elif self._score_sort == "asc":
            arrow = "  ▲"
        item.setText("⭐  Score" + arrow)

    def _apply_alt_colors(self):
        """Tint every other *visible* row when the option is on. Operates on
        the cell-container widgets (objectName 'cell-wrap'); transparent child
        labels show the tint while combos/chips/buttons keep their own style.

        Cheap when the option is off: bails immediately unless a previous
        pass actually applied tints that now need clearing. Also skips rows
        whose target style hasn't changed so dragging the slider stays fast."""
        if not hasattr(self, "alt_colors_check"):
            return
        on = self.alt_colors_check.isChecked()
        if not on and not getattr(self, "_alt_dirty", False):
            return
        visible_i = 0
        for i, row in enumerate(self.row_widgets):
            if self.table.isRowHidden(i):
                continue
            if on and (visible_i % 2 == 1):
                css = "#cell-wrap { background: #161d27; }"
            else:
                # Matches the default QWidget background so non-alt rows
                # look exactly as before.
                css = "#cell-wrap { background: #0d1117; }"
            if row.get("_alt_css") != css:
                for cell in row.get("cells", []):
                    cell.setStyleSheet(css)
                row["_alt_css"] = css
            visible_i += 1
        self._alt_dirty = on

    def _on_min_score_changed(self, value: int):
        threshold = value / 100.0
        self.min_score_label.setText(f"{threshold:.2f}")
        # Slider movement acts as a "refresh" — wipe all manual-pick flags
        # so the new threshold can re-evaluate every row uniformly.
        for row in self.row_widgets:
            row["user_picked"] = False
        for row in self.row_widgets:
            if row["is_existing"] or row["skip_btn"].isChecked():
                continue
            auto_v = row["auto_video"]
            auto_sc = row["auto_score"]
            if not auto_v:
                continue
            current = row["combo"].currentData(QtCore.Qt.UserRole + 1)
            auto_str = str(auto_v)
            # Only auto-adjust rows still at default (auto-match selected or skip)
            if current is None or current == auto_str:
                want_str = auto_str if auto_sc >= threshold else None
                target_idx = 0
                if want_str:
                    for i in range(row["combo"].count()):
                        if row["combo"].itemData(i, QtCore.Qt.UserRole + 1) == want_str:
                            target_idx = i
                            break
                if row["combo"].currentIndex() != target_idx:
                    row["combo"].setCurrentIndex(target_idx)
        # After adjusting selections, also update which rows are visible
        # so the table reflects the new threshold live.
        self._reapply_filters()

    # ----- apply -----
    @staticmethod
    def _next_available(base: Path) -> Path:
        """Return base if it doesn't exist, else base + ' (N)' for the smallest N >= 2."""
        if not base.exists():
            return base
        n = 2
        while True:
            candidate = base.with_name(f"{base.name} ({n})")
            if not candidate.exists():
                return candidate
            n += 1

    @staticmethod
    def _same_path(a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return False

    @staticmethod
    def _size(p: Path):
        try:
            return p.stat().st_size
        except OSError:
            return None

    def _do_pair(self, out: Path, move: bool, pairs: list) -> tuple[set, list]:
        """Move or copy each pair. Two pair shapes are accepted:
          - Regular: {"id", "fs_paths": [Path], "video_path": Path} → move/copy into out/{id}/
          - Existing: {"id", "subfolder": Path}                     → move/copy whole subfolder into out/

        When the destination already exists, we compare every file by size:
          - All sizes match  → MERGE (drop the loose source if moving, no-op if copying).
          - Any size differs → KEEP BOTH by suffixing the new folder name " (N)".

        Returns (succeeded_ids, errors).
        """
        succeeded_ids: set = set()
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return succeeded_ids, [f"Couldn't create output folder: {e}"]

        op = shutil.move if move else shutil.copy2
        errors: list[str] = []
        claimed: set = set()
        total_pairs = len(pairs)

        for pair_idx, m in enumerate(pairs):
            if hasattr(self, "status_label") and total_pairs > 1:
                self.status_label.setText(
                    f"{'Moving' if move else 'Copying'} pair {pair_idx + 1}/{total_pairs}…"
                )
                QtCore.QCoreApplication.processEvents()
            canonical = m["id"]
            sub: Path | None = m.get("subfolder")

            # Existing-pair branch — move or copy the whole subfolder
            if sub is not None:
                target = out / sub.name
                if self._same_path(target, sub):
                    succeeded_ids.add(canonical)
                    continue

                if target.exists():
                    # Compare file-by-file by size. Identical → merge.
                    try:
                        src_files = [f for f in sub.iterdir() if f.is_file()]
                    except OSError:
                        src_files = []
                    contents_match = bool(src_files) and all(
                        (target / f.name).exists()
                        and self._size(target / f.name) == self._size(f)
                        for f in src_files
                    )

                    if contents_match:
                        if move:
                            try:
                                shutil.rmtree(str(sub))
                            except OSError as e:
                                errors.append(f"{canonical}: merge cleanup failed: {e}")
                                continue
                        self._log(f"Merged identical: {sub.name}/")
                        succeeded_ids.add(canonical)
                        continue

                    renamed = self._next_available(target)
                    self._log(f"Renamed (collision): {sub.name}/ → {renamed.name}/")
                    target = renamed

                try:
                    if move:
                        shutil.move(str(sub), str(target))
                    else:
                        shutil.copytree(str(sub), str(target))
                    succeeded_ids.add(canonical)
                except (OSError, shutil.Error) as e:
                    errors.append(f"{canonical}: {e}")
                continue

            # Regular pair branch — script(s) + video into out/{id}/
            video_path: Path = m["video_path"]
            vk = str(video_path)
            if vk in claimed:
                errors.append(f"{canonical}: '{video_path.name}' already used")
                continue
            claimed.add(vk)

            if not video_path.exists():
                errors.append(f"Missing video: {video_path.name}")
                continue

            target_dir = out / canonical

            # Build the (source -> dest filename) plan for video + every funscript
            plan: list[tuple[Path, str]] = [(video_path, canonical + video_path.suffix)]
            for fs_path in m["fs_paths"]:
                _, axis = split_axis(fs_path.stem)
                plan.append((fs_path, canonical + axis + ".funscript"))

            # If the target folder already exists, decide between merge and rename
            if target_dir.exists():
                all_match = True
                for src_p, dst_name in plan:
                    dst_p = target_dir / dst_name
                    if src_p.exists() and self._same_path(dst_p, src_p):
                        continue  # already in place
                    s_sz = self._size(src_p)
                    d_sz = self._size(dst_p) if dst_p.exists() else None
                    if s_sz is None or d_sz is None or s_sz != d_sz:
                        all_match = False
                        break

                if all_match:
                    if move:
                        for src_p, dst_name in plan:
                            dst_p = target_dir / dst_name
                            if self._same_path(dst_p, src_p):
                                continue
                            try:
                                if src_p.exists():
                                    src_p.unlink()
                            except OSError as e:
                                errors.append(
                                    f"{canonical}: merge cleanup failed for {src_p.name}: {e}"
                                )
                    self._log(f"Merged identical: {canonical}/")
                    succeeded_ids.add(canonical)
                    continue

                renamed = self._next_available(target_dir)
                self._log(f"Renamed (collision): {canonical}/ → {renamed.name}/")
                target_dir = renamed

            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                errors.append(f"{canonical}: {e}")
                continue

            new_video = target_dir / (canonical + video_path.suffix)
            same = self._same_path(new_video, video_path)
            if new_video.exists() and not same:
                errors.append(f"Skipped (exists): {new_video.name}")
                continue
            try:
                if not same:
                    op(str(video_path), str(new_video))
            except (OSError, shutil.Error) as e:
                errors.append(f"{video_path.name}: {e}")
                continue

            for fs_path in m["fs_paths"]:
                if not fs_path.exists():
                    errors.append(f"Missing funscript: {fs_path.name}")
                    continue
                _, axis = split_axis(fs_path.stem)
                new_fs = target_dir / (canonical + axis + ".funscript")
                fs_same = self._same_path(new_fs, fs_path)
                if new_fs.exists() and not fs_same:
                    errors.append(f"Skipped (exists): {new_fs.name}")
                    continue
                try:
                    if not fs_same:
                        op(str(fs_path), str(new_fs))
                except (OSError, shutil.Error) as e:
                    errors.append(f"{fs_path.name}: {e}")
                    continue

            succeeded_ids.add(canonical)

        return succeeded_ids, errors

    def _remove_rows_by_ids(self, ids: set):
        """Drop just the rows whose id is in `ids` from the table, leaving
        every other row (and its current combo selection) untouched. Used
        after a successful move so the list isn't blown away by a re-scan.

        self.row_widgets[i] is kept 1:1 with table row i (rows are only ever
        appended in _add_row), so we can delete by index in reverse."""
        if not ids:
            return
        to_remove = [
            i for i, row in enumerate(self.row_widgets) if row["id"] in ids
        ]
        for idx in sorted(to_remove, reverse=True):
            self.table.removeRow(idx)
            del self.row_widgets[idx]
        if not self.row_widgets:
            self._toggle_empty("🎉  All paired.")
            self.apply_btn.setEnabled(False)
        # Keep visibility filters consistent with the trimmed list.
        self._reapply_filters()

    def _apply(self):
        if not self.row_widgets or not getattr(self, "scan_src_paths", None):
            return
        out_str = self.output_entry.text().strip()
        out = Path(out_str) if out_str else self.scan_src_paths[0]
        move = self.move_check.isChecked()
        self._save_state()

        selected: list[dict] = []
        for w in self.row_widgets:
            if w["combo"].currentIndex() == 0:
                continue
            sel_str = w["combo"].currentData(QtCore.Qt.UserRole + 1)
            if not sel_str:
                continue
            video_path: Path | None = None
            for v in w["videos_pool"]:
                if str(v) == sel_str:
                    video_path = v
                    break
            if video_path is None:
                continue
            selected.append({
                "id": w["id"],
                "fs_paths": w["fs_paths"],
                "video_path": video_path,
                "subfolder": w["subfolder"] if w["is_existing"] else None,
            })

        if not selected:
            QtWidgets.QMessageBox.information(self, "Nothing to do", "No videos selected.")
            return

        if move:
            ans = QtWidgets.QMessageBox.question(
                self, "Confirm move",
                f"Move {len(selected)} pair(s) into:\n{out}\n\n"
                "This removes them from the source folder(s).",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return

        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("Working…")
        self.status_label.setText(f"{'Moving' if move else 'Copying'} {len(selected)} pair(s)…")
        self._log(f"{'Moving' if move else 'Copying'} {len(selected)} pair(s) → {out}")
        QtCore.QCoreApplication.processEvents()

        succeeded_ids, errors = self._do_pair(out, move, selected)
        succeeded = len(succeeded_ids)
        self._log(f"{'Moved' if move else 'Copied'} {succeeded}/{len(selected)} pair(s). {len(errors)} issue(s).")
        for e in errors:
            self._log(f"  ! {e}")

        self.apply_btn.setEnabled(True)
        self.apply_btn.setText("Apply")
        op_word = "Moved" if move else "Copied"

        if errors:
            head = f"{op_word} {succeeded} pair(s) into:\n{out}\n\n{len(errors)} issue(s):\n"
            shown = "\n".join(errors[:15])
            extra = f"\n… and {len(errors) - 15} more" if len(errors) > 15 else ""
            QtWidgets.QMessageBox.warning(self, "Done with issues", head + shown + extra)
        else:
            QtWidgets.QMessageBox.information(
                self, "Done", f"{op_word} {succeeded} pair(s) into:\n{out}"
            )

        self.status_label.setText(f"{op_word} {succeeded} pair(s). {len(errors)} issue(s).")
        # Only trim the rows that actually moved — never re-scan (that would
        # rebuild the whole list and lose the user's other selections).
        # Copied pairs stay in the list since their source files still exist.
        if move and succeeded_ids:
            self._remove_rows_by_ids(succeeded_ids)

    def _apply_single(self, row: dict):
        """Apply (move/copy) the pair for a single row, using the global
        Move-vs-Copy toggle. Behaves like _apply but on one row only."""
        if row["combo"].currentIndex() == 0:
            QtWidgets.QMessageBox.information(
                self, "No video selected",
                "Pick a matched video for this row first (or use the search box in the dropdown)."
            )
            return
        sel_str = row["combo"].currentData(QtCore.Qt.UserRole + 1)
        video_path: Path | None = None
        for v in row["videos_pool"]:
            if str(v) == sel_str:
                video_path = v
                break
        if video_path is None:
            return

        out_str = self.output_entry.text().strip()
        if out_str:
            out = Path(out_str)
        elif getattr(self, "scan_src_paths", None):
            out = self.scan_src_paths[0]
        else:
            QtWidgets.QMessageBox.warning(
                self, "No output folder",
                "Set an output folder before applying."
            )
            return

        move = self.move_check.isChecked()
        if move:
            ans = QtWidgets.QMessageBox.question(
                self, "Confirm move",
                f"Move this pair into:\n{out}\n\nThis removes it from the source folder.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return

        selected = [{
            "id": row["id"],
            "fs_paths": row["fs_paths"],
            "video_path": video_path,
            "subfolder": row["subfolder"] if row["is_existing"] else None,
        }]

        apply_btn = row["apply_btn"]
        apply_btn.setEnabled(False)
        apply_btn.setText("Working…")
        op_word_ing = "Moving" if move else "Copying"
        self.status_label.setText(f"{op_word_ing} 1 pair…")
        self._log(f"{op_word_ing} 1 pair → {out}")
        QtCore.QCoreApplication.processEvents()

        succeeded_ids, errors = self._do_pair(out, move, selected)
        succeeded = len(succeeded_ids)
        op_word = "Moved" if move else "Copied"
        self._log(f"{op_word} {succeeded}/1 pair(s). {len(errors)} issue(s).")
        for e in errors:
            self._log(f"  ! {e}")

        if errors:
            apply_btn.setEnabled(True)
            apply_btn.setText("Apply")
            QtWidgets.QMessageBox.warning(
                self, "Issue applying pair", "\n".join(errors[:5])
            )
            self.status_label.setText(f"{op_word} 0. {len(errors)} issue(s).")
            return

        # Success. If we moved, the row's source files are gone, so drop
        # just this row (no re-scan — keeps every other row intact). If we
        # copied, the source still exists, so keep the row and mark it done.
        self.status_label.setText(f"{op_word} 1 pair.")
        if move and row["id"] in succeeded_ids:
            self._remove_rows_by_ids({row["id"]})
            # `row` / `apply_btn` are now deleted — don't touch them again.
        else:
            apply_btn.setText("Done ✓")
            apply_btn.setEnabled(False)


def _make_app_icon() -> QtGui.QIcon:
    """Build the app icon programmatically so the script has no external
    icon file dependency. Multiple pixmap sizes are bundled so the icon
    looks crisp in the taskbar, title bar, and the alt-tab switcher."""
    icon = QtGui.QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        # Rounded square background in the app's accent blue
        radius = size * 0.22
        p.setBrush(QtGui.QColor("#2f81f7"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(0, 0, size, size, radius, radius)

        # Bold "M" centered, slight optical lift so it sits visually centered
        p.setPen(QtGui.QColor("#ffffff"))
        font = QtGui.QFont("Segoe UI")
        font.setPixelSize(int(size * 0.62))
        font.setWeight(QtGui.QFont.Black)
        p.setFont(font)
        rect = QtCore.QRect(0, -int(size * 0.02), size, size)
        p.drawText(rect, QtCore.Qt.AlignCenter, "M")
        p.end()
        icon.addPixmap(pm)
    return icon


def main():
    # On Windows, give the process its own AppUserModelID so the taskbar
    # uses our window icon instead of the generic Python interpreter icon.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "FunscriptMatcher.Local"
            )
        except Exception:
            pass

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setWindowIcon(_make_app_icon())
    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            log = Path(__file__).parent / "matcher.error.log"
            with open(log, "a", encoding="utf-8") as f:
                f.write("\n--- error ---\n" + tb)
        except Exception:
            pass
        try:
            from PySide6 import QtWidgets as _W
            _app = _W.QApplication.instance() or _W.QApplication(sys.argv)
            _W.QMessageBox.critical(None, "Matcher error", tb)
        except Exception:
            try:
                import tkinter as _tk
                from tkinter import messagebox as _mb
                _r = _tk.Tk(); _r.withdraw()
                _mb.showerror("Matcher error", tb)
                _r.destroy()
            except Exception:
                print(tb)
        sys.exit(1)
