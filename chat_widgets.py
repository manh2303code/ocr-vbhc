"""Native widgets for the legal-assistant chat dock.

Qt WebEngine is optional in this app, so the chat is built from plain widgets:
one view per message, appended as the conversation grows, and the answer being
written is updated in place. The old panel rebuilt the whole transcript as one
HTML string on every token; nothing here does that.

Colours come from theme_tokens through chat_colors(), and every pair the chat
draws is listed in chat_contrast_pairs() so the accessibility tests see them.
"""
import html
import math
import re
import time

from PyQt5.QtCore import (
    QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
    QIcon,
)
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from theme_tokens import colors_for
from ui_icons import icon


AA = 4.5
# WCAG 1.4.11: focus rings and other non-text indicators need 3:1.
NON_TEXT = 3.0
# Frame budget for redrawing the answer being written (~30 fps).
STREAM_FRAME_MS = 33
# The working animation shares the 30 fps clock. 20 fps was measured too:
# 9.6 against 11.4 ms of GUI time per second, not worth the choppier motion.
ANIMATION_FRAME_MS = STREAM_FRAME_MS


def current_theme():
    try:
        from theme_manager_v2 import ThemeManager

        return "dark" if ThemeManager.instance().is_dark else "light"
    except Exception:
        return "light"


def chat_colors(theme):
    """Colour roles of the chat, resolved from the shared token table.

    The dock is a reading surface like the document pane, so it sits on
    `surface`; the reader's question is the one tinted block, in the colour
    the app uses for a selected row.
    """
    tokens = colors_for(theme)
    dark = theme == "dark"
    return {
        "page": tokens["surface"],
        "text": tokens["text"],
        "secondary": tokens["text_secondary"],
        "muted": tokens["text_muted"],
        "question": tokens["primary_soft"],
        "quiet": tokens["surface_alt"],
        "hover": tokens["surface_hover"],
        "pressed": tokens["surface_pressed"],
        "border": tokens["border"],
        "border_strong": tokens["border_strong"],
        "focus": tokens["border_focus"],
        "accent": tokens["accent_gold"],
        "accent_soft": tokens["accent_gold_soft"],
        "fill": tokens["primary"],
        "fill_hover": tokens["primary_hover"],
        "on_fill": tokens["text_on_primary"],
        "danger": tokens["cta"],
        "link": tokens["accent_gold"] if dark else tokens["info"],
    }


def chat_contrast_pairs(theme):
    """(role, foreground, background, minimum ratio) for everything drawn."""
    c = chat_colors(theme)
    return [
        ("answer", c["text"], c["page"], AA),
        ("question block", c["text"], c["question"], AA),
        ("status", c["secondary"], c["page"], AA),
        ("status shimmer", c["text"], c["page"], AA),
        ("meta", c["muted"], c["page"], AA),
        ("notice", c["muted"], c["page"], AA),
        ("citation", c["accent"], c["accent_soft"], AA),
        ("link", c["link"], c["page"], AA),
        ("source chip", c["secondary"], c["page"], AA),
        ("source chip hover", c["text"], c["hover"], AA),
        ("knowledge warning", c["secondary"], c["page"], AA),
        ("history warning", c["danger"], c["page"], AA),
        ("action", c["secondary"], c["page"], AA),
        ("action hover", c["secondary"], c["hover"], AA),
        ("action icon", c["secondary"], c["page"], NON_TEXT),
        ("error title", c["danger"], c["page"], AA),
        ("error detail", c["secondary"], c["page"], AA),
        ("empty title", c["text"], c["page"], AA),
        ("empty body", c["secondary"], c["page"], AA),
        ("suggestion", c["text"], c["page"], AA),
        ("suggestion hover", c["text"], c["hover"], AA),
        ("suggestion icon", c["secondary"], c["page"], NON_TEXT),
        ("disclaimer", c["muted"], c["page"], AA),
        ("session", c["text"], c["page"], AA),
        ("session hover", c["text"], c["hover"], AA),
        ("header icon", c["secondary"], c["page"], NON_TEXT),
        ("header icon hover", c["secondary"], c["hover"], NON_TEXT),
        ("composer text", c["text"], c["page"], AA),
        ("placeholder", c["muted"], c["page"], AA),
        ("scope", c["secondary"], c["page"], AA),
        ("scope hover", c["secondary"], c["hover"], AA),
        ("send button", c["on_fill"], c["fill"], AA),
        ("send button hover", c["on_fill"], c["fill_hover"], AA),
        ("focus ring", c["focus"], c["page"], NON_TEXT),
        ("focus ring on hover", c["focus"], c["hover"], NON_TEXT),
        ("caret", c["accent"], c["page"], NON_TEXT),
    ]


def chat_stylesheet(theme):
    """Stylesheet for the chat dock body, generated from the tokens.

    Flat: a hairline only where a control needs an edge (the composer, the
    source chips, the jump button), 4px corners, 1px focus ring in gold as
    in the rest of the app.
    """
    c = chat_colors(theme)
    return """
QWidget#chatPanelBody {{ background: {page}; }}
QScrollArea#chatTranscript, QWidget#chatTranscriptContent {{ background: {page}; border: none; }}
QLabel#chatKnowledgeStatus {{ color: {secondary}; font-size: 8.5pt; padding: 0 2px; }}
QLabel#chatHistoryStatus {{ color: {danger}; font-size: 8.5pt; padding: 0 2px; }}
QLabel#chatHiddenNote {{ color: {muted}; font-size: 8.5pt; }}

QComboBox#chatSessionCombo {{
    background: transparent; color: {text}; border: 1px solid transparent; border-radius: 4px;
    padding: 2px 24px 2px 9px; min-height: 24px; font-size: 10pt; font-weight: 500;
}}
QComboBox#chatSessionCombo:hover {{ background: {hover}; }}
QComboBox#chatSessionCombo:on {{ background: {pressed}; }}
QComboBox#chatSessionCombo:focus {{ border-color: {focus}; }}
QToolButton[chatRole="header"] {{
    background: transparent; border: 1px solid transparent; border-radius: 4px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0;
}}
QToolButton[chatRole="header"]:hover {{ background: {hover}; }}
QToolButton[chatRole="header"]:pressed {{ background: {pressed}; }}
QToolButton[chatRole="header"]:focus {{ border-color: {focus}; }}

QLabel#chatUserBubble {{
    background: {question}; color: {text}; border-radius: 4px; padding: 7px {inset}px;
    font-size: 10pt; font-weight: 500;
}}
QTextBrowser#chatAnswer {{
    background: transparent; color: {text}; border: none; padding: 0; margin: 0;
    font-size: 10pt;
    selection-background-color: {question}; selection-color: {text};
}}
QToolButton[chatRole="chip"] {{
    background: transparent; color: {secondary}; border: 1px solid {border}; border-radius: 4px;
    padding: 1px 4px 1px 3px; min-width: 0; min-height: 0;
}}
QToolButton[chatRole="chip"]:hover {{ background: {hover}; color: {text}; border-color: {border_strong}; }}
QToolButton[chatRole="chip"]:pressed {{ background: {pressed}; }}
QToolButton[chatRole="chip"]:focus {{ border-color: {focus}; }}
QLabel#chatMoreSources {{ color: {muted}; font-size: 8.5pt; }}
QToolButton[chatRole="action"] {{
    background: transparent; color: {secondary}; border: 1px solid transparent; border-radius: 4px;
    padding: 3px; font-size: 8.5pt; min-width: 0; min-height: 0;
}}
QToolButton[chatRole="action"][labelled="true"] {{ padding: 2px 8px 2px 4px; }}
QToolButton[chatRole="action"]:hover {{ background: {hover}; }}
QToolButton[chatRole="action"]:pressed {{ background: {pressed}; }}
QToolButton[chatRole="action"]:focus {{ border-color: {focus}; }}
QLabel#chatMeta, QLabel#chatNotice {{ color: {muted}; font-size: 8.5pt; }}
QFrame#chatErrorCard {{ background: transparent; border: none; }}
QLabel#chatErrorTitle {{ color: {danger}; font-weight: 600; }}
QLabel#chatErrorDetail {{ color: {secondary}; font-size: 9pt; }}
QLabel#chatSteps {{ color: {secondary}; font-size: 9pt; }}
QLabel#chatElapsed {{ color: {muted}; font-size: 8.5pt; }}

QFrame#chatComposer {{ background: {page}; border: 1px solid {border_strong}; border-radius: 4px; }}
QFrame#chatComposer[focused="true"] {{ border-color: {focus}; }}
QPlainTextEdit#chatQuestionInput {{
    background: transparent; color: {text}; border: none; padding: 0; min-height: 0;
    font-size: 10pt;
    selection-background-color: {question}; selection-color: {text};
}}
QComboBox#chatScopeCombo {{
    background: transparent; color: {secondary}; border: 1px solid transparent; border-radius: 4px;
    padding: 1px 22px 1px 2px; min-height: 22px; font-size: 9pt;
}}
QComboBox#chatScopeCombo:hover {{ background: {hover}; }}
QComboBox#chatScopeCombo:on {{ background: {pressed}; }}
QComboBox#chatScopeCombo:focus {{ border-color: {focus}; }}
QComboBox#chatScopeCombo::drop-down, QComboBox#chatSessionCombo::drop-down {{
    border: none; width: 20px;
}}
QComboBox#chatScopeCombo::down-arrow, QComboBox#chatSessionCombo::down-arrow {{
    image: none; width: 0; height: 0;
}}
QToolButton#chatSendButton, QToolButton#chatStopButton {{
    background: {fill}; border: 1px solid {fill}; border-radius: 4px; padding: 0;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
}}
QToolButton#chatSendButton:hover, QToolButton#chatStopButton:hover {{
    background: {fill_hover}; border-color: {fill_hover};
}}
QToolButton#chatSendButton:focus, QToolButton#chatStopButton:focus {{ border-color: {focus}; }}
QToolButton#chatStopButton:disabled {{ background: {border_strong}; border-color: {border_strong}; }}

QLabel#chatEmptyTitle {{ color: {text}; font-size: 10.5pt; font-weight: 600; padding: 0 {inset}px; }}
QLabel#chatEmptyBody {{ color: {secondary}; font-size: 9pt; padding: 0 {inset}px; }}
QLabel#chatEmptyNote {{ color: {muted}; font-size: 8.5pt; padding: 0 {inset}px; }}
QPushButton[chatRole="suggestion"] {{
    background: transparent; color: {text}; border: 1px solid transparent; border-radius: 4px;
    padding: 5px {inset}px; text-align: left; font-size: 9pt; font-weight: 400; min-height: 20px;
}}
QPushButton[chatRole="suggestion"]:hover {{ background: {hover}; }}
QPushButton[chatRole="suggestion"]:pressed {{ background: {pressed}; }}
QPushButton[chatRole="suggestion"]:focus {{ border-color: {focus}; }}
QPushButton[chatRole="suggestion"]:disabled {{
    background: transparent; color: {muted}; border-color: transparent;
}}
QToolButton#chatJumpButton {{
    background: {page}; border: 1px solid {border_strong}; border-radius: 4px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0;
}}
QToolButton#chatJumpButton:hover {{ background: {hover}; }}
""".format(inset=TEXT_INSET, **c)


# ── Markdown → the rich-text subset Qt text documents draw ──────────────
# With the spaces before it: they become one non-breaking space, so a
# citation never starts a line on its own.
_CITE_RE = re.compile(r"([ \t]*)\[(\d{1,3})\]")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# Single-star emphasis must hug its text, so "3 * 4" stays arithmetic.
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# Placed where the caret belongs before rendering; survives escaping intact.
CARET_MARK = "⁣"


def _inline_plain(text, cite):
    safe = html.escape(text, quote=False)
    safe = _BOLD_RE.sub(r"<b>\1</b>", safe)
    safe = _ITALIC_RE.sub(r"<i>\1</i>", safe)
    if cite is not None:
        safe = _CITE_RE.sub(
            lambda match: ("&nbsp;" if match.group(1) else "") + cite(int(match.group(2))), safe
        )
    return safe


def _inline(text, cite):
    parts, position = [], 0
    for match in _CODE_RE.finditer(text):
        parts.append(_inline_plain(text[position:match.start()], cite))
        parts.append("<code>{}</code>".format(html.escape(match.group(1), quote=False)))
        position = match.end()
    parts.append(_inline_plain(text[position:], cite))
    return "".join(parts)


def _table_cells(line):
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def markdown_to_html(text, cite=None):
    """Render the Markdown a chat model writes as escaped rich text.

    Everything is escaped first, so the model cannot inject markup; only the
    constructs below are produced. Unclosed markers (a half-streamed "**")
    stay literal until their closing half arrives.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    paragraph = []
    current = {"list": None}

    def flush_paragraph():
        if paragraph:
            out.append("<p>{}</p>".format("<br>".join(paragraph)))
            del paragraph[:]

    def flush_list():
        listing = current["list"]
        if not listing:
            return
        tag, start, items = listing
        attrs = ' start="{}"'.format(start) if tag == "ol" and start != 1 else ""
        rendered = []
        for body, children in items:
            nested = ""
            if children:
                nested = "<ul>{}</ul>".format("".join("<li>{}</li>".format(c) for c in children))
            rendered.append("<li>{}{}</li>".format(body, nested))
        out.append("<{0}{1}>{2}</{0}>".format(tag, attrs, "".join(rendered)))
        current["list"] = None

    def flush():
        flush_paragraph()
        flush_list()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1  # the closing fence; absent while the answer streams
            out.append("<pre>{}</pre>".format(html.escape("\n".join(code), quote=False)))
            continue
        if not stripped:
            flush()
            index += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            size = "11.5pt" if len(heading.group(1)) <= 3 else "10.5pt"
            out.append(
                '<p class="heading"><span style="font-size:{};"><b>{}</b></span></p>'.format(
                    size, _inline(heading.group(2), cite)
                )
            )
            index += 1
            continue
        if _RULE_RE.match(line):
            flush()
            out.append("<hr>")
            index += 1
            continue
        if (stripped.startswith("|") and index + 1 < len(lines)
                and _TABLE_SEP_RE.match(lines[index + 1])):
            flush()
            header = _table_cells(line)
            rows = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_table_cells(lines[index]))
                index += 1
            head = "".join("<th>{}</th>".format(_inline(cell, cite)) for cell in header)
            body = "".join(
                "<tr>{}</tr>".format("".join("<td>{}</td>".format(_inline(cell, cite)) for cell in row))
                for row in rows
            )
            out.append(
                '<table border="1" cellspacing="0" cellpadding="4"><tr>{}</tr>{}</table>'.format(head, body)
            )
            continue
        bullet = _BULLET_RE.match(line)
        ordered = None if bullet else _ORDERED_RE.match(line)
        if bullet or ordered:
            flush_paragraph()
            indent = len((bullet or ordered).group(1).replace("\t", "    "))
            body = _inline((bullet.group(2) if bullet else ordered.group(3)), cite)
            listing = current["list"]
            if indent >= 2 and listing and listing[2]:
                listing[2][-1][1].append(body)
            else:
                tag = "ul" if bullet else "ol"
                if not listing or listing[0] != tag:
                    flush_list()
                    start = int(ordered.group(2)) if ordered else 1
                    current["list"] = [tag, start, []]
                current["list"][2].append([body, []])
            index += 1
            continue
        if current["list"] and line[:1] in (" ", "\t"):
            # A wrapped continuation of the previous list item.
            items = current["list"][2]
            items[-1][0] += "<br>" + _inline(stripped, cite)
            index += 1
            continue
        flush_list()
        paragraph.append(_inline(stripped, cite))
        index += 1
    flush()
    return "".join(out)


def answer_stylesheet(theme):
    """Document stylesheet for answers: spacing, code and link colours.

    Set once per view as the document's default stylesheet, so a redraw
    parses only the answer, not this as well.
    """
    c = chat_colors(theme)
    return (
        "p{{margin-top:0px;margin-bottom:8px;line-height:135%;}}"
        "p.heading{{margin-top:4px;margin-bottom:6px;}}"
        "ul,ol{{margin-top:0px;margin-bottom:8px;margin-left:0px;-qt-list-indent:1;}}"
        "li{{margin-bottom:3px;line-height:135%;}}"
        "code{{font-family:'Cascadia Mono','Consolas',monospace;background-color:{code};}}"
        "pre{{font-family:'Cascadia Mono','Consolas',monospace;background-color:{code};"
        "margin-top:0px;margin-bottom:8px;}}"
        "table{{margin-bottom:8px;border-color:{border};}}"
        "th{{background-color:{code};font-weight:600;}}"
        "a{{color:{link};text-decoration:none;}}"
    ).format(code=c["quiet"], border=c["border"], link=c["link"])


def answer_document(body_html, theme, streaming=False):
    """Answer HTML with the writing caret where the next word will appear."""
    c = chat_colors(theme)
    caret = '<span style="background-color:{};font-size:7pt;">&nbsp;</span>'.format(c["accent"])
    if streaming:
        if CARET_MARK in body_html:
            return body_html.replace(CARET_MARK, caret)
        return body_html + caret
    return body_html.replace(CARET_MARK, "")


def citation_anchor(href, number, theme):
    """A citation drawn as a small pill that opens its source."""
    c = chat_colors(theme)
    return (
        '<a href="{href}" style="color:{fg};background-color:{bg};'
        'font-weight:600;text-decoration:none;">&nbsp;{n}&nbsp;</a>'
    ).format(href=html.escape(href, quote=True), fg=c["accent"], bg=c["accent_soft"], n=number)


def format_seconds(seconds):
    if seconds < 10:
        return "{:.1f} giây".format(seconds).replace(".", ",")
    return "{} giây".format(int(round(seconds)))


def citation_badge(number, theme, font, height=14):
    """The citation number drawn as the pill it is in the answer text.

    A source chip leads with it, so [2] in the text and the chip that opens
    source 2 look the same. Returns the icon and the size to show it at.
    """
    c = chat_colors(theme)
    badge_font = QFont(font)
    badge_font.setPointSizeF(7.5)
    badge_font.setWeight(QFont.DemiBold)
    text = str(number)
    width = max(height, int(math.ceil(QFontMetricsF(badge_font).horizontalAdvance(text))) + 6)
    ratio = 2.0
    pixmap = QPixmap(int(width * ratio), int(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(c["accent_soft"]))
    painter.drawRoundedRect(QRectF(0, 0, width, height), 3, 3)
    painter.setPen(QColor(c["accent"]))
    painter.setFont(badge_font)
    painter.drawText(QRectF(0, 0, width, height), Qt.AlignCenter, text)
    painter.end()
    return QIcon(pixmap), QSize(width, height)


# ── Layout and small widgets ─────────────────────────────────────────────
class FlowLayout(QLayout):
    """Lay chips out left to right, wrapping onto new rows."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, measure_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            width = min(hint.width(), max(1, area.width()))
            if x > area.x() and x + width > area.x() + area.width():
                x = area.x()
                y += row_height + self._gap
                row_height = 0
            if not measure_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self._gap
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


def _sparkle_path(radius):
    """Four-pointed sparkle with concave sides, centred on the origin."""
    pinch = radius * 0.14
    path = QPainterPath()
    path.moveTo(0, -radius)
    path.quadTo(pinch, -pinch, radius, 0)
    path.quadTo(pinch, pinch, 0, radius)
    path.quadTo(-pinch, pinch, -radius, 0)
    path.quadTo(-pinch, -pinch, 0, -radius)
    return path


class WorkingIndicator(QWidget):
    """The assistant's sparkle, turning and breathing while it works.

    Painted, so no GIF ships with the app. Its timer runs only between
    start() and stop(), and the same tick drives the status label shimmer
    and the elapsed time, so there is one animation clock.
    """

    frame = pyqtSignal(float)

    def __init__(self, parent=None, size=18):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAccessibleName("Trợ lý đang làm việc")
        self._timer = QTimer(self)
        self._timer.setInterval(ANIMATION_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._started = None
        self._phase = 0.0
        c = chat_colors("light")  # until the panel applies its theme
        self._color = QColor(c["accent"])
        self._glow = QColor(c["accent_soft"])
        self._background = QColor(c["page"])
        # Paints its own background, so a frame repaints these 18x18 pixels
        # and not every transparent parent up to the window.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_colors(self, color, glow, background):
        self._color = QColor(color)
        self._glow = QColor(glow)
        self._background = QColor(background)
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._started = time.perf_counter()
            self._phase = 0.0
            self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self._started = None
        self._phase = 0.0
        self.update()

    def is_running(self):
        return self._timer.isActive()

    def _tick(self):
        if self._started is None:
            return
        self._phase = time.perf_counter() - self._started
        self.update()
        self.frame.emit(self._phase)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._background)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = min(self.width(), self.height())
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        running = self.is_running()
        pulse = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi / 1.4) if running else 1.0
        if running:
            glow = QColor(self._glow)
            glow.setAlphaF(0.35 + 0.45 * pulse)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            halo = side * (0.36 + 0.14 * pulse)
            painter.drawEllipse(QPointF(0, 0), halo, halo)
            painter.rotate((self._phase * 120.0) % 360.0)
        scale = 0.80 + 0.20 * pulse
        painter.fillPath(_sparkle_path(side * 0.47 * scale), self._color)
        painter.end()


class ShimmerLabel(QLabel):
    """A status line whose text carries a moving highlight while busy.

    The highlight moves towards the full text colour, never away from it,
    so the text never drops below its resting contrast.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._phase = None
        c = chat_colors("light")  # until the panel applies its theme
        self._base = QColor(c["secondary"])
        self._highlight = QColor(c["text"])
        self._background = QColor(c["page"])

    def set_shimmer_colors(self, base, highlight, background):
        self._base = QColor(base)
        self._highlight = QColor(highlight)
        self._background = QColor(background)

    def set_phase(self, phase):
        if self._phase is None:
            # Opaque only while it animates, for the same reason as the
            # indicator; at rest it is an ordinary transparent label.
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._phase = phase
        self.update()

    def stop_shimmer(self):
        self._phase = None
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.update()

    def paintEvent(self, event):
        if self._phase is None or not self.text():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._background)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.contentsRect()
        metrics = self.fontMetrics()
        text = metrics.elidedText(self.text(), Qt.ElideRight, rect.width())
        width = max(1, metrics.horizontalAdvance(text))
        band = 0.22
        centre = ((self._phase / 1.8) % 1.0) * (1.0 + 2 * band) - band
        gradient = QLinearGradient(rect.left(), 0, rect.left() + width, 0)
        gradient.setColorAt(0.0, self._base)
        for position, colour in ((centre - band, self._base), (centre, self._highlight),
                                 (centre + band, self._base)):
            if 0.0 <= position <= 1.0:
                gradient.setColorAt(position, colour)
        gradient.setColorAt(1.0, self._base)
        painter.setPen(QPen(QBrush(gradient), 1))
        painter.setFont(self.font())
        painter.drawText(rect, int(self.alignment()) | Qt.TextSingleLine, text)
        painter.end()


class ChatInput(QPlainTextEdit):
    """Question box: Enter sends, Shift+Enter breaks the line, Esc stops."""

    submitted = pyqtSignal()
    escape_pressed = pyqtSignal()
    MIN_ROWS = 1
    MAX_ROWS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.capture_escape = False
        self.recall_text = ""
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().setDocumentMargin(4)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _rows_height(self, rows):
        chrome = 2 * self.frameWidth() + 2 * int(self.document().documentMargin()) + 4
        return rows * self.fontMetrics().lineSpacing() + chrome

    def minimum_rows_height(self):
        return self._rows_height(self.MIN_ROWS)

    def maximum_rows_height(self):
        return self._rows_height(self.MAX_ROWS)

    def _fit(self):
        layout_lines = int(self.document().documentLayout().documentSize().height())
        rows = max(self.MIN_ROWS, self.document().blockCount(), layout_lines)
        height = min(self.maximum_rows_height(), self._rows_height(rows))
        if height != self.height() or self.minimumHeight() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Wrapping changes with the width, and with it the number of rows.
        QTimer.singleShot(0, self._fit)

    def event(self, event):
        if (event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Escape
                and self.capture_escape):
            # Claim Esc before the window-wide shortcut sees it.
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = int(event.modifiers() & ~Qt.KeypadModifier)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if modifiers & int(Qt.ShiftModifier):
                self.textCursor().insertText("\n")
                self.ensureCursorVisible()
                event.accept()
                return
            if modifiers in (0, int(Qt.ControlModifier)):
                event.accept()
                self.submitted.emit()
                return
        if key == Qt.Key_Escape and self.capture_escape:
            event.accept()
            self.escape_pressed.emit()
            return
        if key == Qt.Key_Up and not self.toPlainText() and self.recall_text:
            self.setPlainText(self.recall_text)
            self.moveCursor(QTextCursor.End)
            event.accept()
            return
        super().keyPressEvent(event)


class CompactCombo(QComboBox):
    """A borderless combo box that draws its own chevron.

    The chevron is the app's Fluent chevron-down in the colour the panel
    gives it (the stylesheet hides the drop-down image), so it follows the
    theme without per-theme image files and shows in tests without the app
    stylesheet.
    """

    CHEVRON = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chevron = chat_colors("light")["secondary"]
        # As wide as the choice shown, not the longest one: without a border
        # a wide box leaves its chevron stranded far from its text.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.currentIndexChanged.connect(lambda _index: self.updateGeometry())

    def set_chevron_color(self, color):
        self._chevron = color
        self.update()

    def sizeHint(self):
        hint = super().sizeHint()
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = self.fontMetrics().horizontalAdvance(self.currentText() or " ")
        size = self.style().sizeFromContents(
            QStyle.CT_ComboBox, option, QSize(text + 4, hint.height()), self
        )
        return QSize(size.width(), hint.height())

    def minimumSizeHint(self):
        hint = self.sizeHint()
        return QSize(min(hint.width(), 96), hint.height())

    def paintEvent(self, _event):
        # QComboBox clips a long choice mid-word; this ends it with "…".
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        field = self.style().subControlRect(
            QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self
        )
        option.currentText = self.fontMetrics().elidedText(
            option.currentText, Qt.ElideRight, max(0, field.width())
        )
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)
        size = self.CHEVRON
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        icon("chevron-down", size=size, color=self._chevron).paint(
            painter,
            QRect(self.width() - size - 6, (self.height() - size) // 2, size, size),
            Qt.AlignCenter, mode,
        )
        painter.end()


class Transcript(QScrollArea):
    """Scrolling column of message views that follows the newest text.

    It stays pinned to the bottom while the reader is there, stops following
    as soon as they scroll up to read, and offers a button back down.
    """

    PIN_SLACK = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatTranscript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content.setObjectName("chatTranscriptContent")
        self._layout = QVBoxLayout(self._content)
        # Right: room between the text and the scroll bar.
        self._layout.setContentsMargins(0, 4, 6, 12)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)
        self._footer = None
        self.setWidget(self._content)
        self._pinned = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_value_changed)
        bar.rangeChanged.connect(self._on_range_changed)
        self.jump_button = QToolButton(self)
        self.jump_button.setObjectName("chatJumpButton")
        self.jump_button.setToolTip("Cuộn xuống nội dung mới nhất")
        self.jump_button.setAccessibleName("Cuộn xuống nội dung mới nhất")
        self.jump_button.setCursor(Qt.PointingHandCursor)
        self.jump_button.clicked.connect(self.scroll_to_bottom)
        self.jump_button.hide()

    @property
    def pinned(self):
        return self._pinned

    def set_footer(self, widget):
        """A widget kept after every message (the live status row)."""
        self._footer = widget
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def _insert_position(self):
        position = self._layout.count() - 1  # before the stretch
        if self._footer is not None:
            position = self._layout.indexOf(self._footer)
        return position

    def add_view(self, widget):
        """Put a view last, above the footer; one already here moves there.

        The view is taken out first: inserting a widget the layout already
        holds counts its old slot when the position is worked out, which
        dropped it below the footer.
        """
        self._layout.removeWidget(widget)
        self._layout.insertWidget(self._insert_position(), widget)
        if widget.isHidden() and widget.testAttribute(Qt.WA_WState_ExplicitShowHide):
            widget.show()  # taken out earlier with remove_view(delete=False)

    def views(self):
        found = []
        for index in range(self._layout.count()):
            widget = self._layout.itemAt(index).widget()
            if widget is not None and widget is not self._footer:
                found.append(widget)
        return found

    def remove_view(self, widget, delete=True):
        self._layout.removeWidget(widget)
        widget.hide()
        if delete:
            widget.setParent(None)
            widget.deleteLater()

    def clear_views(self, keep=()):
        for widget in self.views():
            if widget not in keep:
                self.remove_view(widget)

    def scroll_to_bottom(self):
        self._pinned = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._update_jump()

    def _on_value_changed(self, value):
        self._pinned = value >= self.verticalScrollBar().maximum() - self.PIN_SLACK
        self._update_jump()

    def _on_range_changed(self, _minimum, maximum):
        if self._pinned:
            self.verticalScrollBar().setValue(maximum)
        self._update_jump()

    def _update_jump(self):
        show = not self._pinned and self.verticalScrollBar().maximum() > 0
        if show:
            self._place_jump()
        self.jump_button.setVisible(show)
        if show:
            self.jump_button.raise_()

    def _place_jump(self):
        size = 28
        viewport = self.viewport().geometry()
        self.jump_button.setGeometry(
            viewport.center().x() - size // 2, viewport.bottom() - size - 10, size, size
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_jump()


# ── Message views ───────────────────────────────────────────────────────
# Answers, sources, actions and the status line are inset by this much, the
# padding of the question block, so every line of text in the transcript
# starts on one edge and only the question's tint reaches past it.
TEXT_INSET = 10


class UserMessageView(QWidget):
    """The reader's question: a tinted block across the reading column.

    The transcript reads like a page of questions and answers, so the
    question heads its answer rather than sitting in a chat bubble.
    """

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        layout = QVBoxLayout(self)
        # A new question starts a little further from the answer above it
        # than its own answer starts from it.
        layout.setContentsMargins(0, 6, 0, 0)
        self.bubble = QLabel(text)
        self.bubble.setObjectName("chatUserBubble")
        self.bubble.setTextFormat(Qt.PlainText)
        self.bubble.setWordWrap(True)
        self.bubble.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.bubble.setAccessibleName("Câu hỏi")
        # The padding alone sets where the text starts (TEXT_INSET), not an
        # indent Qt adds for a label that has a frame.
        self.bubble.setIndent(0)
        self.bubble.setMargin(0)
        layout.addWidget(self.bubble)


class AnswerView(QTextBrowser):
    """Read-only answer text whose height follows its document.

    A word-wrapped rich-text QLabel was measured first: it laid the whole
    document out several times per update (size hint, then height for the
    actual width), which made each redraw cost about 1.6 ms of layout. Here
    the document is laid out once, at the view's width, and the height is
    fixed from the result, so the transcript's layout has nothing to ask.
    """

    link_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatAnswer")
        self.setFrameShape(QFrame.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setTabChangesFocus(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setCursor(Qt.IBeamCursor)
        self.document().setDocumentMargin(0)
        self.anchorClicked.connect(lambda url: self.link_activated.emit(url.toString()))
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._html = ""
        self._css = None
        self.setFixedHeight(0)

    def set_stylesheet(self, css):
        if css != self._css:
            self._css = css
            self.document().setDefaultStyleSheet(css)
            if self._html:
                self.setHtml(self._html)

    def set_html(self, document_html):
        self._html = document_html
        self.setHtml(document_html)

    def html(self):
        return self._html

    def _fit_height(self, size):
        height = int(math.ceil(size.height()))
        if height != self.height():
            self.setFixedHeight(height)

    def wheelEvent(self, event):
        # Never scrolls itself: the wheel belongs to the transcript.
        event.ignore()

    def setSource(self, _url):
        # Links are handled by the panel; never navigate the view away.
        return


class NoteView(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("chatHiddenNote")
        self.setAlignment(Qt.AlignCenter)


class AssistantTurnView(QWidget):
    """One answer: sources, text, then actions — or an error or stop notice."""

    link_activated = pyqtSignal(str)
    open_source = pyqtSignal(int, int)
    copy_requested = pyqtSignal()
    regenerate_requested = pyqtSignal()
    retry_requested = pyqtSignal()

    MAX_CHIPS = 8

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._sources_caption = ""
        self._copy_timer = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(TEXT_INSET, 0, TEXT_INSET, 0)
        # The answer's last paragraph keeps its 8px bottom margin, so the
        # actions sit 14px under the text and the chips 6px above it.
        layout.setSpacing(6)

        # The sources come first, as small chips; what they are (cited, being
        # read, read before a stop) is said by the status line while it
        # matters and by the tooltip afterwards, not by a caption.
        self.sources_box = QWidget()
        sources_layout = QVBoxLayout(self.sources_box)
        sources_layout.setContentsMargins(0, 0, 0, 0)
        sources_layout.setSpacing(0)
        self.chips_host = QWidget()
        self.chips_layout = FlowLayout(self.chips_host, spacing=4)
        sources_layout.addWidget(self.chips_host)
        self.sources_box.hide()
        layout.addWidget(self.sources_box)

        self.body = AnswerView()
        self.body.link_activated.connect(self.link_activated.emit)
        self.body.hide()
        layout.addWidget(self.body)

        self.notice = QLabel()
        self.notice.setObjectName("chatNotice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)

        self.error_card = QFrame()
        self.error_card.setObjectName("chatErrorCard")
        error_layout = QHBoxLayout(self.error_card)
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(6)
        self.error_icon = QLabel()
        self.error_icon.setFixedSize(16, 16)
        error_text = QVBoxLayout()
        error_text.setSpacing(2)
        self.error_title = QLabel()
        self.error_title.setObjectName("chatErrorTitle")
        self.error_title.setWordWrap(True)
        self.error_detail = QLabel()
        self.error_detail.setObjectName("chatErrorDetail")
        self.error_detail.setWordWrap(True)
        self.error_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        error_text.addWidget(self.error_title)
        error_text.addWidget(self.error_detail)
        error_layout.addWidget(self.error_icon, 0, Qt.AlignTop)
        error_layout.addLayout(error_text, 1)
        self.error_card.hide()
        layout.addWidget(self.error_card)

        self.actions = QWidget()
        actions_layout = QHBoxLayout(self.actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(2)
        self.copy_button = self._action("Sao chép câu trả lời kèm nguồn")
        self.copy_button.clicked.connect(self.copy_requested.emit)
        self.regenerate_button = self._action("Tạo lại câu trả lời")
        self.regenerate_button.clicked.connect(self.regenerate_requested.emit)
        # The one labelled action: error and stop messages tell the reader
        # to press "Thử lại", so the button has to say it.
        self.retry_button = self._action("Gửi lại câu hỏi này", label="Thử lại")
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.meta_label = QLabel()
        self.meta_label.setObjectName("chatMeta")
        for widget in (self.copy_button, self.regenerate_button, self.retry_button):
            actions_layout.addWidget(widget)
            widget.hide()
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.meta_label)
        self.actions.hide()
        layout.addWidget(self.actions)
        self.apply_theme(theme)

    ACTION_ICON = 16

    def _action(self, tooltip, label=""):
        button = QToolButton()
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty("chatRole", "action")
        button.setIconSize(QSize(self.ACTION_ICON, self.ACTION_ICON))
        if label:
            button.setText(label)
            button.setProperty("labelled", True)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def apply_theme(self, theme):
        self._theme = theme
        c = chat_colors(theme)
        size = self.ACTION_ICON
        self.body.set_stylesheet(answer_stylesheet(theme))
        self.error_icon.setPixmap(icon("circle-alert", size=16, color=c["danger"]).pixmap(16, 16))
        self.copy_button.setIcon(icon("copy", size=size, color=c["secondary"]))
        self.regenerate_button.setIcon(icon("refresh-cw", size=size, color=c["secondary"]))
        self.retry_button.setIcon(icon("refresh-cw", size=size, color=c["secondary"]))
        for chip in self.source_chips():
            self._paint_badge(chip)

    def show_copied(self):
        """Confirm a copy for a moment, in place of the copy icon."""
        c = chat_colors(self._theme)
        self.copy_button.setIcon(icon("check", size=self.ACTION_ICON, color=c["secondary"]))
        self.copy_button.setToolTip("Đã chép")
        # A child timer: it goes with the view, so it never fires into one
        # that was closed in the meantime.
        if self._copy_timer is None:
            self._copy_timer = QTimer(self)
            self._copy_timer.setSingleShot(True)
            self._copy_timer.setInterval(1500)
            self._copy_timer.timeout.connect(self._restore_copy)
        self._copy_timer.start()

    def _restore_copy(self):
        c = chat_colors(self._theme)
        self.copy_button.setIcon(icon("copy", size=self.ACTION_ICON, color=c["secondary"]))
        self.copy_button.setToolTip(self.copy_button.accessibleName())

    # Sources ------------------------------------------------------------
    def _paint_badge(self, chip):
        badge, size = citation_badge(chip.property("citationNumber"), self._theme, chip.font())
        chip.setIcon(badge)
        chip.setIconSize(size)

    def set_sources(self, citations, caption):
        """Show citations as chips: the number pill, the document number and
        page. caption says what they are; it goes to the tooltip."""
        while self.chips_layout.count():
            # Read the widget once: unparenting it empties the layout item.
            old = self.chips_layout.takeAt(0).widget()
            if old is not None:
                old.hide()
                old.setParent(None)
                old.deleteLater()
        citations = list(citations or [])
        self._sources_caption = caption if citations else ""
        if not citations:
            self.sources_box.hide()
            return
        for citation in citations[:self.MAX_CHIPS]:
            chip = QToolButton(self.chips_host)
            chip.setProperty("chatRole", "chip")
            chip.setProperty("citationNumber", int(citation.n))
            chip.setFocusPolicy(Qt.StrongFocus)
            # The size comes from this font. QToolButton caches its size hint,
            # so a font that arrived later through the stylesheet would leave
            # every chip as wide as it was in the default font.
            font = chip.font()
            font.setPointSizeF(8.5)
            chip.setFont(font)
            chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self._paint_badge(chip)
            chip.setCursor(Qt.PointingHandCursor)
            number = (citation.so_ki_hieu or "").strip() or "Văn bản #{}".format(citation.doc_id)
            if len(number) > 26:
                number = number[:25] + "…"
            page = citation.page or 1
            chip.setText("{}, tr. {}".format(number, citation.page) if citation.page else number)
            tip = "Mở {} tại trang {}".format(citation.so_ki_hieu or "văn bản", page)
            chip.setToolTip("{} [{}]\n{}".format(caption, citation.n, tip))
            chip.setAccessibleName("Nguồn {}: {}".format(citation.n, tip))
            chip.clicked.connect(
                lambda _checked=False, doc=int(citation.doc_id), at=int(page):
                self.open_source.emit(doc, at)
            )
            self.chips_layout.addWidget(chip)
        if len(citations) > self.MAX_CHIPS:
            more = QLabel("+{} nguồn".format(len(citations) - self.MAX_CHIPS), self.chips_host)
            more.setObjectName("chatMoreSources")
            more.setToolTip(caption)
            self.chips_layout.addWidget(more)
        self.sources_box.setToolTip(caption)
        self.sources_box.setAccessibleName(caption)
        self.sources_box.show()

    def sources_caption(self):
        """What the chips are: cited, being read, or read before a stop."""
        return self._sources_caption

    def source_chips(self):
        return [
            self.chips_layout.itemAt(index).widget()
            for index in range(self.chips_layout.count())
            if isinstance(self.chips_layout.itemAt(index).widget(), QToolButton)
        ]

    # Body ---------------------------------------------------------------
    def set_body_html(self, document_html):
        self.body.set_html(document_html)
        self.body.setVisible(bool(document_html))

    def body_html(self):
        return self.body.html() if self.body.isVisibleTo(self) else ""

    def plain_text(self):
        if not self.body_html():
            return ""
        return self.body.toPlainText().replace("\xa0", " ").strip()

    # States -------------------------------------------------------------
    def set_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def notice_text(self):
        return self.notice.text() if self.notice.isVisibleTo(self) else ""

    def show_error(self, title, detail):
        self.error_title.setText(title)
        self.error_detail.setText(detail)
        self.error_detail.setVisible(bool(detail))
        self.error_card.show()

    def error_visible(self):
        return self.error_card.isVisibleTo(self)

    def error_text(self):
        return "{} {}".format(self.error_title.text(), self.error_detail.text()).strip()

    def show_actions(self, copy=False, regenerate=False, retry=False, meta="", meta_detail=""):
        """meta: a few quiet words on screen (the time taken); meta_detail:
        the rest (speed and so on), one hover away."""
        self.copy_button.setVisible(copy)
        self.regenerate_button.setVisible(regenerate)
        self.retry_button.setVisible(retry)
        self.meta_label.setText(meta)
        self.meta_label.setToolTip(meta_detail)
        self.meta_label.setVisible(bool(meta))
        self.actions.setVisible(copy or regenerate or retry or bool(meta))

    def set_regenerate_visible(self, visible):
        self.regenerate_button.setVisible(visible)

    def set_retry_visible(self, visible):
        self.retry_button.setVisible(visible)
        self.actions.setVisible(
            visible or self.copy_button.isVisibleTo(self.actions)
            or self.regenerate_button.isVisibleTo(self.actions)
            or bool(self.meta_label.text())
        )

    def retry_available(self):
        return self.retry_button.isVisibleTo(self)

    def meta_text(self):
        return self.meta_label.text()

    def meta_detail(self):
        return self.meta_label.toolTip()


class StatusRow(QWidget):
    """Live line under the answer: sparkle, current step, elapsed time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(TEXT_INSET, 0, TEXT_INSET, 0)
        layout.setSpacing(8)
        self.indicator = WorkingIndicator(self, size=16)
        self.label = ShimmerLabel()
        self.label.setObjectName("chatSteps")
        self.label.setMinimumWidth(40)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.elapsed = QLabel()
        self.elapsed.setObjectName("chatElapsed")
        layout.addWidget(self.indicator)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.elapsed)
        self.indicator.frame.connect(self._on_frame)
        self._started = None
        self._shown_second = None
        self._rate = ""
        self.hide()

    def apply_theme(self, theme):
        c = chat_colors(theme)
        self.indicator.set_colors(c["accent"], c["accent_soft"], c["page"])
        self.label.set_shimmer_colors(c["secondary"], c["text"], c["page"])

    def begin(self, text):
        self._started = time.perf_counter()
        self._shown_second = None
        self._rate = ""
        self.label.setText(text)
        self.elapsed.setText("")
        self.elapsed.setToolTip("")
        self.show()
        self.indicator.start()

    def end(self):
        self.indicator.stop()
        self.label.stop_shimmer()
        self._started = None
        self.hide()

    def set_rate(self, text):
        """The writing speed: one hover away on the elapsed time, so the line
        on screen stays one word long."""
        self._rate = text
        self.elapsed.setToolTip("Tốc độ: {}".format(text) if text else "")

    def _on_frame(self, phase):
        self.label.set_phase(phase)
        if self._started is None:
            return
        second = int(time.perf_counter() - self._started)
        if second != self._shown_second:
            self._shown_second = second
            self.elapsed.setText("{} giây".format(second) if second else "")


class SuggestionButton(QPushButton):
    """A quiet row that asks a question; cut short with "…" when the dock is
    narrow, the whole question then in its tooltip.

    A push button is never narrower than its text, and one long suggestion
    made the transcript wider than the dock, clipping every line in it.
    """

    # Left and right padding, the icon and the gap after it, the border
    # (chat_stylesheet).
    CHROME = TEXT_INSET + 16 + 6 + TEXT_INSET + 2

    def __init__(self, question, parent=None):
        super().__init__(question, parent)
        self._question = question
        self._tip = ""
        self._sized = False  # laid out at least once: its width means something
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def set_explanation(self, tip):
        """A tooltip of its own (why it is disabled), shown before the text."""
        self._tip = tip
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sized = True
        self._fit()

    def _fit(self):
        shown = self._question
        if self._sized:
            width = max(0, self.width() - self.CHROME)
            shown = self.fontMetrics().elidedText(self._question, Qt.ElideRight, width)
        if shown != self.text():
            self.setText(shown)
        tips = [tip for tip in (self._tip, self._question if shown != self._question else "") if tip]
        self.setToolTip("\n".join(tips))


class EmptyStateView(QWidget):
    """What a new conversation shows: what the assistant does, and a start.

    Left-aligned on the text edge of the transcript; the suggestions are
    quiet rows, and the legal note is said once, here, in small print.
    """

    chosen = pyqtSignal(str, bool)

    SUGGESTIONS = (
        "Kho hiện có bao nhiêu văn bản, gồm những loại nào?",
        "Liệt kê các quyết định trong kho",
        "Có văn bản nào đã hết hiệu lực không?",
    )
    DOCUMENT_SUGGESTION = "Tóm tắt nội dung chính của văn bản đang mở"
    NOTE = "Công cụ hỗ trợ tra cứu, không thay thế tư vấn pháp lý."

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(2)
        title = QLabel("Hỏi đáp trên kho văn bản")
        title.setObjectName("chatEmptyTitle")
        body = QLabel("Trợ lý tìm trong kho của bạn và trả lời kèm nguồn trích dẫn.")
        body.setObjectName("chatEmptyBody")
        for label in (title, body):
            label.setWordWrap(True)
            label.setIndent(0)  # the padding sets the text edge (TEXT_INSET)
            layout.addWidget(label)
        layout.addSpacing(10)
        self._buttons = []
        for question in self.SUGGESTIONS:
            button = self._suggestion(question)
            button.clicked.connect(
                lambda _checked=False, q=question: self.chosen.emit(q, False)
            )
            self._buttons.append(button)
            layout.addWidget(button)
        self._document_button = self._suggestion(self.DOCUMENT_SUGGESTION)
        self._document_button.clicked.connect(
            lambda _checked=False: self.chosen.emit(self.DOCUMENT_SUGGESTION, True)
        )
        layout.addWidget(self._document_button)
        self.set_document_available(False)
        layout.addSpacing(12)
        self.note = QLabel(self.NOTE)
        self.note.setObjectName("chatEmptyNote")
        self.note.setWordWrap(True)
        self.note.setIndent(0)
        layout.addWidget(self.note)
        self.apply_theme(theme)

    def _suggestion(self, question):
        button = SuggestionButton(question)
        button.setProperty("chatRole", "suggestion")
        button.setProperty("question", question)
        button.setCursor(Qt.PointingHandCursor)
        button.setIconSize(QSize(16, 16))
        return button

    def apply_theme(self, theme):
        c = chat_colors(theme)
        for button in self._buttons:
            button.setIcon(icon("search", size=16, color=c["secondary"]))
        self._document_button.setIcon(icon("file-text", size=16, color=c["secondary"]))

    def suggestion_buttons(self):
        return list(self._buttons)

    def document_button(self):
        return self._document_button

    def set_document_available(self, available):
        self._document_button.setEnabled(bool(available))
        self._document_button.set_explanation(
            "" if available else "Mở một văn bản trong kho để dùng gợi ý này"
        )
