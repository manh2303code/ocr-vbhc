"""
Lightweight toast notification widget — non-blocking alerts.

Performance:
- 2 widgets per toast (QFrame + QLabel)
- Stack managed centrally, no polling
- Auto-dismiss via QTimer (one-shot per toast)
- Fade-out animation on dismiss

API:
    from toast_widget import toast_success, toast_error, toast_warning, toast_info
    toast_success(parent, "Đã lưu thay đổi")
    toast_error(parent, "Không thể kết nối server")
    toast_warning(parent, "Dung lượng thấp")
    toast_info(parent, "OCR hoàn tất")

Stacks bottom-right corner of parent, max 5 toasts visible. Older toasts
auto-dismiss when stack exceeds 5.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSize,
)
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QLabel, QFrame, QHBoxLayout, QGraphicsOpacityEffect,
)

logger = logging.getLogger("Toast")


TOAST_CONFIGS = {
    "success": {
        "bg": "#F0FDF4",
        "border": "#16A34A",
        "text": "#14532D",
        "icon": "✓",
    },
    "error": {
        "bg": "#FEF2F2",
        "border": "#DC2626",
        "text": "#7F1D1D",
        "icon": "✕",
    },
    "warning": {
        "bg": "#FFFBEB",
        "border": "#D97706",
        "text": "#78350F",
        "icon": "⚠",
    },
    "info": {
        "bg": "#EFF6FF",
        "border": "#1E3924",
        "text": "#0F2B5B",
        "icon": "i",
    },
}


class Toast(QFrame):
    """Single toast — self-dismissing with fade-out."""

    WIDTH = 320
    HEIGHT = 48
    MARGIN = 16

    def __init__(self, parent: QWidget, message: str, kind: str = "info",
                 duration_ms: int = 5000):
        super().__init__(parent)
        cfg = TOAST_CONFIGS.get(kind, TOAST_CONFIGS["info"])
        self._kind = kind
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            Toast {{
                background: {cfg['bg']};
                border: 1px solid {cfg['border']};
                border-left: 4px solid {cfg['border']};
                border-radius: 6px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # Icon glyph (unicode — no SVG overhead for a decorative mark)
        icon_label = QLabel(cfg["icon"])
        icon_label.setFixedSize(20, 20)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(
            f"color: {cfg['border']}; font-weight: 700; font-size: 14pt;"
            f" background: transparent;"
        )
        layout.addWidget(icon_label)

        # Message — single line, truncated
        msg_label = QLabel(message)
        msg_label.setStyleSheet(
            f"color: {cfg['text']}; font-size: 10pt; background: transparent;"
        )
        msg_label.setWordWrap(False)
        # Qt elides text automatically with .setTextElideMode on itemview;
        # QLabel truncates via width + setSizePolicy. We cap message length.
        if len(message) > 60:
            msg_label.setText(message[:57] + "...")
            msg_label.setToolTip(message)
        layout.addWidget(msg_label, 1)

        # Opacity effect for fade-in/out
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_anim.setDuration(200)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Auto-dismiss timer (single-shot)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._dismiss)
        self._duration_ms = duration_ms

    def show_at(self, x: int, y: int) -> None:
        """Display at parent coords + start auto-dismiss timer."""
        self.move(x, y)
        self.show()
        self.raise_()
        # Fade in
        self._fade_anim.stop()
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        # Start auto-dismiss
        self._dismiss_timer.start(self._duration_ms)

    def _dismiss(self) -> None:
        """Fade out + hide."""
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(0.0)

        def _after():
            if self._opacity.opacity() < 0.05:
                self.hide()
                self.deleteLater()
                # Notify manager to reflow stack.
                mgr = ToastManager._instance
                if mgr:
                    mgr._on_toast_dismissed(self)

        self._fade_anim.finished.connect(_after)
        self._fade_anim.start()

    def mousePressEvent(self, event):
        """Click toast → dismiss immediately."""
        self._dismiss_timer.stop()
        self._dismiss()


class ToastManager:
    """Singleton managing stacked toasts per-parent."""

    _instance: Optional["ToastManager"] = None
    MAX_VISIBLE = 5

    def __init__(self):
        self._stacks: dict = {}  # parent QWidget → list[Toast]
        ToastManager._instance = self

    @classmethod
    def instance(cls) -> "ToastManager":
        if cls._instance is None:
            cls()
        return cls._instance

    def show(self, parent: QWidget, message: str, kind: str = "info",
             duration_ms: int = 5000) -> Toast:
        stack = self._stacks.setdefault(parent, [])

        # Enforce max — dismiss oldest if over limit.
        while len(stack) >= self.MAX_VISIBLE:
            oldest = stack[0]
            oldest._dismiss()
            # Remove from stack before next append — dismissed handler
            # does removal too but we need position slot now.
            if oldest in stack:
                stack.remove(oldest)

        toast = Toast(parent, message, kind, duration_ms)
        stack.append(toast)

        # Position: bottom-right of parent, stacked upward
        x = parent.width() - Toast.WIDTH - Toast.MARGIN
        y = parent.height() - Toast.MARGIN - Toast.HEIGHT
        # Offset by each existing toast's height + gap
        gap = 8
        for i, existing in enumerate(stack[:-1]):  # exclude the new one
            y -= (Toast.HEIGHT + gap)

        toast.show_at(x, y)
        return toast

    def _on_toast_dismissed(self, toast: Toast) -> None:
        """Called when a toast fades out — remove from stack + reflow."""
        for parent, stack in self._stacks.items():
            if toast in stack:
                stack.remove(toast)
                self._reflow(parent)
                break

    def _reflow(self, parent: QWidget) -> None:
        """Reposition remaining toasts after one dismissed."""
        stack = self._stacks.get(parent, [])
        if not stack:
            return
        x = parent.width() - Toast.WIDTH - Toast.MARGIN
        y_start = parent.height() - Toast.MARGIN - Toast.HEIGHT
        gap = 8
        for i, t in enumerate(stack):
            y = y_start - i * (Toast.HEIGHT + gap)
            t.move(x, y)


# ── Public API ────────────────────────────────────────────────────
def toast_success(parent: QWidget, message: str, duration_ms: int = 4000) -> Toast:
    return ToastManager.instance().show(parent, message, "success", duration_ms)


def toast_error(parent: QWidget, message: str, duration_ms: int = 6000) -> Toast:
    return ToastManager.instance().show(parent, message, "error", duration_ms)


def toast_warning(parent: QWidget, message: str, duration_ms: int = 5000) -> Toast:
    return ToastManager.instance().show(parent, message, "warning", duration_ms)


def toast_info(parent: QWidget, message: str, duration_ms: int = 4000) -> Toast:
    return ToastManager.instance().show(parent, message, "info", duration_ms)
