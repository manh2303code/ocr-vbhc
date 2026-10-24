"""
Rotating SVG spinner widget.

Loads a Lucide-style SVG icon and rotates it via `QPropertyAnimation` on a
custom `_angle` property. Driven by Qt's animation framework so no manual
QTimer needed. Pauses during main-thread blocks (accepted tradeoff — during
MainWindow heavy init the spinner briefly freezes; status text is updated
before the block so the user sees context).

Usage:
    spinner = LoadingSpinner("icons/fluent/loader-2.svg", size=48, color="#00fbfb")
    layout.addWidget(spinner)
"""
from pathlib import Path

from PyQt5.QtCore import Qt, QPropertyAnimation, QByteArray, QRectF, pyqtProperty
from PyQt5.QtGui import QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QWidget


class LoadingSpinner(QWidget):
    """Smoothly rotating SVG icon."""

    def __init__(self, svg_path, size=48, color="#00fbfb",
                 duration_ms=1000, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._size = size
        self._angle = 0.0

        # Load SVG and recolor stroke. Lucide SVGs use `currentColor` or a
        # hard-coded color — replace both to match our theme accent.
        svg_text = Path(svg_path).read_text(encoding="utf-8")
        svg_text = (
            svg_text.replace("currentColor", color)
                    .replace('stroke="#000"', f'stroke="{color}"')
                    .replace('stroke="#000000"', f'stroke="{color}"')
        )
        self._renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))

        # Rotation animation (infinite loop).
        self._anim = QPropertyAnimation(self, b"angle", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(duration_ms)
        self._anim.setLoopCount(-1)
        self._anim.start()

    # ── QPropertyAnimation target ───────────────────────────────
    def _get_angle(self):
        return self._angle

    def _set_angle(self, value):
        self._angle = value
        self.update()

    angle = pyqtProperty(float, _get_angle, _set_angle)

    # ── Paint ────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Rotate around center.
        painter.translate(self._size / 2, self._size / 2)
        painter.rotate(self._angle)
        painter.translate(-self._size / 2, -self._size / 2)
        # QSvgRenderer.render requires QRectF (float), not QRect (int).
        self._renderer.render(painter, QRectF(0, 0, self._size, self._size))

    def stop(self):
        """Stop animation — called before widget is destroyed."""
        self._anim.stop()
