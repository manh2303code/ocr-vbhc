"""
Lightweight toast notifications — non-blocking alerts.

A toast is a small floating surface: the kind as a Fluent icon in its token
colour (success, danger, warning; info in the quiet secondary tone), the
message in the text colour, up to two lines, the rest in the tooltip. Like
the other floating parts of the 2026-10 redesign it is the surface colour
with a border_strong hairline and the 6px popover radius, no tinted fill and
no shadow; the surface comes from the stylesheet ("AREA: dialogs" in
devtools/theme_template.qss), the icon from theme_tokens, recoloured on
ThemeManager.theme_changed.

Performance:
- 3 widgets per toast (QFrame + 2 QLabel)
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
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
)
from PyQt5.QtGui import QFontMetrics, QTextLayout
from PyQt5.QtWidgets import (
    QWidget, QLabel, QFrame, QHBoxLayout, QGraphicsOpacityEffect,
)

import theme_tokens
from ui_icons import icon

logger = logging.getLogger("Toast")


# kind -> (Fluent icon, token that colours it)
TOAST_CONFIGS = {
    "success": {"icon": "check-circle", "token": "success"},
    "error": {"icon": "circle-alert", "token": "danger"},
    "warning": {"icon": "warning", "token": "warning"},
    "info": {"icon": "info", "token": "text_secondary"},
}


def _current_theme() -> str:
    try:
        from theme_manager_v2 import ThemeManager
        return ThemeManager.instance().current
    except Exception:
        return "light"


def _two_lines(text: str, font, width: int) -> str:
    """The text as it fits in two wrapped lines, the second ending in "…"
    when there is more."""
    layout = QTextLayout(text, font)
    layout.beginLayout()
    starts = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        starts.append(line.textStart())
    layout.endLayout()
    if len(starts) <= 2:
        return text
    second = starts[1]
    return text[:second] + QFontMetrics(font).elidedText(text[second:], Qt.ElideRight, width)


class Toast(QFrame):
    """Single toast — self-dismissing with fade-out."""

    WIDTH = 340
    HEIGHT = 56
    MARGIN = 16
    ICON_SIZE = 18

    def __init__(self, parent: QWidget, message: str, kind: str = "info",
                 duration_ms: int = 5000):
        super().__init__(parent)
        self.setObjectName("toast")
        self._kind = kind if kind in TOAST_CONFIGS else "info"
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("kind", self._kind)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        layout.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        # The message wraps to two lines; anything longer is in the tooltip.
        self._message = message
        self._msg_label = QLabel()
        self._msg_label.setObjectName("toastMessage")
        self._msg_label.setTextFormat(Qt.PlainText)
        self._msg_label.setWordWrap(True)
        layout.addWidget(self._msg_label, 1)
        self._fit_message()

        self._apply_theme()
        try:
            from theme_manager_v2 import ThemeManager
            ThemeManager.instance().theme_changed.connect(self.apply_theme)
        except Exception as exc:
            logger.debug(f"Theme signal unavailable: {exc}")

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

    def _fit_message(self) -> None:
        self._msg_label.ensurePolished()
        margins = self.layout().contentsMargins()
        # A pixel or two short of the label, so its own wrapping never
        # needs a third line for what was measured as two.
        width = (self.WIDTH - margins.left() - margins.right()
                 - self.ICON_SIZE - self.layout().spacing() - 2)
        shown = _two_lines(self._message, self._msg_label.font(), width)
        self._msg_label.setText(shown)
        self.setToolTip(self._message if shown != self._message else "")

    def _apply_theme(self) -> None:
        """The kind's icon in its token colour; the surface and the text
        colour are the stylesheet's."""
        config = TOAST_CONFIGS[self._kind]
        colour = theme_tokens.colors_for(_current_theme())[config["token"]]
        self._icon_label.setPixmap(
            icon(config["icon"], self.ICON_SIZE, colour).pixmap(self.ICON_SIZE, self.ICON_SIZE))

    def apply_theme(self, name: str = "") -> None:
        """Public hook called on ThemeManager.theme_changed."""
        self._apply_theme()

    def show_at(self, x: int, y: int) -> None:
        """Display at parent coords + start auto-dismiss timer."""
        self.move(x, y)
        self.show()
        self.raise_()
        self._fit_message()  # the stylesheet's font is known once shown
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
    GAP = 8

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
        y -= (len(stack) - 1) * (Toast.HEIGHT + self.GAP)

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
        for i, t in enumerate(stack):
            y = y_start - i * (Toast.HEIGHT + self.GAP)
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
