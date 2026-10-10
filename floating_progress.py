"""
Non-blocking floating progress manager.

Performance-optimized:
- 5 widgets total (QFrame + 2 QLabel + QProgressBar + 2 QPushButton)
- No custom paint / no drop shadow effect (expensive on Windows)
- Border via CSS border-radius (GPU composited)
- No polling timer — updates driven by worker thread emit()
- Minimize = hide widget (preserves state, 0 re-create cost)
- Drag via 2 event handlers (mousePress, mouseMove) — no event filter

API (for callers):
    fp = FloatingProgress(parent=mainwindow)
    fp.set_progress(0.45, "Đang xử lý file 7/10: 01.01.pdf")
    fp.completed.connect(on_done)     # emitted when user clicks close
    fp.cancelled.connect(on_cancel)   # emitted when user clicks cancel
    fp.show()
    # ... later:
    fp.set_progress(1.0, "Hoàn tất")
    fp.auto_close_after(2000)   # fade-out 2s after completion

Replaces modal QDialog for batch OCR. For single-doc OCR <30s the
existing blocking dialog remains (simpler UX).
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import (
    Qt, pyqtSignal, QPoint, QTimer, QPropertyAnimation, QEasingCurve,
)
from PyQt5.QtGui import QMouseEvent, QPalette, QColor
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QProgressBar, QPushButton,
    QHBoxLayout, QVBoxLayout, QSizePolicy, QGraphicsOpacityEffect,
)

logger = logging.getLogger("FloatingProgress")


class FloatingProgress(QFrame):
    """Draggable floating progress widget (non-modal, top-right docked).

    Emits:
        cancelled()  when user clicks cancel button
        minimized(bool)  when user toggles minimize (True=collapsed)
        closed()     when user clicks X (same as cancelled if still running)
    """

    cancelled = pyqtSignal()
    minimized = pyqtSignal(bool)
    closed = pyqtSignal()

    # Fixed size constants — computed once, no layout jitter.
    WIDTH_EXPANDED = 340
    HEIGHT_EXPANDED = 130
    WIDTH_COLLAPSED = 180
    HEIGHT_COLLAPSED = 36

    def __init__(self, parent: Optional[QWidget] = None, title: str = "Đang xử lý"):
        # Parent is the MainWindow — keeps widget stacked above MainWindow
        # but NOT a top-level Qt.Tool window (avoids taskbar entry).
        super().__init__(parent)
        self._title_text = title
        self._is_collapsed = False
        self._drag_offset = QPoint()
        self._is_cancelled = False

        # Frameless floating behavior via parent-relative positioning.
        # Simpler than Qt.Tool + setWindowFlags (which creates new OS window).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self._apply_theme()
        self.resize(self.WIDTH_EXPANDED, self.HEIGHT_EXPANDED)

        # Root layout — vertical with tight margins.
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)

        # ── Header row: title + min/close ─────────────────
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            "color: #0F2B5B; font-weight: 700; font-size: 10pt;"
            " background: transparent;"
        )
        header.addWidget(self._title, 1)

        # Minimize button — small, unobtrusive
        self._min_btn = QPushButton("–")
        self._min_btn.setFixedSize(24, 24)
        self._min_btn.setCursor(Qt.PointingHandCursor)
        self._min_btn.setToolTip("Thu gọn")
        self._min_btn.setStyleSheet(self._small_btn_css())
        self._min_btn.clicked.connect(self._toggle_collapse)
        header.addWidget(self._min_btn)

        # Close button — X
        self._close_btn = QPushButton("×")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip("Đóng / hủy")
        self._close_btn.setStyleSheet(self._small_btn_css(danger=True))
        self._close_btn.clicked.connect(self._on_close)
        header.addWidget(self._close_btn)

        root.addLayout(header)

        # ── Progress bar ──────────────────────────────────
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)  # 0-1000 for 0.1% resolution
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setStyleSheet(
            "QProgressBar {"
            " background: #E5E4DE; border: none; border-radius: 3px; }"
            "QProgressBar::chunk {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #1E3924, stop:1 #D4AF37); border-radius: 3px; }"
        )
        root.addWidget(self._bar)

        # ── Status row: percent + detail ──────────────────
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self._pct_label = QLabel("0%")
        self._pct_label.setStyleSheet(
            "color: #6B7280; font-size: 9pt; font-weight: 600; background: transparent;"
        )
        self._pct_label.setFixedWidth(40)
        self._detail_label = QLabel("")
        self._detail_label.setStyleSheet(
            "color: #4B5563; font-size: 9pt; background: transparent;"
        )
        self._detail_label.setTextFormat(Qt.PlainText)
        status_row.addWidget(self._pct_label)
        status_row.addWidget(self._detail_label, 1)
        root.addLayout(status_row)

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

    # ── Theme / CSS helpers ─────────────────────────────────
    def _apply_theme(self) -> None:
        """Apply current theme colors. Re-callable on theme change."""
        try:
            from theme_manager_v2 import ThemeManager
            is_dark = ThemeManager.instance().is_dark
        except Exception:
            is_dark = False

        if is_dark:
            self.setStyleSheet("""
                FloatingProgress {
                    background: #1A2332;
                    border: 1px solid #E5C466;
                    border-radius: 10px;
                }
            """)
            if hasattr(self, '_title'):
                self._title.setStyleSheet(
                    "color: #E5C466; font-weight: 700; font-size: 10pt;"
                    " background: transparent;"
                )
            if hasattr(self, '_pct_label'):
                self._pct_label.setStyleSheet(
                    "color: #A8B2D1; font-size: 9pt; font-weight: 600; background: transparent;"
                )
            if hasattr(self, '_detail_label'):
                self._detail_label.setStyleSheet(
                    "color: #E8EBF5; font-size: 9pt; background: transparent;"
                )
        else:
            self.setStyleSheet("""
                FloatingProgress {
                    background: #FFFFFF;
                    border: 1px solid #D4AF37;
                    border-radius: 10px;
                }
            """)
            if hasattr(self, '_title'):
                self._title.setStyleSheet(
                    "color: #0F2B5B; font-weight: 700; font-size: 10pt;"
                    " background: transparent;"
                )
            if hasattr(self, '_pct_label'):
                self._pct_label.setStyleSheet(
                    "color: #6B7280; font-size: 9pt; font-weight: 600; background: transparent;"
                )
            if hasattr(self, '_detail_label'):
                self._detail_label.setStyleSheet(
                    "color: #4B5563; font-size: 9pt; background: transparent;"
                )

    def apply_theme(self, name: str) -> None:
        """Public hook called by ThemeManager.theme_changed signal."""
        self._apply_theme()

    @staticmethod
    def _small_btn_css(danger: bool = False) -> str:
        color = "#B91C1C" if danger else "#4B5563"
        hover_bg = "#FEE2E2" if danger else "#F0EFE8"
        return (
            "QPushButton {"
            f" background: transparent; color: {color};"
            " border: none; border-radius: 4px; font-size: 14pt;"
            " font-weight: 600; padding: 0; }"
            f"QPushButton:hover {{ background: {hover_bg}; }}"
        )

    # ── Public API ──────────────────────────────────────────
    def set_progress(self, fraction: float, detail: str = "") -> None:
        """Update progress bar + status text.

        Args:
            fraction: 0.0-1.0 progress ratio
            detail: status string (single line, truncated at 60 chars)
        """
        if self._is_cancelled:
            return  # ignore late updates after cancel
        fraction = max(0.0, min(1.0, float(fraction)))
        self._bar.setValue(int(fraction * 1000))
        self._pct_label.setText(f"{int(fraction * 100)}%")
        if detail:
            # Truncate long detail so it fits in one line.
            if len(detail) > 60:
                detail = detail[:57] + "..."
            self._detail_label.setText(detail)

    def set_title(self, title: str) -> None:
        self._title_text = title
        self._title.setText(title)

    def mark_completed(self, message: str = "Hoàn tất") -> None:
        """Mark as done — progress bar turns solid gold, message shown."""
        self._bar.setValue(1000)
        self._pct_label.setText("100%")
        self._detail_label.setText(message)
        # Update title color to green-ish for completion signal.
        self._title.setStyleSheet(
            "color: #16A34A; font-weight: 700; font-size: 10pt;"
            " background: transparent;"
        )

    def auto_close_after(self, ms: int = 2500) -> None:
        """Schedule fade-out + hide after N milliseconds."""
        self._auto_close_timer.start(ms)

    def show_above(self) -> None:
        """Position top-right of parent, fade in."""
        parent = self.parentWidget()
        if parent:
            # 16px margin from right/top edges.
            x = parent.width() - self.width() - 16
            y = 16
            self.move(x, y)
        self.raise_()
        self.show()
        self._fade_in()

    # ── Internal ────────────────────────────────────────────
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
        """Shrink to pill-badge shape or expand back."""
        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            self._bar.hide()
            self._detail_label.hide()
            self._pct_label.setFixedWidth(80)  # wider when alone
            self._min_btn.setText("+")
            self._min_btn.setToolTip("Mở rộng")
            self.resize(self.WIDTH_COLLAPSED, self.HEIGHT_COLLAPSED)
            # Snap back to corner after resize.
            parent = self.parentWidget()
            if parent:
                self.move(parent.width() - self.width() - 16, 16)
        else:
            self._bar.show()
            self._detail_label.show()
            self._pct_label.setFixedWidth(40)
            self._min_btn.setText("–")
            self._min_btn.setToolTip("Thu gọn")
            self.resize(self.WIDTH_EXPANDED, self.HEIGHT_EXPANDED)
            parent = self.parentWidget()
            if parent:
                self.move(parent.width() - self.width() - 16, 16)
        self.minimized.emit(self._is_collapsed)

    def _on_close(self) -> None:
        """Close button clicked — emit cancelled if still running."""
        self._is_cancelled = True
        self.cancelled.emit()
        self._fade_out_and_hide()

    # ── Drag (mouse press + move on header area) ────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Only allow drag from the TOP portion of the widget (above progress bar).
        if event.button() == Qt.LeftButton and event.y() < 32:
            self._drag_offset = event.globalPos() - self.pos()
            # But note: self.pos() is parent-relative. Use widget's top-left
            # in parent coords. Recalc:
            if self.parentWidget():
                parent_top_left = self.mapToParent(self.rect().topLeft())
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
