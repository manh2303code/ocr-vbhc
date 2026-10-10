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
"""
from bs4 import BeautifulSoup

from math_render import strip_math_tags


def build_display_html(chandra_html: str) -> str:
    """Convert Chandra OCR HTML (div+data-label) to styled HTML for QTextEdit.

    Maps data-label to visual styles matching NĐ 30/2020 formatting:
    - Section-Header → bold, centered
    - Page-Header → small, right-aligned
    - List-Group → bulleted list
    - Table → bordered table (styled via DOM attrs, not string-replace)
    - Text/Form → paragraph; ALSO renders nested <table>/<ul>/<img> siblings
    - Top-level <table>/<p>/<ul>/<img> (landscape pages) → rendered at root
    - <math>…</math> blocks (Equation-Block label) → Unicode math text
      (fractions as `(num)/(denom)`, `\\times` → `×`, `\\%` → `%`)
    """
    # Pre-process: expand <math>LaTeX</math> blocks to readable Unicode text
    # so BS4 and downstream renderers see normal strings only.
    chandra_html = strip_math_tags(chandra_html, plain=False)
    soup = BeautifulSoup(chandra_html, 'html.parser')
    # Support HTML optionally wrapped in <html><body>...</body></html>
    root = soup.body or soup

    # Shared renderers (bs4-attr based — no string-replace prefix collision)
    def _style_table(table_tag):
        table_tag['cellpadding'] = '4'
        table_tag['cellspacing'] = '0'
        table_tag['style'] = (
            'border-collapse:collapse; font-size:8pt; margin:4px 0; width:100%;'
        )
        for th in table_tag.find_all('th'):
            th['style'] = (
                'border:1px solid #999; background:#f0f0f0; '
                'padding:3px; font-weight:bold;'
            )
        for td in table_tag.find_all('td'):
            td['style'] = 'border:1px solid #ccc; padding:3px;'
        return str(table_tag)

    def _render_list(ul_tag):
        items = ''.join(
            f'<li style="font-size:8pt;">{li.get_text()}</li>'
            for li in ul_tag.find_all('li')
        )
        return f'<ul style="margin:2px 0 2px 16px;">{items}</ul>'

    def _render_image(img_tag):
        if img_tag.get('alt'):
            return ('<p style="text-align:center; color:#888; '
                    'font-style:italic; font-size:7pt;">[Hình]</p>')
        return ''

    # Text color intentionally comes from the QTextEdit theme. Hard-coding the
    # light-theme ink here made cached OCR unreadable after switching to dark.
    parts = ['<div style="font-family: Be Vietnam Pro, Segoe UI, sans-serif; font-size: 9pt; '
             'line-height: 1.45; padding: 2px;">']

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
                parts.append(f'<p style="font-size:9pt; margin:2px 0; text-align:justify;">{txt}</p>')
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
                f'<p style="text-align:right; font-size:7pt; color:#999; margin:1px 0;">{txt}</p>'
            )

        elif label == 'Section-Header':
            p = div.find('p')
            txt = p.decode_contents() if p else div.get_text()
            parts.append(
                f'<p style="text-align:center; font-size:10pt; margin:4px 0;">{txt}</p>'
            )

        elif label == 'List-Group':
            ul = div.find('ul')
            if ul:
                parts.append(_render_list(ul))
            else:
                p = div.find('p')
                txt = p.decode_contents() if p else div.get_text()
                parts.append(f'<p style="font-size:8pt;">{txt}</p>')

        elif label == 'Table':
            table = div.find('table')
            if table:
                parts.append(_style_table(table))
            else:
                txt = div.get_text(separator='\n', strip=True)
                parts.append(f'<p style="font-size:8pt; margin:2px 0;">{txt}</p>')

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
                        parts.append(
                            f'<p style="font-size:9pt; margin:2px 0; text-align:justify;">{txt}</p>'
                        )
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
                    parts.append(
                        f'<p style="font-size:9pt; margin:2px 0; text-align:justify;">{txt}</p>'
                    )

    parts.append('</div>')
    return '\n'.join(parts)
