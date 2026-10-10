"""Register the bundled Vietnamese UI font before any application widget exists."""
from pathlib import Path

from PyQt5.QtGui import QFont, QFontDatabase


FONT_FAMILY = "Be Vietnam Pro"
_ROOT = Path(__file__).resolve().parent
_FONT_DIR = _ROOT / "assets" / "fonts"
_FONT_FILES = (
    "BeVietnamPro-Regular.ttf",
    "BeVietnamPro-Medium.ttf",
    "BeVietnamPro-SemiBold.ttf",
    "BeVietnamPro-Bold.ttf",
)


def load_application_fonts(app):
    """Load local fonts and set a deterministic font even on fontless runtimes."""
    loaded = set()
    for name in _FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(_FONT_DIR / name))
        if font_id >= 0:
            loaded.update(QFontDatabase.applicationFontFamilies(font_id))

    family = FONT_FAMILY if FONT_FAMILY in loaded else _load_local_fallback(loaded)
    if family:
        app.setFont(QFont(family, 10))
    return loaded


def _load_local_fallback(loaded):
    candidates = (
        _ROOT / "runtime" / "Lib" / "site-packages" / "matplotlib"
        / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf",
    )
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            loaded.update(families)
            if families:
                return families[0]
    return ""
