"""
Loading overlay for intro → MainWindow transition.

2026-04-17 rewrite: replaced rotating spinner with determinate QProgressBar.
Rationale from user feedback — a spinner implies continuous motion, so when
the main thread blocks (MainWindow.__init__ starts llama-server, loads
embedding+reranker models, takes 2-5s) the spinner freezes and looks broken.
A progress bar jumping between known stages (15/35/65/90/100%) is honest UX:
user reads "Đang tải mô hình AI..." and the bar holding at 65% during the
block reads as "I know what stage we're at" not "the app froze".

2026-10 redesign: the home page's startup line (frontend/landing: a message,
a 2px line in the olive, the percent underneath) on a floating surface with
a border_strong hairline, in both themes. Colours come from the stylesheet
("AREA: dialogs" in devtools/theme_template.qss).

Usage:
    overlay = LoadingOverlay(parent=intro_page._container)
    overlay.center_in(intro_page._container)
    overlay.fade_in(duration=200)
    overlay.set_progress(35, "Đang khởi động OCR engine...")
"""
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QProgressBar, QGraphicsOpacityEffect
)


class LoadingOverlay(QWidget):
    """Centered loading panel: title + status text + progress line + percent.

    Fixed size so layout stays stable across stage transitions. Uses
    QGraphicsOpacityEffect for container fade (setWindowOpacity is
    top-level-only and this is a child widget).
    """

    WIDTH = 360
    HEIGHT = 120

    def __init__(self, accent_color=None, parent=None):
        """accent_color: an optional colour for the progress line; by default
        it is the olive of the theme, like the home page's startup line."""
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(0)

        # Title — stage group header.
        self._title = QLabel("Đang khởi động")
        self._title.setObjectName("loadingTitle")
        layout.addWidget(self._title)
        layout.addSpacing(2)

        # Status — detailed stage description, updated per step.
        self._status = QLabel("Chuẩn bị môi trường...")
        self._status.setObjectName("loadingStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch()

        # Progress line — determinate, advances via QPropertyAnimation.
        self._progress = QProgressBar()
        self._progress.setObjectName("loadingBar")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        if accent_color:
            self._progress.setStyleSheet(
                "QProgressBar::chunk {{ background: {}; }}".format(accent_color))
        layout.addWidget(self._progress)

        # The percent under the line, at its start (as on the home page).
        layout.addSpacing(6)
        self._pct = QLabel("0%")
        self._pct.setObjectName("loadingPercent")
        layout.addWidget(self._pct)

        # Opacity effect for container fade (child widgets can't setWindowOpacity).
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Keep animation refs alive — QPropertyAnimation gets GC'd otherwise.
        self._fade_anim = None
        self._progress_anim = None

    # ── Positioning ──────────────────────────────────────────────
    def center_in(self, parent_widget):
        x = (parent_widget.width() - self.WIDTH) // 2
        y = (parent_widget.height() - self.HEIGHT) // 2
        self.move(x, y)

    # ── Stage progress ────────────────────────────────────────────
    def set_progress(self, pct, status_text=None, title_text=None):
        """Animate the progress bar to `pct` (0-100) over 300ms OutCubic.

        Text labels update immediately — bar value eases in.
        """
        pct = max(0, min(100, int(pct)))
        if title_text is not None:
            self._title.setText(title_text)
        if status_text is not None:
            self._status.setText(status_text)
        self._pct.setText(f"{pct}%")

        self._progress_anim = QPropertyAnimation(self._progress, b"value")
        self._progress_anim.setDuration(300)
        self._progress_anim.setStartValue(self._progress.value())
        self._progress_anim.setEndValue(pct)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._progress_anim.start()

    # ── Fade ──────────────────────────────────────────────────────
    def fade_in(self, duration=200, on_done=None):
        self.show()
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        if on_done is not None:
            self._fade_anim.finished.connect(on_done)
        self._fade_anim.start()

    def fade_out(self, duration=200, on_done=None):
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_anim.setDuration(duration)
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        if on_done is not None:
            self._fade_anim.finished.connect(on_done)
        self._fade_anim.start()
