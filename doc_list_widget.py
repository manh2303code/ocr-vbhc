"""
Minimalist document list widget — DocumentListWidget.

2026-04-18 v3 rewrite: match the semantic-search hover pattern.
AISearchDialog uses `QTableWidgetItem.setToolTip(html)` — plain Qt native
tooltip. Works real-time with no custom code (Qt handles show/hide/
position automatically). Previous custom QFrame implementation was brittle
on Windows (translucent bg, viewport event filter misses).

2026-10 minimal redesign: a row is a status dot, the document number (or
the file name while there is none) and the issue date. The legend that
explained the dots, the summary, page count and file name moved into the
row tooltip, which is built from the theme tokens so it reads in both
themes. Colours come from theme_tokens only; the row and list styling is
in the theme stylesheet (objectNames library_list, doc_row_*).

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
"""
from __future__ import annotations

import os
import html as html_lib
from typing import Optional

from PyQt5.QtCore import Qt, QPoint, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QLabel, QHBoxLayout,
    QSizePolicy,
)


def _theme_tokens(theme: Optional[str] = None) -> dict:
    """Colours of a theme (the current one when not given)."""
    from theme_tokens import colors_for
    if theme is None:
        try:
            from theme_manager_v2 import ThemeManager
            theme = ThemeManager.instance().current
        except Exception:
            theme = "light"
    return colors_for(theme)


STATUS_LABELS = {
    'done':    'Đã OCR đầy đủ',
    'partial': 'OCR chưa đủ, văn bản còn ngắn',
    'pending': 'Chưa OCR',
}


# ── Row widget ─────────────────────────────────────────────────────
class DocumentRow(QWidget):
    """Single list row: [status dot] [số hiệu or file name] [issue date]."""

    # status -> theme token of its dot
    STATUS_TOKENS = {
        'done': 'success',
        'partial': 'warning',
        'pending': 'text_muted',
    }

    def __init__(self, doc_id: int, filename: str, status: str = 'done',
                 parent: Optional[QWidget] = None, meta: Optional[dict] = None):
        super().__init__(parent)
        self._doc_id = doc_id
        self._status = status
        self._filename = filename
        meta = meta or {}
        so_hieu = (meta.get('so_hieu') or '').strip()
        self._title = so_hieu or filename
        self.setAttribute(Qt.WA_StyledBackground, False)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(8)

        self._dot = _DotWidget(status)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)

        self._name = QLabel()
        self._name.setObjectName("doc_row_title")
        # A number is the document's own name; a file name is a stand-in.
        self._name.setProperty("kind", "number" if so_hieu else "file")
        self._name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self._name, stretch=1)

        self._date = QLabel(_short_date(meta.get('issued', '')))
        self._date.setObjectName("doc_row_date")
        self._date.setVisible(bool(self._date.text()))
        row.addWidget(self._date, 0, Qt.AlignVCenter)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(28)
        self._elide()

    def _elide(self) -> None:
        """Fit the title into what the date leaves: a number keeps its start,
        a file name its start and its extension. In a narrow panel the date
        gives way to the title (it is in the tooltip too)."""
        self._name.ensurePolished()
        self._date.ensurePolished()
        names = QFontMetrics(self._name.font())
        spare = self.width() - 10 - 10 - 9 - 8
        date_width = QFontMetrics(self._date.font()).horizontalAdvance(self._date.text()) + 8
        show_date = bool(self._date.text()) and (
            names.horizontalAdvance(self._title) <= spare - date_width or spare >= 200)
        self._date.setVisible(show_date)
        if show_date:
            spare -= date_width
        mode = Qt.ElideMiddle if self._name.property("kind") == "file" else Qt.ElideRight
        self._name.setText(names.elidedText(self._title, mode, max(48, spare)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def apply_theme(self, name: str) -> None:
        """Public hook for ThemeManager.theme_changed: the stylesheet colours
        the labels; the painted dot reads its token again."""
        self._dot.update()
        self._elide()

    @property
    def doc_id(self) -> int:
        return self._doc_id

    def set_status(self, status: str) -> None:
        self._status = status
        self._dot.set_status(status)


class _DotWidget(QWidget):
    """Status: filled (done), half (partial), ring (pending), 7px, in the
    success / warning / muted token of the current theme. Its meaning is
    in the row tooltip."""

    STATUS_LABELS = STATUS_LABELS

    def __init__(self, status: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._status = status
        self.setFixedSize(9, 9)

    def set_status(self, status: str) -> None:
        self._status = status
        self.update()

    def paintEvent(self, _event):
        token = DocumentRow.STATUS_TOKENS.get(self._status, 'text_muted')
        color = QColor(_theme_tokens()[token])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._status == 'done':
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(1, 1, 7, 7)
        elif self._status == 'partial':
            p.setPen(QPen(color, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(1, 1, 7, 7)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawChord(1, 1, 7, 7, -90 * 16, 180 * 16)
        else:
            p.setPen(QPen(color, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(1, 1, 7, 7)
        p.end()


def _short_date(value: str) -> str:
    """The issue date as stored (dd/mm/yyyy); anything long is left out."""
    value = (value or "").strip()
    return value if 0 < len(value) <= 10 else ""


# ── Tooltip HTML builder ───────────────────────────────────────────
def build_tooltip_html(so_hieu: str, loai_vb: str, nd_chinh: str,
                       pages: int = 0, filename: str = "", issued: str = "",
                       status: str = "", theme: Optional[str] = None) -> str:
    """Rich HTML for QListWidgetItem.setToolTip(), in the theme's colours.

        336/KH-KTNS                       (bold, sans)
        Kế hoạch, ban hành 02/08/2017, 4 trang     (secondary)
        Trích yếu / nội dung chính        (Times New Roman: document text)
        Đã OCR đầy đủ   01.01.52.441.03.pdf        (muted)

    The tooltip's background and text colour come from the QToolTip rule
    of the theme stylesheet; the secondary and muted colours here come from
    the same tokens, so the tooltip reads in light and dark. ND chính is
    capped at 1500 characters for speed; Qt wraps it.
    """
    tokens = _theme_tokens(theme)

    def esc(s: str) -> str:
        return html_lib.escape(s or "", quote=False)

    so = (so_hieu or "").strip()
    nd = (nd_chinh or "").strip()
    sans = "'Be Vietnam Pro', 'Segoe UI', sans-serif"
    paper = "'Times New Roman', serif"

    parts = [
        f"<p style=\"margin:0; font-family:{sans}; font-size:10pt; font-weight:600;\">"
        f"{esc(so or filename or '(không tên)')}</p>"
    ]

    details = [loai_vb.strip() if loai_vb else ""]
    if _short_date(issued):
        details.append(f"ban hành {_short_date(issued)}")
    if pages and pages > 0:
        details.append(f"{pages} trang")
    details = [d for d in details if d]
    if details:
        parts.append(
            f"<p style=\"margin:2px 0 0 0; font-family:{sans}; font-size:9pt;"
            f" color:{tokens['text_secondary']};\">{esc(', '.join(details))}</p>"
        )

    if nd:
        nd_show = nd if len(nd) <= 1500 else nd[:1497] + "..."
        parts.append(
            f"<p style=\"margin:6px 0 0 0; font-family:{paper}; font-size:10.5pt;\">"
            f"{esc(nd_show)}</p>"
        )

    footer = [STATUS_LABELS.get(status, "")]
    if so and filename:
        footer.append(filename)
    footer = [f for f in footer if f]
    if footer:
        parts.append(
            f"<p style=\"margin:6px 0 0 0; font-family:{sans}; font-size:8.5pt;"
            f" color:{tokens['text_muted']};\">"
            + "&nbsp;&nbsp;&nbsp;".join(esc(f) for f in footer) + "</p>"
        )

    return "".join(parts)


# ── List widget ────────────────────────────────────────────────────
class DocumentListWidget(QListWidget):
    """QListWidget with Qt-native tooltips + context menu routing.

    Stores per-item data:
        Qt.UserRole       → doc_id (int)
        Qt.UserRole + 1   → stats dict
        Qt.UserRole + 2   → meta dict (so_hieu, nd_chinh, pages, urgency,
                                       size_mb, path, filename, loai_vb,
                                       issued)
        Qt.UserRole + 3   → status ('done' | 'partial' | 'pending')
    """

    doc_clicked = pyqtSignal(int)
    context_menu_requested = pyqtSignal(int, QPoint)
    # Fires first time mouse enters an item — owner can lazy-load DB data
    # and call update_item_meta() to refresh the tooltip.
    hover_requested = pyqtSignal(int, 'PyQt_PyObject')

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("library_list")
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setFrameShape(QListWidget.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        # Fire hover_requested when cursor first enters an item.
        # Qt native setToolTip() handles actual tooltip display.
        self.itemEntered.connect(self._on_item_entered)

        self._lazy_loaded: set = set()

    def apply_theme(self, name: str) -> None:
        """Public hook — rebuild the tooltips in the new theme's colours and
        repaint the rows (their labels follow the stylesheet)."""
        for i in range(self.count()):
            item = self.item(i)
            meta = item.data(Qt.UserRole + 2) or {}
            item.setToolTip(self._build_tooltip_for(
                meta, meta.get('filename', ''), item.data(Qt.UserRole + 3) or '', name))
            widget = self.itemWidget(item)
            if widget and hasattr(widget, 'apply_theme'):
                widget.apply_theme(name)

    # ── Public API ───────────────────────────────────────────────
    def add_document(self, doc_id: int, filename: str, status: str,
                     stats: dict, meta: dict) -> None:
        """Append a document row. Tooltip built from meta dict."""
        row = DocumentRow(doc_id, filename, status, meta=meta or {})
        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        item.setData(Qt.UserRole, doc_id)
        item.setData(Qt.UserRole + 1, stats or {})
        item.setData(Qt.UserRole + 2, meta or {})
        item.setData(Qt.UserRole + 3, status)
        item.setToolTip(self._build_tooltip_for(meta or {}, filename, status))
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
                # Re-classify status if patch included a status hint.
                status = meta_patch.get('_reclassify_status')
                if status:
                    it.setData(Qt.UserRole + 3, status)
                    widget = self.itemWidget(it)
                    if isinstance(widget, DocumentRow):
                        widget.set_status(status)
                filename = existing.get('filename', '')
                it.setToolTip(self._build_tooltip_for(
                    existing, filename, it.data(Qt.UserRole + 3) or ''))
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
    def _build_tooltip_for(self, meta: dict, filename: str, status: str = '',
                           theme: Optional[str] = None) -> str:
        return build_tooltip_html(
            so_hieu=meta.get('so_hieu', ''),
            loai_vb=meta.get('loai_vb', ''),
            nd_chinh=meta.get('nd_chinh', ''),
            pages=int(meta.get('pages', 0) or 0),
            filename=filename or meta.get('filename', ''),
            issued=meta.get('issued', ''),
            status=status,
            theme=theme,
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
