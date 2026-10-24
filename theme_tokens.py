"""
Design tokens — single source of truth for colors/spacing/typography.

Olive, gold and red accents on neutral
document paper, in a light and a dark palette with the same token names.
theme.qss and theme_dark.qss are generated from devtools/theme_template.qss
with these values (devtools/build_qss.py); the landing page and the chat
dock read them too.

Usage:
    from theme_tokens import COLORS, FONT, qss_content
    main_window.setStyleSheet(qss_content())
"""
from pathlib import Path


# ── Palette ────────────────────────────────────────────────────
# Olive, gold and red accents on neutral
# document paper. 2026-10 redesign: the warm cream page (#F5F1E8) was the
# most generic-looking choice in the app and is gone; olive carries actions
# and selection, gold is a small mark (document number, focus), never a fill
# for large areas.
COLORS = {
    # Surfaces — neutral printing paper, a faint green-grey cast
    'bg':              '#EEF0EE',   # window canvas
    'surface':         '#FFFFFF',   # panels, inputs, document
    'surface_alt':     '#F6F7F6',   # alternating row, quiet panel
    'surface_hover':   '#E8EBE8',
    'surface_pressed': '#DCE1DC',

    # Primary — olive, deepened for white text
    'primary':         '#2B4A33',
    'primary_hover':   '#36593F',
    'primary_muted':   '#3D6648',
    'primary_soft':    '#E3EBE5',   # selected row, checked tool button

    # Accent — emblem gold, darkened to read on every light surface
    'accent_gold':      '#7A5D0F',
    'accent_gold_hover':'#664E0C',
    'accent_gold_soft': '#F4EBCF',

    # CTA — flag red (urgent actions, dangerous ops)
    'cta':       '#B3263E',
    'cta_hover': '#931E33',

    # Text — olive-tinted ink
    'text':            '#18211B',
    'text_secondary':  '#48524B',
    'text_muted':      '#5D6760',
    'text_on_primary': '#FFFFFF',

    # Borders
    'border':        '#D5DAD5',
    'border_strong': '#B7BFB8',
    'border_focus':  '#7A5D0F',   # tracks accent_gold; a test fails if they drift

    # Status
    'success': '#1F7A3D',
    'warning': '#D97706',   # a fill under dark text, never text itself
    'danger':  '#B3263E',
    'info':    '#2F5E9E',

    # NĐ30 urgency
    'urgency_hoa_toc':     '#DC2626',
    'urgency_thuong_khan': '#EA580C',
    'urgency_khan':        '#B58A12',

    # NĐ30 classification
    'classif_mat':       '#DB2777',
    'classif_toi_mat':   '#9333EA',
    'classif_tuyet_mat': '#A21CAF',
}


# ── Spacing / Radii / Fonts ────────────────────────────────────
SPACING = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16, 'xl': 24, '2xl': 32, '3xl': 48}
RADII   = {'sm': 4, 'md': 8, 'lg': 12, 'xl': 16}

FONT = {
    'family':          'Be Vietnam Pro',
    'family_fallback': 'Segoe UI',
    'xs':   8,   # captions
    'sm':   9,   # secondary text
    'base': 10,  # default body
    'md':   11,  # emphasized body / button
    'lg':   12,  # section labels
    'xl':   14,  # page titles
    '2xl':  18,  # hero
    '3xl':  24,
}


# ── Dark palette ────────────────────────────────────────────────
# Dark mode already existed, but only as stylesheet literals in theme_dark.qss,
# a twelve-key QPalette in theme_manager_v2, and hex copied by hand into
# analytics_dialog and chat_panel. They agreed by luck, and nothing checked
# their contrast — the accessibility suite only ever saw the light theme.
#
# One value could not simply be reused. `primary` is olive #4A6B52, and in a
# dark theme no single olive works as both text and fill: to read against a
# dark surface it must be light, to carry light text it must stay dark, and
# those pull apart. It is a fill here, and primary_muted carries the brand into
# text — which is what the landing page needed, since it styles .brand and the
# about headings with the olive and was rendering them at 2.39 in dark mode.
DARK_COLORS = {
    # Surfaces — the same paper, inverted: near-black with the olive cast
    'bg':              '#121513',
    'surface':         '#191D1A',
    'surface_alt':     '#1F2420',
    'surface_hover':   '#262C27',
    'surface_pressed': '#2F3630',

    # Primary — olive, fill only; see the note above
    'primary':         '#3F6A4B',
    'primary_hover':   '#4A7857',
    'primary_muted':   '#8CB497',   # the brand colour when it has to be text
    'primary_soft':    '#1F2B22',

    'accent_gold':      '#E2C46A',
    'accent_gold_hover': '#D3B356',
    'accent_gold_soft': '#352E17',

    'cta':       '#F28B8B',
    'cta_hover': '#EC7474',

    'text':            '#E6E9E6',
    'text_secondary':  '#B2BAB3',
    'text_muted':      '#929B94',
    'text_on_primary': '#FFFFFF',

    'border':        '#2B312C',
    'border_strong': '#3F4741',
    'border_focus':  '#E2C46A',   # tracks accent_gold

    # Status — lightened so they read on dark; dark text goes on top of them
    'success': '#6FC48E',
    'warning': '#E3A857',
    'danger':  '#F28B8B',
    'info':    '#8DB0E6',

    'urgency_hoa_toc':     '#FF6B6B',
    'urgency_thuong_khan': '#FF9F5A',
    'urgency_khan':        '#E2C46A',

    'classif_mat':       '#F472B6',
    'classif_toi_mat':   '#C084FC',
    'classif_tuyet_mat': '#D8A0F0',
}

_PALETTES = {"light": COLORS, "dark": DARK_COLORS}


def colors_for(theme):
    """Palette for a theme name, defaulting to light for anything unknown.

    Defaulting to light matters: a typo or a stale persisted setting should not
    hand a widget the dark palette while everything around it stays light.
    """
    return _PALETTES.get(theme, COLORS)


# ── QSS loader ──────────────────────────────────────────────────
_QSS_PATH = Path(__file__).parent / "theme.qss"


def qss_content():
    """Return theme.qss contents as a single string.

    Returns empty string if theme.qss missing (graceful fallback — app still
    works without stylesheet).
    """
    if _QSS_PATH.exists():
        return _QSS_PATH.read_text(encoding="utf-8")
    return ""
