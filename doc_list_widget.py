"""
Minimalist document list widget — DocumentListWidget.

2026-04-18 v3 rewrite: match the semantic-search hover pattern.
AISearchDialog uses `QTableWidgetItem.setToolTip(html)` — plain Qt native
tooltip. Works real-time with no custom code (Qt handles show/hide/
position automatically). Previous custom QFrame implementation was brittle
on Windows (translucent bg, viewport event filter misses).

Public API:
    widget.clear()
    widget.add_document(doc_id, filename, status, stats, meta)
    widget.update_item_meta(doc_id, meta_patch)
    widget.selected_doc_ids() -> list[int]
    widget.find_and_select(doc_id) -> bool
    signals:
        doc_clicked(int)
        context_menu_requested(int, QPoint)
        hover_requested(int, QListWidgetItem)  — for lazy DB fetch

Tooltip styling is set globally on QApplication via brand-themed QToolTip
stylesheet (cream bg, gold border, olive text). Set in main_window_1
after QApplication creation.
"""
from __future__ import annotations

import os
import html as html_lib
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QPoint, QSize, QEvent, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QLabel, QHBoxLayout,
    QSizePolicy,
)


# ── Row widget ─────────────────────────────────────────────────────
class DocumentRow(QWidget):
    """Single list row: [status dot] [STT 3-digit] [filename]."""

    STATUS_COLORS = {
        'done': '#16A34A',
        'partial': '#D97706',
        'pending': '#5F687C',
    }

    def __init__(self, doc_id: int, filename: str, status: str = 'done',
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._doc_id = doc_id
        self._status = status
        self._filename = filename
        self.setAttribute(Qt.WA_StyledBackground, False)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        self._dot = _DotWidget(status)
        row.addWidget(self._dot)

        self._stt = QLabel(f"{doc_id:03d}")
        stt_font = QFont("Consolas, 'Courier New', monospace")
        stt_font.setPointSize(9)
        self._stt.setFont(stt_font)
        self._stt.setFixedWidth(36)
        row.addWidget(self._stt)

        self._name = QLabel()
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_font = QFont("Be Vietnam Pro, Segoe UI")
        name_font.setPointSize(10)
        self._name.setFont(name_font)
        self._name.setText(self._elide(filename, 160))
        row.addWidget(self._name, stretch=1)
        # Apply theme colors now + on future theme changes.
        self._apply_text_colors()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(32)

    def _elide(self, text: str, width: int) -> str:
        return QFontMetrics(self._name.font()).elidedText(
            text, Qt.ElideMiddle, max(48, width)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._name.setText(self._elide(self._filename, self.width() - 88))

    def _apply_text_colors(self) -> None:
        """Apply theme-appropriate text colors. Re-callable on theme change."""
        try:
            from theme_manager_v2 import ThemeManager
            is_dark = ThemeManager.instance().is_dark
        except Exception:
            is_dark = False
        if is_dark:
            self._stt.setStyleSheet("color: #A8B2D1;")
            self._name.setStyleSheet("color: #E8EBF5;")
        else:
            self._stt.setStyleSheet("color: #6B7280;")
            self._name.setStyleSheet("color: #17231B;")

    def apply_theme(self, name: str) -> None:
        """Public hook for ThemeManager.theme_changed signal."""
        self._apply_text_colors()

    @property
    def doc_id(self) -> int:
        return self._doc_id

    def set_status(self, status: str) -> None:
        self._status = status
        self._dot.set_status(status)


class _DotWidget(QWidget):
    """Status indicator — 3 shapes: filled/half/ring + tooltip."""

    STATUS_LABELS = {
        'done':    'Đã OCR đầy đủ',
        'partial': 'OCR chưa đủ (văn bản ngắn)',
        'pending': 'Chưa OCR',
    }

    def __init__(self, status: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._status = status
        self._color = QColor(DocumentRow.STATUS_COLORS.get(status, '#5F687C'))
        self.setFixedSize(12, 12)
        self.setToolTip(self.STATUS_LABELS.get(status, ''))

    def set_status(self, status: str) -> None:
        self._status = status
        self._color = QColor(DocumentRow.STATUS_COLORS.get(status, '#5F687C'))
        self.setToolTip(self.STATUS_LABELS.get(status, ''))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._status == 'done':
            p.setBrush(self._color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(1, 1, 10, 10)
        elif self._status == 'partial':
            pen = QPen(self._color, 1.4)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(1, 1, 10, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(self._color)
            p.drawChord(1, 1, 10, 10, -90 * 16, 180 * 16)
        else:
            pen = QPen(self._color, 1.4)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(1, 1, 10, 10)
        p.end()


# ── Tooltip HTML builder ───────────────────────────────────────────
def build_tooltip_html(so_hieu: str, loai_vb: str, nd_chinh: str,
                       pages: int = 0, filename: str = "") -> str:
    """Rich HTML for QListWidgetItem.setToolTip().

    Layout (user spec 2026-04-18 v5):
        Line 1:  <b>Số hiệu</b>  <i>N trang</i>    (10pt / 9pt)
        Line 2:  Nội dung chính — full, Qt auto-wraps    (9pt regular)

    Size calibration: user reported previous 11-12pt felt "too big" on
    their HiDPI display → shrunk to 9-10pt range. Qt tooltip auto-wraps
    long text naturally; removed character truncation so long ND chính
    documents show the full content.
    """
    def esc(s: str) -> str:
        return html_lib.escape(s or "", quote=False)

    so = (so_hieu or "").strip()
    nd = (nd_chinh or "").strip()

    # Elegant font stack — prioritises serif for classic official look.
    ff = "'Cambria', 'Be Vietnam Pro', 'Segoe UI', serif"

    parts = []

    # Line 1: Số hiệu (bold) + Số trang (italic, muted), same line.
    line1_bits = []
    if so:
        line1_bits.append(
            f"<b style=\"color:#0F2B5B; font-size:10pt;\">{esc(so)}</b>"
        )
    else:
        line1_bits.append(
            "<span style=\"color:#5F687C; font-style:italic; font-size:10pt;\">"
            "(Chưa có số hiệu)</span>"
        )
    if pages and pages > 0:
        line1_bits.append(
            f"<i style=\"color:#6B7280; font-size:9pt;\">{pages} trang</i>"
        )

    parts.append(
        f"<p style=\"margin:0 0 3px 0; font-family:{ff};\">"
        + "&nbsp;&nbsp;".join(line1_bits)
        + "</p>"
    )

    # Line 2: Nội dung chính — no truncation, let Qt wrap naturally.
    # Cap only for memory safety: 1500 chars still renders fast.
    if nd:
        nd_show = nd if len(nd) <= 1500 else nd[:1497] + "..."
        parts.append(
            f"<p style=\"margin:0; font-family:{ff}; font-size:9pt;"
            f" color:#374151; font-weight:normal;\">"
            f"{esc(nd_show)}</p>"
        )
    elif not so:
        parts.append(
            f"<p style=\"margin:0; font-family:{ff}; font-size:8.5pt;"
            f" color:#5F687C; font-style:italic;\">{esc(filename)}</p>"
        )

    return "".join(parts)


# ── List widget ────────────────────────────────────────────────────
class DocumentListWidget(QListWidget):
    """QListWidget with Qt-native tooltips + context menu routing.

    Stores per-item data:
        Qt.UserRole       → doc_id (int)
        Qt.UserRole + 1   → stats dict
        Qt.UserRole + 2   → meta dict (so_hieu, nd_chinh, pages, urgency,
                                       size_mb, path, filename, loai_vb)
    """

    doc_clicked = pyqtSignal(int)
    context_menu_requested = pyqtSignal(int, QPoint)
    # Fires first time mouse enters an item — owner can lazy-load DB data
    # and call update_item_meta() to refresh the tooltip.
    hover_requested = pyqtSignal(int, 'PyQt_PyObject')

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setUniformItemSizes(False)
        self.setSpacing(1)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        # Fire hover_requested when cursor first enters an item.
        # Qt native setToolTip() handles actual tooltip display.
        self.itemEntered.connect(self._on_item_entered)

        self._apply_list_style()

        self._lazy_loaded: set = set()

    def _apply_list_style(self) -> None:
        """Apply theme to list widget itself (background + selection)."""
        try:
            from theme_manager_v2 import ThemeManager
            is_dark = ThemeManager.instance().is_dark
        except Exception:
            is_dark = False
        if is_dark:
            self.setStyleSheet("""
                QListWidget {
                    background: #0F1419;
                    color: #E8EBF5;
                    border: 1px solid #2D3B52;
                    border-radius: 6px;
                    outline: 0;
                }
                QListWidget::item { border: none; border-radius: 4px; }
                QListWidget::item:hover { background: #1E2B3D; }
                QListWidget::item:selected { background: #4A6B52; color: #E8EBF5; }
            """)
        else:
            self.setStyleSheet("""
                QListWidget {
                    background: #FFFEFA;
                    border: 1px solid #D8D1C3;
                    border-radius: 6px;
                    outline: 0;
                }
                QListWidget::item { border: none; border-radius: 4px; }
                QListWidget::item:hover { background: #F5F4EE; }
                QListWidget::item:selected { background: #E6EDE8; color: #24432D; }
            """)

    def apply_theme(self, name: str) -> None:
        """Public hook — refresh list style + all row widgets."""
        self._apply_list_style()
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget and hasattr(widget, 'apply_theme'):
                widget.apply_theme(name)

    # ── Public API ───────────────────────────────────────────────
    def add_document(self, doc_id: int, filename: str, status: str,
                     stats: dict, meta: dict) -> None:
        """Append a document row. Tooltip built from meta dict."""
        row = DocumentRow(doc_id, filename, status)
        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        item.setData(Qt.UserRole, doc_id)
        item.setData(Qt.UserRole + 1, stats or {})
        item.setData(Qt.UserRole + 2, meta or {})
        item.setToolTip(self._build_tooltip_for(meta or {}, filename))
        self.addItem(item)
        self.setItemWidget(item, row)

    def update_item_meta(self, doc_id: int, meta_patch: dict) -> None:
        """Merge patch into item meta + refresh tooltip."""
        for i in range(self.count()):
            it = self.item(i)
            if it.data(Qt.UserRole) == doc_id:
                existing = it.data(Qt.UserRole + 2) or {}
                existing.update(meta_patch or {})
                it.setData(Qt.UserRole + 2, existing)
                filename = existing.get('filename', '')
                it.setToolTip(self._build_tooltip_for(existing, filename))
                # Re-classify status if patch included a status hint.
                status = meta_patch.get('_reclassify_status')
                if status:
                    widget = self.itemWidget(it)
                    if isinstance(widget, DocumentRow):
                        widget.set_status(status)
                break

    def selected_doc_ids(self) -> list[int]:
        return [item.data(Qt.UserRole) for item in self.selectedItems()]

    def doc_id_at(self, pos: QPoint) -> Optional[int]:
        item = self.itemAt(pos)
        return item.data(Qt.UserRole) if item else None

    def find_and_select(self, doc_id: int) -> bool:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.UserRole) == doc_id:
                self.setCurrentRow(i)
                self.scrollToItem(item)
                return True
        return False

    # ── Internal ─────────────────────────────────────────────────
    def _build_tooltip_for(self, meta: dict, filename: str) -> str:
        return build_tooltip_html(
            so_hieu=meta.get('so_hieu', ''),
            loai_vb=meta.get('loai_vb', ''),
            nd_chinh=meta.get('nd_chinh', ''),
            pages=int(meta.get('pages', 0) or 0),
            filename=filename or meta.get('filename', ''),
        )

    def _on_item_entered(self, item: QListWidgetItem) -> None:
        """First time cursor enters an item — trigger lazy DB fetch."""
        if item is None:
            return
        doc_id = item.data(Qt.UserRole)
        if doc_id is None:
            return
        if doc_id not in self._lazy_loaded:
            self._lazy_loaded.add(doc_id)
            self.hover_requested.emit(int(doc_id), item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        doc_id = item.data(Qt.UserRole)
        if doc_id is not None:
            self.doc_clicked.emit(int(doc_id))

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        doc_id = item.data(Qt.UserRole)
        if doc_id is None:
            return
        global_pos = self.viewport().mapToGlobal(pos)
        self.context_menu_requested.emit(int(doc_id), global_pos)


# ── Helpers ────────────────────────────────────────────────────────
def compute_file_size_mb(path: Optional[str]) -> float:
    """Safely compute file size in MB. Returns 0 if path missing/unreadable."""
    if not path:
        return 0.0
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except (OSError, TypeError):
        return 0.0


def classify_status(doc_tuple, van_ban_text: str = "") -> str:
    """Return 'done' | 'partial' | 'pending' for a document tuple."""
    text = van_ban_text or ""
    if not text.strip():
        return 'pending'
    return 'done' if len(text) >= 300 else 'partial'


# Global QToolTip stylesheet — light theme matching the app.
# Apply once at app startup:
#     from doc_list_widget import apply_tooltip_style
#     apply_tooltip_style(app)
BRAND_TOOLTIP_QSS = """
QToolTip {
    background-color: #FFFAEA;
    color: #24432D;
    border: 1px solid #846314;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: 'Cambria', 'Be Vietnam Pro', 'Segoe UI', serif;
    font-size: 9pt;
    opacity: 240;
}
"""


def apply_tooltip_style(app) -> None:
    """Append the brand tooltip stylesheet to the app's existing style."""
    current = app.styleSheet() or ""
    if "QToolTip {" in current:
        return  # already applied
    app.setStyleSheet(current + "\n" + BRAND_TOOLTIP_QSS)
