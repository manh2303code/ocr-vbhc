"""
Intro/Splash Page — replaces login system.
Design based on Stitch-generated HTML (dark theme, pipeline visualization).

Animation flow:
  Phase 1 (0-1.5s):  Logo zoom-in + glow
  Phase 2 (1.5-2.5s): Logo fade out, title fade in
  Phase 3 (2.5-4s):   Pipeline mascot + features slide in
  Phase 4 (4s+):      CTA button pulse — click → MainWindow
"""
import math
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import (
    QEasingCurve, QPropertyAnimation, QParallelAnimationGroup,
    QSequentialAnimationGroup, QPoint, QRect, QSize, Qt, QTimer,
    pyqtProperty, pyqtSignal,
)
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt5.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
)

ASSETS_DIR = Path(__file__).parent / "assets"

# ── Color palette (from Stitch design) ──────────────────────────
BG_COLOR = QColor("#0c1324")
SURFACE_COLOR = QColor("#191f31")
SURFACE_VARIANT = QColor("#2e3447")
CYAN_PRIMARY = QColor("#00fbfb")
CYAN_DIM = QColor("#00dddd")
GOLD = QColor("#e9c349")
TEXT_PRIMARY = QColor("#dce1fb")
TEXT_SECONDARY = QColor("#b9cac9")
TEXT_MUTED = QColor("#484f58")
BORDER_COLOR = QColor("#3a4a49")


class AnimatedBackground(QWidget):
    """Dark background with subtle animated dot grid (aura waves)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)  # 20 FPS — lightweight

    def _tick(self):
        self._phase += 0.05
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), BG_COLOR)

        # Radial glow at center
        cx, cy = self.width() / 2, self.height() * 0.45
        grad = QRadialGradient(cx, cy, self.width() * 0.4)
        grad.setColorAt(0.0, QColor(0, 251, 251, 18))
        grad.setColorAt(0.4, QColor(233, 195, 73, 10))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())

        # Dot grid with wave motion
        gap = 40
        painter.setPen(Qt.NoPen)
        dot_color = QColor(0, 251, 251, 20)
        painter.setBrush(dot_color)
        for gx in range(0, self.width() + gap, gap):
            for gy in range(0, self.height() + gap, gap):
                dx = math.sin(self._phase + gx * 0.008 + gy * 0.008) * 6
                dy = math.cos(self._phase + gx * 0.008 + gy * 0.008) * 6
                painter.drawEllipse(int(gx + dx), int(gy + dy), 2, 2)

        # Scan line
        scan_x = int((self._phase * 80) % (self.width() + 300)) - 150
        scan_grad = QLinearGradient(scan_x, 0, scan_x + 300, 0)
        scan_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        scan_grad.setColorAt(0.5, QColor(0, 251, 251, 25))
        scan_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(scan_grad)
        painter.drawRect(scan_x, 0, 300, self.height())

        painter.end()


class GlowButton(QPushButton):
    """Cyan glow CTA button with pulse animation."""

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._glow_radius = 20
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(56)
        self.setMinimumWidth(280)
        self.setFont(QFont("Be Vietnam Pro", 15, QFont.Bold))
        self._update_style()

        # Pulse animation
        self._pulse_timer = QTimer(self)
        self._pulse_phase = 0.0
        self._pulse_timer.timeout.connect(self._pulse_tick)

    def start_pulse(self):
        self._pulse_timer.start(50)

    def stop_pulse(self):
        self._pulse_timer.stop()

    def _pulse_tick(self):
        self._pulse_phase += 0.1
        self._glow_radius = 20 + int(math.sin(self._pulse_phase) * 8)
        self._update_style()

    def _update_style(self):
        r = self._glow_radius
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: #00fbfb;
                color: #002020;
                border: none;
                border-radius: 28px;
                padding: 0 40px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #00dddd;
            }}
            QPushButton:pressed {{
                background-color: #00bbbb;
            }}
        """)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Glow shadow
        r = self._glow_radius
        glow = QRadialGradient(self.width() / 2, self.height() / 2,
                               max(self.width(), self.height()) * 0.7)
        glow.setColorAt(0.0, QColor(0, 251, 251, 60))
        glow.setColorAt(0.5, QColor(0, 251, 251, 20))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(-r, -r,
                                self.width() + r * 2,
                                self.height() + r * 2,
                                28 + r, 28 + r)

        # Button body
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 28, 28)
        painter.setBrush(QColor("#00fbfb") if not self.isDown() else QColor("#00bbbb"))
        painter.drawPath(path)

        # Text
        painter.setPen(QColor("#002020"))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())

        painter.end()


class FeatureCard(QWidget):
    """Glassmorphism feature card with icon, title, description."""

    def __init__(self, icon_text: str, title: str, desc: str,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(10)

        icon_label = QLabel(icon_text)
        icon_label.setFont(QFont("Segoe UI Emoji", 18))
        icon_label.setFixedWidth(30)
        header.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setFont(QFont("Be Vietnam Pro", 13, QFont.Bold))
        title_label.setStyleSheet("color: #dce1fb;")
        header.addWidget(title_label)
        header.addStretch()

        layout.addLayout(header)

        desc_label = QLabel(desc)
        desc_label.setFont(QFont("Be Vietnam Pro", 11))
        desc_label.setStyleSheet("color: #b9cac9;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Glass background
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, 8, 8)

        painter.setBrush(QColor(46, 52, 71, 80))
        painter.setPen(QPen(QColor(58, 74, 73, 60), 1))
        painter.drawPath(path)

        # Left cyan accent border
        painter.setPen(Qt.NoPen)
        painter.setBrush(CYAN_PRIMARY)
        accent = QPainterPath()
        accent.addRoundedRect(0, 8, 4, self.height() - 16, 2, 2)
        painter.drawPath(accent)

        painter.end()


def _make_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    return effect


def _fade_in(effect: QGraphicsOpacityEffect, duration: int = 600,
             start: float = 0.0, end: float = 1.0) -> QPropertyAnimation:
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


class IntroPage(QWidget):
    """Full-screen intro/splash page replacing the login system.

    Signals:
        start_clicked: emitted when user clicks "Bắt đầu sử dụng"
    """

    start_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("OCR VBHC — Hệ thống số hóa văn bản hành chính")
        self.setMinimumSize(1200, 800)

        # App icon (bear mascot)
        icon_path = ASSETS_DIR / "app_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Background
        self._bg = AnimatedBackground(self)

        # Main layout on top of background
        self._container = QWidget(self)
        main_layout = QVBoxLayout(self._container)
        main_layout.setContentsMargins(40, 12, 40, 12)
        main_layout.setSpacing(0)

        # ── Section 1: Logo + Org Name (compact) ────────────────
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setAlignment(Qt.AlignCenter)
        header_layout.setSpacing(4)
        header_layout.setContentsMargins(0, 0, 0, 0)

        # Logo — small
        self._logo_label = QLabel()
        self._logo_label.setAlignment(Qt.AlignCenter)
        logo_path = ASSETS_DIR / "app_icon_256.png"
        if logo_path.exists():
            pm = QPixmap(str(logo_path)).scaled(
                56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._logo_label.setPixmap(pm)
        header_layout.addWidget(self._logo_label)

        org_name = QLabel("OCR VBHC")
        org_name.setAlignment(Qt.AlignCenter)
        org_name.setFont(QFont("Be Vietnam Pro", 11, QFont.Bold))
        org_name.setStyleSheet("color: #dce1fb; letter-spacing: 3px;")
        header_layout.addWidget(org_name)

        sub_org = QLabel("HỆ THỐNG SỐ HÓA VĂN BẢN HÀNH CHÍNH")
        sub_org.setAlignment(Qt.AlignCenter)
        sub_org.setFont(QFont("Be Vietnam Pro", 8))
        sub_org.setStyleSheet("color: #8b949e;")
        header_layout.addWidget(sub_org)

        # Divider line
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setMaximumWidth(300)
        divider.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 transparent, stop:0.5 #00fbfb, stop:1 transparent);"
        )
        header_layout.addSpacing(6)
        header_layout.addWidget(divider, 0, Qt.AlignCenter)

        self._header_effect = _make_opacity_effect(header_widget)
        main_layout.addWidget(header_widget)

        # ── Section 2: Hero Title (smaller) ─────────────────────
        title_widget = QWidget()
        title_layout = QVBoxLayout(title_widget)
        title_layout.setAlignment(Qt.AlignCenter)
        title_layout.setSpacing(4)
        title_layout.setContentsMargins(0, 6, 0, 0)

        hero_title = QLabel("HỆ THỐNG SỐ HÓA VĂN BẢN HÀNH CHÍNH")
        hero_title.setAlignment(Qt.AlignCenter)
        hero_title.setFont(QFont("Be Vietnam Pro", 22, QFont.ExtraBold))
        hero_title.setStyleSheet("color: #dce1fb;")
        title_layout.addWidget(hero_title)

        # Subtitle
        subtitle = QLabel("Trích xuất  •  Tìm kiếm AI  •  Xuất báo cáo")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("Be Vietnam Pro", 11))
        subtitle.setStyleSheet("color: #b9cac9;")
        title_layout.addWidget(subtitle)

        self._title_effect = _make_opacity_effect(title_widget)
        main_layout.addWidget(title_widget)

        # ── Section 3: Pipeline Mascot (LARGE — hero visual) ────
        pipeline_widget = QWidget()
        pipeline_layout = QVBoxLayout(pipeline_widget)
        pipeline_layout.setAlignment(Qt.AlignCenter)
        pipeline_layout.setContentsMargins(0, 8, 0, 4)

        mascot_label = QLabel()
        mascot_label.setAlignment(Qt.AlignCenter)
        mascot_path = ASSETS_DIR / "pipeline_mascot.png"
        if mascot_path.exists():
            pm = QPixmap(str(mascot_path)).scaled(
                960, 380, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            mascot_label.setPixmap(pm)
        pipeline_layout.addWidget(mascot_label)

        self._pipeline_effect = _make_opacity_effect(pipeline_widget)
        main_layout.addWidget(pipeline_widget)

        # ── Section 4: CTA Button ───────────────────────────────
        cta_widget = QWidget()
        cta_layout = QVBoxLayout(cta_widget)
        cta_layout.setAlignment(Qt.AlignCenter)
        cta_layout.setContentsMargins(0, 4, 0, 2)
        cta_layout.setSpacing(4)

        self._cta_btn = GlowButton("🚀  Bắt đầu sử dụng")
        self._cta_btn.clicked.connect(self._on_start)
        cta_layout.addWidget(self._cta_btn, 0, Qt.AlignCenter)

        hint = QLabel("Nhấn Enter hoặc click để tiếp tục")
        hint.setAlignment(Qt.AlignCenter)
        hint.setFont(QFont("Be Vietnam Pro", 10))
        hint.setStyleSheet("color: #484f58;")
        cta_layout.addWidget(hint)

        self._cta_effect = _make_opacity_effect(cta_widget)
        main_layout.addWidget(cta_widget)

        # ── Section 5: Feature Cards ────────────────────────────
        features_widget = QWidget()
        features_layout = QHBoxLayout(features_widget)
        features_layout.setSpacing(12)
        features_layout.setContentsMargins(60, 4, 60, 0)

        cards_data = [
            ("📄", "OCR Thông Minh",
             "Nhận dạng 12 trường NĐ 30/2020 tự động với độ chính xác cao."),
            ("🔍", "Tìm Kiếm AI",
             "Tìm kiếm bằng từ khóa và công nghệ RAG trên dữ liệu lớn."),
            ("📤", "Xuất Đa Định Dạng",
             "Word, Excel, HTML, Markdown — xuất dữ liệu chỉ một nút bấm."),
        ]
        self._cards = []
        for icon, title, desc in cards_data:
            card = FeatureCard(icon, title, desc)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            features_layout.addWidget(card)
            self._cards.append(card)

        self._features_effect = _make_opacity_effect(features_widget)
        main_layout.addWidget(features_widget)

        # ── Footer ──────────────────────────────────────────────
        main_layout.addStretch()

        footer = QLabel("© 2026 OCR VBHC")
        footer.setAlignment(Qt.AlignCenter)
        footer.setFont(QFont("Be Vietnam Pro", 9))
        footer.setStyleSheet("color: #b9cac9; opacity: 0.6;")
        main_layout.addWidget(footer)

        version = QLabel("Phiên bản 2.0.0  •  Chạy hoàn toàn offline  •  Không cần kết nối mạng")
        version.setAlignment(Qt.AlignCenter)
        version.setFont(QFont("Be Vietnam Pro", 8))
        version.setStyleSheet("color: #484f58;")
        main_layout.addWidget(version)

        # ── Start animation sequence ────────────────────────────
        QTimer.singleShot(200, self._run_animations)

    # ── Animation sequence ──────────────────────────────────────

    def _run_animations(self):
        self._anim_group = QSequentialAnimationGroup(self)

        # Phase 1: Header (logo + org name) fade in
        phase1 = _fade_in(self._header_effect, 800)

        # Phase 2: Title fade in
        phase2 = _fade_in(self._title_effect, 600)

        # Phase 3: Pipeline + CTA (parallel)
        phase3 = QParallelAnimationGroup()
        phase3.addAnimation(_fade_in(self._pipeline_effect, 800))
        phase3.addAnimation(_fade_in(self._cta_effect, 800))

        # Phase 4: Features fade in
        phase4 = _fade_in(self._features_effect, 600)

        self._anim_group.addAnimation(phase1)
        self._anim_group.addPause(200)
        self._anim_group.addAnimation(phase2)
        self._anim_group.addPause(200)
        self._anim_group.addAnimation(phase3)
        self._anim_group.addPause(200)
        self._anim_group.addAnimation(phase4)

        # Start CTA pulse after all animations complete
        self._anim_group.finished.connect(self._cta_btn.start_pulse)

        self._anim_group.start()

    # ── Event handlers ──────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._on_start()
        super().keyPressEvent(event)

    def _on_start(self):
        if not self._cta_btn.isEnabled():
            return
        self._cta_btn.stop_pulse()
        self._cta_btn.setEnabled(False)
        self.start_clicked.emit()

    # ── Layout management ───────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg.setGeometry(self.rect())
        self._container.setGeometry(self.rect())


# ── Standalone test ─────────────────────────────────────────────

if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("OCR VBHC Intro")

    # Try to load Be Vietnam Pro font
    font_path = ASSETS_DIR / "BeVietnamPro-Regular.ttf"
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))

    page = IntroPage()
    page.start_clicked.connect(lambda: print("START CLICKED — would launch MainWindow"))
    page.showMaximized()

    sys.exit(app.exec_())
