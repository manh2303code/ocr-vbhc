"""
Pure renderer: Chandra OCR HTML → styled HTML for QTextEdit display.

Extracted from MainWindow._build_display_html (main_window_1.py) so it can be
unit-tested without initializing PyQt5/YOLO/torch. No Qt imports, no I/O.

Iterates root.children (not root.body only) so top-level <table> / <p> / <ul>
from landscape pages render correctly alongside the standard div-wrapped
structure. This is the H10 fix — iterating only root.body drops landscape
tables that Chandra emits as direct root children.

Test fixtures live in tests/regression_baseline/ (normal portrait) and
tests/fixtures/landscape_html/ (edge case). Both sets are regression-tested
in tests/test_regression_landscape_and_normal.py.

2026-10 redesign: the text is set in Times New Roman, the typeface of the
administrative documents themselves, at a reading size; the only colours are
theme tokens (table rules, the image placeholder), passed in per theme. The
text colour is left to the QTextEdit, so a theme swap recolours it.
"""
from bs4 import BeautifulSoup

import theme_tokens
from math_render import strip_math_tags

DOCUMENT_FONT = "'Times New Roman', serif"
_BODY_PT = 11
_SMALL_PT = 10.5    # lists, tables: the secondary blocks of a document
_HEADER_PT = 9.5    # page header (page number, running head)
_PARAGRAPH = (
    'font-size:{}pt; line-height:140%; margin:0 0 4px 0; text-align:justify;'.format(_BODY_PT)
)


def build_display_html(chandra_html: str, colors=None) -> str:
    """Convert Chandra OCR HTML (div+data-label) to styled HTML for QTextEdit.

    Maps data-label to visual styles matching NĐ 30/2020 formatting:
    - Section-Header → centered
    - Page-Header → small, centered (NĐ 30 numbers pages at the top centre)
    - List-Group → bulleted list
    - Table → ruled table (styled via DOM attrs, not string-replace)
    - Text/Form → justified paragraph; ALSO renders nested <table>/<ul>/<img>
    - Top-level <table>/<p>/<ul>/<img> (landscape pages) → rendered at root
    - <math>…</math> blocks (Equation-Block label) → Unicode math text
      (fractions as `(num)/(denom)`, `\\times` → `×`, `\\%` → `%`)

    `colors` is a theme_tokens palette (colors_for(theme)); light by default.
    """
    colors = colors or theme_tokens.COLORS
    # Pre-process: expand <math>LaTeX</math> blocks to readable Unicode text
    # so BS4 and downstream renderers see normal strings only.
    chandra_html = strip_math_tags(chandra_html, plain=False)
    soup = BeautifulSoup(chandra_html, 'html.parser')
    # Support HTML optionally wrapped in <html><body>...</body></html>
    root = soup.body or soup

    def _paragraph(txt):
        return f'<p style="{_PARAGRAPH}">{txt}</p>'

    # Shared renderers (bs4-attr based — no string-replace prefix collision)
    def _style_table(table_tag):
        rule = colors['border_strong']
        table_tag['cellpadding'] = '4'
        table_tag['cellspacing'] = '0'
        table_tag['style'] = (
            f'border-collapse:collapse; font-size:{_SMALL_PT}pt; margin:6px 0; width:100%;'
        )
        for th in table_tag.find_all('th'):
            th['style'] = (
                f'border:1px solid {rule}; background:{colors["surface_alt"]}; '
                'padding:3px; font-weight:bold;'
            )
        for td in table_tag.find_all('td'):
            td['style'] = f'border:1px solid {rule}; padding:3px;'
        return str(table_tag)

    def _render_list(ul_tag):
        items = ''.join(
            f'<li style="font-size:{_SMALL_PT}pt; line-height:135%;">{li.get_text()}</li>'
            for li in ul_tag.find_all('li')
        )
        return f'<ul style="margin:2px 0 6px 16px;">{items}</ul>'

    def _render_image(img_tag):
        if img_tag.get('alt'):
            return (f'<p style="text-align:center; color:{colors["text_muted"]}; '
                    f'font-style:italic; font-size:{_HEADER_PT}pt;">[Hình]</p>')
        return ''

    parts = [f'<div style="font-family:{DOCUMENT_FONT}; font-size:{_BODY_PT}pt;">']

    # Iterate all top-level children — handles both div-wrapped structure
    # (standard Chandra) and bare <table>/<p> at root (landscape pages).
    for child in root.children:
        if not hasattr(child, 'name') or child.name is None:
            continue

        # Top-level <table> (landscape annex pages — not wrapped in div)
        if child.name == 'table':
            parts.append(_style_table(child))
            continue
        # Top-level <p> (stray paragraphs between markers)
        if child.name == 'p':
            txt = child.decode_contents()
            if txt.strip():
                parts.append(_paragraph(txt))
            continue
        # Top-level <ul>
        if child.name == 'ul':
            parts.append(_render_list(child))
            continue
        # Top-level <img>
        if child.name == 'img':
            rendered = _render_image(child)
            if rendered:
                parts.append(rendered)
            continue
        # Standard case: <div data-label=...>
        if child.name != 'div':
            continue

        div = child
        label = div.get('data-label', 'Text')

        if label == 'Page-Header':
            p = div.find('p')
            txt = p.decode_contents() if p else div.get_text()
            parts.append(
                f'<p style="text-align:center; font-size:{_HEADER_PT}pt; '
                f'line-height:130%; margin:0 0 8px 0;">{txt}</p>'
            )

        elif label == 'Section-Header':
            p = div.find('p')
            txt = p.decode_contents() if p else div.get_text()
            parts.append(
                f'<p style="text-align:center; font-size:{_BODY_PT}pt; '
                f'line-height:135%; margin:8px 0 4px 0;">{txt}</p>'
            )

        elif label == 'List-Group':
            ul = div.find('ul')
            if ul:
                p = div.find('p')
                if p is not None and p.get_text(strip=True):
                    parts.append(_paragraph(p.decode_contents()))
                parts.append(_render_list(ul))
            else:
                p = div.find('p')
                txt = p.decode_contents() if p else div.get_text()
                parts.append(_paragraph(txt))

        elif label == 'Table':
            table = div.find('table')
            if table:
                parts.append(_style_table(table))
            else:
                txt = div.get_text(separator='\n', strip=True)
                parts.append(f'<p style="font-size:{_SMALL_PT}pt; margin:2px 0;">{txt}</p>')

        elif label == 'Image':
            img = div.find('img')
            if img is not None:
                parts.append(_render_image(img))

        else:  # Text, Form, etc. — render mixed children (may include nested <table>)
            rendered_any = False
            for sub_child in div.children:
                if not hasattr(sub_child, 'name') or sub_child.name is None:
                    continue
                if sub_child.name == 'p':
                    txt = sub_child.decode_contents()
                    if txt.strip():
                        parts.append(_paragraph(txt))
                        rendered_any = True
                elif sub_child.name == 'table':
                    parts.append(_style_table(sub_child))
                    rendered_any = True
                elif sub_child.name == 'ul':
                    parts.append(_render_list(sub_child))
                    rendered_any = True
                elif sub_child.name == 'img':
                    rendered = _render_image(sub_child)
                    if rendered:
                        parts.append(rendered)
                        rendered_any = True
            if not rendered_any:
                # Fallback: render div's text if no recognizable children
                txt = div.get_text(separator='\n', strip=True)
                if txt:
                    parts.append(_paragraph(txt))

    parts.append('</div>')
    return '\n'.join(parts)
