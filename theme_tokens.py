"""
Design tokens — single source of truth for colors/spacing/typography.

Light theme: olive, gold and red accents on a warm-cream document-paper
background.

Intro page keeps its existing dark theme; these tokens apply only to
MainWindow + its dialogs via `setStyleSheet` scoped to MainWindow root.

Usage:
    from theme_tokens import COLORS, FONT, qss_content
    main_window.setStyleSheet(qss_content())
"""
from pathlib import Path


# ── Palette ────────────────────────────────────────────────────
COLORS = {
    # Surfaces — warm administrative paper
    'bg':              '#F5F1E8',   # page background (warm paper)
    'surface':         '#FFFEFA',   # card / input surface
    'surface_alt':     '#EFEADF',   # alternating row, muted panel
    'surface_hover':   '#EAE4D7',   # hover state
    'surface_pressed': '#E0D8C9',   # active/pressed

    # Primary — olive green
    'primary':         '#24432D',
    'primary_hover':   '#315A3E',
    'primary_muted':   '#52745D',
    'primary_soft':    '#E2EAE4',   # bg tint (selected row)

    # Accent — emblem gold
    # Darkened from #8E6B16: that reached AA on `surface` but only 4.37 on the
    # page background and 4.10 on alternating rows.
    'accent_gold':      '#846314',
    'accent_gold_hover':'#755811',
    'accent_gold_soft': '#F2E8C8',

    # CTA — flag red (urgent actions, dangerous ops)
    'cta':       '#B3263E',
    'cta_hover': '#931E33',

    # Text
    'text':            '#17231B',
    'text_secondary':  '#5C665F',
    # Darkened from #667085 for the same reason: 4.41 on bg, 4.15 on rows.
    'text_muted':      '#5F687C',
    'text_on_primary': '#FFFEFA',

    # Borders
    'border':        '#D8D1C3',
    'border_strong': '#C6BBA8',
    'border_focus':  '#846314',   # tracks accent_gold; a test fails if they drift

    # Status
    'success': '#1E7A2B',
    'warning': '#D97706',
    'danger':  '#C41E3A',
    'info':    '#235FE1',   # darkened: 4.31 on alternating rows was below AA

    # NĐ30 urgency — strong contrast on cream bg
    'urgency_hoa_toc':     '#DC2626',
    'urgency_thuong_khan': '#EA580C',
    'urgency_khan':        '#D4AF37',

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
    # Surfaces — deep slate, warm paper inverted
    'bg':              '#0F1419',
    'surface':         '#1A2332',
    'surface_alt':     '#1E2B3D',
    'surface_hover':   '#253349',
    'surface_pressed': '#2D3B52',

    # Primary — olive, fill only; see the note above
    'primary':         '#4A6B52',
    'primary_hover':   '#557A5E',
    'primary_muted':   '#7FA187',   # the brand colour when it has to be text
    'primary_soft':    '#243B31',

    'accent_gold':      '#E5C466',
    'accent_gold_hover': '#D4B255',
    'accent_gold_soft': '#3A3218',

    'cta':       '#F87171',
    'cta_hover': '#EF5A5A',

    'text':            '#E8EBF5',
    'text_secondary':  '#A8B2D1',
    'text_muted':      '#8C97B5',
    'text_on_primary': '#FFFEFA',

    'border':        '#2D3B52',
    'border_strong': '#3A4A64',
    'border_focus':  '#E5C466',   # tracks accent_gold

    # Status — lightened so they read on dark; dark text goes on top of them
    'success': '#7BD88F',
    'warning': '#F5B841',
    'danger':  '#F87171',
    'info':    '#7FB2FF',

    'urgency_hoa_toc':     '#FF6B6B',
    'urgency_thuong_khan': '#FF9F5A',
    'urgency_khan':        '#E5C466',

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
