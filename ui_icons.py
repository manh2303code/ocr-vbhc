"""
Central SVG icon loader with QIcon cache.

Performance-optimized:
- QIcon loaded once per (name, size, color) — stored in module dict
- SVG → QIcon uses QSvgRenderer → QPixmap (Qt-native, GPU composited)
- First load ~2-5ms, cached loads <100µs
- Recolor via QPainter.CompositionMode_SourceIn — no DOM mutation

Usage:
    from ui_icons import icon, make_tool_action
    btn.setIcon(icon("panel-left", size=20))
    action = make_tool_action(self, "save", "Lưu (Ctrl+S)",
                              callback=self.save_doc, shortcut="Ctrl+S")
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QSize, Qt, QByteArray
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColor, QKeySequence
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QAction, QWidget

logger = logging.getLogger("UIIcons")

_ICON_DIR = Path(__file__).parent / "icons" / "fluent"
_CACHE: dict[tuple, QIcon] = {}  # key: (name, size, color) → QIcon


def _default_color() -> str:
    """The quiet icon colour of the current theme, from the tokens."""
    import theme_tokens

    try:
        from theme_manager_v2 import ThemeManager
        theme = ThemeManager.instance().current
        theme = theme() if callable(theme) else theme
    except Exception:
        theme = "light"
    return theme_tokens.colors_for(theme)["text_secondary"]


def icon(name: str, size: int = 20, color: Optional[str] = None) -> QIcon:
    """Load an SVG icon from icons/fluent/ with color tinting + cache.

    Args:
        name: SVG basename without extension (e.g. "panel-left")
        size: pixel size — 16/20/24 most common
        color: hex string for the fill/stroke; None = the current theme's
            quiet icon colour (text_secondary token)

    Returns:
        QIcon ready for setIcon(). Returns empty QIcon if file missing
        (logs warning once, doesn't raise).
    """
    if color is None:
        color = _default_color()
    key = (name, size, color)
    if key in _CACHE:
        return _CACHE[key]

    svg_path = _ICON_DIR / f"{name}.svg"
    if not svg_path.exists():
        logger.warning(f"Icon not found: {svg_path}")
        _CACHE[key] = QIcon()
        return _CACHE[key]

    try:
        # Read + recolor SVG via text substitution (simple, no DOM parse).
        # Fluent icons are filled shapes with fill="currentColor" on the root
        # (devtools/fetch_fluent_icons.py adds it); stroke covers older SVGs.
        svg_text = svg_path.read_text(encoding="utf-8")
        colored_svg = (
            svg_text
            .replace('fill="currentColor"', f'fill="{color}"')
            .replace('stroke="currentColor"', f'stroke="{color}"')
            .replace("stroke='currentColor'", f"stroke='{color}'")
        )

        # Render at 2× size for HiDPI, downscale smoothly.
        render_size = size * 2
        renderer = QSvgRenderer(QByteArray(colored_svg.encode("utf-8")))
        pixmap = QPixmap(render_size, render_size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(painter)
        painter.end()

        # Keep the 2x pixels: at 150-200% display scaling Qt draws them
        # directly instead of upscaling a 1x pixmap into a blur.
        pixmap.setDevicePixelRatio(2.0)
        qicon = QIcon(pixmap)
        _CACHE[key] = qicon
        return qicon
    except Exception as e:
        logger.error(f"Failed to load icon '{name}': {e}")
        _CACHE[key] = QIcon()
        return _CACHE[key]


def make_tool_action(
    parent: QWidget,
    icon_name: str,
    tooltip: str,
    callback=None,
    shortcut: Optional[str] = None,
    checkable: bool = False,
    icon_size: int = 20,
    icon_color: Optional[str] = None,
) -> QAction:
    """Create a QAction ready for QToolBar.addAction() or menu.

    Light-weight: QAction is native Qt, handles tooltip + shortcut
    internally with zero custom widget overhead.

    Args:
        parent: parent QWidget (the MainWindow usually)
        icon_name: icon name (see icons/fluent/ and icons/fluent/MANIFEST.json)
        tooltip: includes shortcut hint ("Tìm kiếm (Ctrl+F)")
        callback: slot connected to triggered signal
        shortcut: QKeySequence string (None to skip)
        checkable: for toggle actions (e.g. hamburger)
        icon_size: icon pixel size
        icon_color: hex color for stroke

    Returns:
        QAction configured and ready.
    """
    action = QAction(parent)
    action.setIcon(icon(icon_name, size=icon_size, color=icon_color))
    action.setToolTip(tooltip)
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    action.setCheckable(checkable)
    if callback:
        if checkable:
            action.toggled.connect(callback)
        else:
            action.triggered.connect(callback)
    return action


def clear_icon_cache() -> None:
    """Drop cache — useful when changing theme color at runtime."""
    _CACHE.clear()


def preload_icons(names: list[str], size: int = 20, color: Optional[str] = None) -> None:
    """Pre-warm cache for known icons at app startup.

    Reduces first-interaction latency. Call from MainWindow.__init__.
    """
    for name in names:
        icon(name, size=size, color=color)
    logger.info(f"Preloaded {len(names)} icons at {size}px")
