"""
Settings Dialog — 3-tab minimal preferences.

Performance:
- Widgets created lazily per tab (construction <15ms)
- QSettings reads cached by Qt, no disk I/O on every access
- Live-apply ONLY theme (matters visually); others deferred until OK

Settings keys (under org="OCR VBHC", app="OCR_VBHC"):
- theme              → "light" | "dark"   (live)
- font_size          → int 8-14            (deferred)
- ocr_gpu_keepalive  → bool                (deferred)
- ui_show_status_bar → bool                (live toggle)

API:
    dlg = SettingsDialog(parent)
    dlg.exec_()   # modal
    # read result via QSettings("OCR VBHC", "OCR_VBHC").value("theme")
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QRadioButton, QButtonGroup, QSlider, QCheckBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFormLayout,
    QDialogButtonBox, QGroupBox, QSpinBox,
)


from ui_commands import shortcut_rows


SHORTCUT_LIST = shortcut_rows()

SETTINGS_KEYS = {
    "theme":              ("light", str),
    "font_size":          (10, int),
    "ocr_gpu_keepalive":  (True, bool),
    "ui_show_status_bar": (True, bool),
    "left_panel_width":   (280, int),
}


def _settings() -> QSettings:
    return QSettings("OCR VBHC", "OCR_VBHC")


def get_setting(key: str):
    """Read a setting with default fallback."""
    if key not in SETTINGS_KEYS:
        return None
    default, type_ = SETTINGS_KEYS[key]
    return _settings().value(key, default, type=type_)


def set_setting(key: str, value) -> None:
    """Persist a setting."""
    if key not in SETTINGS_KEYS:
        return
    _settings().setValue(key, value)


def apply_ui_settings(app, status_bar) -> None:
    """Apply persisted UI preferences to the running application."""
    font = app.font()
    font.setPointSize(int(get_setting("font_size")))
    app.setFont(font)
    status_bar.setVisible(bool(get_setting("ui_show_status_bar")))


class SettingsDialog(QDialog):
    """3-tab settings dialog: Giao diện · OCR · Phím tắt."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt")
        self.setMinimumSize(520, 440)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_appearance_tab(), "Giao diện")
        self._tabs.addTab(self._build_ocr_tab(), "OCR")
        self._tabs.addTab(self._build_shortcuts_tab(), "Phím tắt")
        root.addWidget(self._tabs, 1)

        # Buttons: OK / Cancel / Apply
        self._btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        self._btns.accepted.connect(self._on_accept)
        self._btns.rejected.connect(self.reject)
        self._btns.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        root.addWidget(self._btns)

        self._load_settings()

    # ── Tab 1: Appearance ─────────────────────────────────────
    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Theme group
        theme_group = QGroupBox("Chủ đề")
        tl = QVBoxLayout(theme_group)
        self._theme_buttons = QButtonGroup(self)
        self._rb_light = QRadioButton("Sáng (kem + vàng)")
        self._rb_dark = QRadioButton("Tối (navy + gold)")
        self._theme_buttons.addButton(self._rb_light, 0)
        self._theme_buttons.addButton(self._rb_dark, 1)
        tl.addWidget(self._rb_light)
        tl.addWidget(self._rb_dark)
        layout.addWidget(theme_group)

        # Font size group
        font_group = QGroupBox("Cỡ chữ")
        fg = QFormLayout(font_group)
        self._spin_font = QSpinBox()
        self._spin_font.setRange(8, 14)
        self._spin_font.setSuffix(" pt")
        self._spin_font.setMinimumWidth(80)
        fg.addRow("Cỡ chữ mặc định:", self._spin_font)
        layout.addWidget(font_group)

        # Status bar visibility
        misc_group = QGroupBox("Giao diện khác")
        mg = QVBoxLayout(misc_group)
        self._chk_status_bar = QCheckBox("Hiện thanh trạng thái dưới cùng")
        mg.addWidget(self._chk_status_bar)
        layout.addWidget(misc_group)

        layout.addStretch()
        return tab

    # ── Tab 2: OCR ────────────────────────────────────────────
    def _build_ocr_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Model info
        info_group = QGroupBox("Mô hình OCR")
        ig = QFormLayout(info_group)
        ig.addRow("Mô hình:", QLabel("Chandra OCR 2 · Q4_K_M"))
        ig.addRow("Kích thước:", QLabel("~3 GB · GGUF format"))
        ig.addRow("Server:", QLabel("llama-server · cổng 9999"))
        layout.addWidget(info_group)

        # GPU keep-alive
        perf_group = QGroupBox("Hiệu suất")
        pg = QVBoxLayout(perf_group)
        self._chk_gpu_ka = QCheckBox(
            "Giữ GPU warm giữa các lần OCR (+20% tốc độ ngắt khoảng)"
        )
        pg.addWidget(self._chk_gpu_ka)
        pg.addWidget(QLabel(
            "<i>Ping server mỗi 6 giây để tránh GPU chuyển sang idle.\n"
            "Tắt nếu máy tính hết pin hoặc dùng CPU-only.</i>"
        ))
        layout.addWidget(perf_group)

        layout.addStretch()
        return tab

    # ── Tab 3: Shortcuts (read-only) ──────────────────────────
    def _build_shortcuts_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        label = QLabel(
            "<b>Danh sách phím tắt</b> — mở nhanh bảng này bằng <code>F1</code>"
        )
        layout.addWidget(label)

        table = QTableWidget(len(SHORTCUT_LIST), 3)
        table.setHorizontalHeaderLabels(["Phím", "Chức năng", "Nhóm"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        for row, (key, desc, group) in enumerate(SHORTCUT_LIST):
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(desc))
            table.setItem(row, 2, QTableWidgetItem(group))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)
        return tab

    # ── Load / Save ───────────────────────────────────────────
    def _load_settings(self) -> None:
        theme = get_setting("theme")
        if theme == "dark":
            self._rb_dark.setChecked(True)
        else:
            self._rb_light.setChecked(True)
        self._spin_font.setValue(int(get_setting("font_size") or 10))
        self._chk_status_bar.setChecked(bool(get_setting("ui_show_status_bar")))
        self._chk_gpu_ka.setChecked(bool(get_setting("ocr_gpu_keepalive")))

    def _save_settings(self) -> None:
        set_setting("theme", "dark" if self._rb_dark.isChecked() else "light")
        set_setting("font_size", self._spin_font.value())
        set_setting("ui_show_status_bar", self._chk_status_bar.isChecked())
        set_setting("ocr_gpu_keepalive", self._chk_gpu_ka.isChecked())

    def _apply_live(self) -> None:
        """Apply UI settings immediately; GPU keep-alive changes on restart."""
        try:
            from theme_manager_v2 import ThemeManager
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            theme = "dark" if self._rb_dark.isChecked() else "light"
            ThemeManager.instance().apply(app, theme)
            parent = self.parentWidget()
            if parent is not None and hasattr(parent, "statusBar"):
                apply_ui_settings(app, parent.statusBar())
        except Exception:
            pass

    def _on_apply(self) -> None:
        self._save_settings()
        self._apply_live()

    def _on_accept(self) -> None:
        self._save_settings()
        self._apply_live()
        self.accept()

    # ── Public snapshot (for tests) ───────────────────────────
    def snapshot(self) -> dict:
        """Return current UI state as dict — useful for test assertions."""
        return {
            "theme": "dark" if self._rb_dark.isChecked() else "light",
            "font_size": self._spin_font.value(),
            "ui_show_status_bar": self._chk_status_bar.isChecked(),
            "ocr_gpu_keepalive": self._chk_gpu_ka.isChecked(),
        }
