"""
Theme Manager v2 — Light/Dark toggle with full cascade.

Coordinates theme switching across:
- Global Qt stylesheet (theme.qss / theme_dark.qss)
- QPalette (for widgets that use palette instead of stylesheet)
- Matplotlib theme (dark_background vs default)
- QToolTip (via stylesheet)
- Icon recolor (via ui_icons clear_cache + re-create)
- Inline-styled widgets (registered for re-apply callback)

Performance:
- QSS swap: 1 file read + setStyleSheet (~10ms)
- QPalette: ~5ms
- Icon cache clear + re-preload: ~10ms
- Total toggle: <50ms
- Persists choice via QSettings

API:
    from theme_manager_v2 import ThemeManager
    tm = ThemeManager.instance()
    tm.apply(app, "dark")  # or "light"
    tm.toggle(app)         # flips current
    tm.current  # "light" or "dark"
    tm.theme_changed.connect(callback)  # listen for changes
"""
from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, QSettings, pyqtSignal
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QApplication

import theme_tokens

logger = logging.getLogger("ThemeManager")

_DIR = Path(__file__).parent
LIGHT_QSS_PATH = _DIR / "theme.qss"
DARK_QSS_PATH = _DIR / "theme_dark.qss"


# Palette colors — apply to Qt's default palette so widgets without an explicit
# stylesheet still theme correctly.
#
# Derived from theme_tokens rather than written out again. The two copies had
# already drifted: link was gold here while COLORS["info"] was blue, so a link
# in a Qt widget and a link in the chat transcript were different colours.
def _palette_from(tokens):
    return {
        "window":         tokens["bg"],
        "window_text":    tokens["text"],
        "base":           tokens["surface"],
        "alt_base":       tokens["surface_alt"],
        "text":           tokens["text"],
        "button":         tokens["surface"],
        "button_text":    tokens["primary"],
        "highlight":      tokens["primary"],
        "highlight_text": tokens["text_on_primary"],
        "tooltip_base":   tokens["surface"],
        "tooltip_text":   tokens["text"],
        "link":           tokens["accent_gold"],
    }


LIGHT_PALETTE = _palette_from(theme_tokens.COLORS)
DARK_PALETTE = _palette_from(theme_tokens.DARK_COLORS)


class ThemeManager(QObject):
    """Singleton coordinating theme state + cascade."""

    theme_changed = pyqtSignal(str)  # emits new theme name after apply

    _instance: Optional["ThemeManager"] = None

    def __init__(self):
        super().__init__()
        self._current = "light"
        # Widgets registered for re-apply on theme change.
        self._registered_widgets: list = []
        ThemeManager._instance = self

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls()
        return cls._instance

    @property
    def current(self) -> str:
        return self._current

    @property
    def is_dark(self) -> bool:
        return self._current == "dark"

    def load_persisted(self) -> str:
        """Return saved theme name or 'light' default."""
        s = QSettings("OCR VBHC", "OCR_VBHC")
        return s.value("theme", "light", type=str)

    def save_persisted(self, name: str) -> None:
        QSettings("OCR VBHC", "OCR_VBHC").setValue("theme", name)

    def apply(self, app: QApplication, name: str = "light") -> None:
        """Switch theme. Safe to call anytime.

        Steps:
        1. Load QSS file for the theme
        2. Set app-level stylesheet (cascades to all widgets)
        3. Set QPalette (for widgets using palette)
        4. Configure matplotlib theme
        5. Clear icon cache (icons need recolor)
        6. Notify registered widgets
        7. Emit theme_changed signal
        8. Persist choice
        """
        if name not in ("light", "dark"):
            logger.warning(f"Unknown theme '{name}', defaulting to light")
            name = "light"

        # 1. Load QSS
        qss_path = DARK_QSS_PATH if name == "dark" else LIGHT_QSS_PATH
        try:
            # url() in the stylesheet is relative to the app folder; Qt
            # resolves relative urls against the working directory instead.
            qss_text = qss_path.read_text(encoding="utf-8").replace("@APP_DIR@", _DIR.as_posix())
        except Exception as e:
            logger.error(f"Failed to read {qss_path}: {e}")
            qss_text = ""

        # 2. App stylesheet. setStyleSheet repolishes every widget; if
        # Python's garbage collector runs in the middle of that and deletes
        # a leftover wrapper, Qt destroys a widget it is still walking and
        # crashes (seen in the test suite). Collect afterwards instead.
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            app.setStyleSheet(qss_text)
        finally:
            if gc_was_enabled:
                gc.enable()

        # 3. QPalette
        self._apply_palette(app, name)

        # 4. Matplotlib
        self._apply_matplotlib(name)

        # 5. Icon cache (icons need new stroke color)
        try:
            from ui_icons import clear_icon_cache
            clear_icon_cache()
        except Exception:
            pass

        # Widgets must observe the new state while applying their own theme.
        self._current = name

        # 6. Notify registered widgets (for inline-styled ones)
        for widget_ref in list(self._registered_widgets):
            try:
                widget = widget_ref()  # weak ref
                if widget is not None and hasattr(widget, "apply_theme"):
                    widget.apply_theme(name)
            except Exception as e:
                logger.debug(f"Registered widget apply_theme failed: {e}")

        self.save_persisted(name)
        self.theme_changed.emit(name)
        logger.info(f"Theme applied: {name}")

    def toggle(self, app: QApplication) -> str:
        """Flip light↔dark. Returns new theme name."""
        new_theme = "dark" if self._current == "light" else "light"
        self.apply(app, new_theme)
        return new_theme

    def register_widget(self, widget) -> None:
        """Register a widget to receive apply_theme() callbacks.

        Widget must implement `apply_theme(name: str)` method. Uses weakref
        so widgets aren't kept alive by the theme manager.
        """
        import weakref
        self._registered_widgets.append(weakref.ref(widget))

    # ── Internal helpers ────────────────────────────────────
    def _apply_palette(self, app: QApplication, name: str) -> None:
        """Update QPalette globally so non-stylesheet widgets theme correctly."""
        colors = DARK_PALETTE if name == "dark" else LIGHT_PALETTE
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(colors["window"]))
        pal.setColor(QPalette.WindowText, QColor(colors["window_text"]))
        pal.setColor(QPalette.Base, QColor(colors["base"]))
        pal.setColor(QPalette.AlternateBase, QColor(colors["alt_base"]))
        pal.setColor(QPalette.Text, QColor(colors["text"]))
        pal.setColor(QPalette.Button, QColor(colors["button"]))
        pal.setColor(QPalette.ButtonText, QColor(colors["button_text"]))
        pal.setColor(QPalette.Highlight, QColor(colors["highlight"]))
        pal.setColor(QPalette.HighlightedText, QColor(colors["highlight_text"]))
        pal.setColor(QPalette.ToolTipBase, QColor(colors["tooltip_base"]))
        pal.setColor(QPalette.ToolTipText, QColor(colors["tooltip_text"]))
        pal.setColor(QPalette.Link, QColor(colors["link"]))
        app.setPalette(pal)

    def _apply_matplotlib(self, name: str) -> None:
        """Point matplotlib's global rcParams at the theme (matplotlib_rc).

        Charts created after this call take the theme; drawn figures need a
        redraw. Only when matplotlib is already loaded: importing pyplot here
        cost every start about 300 ms although only the analytics dialog
        draws charts, and that dialog draws inside matplotlib_rc itself.
        rcParams are updated, not reset to a style first, so a font the
        chart code registered survives a theme switch.
        """
        import sys

        mpl = sys.modules.get("matplotlib")
        if mpl is None:
            return
        try:
            mpl.rcParams.update(matplotlib_rc(name))
        except Exception as e:
            logger.debug(f"matplotlib theme apply failed: {e}")


# Chart colours, in the order a chart takes them: olive carries the data,
# gold is the second colour only (a mark, never the main series), then the
# quiet greys. primary_muted is the olive that reads on the surface of both
# themes (primary itself is a dark fill in the dark theme).
CHART_SERIES_TOKENS = ("primary_muted", "accent_gold", "text_muted", "border_strong")


def matplotlib_rc(theme: str) -> dict:
    """rcParams for flat, quiet charts in `theme`; every colour a theme token.

    The figure lies on the panel surface. Axes are hairlines in the border
    colour, left and bottom only; no grid unless a chart turns one on (then
    a solid hairline); ticks without marks; titles in sentence case at the
    left, in the text colour. Text uses the text tokens, never a series
    colour.
    """
    from cycler import cycler

    tokens = theme_tokens.colors_for(theme)
    series = [tokens[key] for key in CHART_SERIES_TOKENS]
    return {
        "figure.facecolor": tokens["surface"],
        "figure.edgecolor": tokens["surface"],
        "savefig.facecolor": tokens["surface"],
        "savefig.edgecolor": tokens["surface"],
        "axes.facecolor": tokens["surface"],
        "axes.edgecolor": tokens["border"],
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
        "axes.axisbelow": True,
        "axes.labelcolor": tokens["text_secondary"],
        "axes.titlecolor": tokens["text"],
        "axes.titlesize": 10,
        "axes.titleweight": "semibold",
        "axes.titlelocation": "left",
        "axes.titlepad": 10,
        "axes.prop_cycle": cycler(color=series),
        "axes.unicode_minus": False,
        "grid.color": tokens["border"],
        "grid.linestyle": "-",
        "grid.linewidth": 0.8,
        "text.color": tokens["text"],
        "xtick.color": tokens["border"],
        "ytick.color": tokens["border"],
        "xtick.labelcolor": tokens["text_secondary"],
        "ytick.labelcolor": tokens["text_secondary"],
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "xtick.minor.size": 0,
        "ytick.minor.size": 0,
        "xtick.major.pad": 6,
        "ytick.major.pad": 6,
        "legend.frameon": False,
        "legend.labelcolor": tokens["text_secondary"],
        "lines.color": series[0],
        "lines.linewidth": 2,
        "patch.facecolor": series[0],
        "patch.edgecolor": tokens["surface"],
        "patch.linewidth": 0,
        "font.size": 9,
    }


def get_theme_color(key: str) -> str:
    """Convenience: return current theme's color for a key.

    Usage in inline-styled widgets:
        btn.setStyleSheet(f"color: {get_theme_color('text')};")
    """
    tm = ThemeManager.instance()
    palette = DARK_PALETTE if tm.is_dark else LIGHT_PALETTE
    return palette.get(key, "#000000")
