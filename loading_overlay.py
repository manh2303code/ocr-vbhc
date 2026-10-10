"""
Loading overlay for intro → MainWindow transition.

2026-04-17 rewrite: replaced rotating spinner with determinate QProgressBar.
Rationale from user feedback — a spinner implies continuous motion, so when
the main thread blocks (MainWindow.__init__ starts llama-server, loads
embedding+reranker models, takes 2-5s) the spinner freezes and looks broken.
A progress bar jumping between known stages (15/35/65/90/100%) is honest UX:
user reads "Đang tải mô hình AI..." and the bar holding at 65% during the
block reads as "I know what stage we're at" not "the app froze".

Usage:
    overlay = LoadingOverlay(parent=intro_page._container)
    overlay.center_in(intro_page._container)
    overlay.fade_in(duration=200)
    overlay.set_progress(35, "Đang khởi động OCR engine...")
"""
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QProgressBar, QGraphicsOpacityEffect
)


class LoadingOverlay(QWidget):
    """Centered loading panel: title + status text + progress bar.

    Fixed size so layout stays stable across stage transitions. Uses
    QGraphicsOpacityEffect for container fade (setWindowOpacity is
    top-level-only and this is a child widget).
    """

    WIDTH = 360
    HEIGHT = 140

    def __init__(self, accent_color="#00fbfb", parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Translucent dark panel matching intro's aesthetic.
        self.setStyleSheet(f"""
            LoadingOverlay {{
                background: rgba(12, 19, 36, 0.92);
                border: 1px solid rgba(0, 251, 251, 0.28);
                border-radius: 14px;
            }}
            QLabel {{ background: transparent; }}
            QProgressBar {{
                background: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 3px;
                height: 6px;
                max-height: 6px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 {accent_color},
                                            stop:1 #00dddd);
                border-radius: 3px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(0)

        # Title — stage group header.
        self._title = QLabel("Đang khởi động")
        self._title.setFont(QFont("Be Vietnam Pro", 12, QFont.Bold))
        self._title.setStyleSheet("color: #ffffff; letter-spacing: 0.02em;")
        layout.addWidget(self._title)
        layout.addSpacing(6)

        # Status — detailed stage description, updated per step.
        self._status = QLabel("Chuẩn bị môi trường...")
        self._status.setFont(QFont("Be Vietnam Pro", 10))
        self._status.setStyleSheet("color: #b9cac9;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch()

        # Progress bar — determinate, advances via QPropertyAnimation at 300ms.
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        # Percent label (right-aligned below bar)
        layout.addSpacing(4)
        self._pct = QLabel("0%")
        self._pct.setFont(QFont("Be Vietnam Pro", 9))
        self._pct.setStyleSheet("color: #839493;")
        self._pct.setAlignment(Qt.AlignRight)
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
