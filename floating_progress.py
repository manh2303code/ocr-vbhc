"""
Non-blocking floating progress for batch OCR.

A small surface that floats over the workspace (top-right of its parent):
the title, the percent and two icon buttons on one row, the thin progress
line of the theme, and the current step underneath. Collapsed, only the
first row stays. Like every floating part of the 2026-10 redesign it is the
surface colour with a border_strong hairline and the 6px popover radius, no
shadow; colours come from the stylesheet ("AREA: dialogs" in
devtools/theme_template.qss) and the icons from theme_tokens, recoloured on
ThemeManager.theme_changed.

Performance-optimized:
- a handful of widgets, no custom paint, no drop shadow effect
- no polling timer: updates are driven by the worker's signals
- minimize = hide the second row (state kept, nothing re-created)
- drag via mousePress/mouseMove on the top row, no event filter

API (for callers):
    fp = FloatingProgress(parent=mainwindow, title="Đang xử lý 12 văn bản")
    fp.set_progress(0.45, "Tệp 7/12: 01.01.pdf")
    fp.cancelled.connect(on_cancel)   # emitted when the user clicks close
    fp.show_above()
    # ... later:
    fp.mark_completed("Hoàn tất 12/12 văn bản")
    fp.auto_close_after(2000)   # fade out 2 s after completion

Replaces a modal QDialog for batch OCR. A single document keeps the
blocking ProgressDialog (simpler for one short job).
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import (
    Qt, pyqtSignal, QPoint, QSize, QTimer, QPropertyAnimation, QEasingCurve,
)
from PyQt5.QtGui import QCursor, QMouseEvent
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QProgressBar, QToolButton,
    QHBoxLayout, QVBoxLayout, QGraphicsOpacityEffect,
)

import theme_tokens
from editor_widgets import ElidedLabel
from ui_icons import icon

logger = logging.getLogger("FloatingProgress")


def _current_theme() -> str:
    try:
        from theme_manager_v2 import ThemeManager
        return ThemeManager.instance().current
    except Exception:
        return "light"


class FloatingProgress(QFrame):
    """Draggable floating progress widget (non-modal, top-right docked).

    Emits:
        cancelled()  when user clicks the close button
        minimized(bool)  when user toggles collapse (True=collapsed)
        closed()     when it has faded out and hidden
    """

    cancelled = pyqtSignal()
    minimized = pyqtSignal(bool)
    closed = pyqtSignal()

    # Fixed sizes: no layout jitter while the text changes. Once completed
    # there is no step line, so the expanded surface is shorter.
    WIDTH_EXPANDED = 340
    HEIGHT_EXPANDED = 84
    HEIGHT_COMPLETED = 58
    WIDTH_COLLAPSED = 280
    HEIGHT_COLLAPSED = 40
    MARGIN = 16
    ICON_SIZE = 16

    def __init__(self, parent: Optional[QWidget] = None, title: str = "Đang xử lý"):
        # Parent is the MainWindow: the widget floats inside it, above its
        # content, and is not a separate OS window (no taskbar entry).
        super().__init__(parent)
        self.setObjectName("floatingProgress")
        self._title_text = title
        self._is_collapsed = False
        self._is_completed = False
        self._drag_offset = QPoint()
        self._is_cancelled = False

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.resize(self.WIDTH_EXPANDED, self.HEIGHT_EXPANDED)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 8, 10)
        root.setSpacing(0)

        # ── Top row: done mark, title, percent, collapse, close ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._done_mark = QLabel()
        self._done_mark.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self._done_mark.hide()
        header.addWidget(self._done_mark)
        self._title = ElidedLabel(title)
        self._title.setObjectName("floatingProgressTitle")
        self._title.setToolTip(title)
        header.addWidget(self._title, 1)
        self._pct_label = QLabel("0%")
        self._pct_label.setObjectName("floatingProgressPercent")
        header.addWidget(self._pct_label)
        header.addSpacing(2)
        self._min_btn = self._tool_button("Thu gọn", self._toggle_collapse)
        header.addWidget(self._min_btn)
        self._close_btn = self._tool_button("Dừng xử lý", self._on_close)
        header.addWidget(self._close_btn)
        root.addLayout(header)

        # ── The progress line and the current step ──
        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 8, 4, 0)
        body.setSpacing(6)
        self._bar = QProgressBar()
        self._bar.setObjectName("floatingProgressBar")
        self._bar.setRange(0, 1000)  # 0.1% resolution
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        body.addWidget(self._bar)
        self._detail_label = ElidedLabel("")
        self._detail_label.setObjectName("floatingProgressDetail")
        self._detail_label.setTextFormat(Qt.PlainText)
        body.addWidget(self._detail_label)
        root.addWidget(self._body)
        root.addStretch(1)

        self._apply_theme()
        try:
            from theme_manager_v2 import ThemeManager
            ThemeManager.instance().theme_changed.connect(self.apply_theme)
        except Exception as exc:
            logger.debug(f"Theme signal unavailable: {exc}")

        # Opacity effect for fade-in/out. Reused, not re-created per show.
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_anim.setDuration(180)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Auto-close timer (optional, after completion).
        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self._fade_out_and_hide)

        self.hide()  # hidden until show_above() called

    def _tool_button(self, tip, slot) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        button.setCursor(QCursor(Qt.PointingHandCursor))
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.clicked.connect(slot)
        return button

    # ── Theme ───────────────────────────────────────────────
    def _apply_theme(self) -> None:
        """Icons from the tokens of the current theme. The surface, the
        hairline and the text colours are the stylesheet's."""
        tokens = theme_tokens.colors_for(_current_theme())
        tone = tokens["text_secondary"]
        self._min_btn.setIcon(icon(
            "chevron-down" if self._is_collapsed else "chevron-up", self.ICON_SIZE, tone))
        self._close_btn.setIcon(icon("x", self.ICON_SIZE, tone))
        self._done_mark.setPixmap(
            icon("status-ok", self.ICON_SIZE, tokens["success"]).pixmap(self.ICON_SIZE, self.ICON_SIZE))

    def apply_theme(self, name: str = "") -> None:
        """Public hook called on ThemeManager.theme_changed."""
        self._apply_theme()

    # ── Public API ──────────────────────────────────────────
    def set_progress(self, fraction: float, detail: str = "") -> None:
        """Update the progress line, the percent and the current step.

        Args:
            fraction: 0.0-1.0 progress ratio
            detail: the current step; one line, ending in "…" when it does
                not fit, the whole of it in the tooltip
        """
        if self._is_cancelled:
            return  # ignore late updates after cancel
        fraction = max(0.0, min(1.0, float(fraction)))
        self._bar.setValue(int(fraction * 1000))
        self._pct_label.setText(f"{int(fraction * 100)}%")
        if detail:
            self._set_detail(detail)

    def set_title(self, title: str) -> None:
        self._title_text = title
        self._title.set_full_text(title)
        self._title.setToolTip(title)

    def mark_completed(self, message: str = "Hoàn tất") -> None:
        """Done: the full line, the check before the title, the message as
        the title; the close button now only closes."""
        self._is_completed = True
        self._bar.setValue(1000)
        self._pct_label.setText("100%")
        self.set_title(message)
        self._set_detail("")
        self._detail_label.hide()
        self._done_mark.show()
        self._close_btn.setToolTip("Đóng")
        self._close_btn.setAccessibleName("Đóng")
        if not self._is_collapsed:
            self.resize(self.WIDTH_EXPANDED, self._expanded_height())

    def auto_close_after(self, ms: int = 2500) -> None:
        """Schedule fade-out + hide after N milliseconds."""
        self._auto_close_timer.start(ms)

    def show_above(self) -> None:
        """Position top-right of parent, fade in."""
        self._snap_to_corner()
        self.raise_()
        self.show()
        self._fade_in()

    # ── Internal ────────────────────────────────────────────
    def _set_detail(self, text: str) -> None:
        self._detail_label.set_full_text(text)
        self._detail_label.setToolTip(text)

    def _expanded_height(self) -> int:
        return self.HEIGHT_COMPLETED if self._is_completed else self.HEIGHT_EXPANDED

    def _snap_to_corner(self) -> None:
        parent = self.parentWidget()
        if parent:
            self.move(parent.width() - self.width() - self.MARGIN, self.MARGIN)

    def _fade_in(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def _fade_out_and_hide(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(0.0)

        def _hide_when_done():
            if self._opacity.opacity() < 0.05:
                self.hide()
                self.closed.emit()
        self._fade_anim.finished.connect(_hide_when_done)
        self._fade_anim.start()

    def _toggle_collapse(self) -> None:
        """Keep only the top row, or show the progress line again."""
        self._is_collapsed = not self._is_collapsed
        self._body.setVisible(not self._is_collapsed)
        tip = "Mở rộng" if self._is_collapsed else "Thu gọn"
        self._min_btn.setToolTip(tip)
        self._min_btn.setAccessibleName(tip)
        if self._is_collapsed:
            self.resize(self.WIDTH_COLLAPSED, self.HEIGHT_COLLAPSED)
        else:
            self.resize(self.WIDTH_EXPANDED, self._expanded_height())
        self._apply_theme()
        self._snap_to_corner()
        self.minimized.emit(self._is_collapsed)

    def _on_close(self) -> None:
        """Close button clicked — emit cancelled (a no-op once finished)."""
        self._is_cancelled = True
        self.cancelled.emit()
        self._fade_out_and_hide()

    # ── Drag (mouse press + move on the top row) ────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Only the top row drags (the progress line and step are below it).
        if event.button() == Qt.LeftButton and event.y() < 32:
            self._drag_offset = event.pos()  # local offset within widget
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.LeftButton and not self._drag_offset.isNull():
            # Calculate new position: cursor-in-parent minus internal offset.
            if self.parentWidget():
                new_pos_in_parent = self.parentWidget().mapFromGlobal(
                    event.globalPos() - self._drag_offset
                )
                # Clamp within parent area.
                px = max(0, min(new_pos_in_parent.x(),
                                self.parentWidget().width() - self.width()))
                py = max(0, min(new_pos_in_parent.y(),
                                self.parentWidget().height() - self.height()))
                self.move(px, py)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = QPoint()
        super().mouseReleaseEvent(event)
