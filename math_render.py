"""
Chandra math formula rendering + cleanup.

Chandra OCR 2 emits inline math via <math>...</math> tags containing LaTeX:
  <math>\\frac{A}{12} \\times 6 \\times 70\\%</math>

Two output modes:
  - display (plain=False): for GUI + DOCX. Keeps readable Unicode operators
    (×, ÷, ±) and parenthesizes fractions so complex numerators stay valid:
    `\\frac{B-(C+D)}{12}` → `(B-(C+D))/(12)`.
  - plain (plain=True): for embedding, Excel export, semantic search. Strips
    all LaTeX syntax so `\\frac` / `\\times` tokens don't pollute the vocab.
    Parens still preserved for math correctness — dropping them on
    multi-term numerators would change the meaning.

Single entry point: `strip_math_tags(html, plain)`. Downstream renderers
(display_html_renderer, html_to_docx) call this BEFORE BS4 parsing so the
rest of the pipeline sees normal text — no special-case branches needed.

Observed LaTeX commands from Chandra output (2026-04-17, doc 32):
  \\frac, \\times, \\%
Extended to common admin-doc math: \\div, \\leq, \\geq, \\ne, \\pm,
greek letters (α β π), superscript numbers.
"""
import re
from typing import Optional


# ── Unicode mappings for display mode ────────────────────────────
_UNICODE_OPS = {
    r'\times': ' × ',
    r'\div':   ' ÷ ',
    r'\pm':    '±',
    r'\mp':    '∓',
    r'\leq':   '≤',
    r'\geq':   '≥',
    r'\ne':    '≠',
    r'\neq':   '≠',
    r'\approx': '≈',
    r'\equiv': '≡',
    r'\infty': '∞',
    r'\sum':   '∑',
    r'\prod':  '∏',
    r'\int':   '∫',
    r'\sqrt':  '√',
    r'\cdot':  '·',
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
    r'\pi':    'π', r'\sigma': 'σ', r'\theta': 'θ', r'\mu': 'μ',
    r'\Alpha': 'Α', r'\Beta': 'Β', r'\Gamma': 'Γ', r'\Delta': 'Δ',
    r'\Pi':    'Π', r'\Sigma': 'Σ', r'\Theta': 'Θ',
    r'\%':     '%',  # literal percent
}

# ── ASCII mappings for plain (embed/Excel) mode ──────────────────
# Keep meaning; prefer tokens that won't confuse search indexes.
_ASCII_OPS = {
    r'\times': ' x ',
    r'\div':   ' / ',
    r'\pm':    '+/-',
    r'\mp':    '-/+',
    r'\leq':   '<=',
    r'\geq':   '>=',
    r'\ne':    '!=',
    r'\neq':   '!=',
    r'\approx': '~=',
    r'\equiv': '==',
    r'\infty': 'inf',
    r'\sum':   'SUM',
    r'\prod':  'PROD',
    r'\int':   'INT',
    r'\sqrt':  'sqrt',
    r'\cdot':  '*',
    r'\alpha': 'alpha', r'\beta': 'beta', r'\gamma': 'gamma',
    r'\delta': 'delta', r'\pi': 'pi', r'\sigma': 'sigma',
    r'\theta': 'theta', r'\mu': 'mu',
    r'\%':     '%',
}

# Regex for \frac{...}{...} — single level of braces only. If Chandra starts
# emitting nested {} we may need a recursive matcher; current output stays flat.
_FRAC_RE = re.compile(r'\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}')

# Any remaining \command — defensive strip so no stray tokens leak.
_GENERIC_CMD_RE = re.compile(r'\\[a-zA-Z]+\*?')

# <math>...</math> blocks (lazy, cross-line)
_MATH_BLOCK_RE = re.compile(r'<math\b[^>]*>(.*?)</math>', re.DOTALL | re.IGNORECASE)


def latex_to_text(s: str, *, plain: bool = False) -> str:
    """Convert a LaTeX fragment to readable text.

    `plain=False` → Unicode operators (×, ÷, ≤, ±, α). Use for GUI / DOCX.
    `plain=True`  → ASCII-safe tokens. Use for embedding / Excel / search.

    Fractions always use `(num)/(denom)` form — dropping the parentheses
    would break meaning when numerator contains subtraction or a sum.
    """
    if not s:
        return s

    # 1. Expand \frac{a}{b} → (a)/(b) — do this FIRST so inner \times inside
    #    numerator/denominator is handled in pass 2.
    s = _FRAC_RE.sub(r'(\1)/(\2)', s)

    # 2. Operator substitutions
    ops = _ASCII_OPS if plain else _UNICODE_OPS
    for cmd, repl in ops.items():
        s = s.replace(cmd, repl)

    # 3. Strip any remaining \commands (defensive — unknown commands become
    #    their inner text via regex removal, e.g. "\mathrm{foo}" leftovers).
    s = _GENERIC_CMD_RE.sub('', s)

    # 4. Clean up braces that were part of LaTeX grouping but aren't math parens.
    #    Any `{x}` remaining (not from \frac which we already consumed) → just x.
    s = re.sub(r'\{([^{}]*)\}', r'\1', s)

    # 5. Collapse whitespace runs (from inserted spaces around × etc.)
    s = re.sub(r'[ \t]+', ' ', s).strip()
    return s


def strip_math_tags(html: str, *, plain: bool = False) -> str:
    """Replace every <math>...</math> block in `html` with its rendered text.

    Call BEFORE BS4 parsing. Downstream renderers see no LaTeX at all.

    `plain=True` variant is for embedding / Excel: no Unicode math ops, no
    braces, no backslash commands — pure text tokens a tokenizer can split
    cleanly.
    """
    if not html or '<math' not in html:
        # Fast path: most Chandra outputs have no math at all.
        return html

    def _sub(m):
        inner = m.group(1)
        return latex_to_text(inner, plain=plain)

    return _MATH_BLOCK_RE.sub(_sub, html)
