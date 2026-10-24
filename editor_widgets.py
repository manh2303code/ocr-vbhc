"""Widgets of the document editor, the right-hand panel of the workspace.

The 2026-10 redesign (docs/design/minimal-ui-2026-10.md) asked for things
the stock Qt widgets do not do on their own:

- a metadata value never hides its text. GrowingTextEdit wraps and grows with
  its content, up to a few lines while the editor is split between full text
  and metadata, without a limit when the metadata is expanded;
- the release-date picker is a small popover under the date field
  (DatePopup), in Vietnamese, with today marked, instead of a separate
  "Chọn ngày" window; the field itself still takes a typed date;
- each section of the editor has one title row with an expand button
  (SectionHeader).

Colours come from theme_tokens; nothing here writes a colour of its own.
"""
import re
from datetime import date

from PyQt5.QtCore import QDate, QEvent, QLocale, QObject, QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractSpinBox, QApplication, QCalendarWidget, QComboBox, QCompleter, QFrame, QGridLayout,
    QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QStackedWidget, QTableView, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

import theme_tokens
from ui_icons import icon

# The typeface Nghị định 30/2020 prescribes for administrative documents, and
# installed on every Windows. Document content is set in it; chrome stays sans.
DOCUMENT_FONT = "Times New Roman"


# ── Dates ──────────────────────────────────────────────────────
_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4}|\d{2})\s*$")
# "ngày 02 tháng 8 năm 2017", the form OCR used to store
_WORDED_DATE = re.compile(r"(\d{1,2})\s*(?:/|tháng)\s*(\d{1,2})\s*(?:/|năm)\s*(\d{4})", re.I)


def parse_document_date(text):
    """A datetime.date from what a user or the OCR wrote, or None.

    Accepts dd/mm/yyyy (the stored form), d/m/yyyy, dd-mm-yyyy, dd.mm.yyyy,
    dd/mm/yy (years 69-99 are 19xx, like strptime's %y) and the worded
    "ngày 02 tháng 8 năm 2017".
    """
    text = (text or "").strip()
    if not text:
        return None
    match = _NUMERIC_DATE.match(text) or _WORDED_DATE.search(text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 1900 if year >= 69 else 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def format_document_date(value):
    """dd/mm/yyyy, the form stored in the database."""
    return "{:02d}/{:02d}/{:04d}".format(value.day, value.month, value.year)


# ── Growing text field ────────────────────────────────────────
class GrowingTextEdit(QTextEdit):
    """A plain-text field that wraps and grows with its content.

    It stands in for QLineEdit where a value can be longer than the field or
    has several lines (cơ quan ban hành, chức vụ, trích yếu, nơi nhận), so it
    offers the QLineEdit calls the editor and its callers use: text(),
    setText(), setCompleter(). Until the user edits it, text() returns
    exactly what setText() was given: a document stores CR LF as one
    paragraph break, and QTextEdit.toPlainText() turns non-breaking spaces
    into plain ones.

    The height follows the wrapped text between min_lines and max_lines;
    max_lines None means no limit (the field then never scrolls).

    single_line=True is for one value that may still be long (số hiệu,
    loại văn bản, người ký): it wraps rather than hides its end, but Enter
    does not start a new line and pasted line breaks become spaces.
    """

    height_changed = pyqtSignal()

    def __init__(self, min_lines=1, max_lines=4, single_line=False, parent=None):
        super().__init__(parent)
        self._min_lines = max(1, int(min_lines))
        self._max_lines = max_lines
        self._single_line = bool(single_line)
        self._completer = None
        self._given = ""
        self._given_shown = ""
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().setDocumentMargin(2)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._sync_scrollbar()
        self._fit()

    # QLineEdit-compatible text API
    def text(self):
        shown = self._shown_text()
        return self._given if shown == self._given_shown else shown

    def setText(self, value):
        self._given = value or ""
        self.setPlainText(self._given)
        self._given_shown = self._shown_text()

    def _shown_text(self):
        raw = self.document().toRawText()
        return raw.replace("\u2029", "\n").replace("\u2028", "\n")

    def setCursorPosition(self, position):
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(int(position), len(self.toPlainText()))))
        self.setTextCursor(cursor)

    # Height
    def max_lines(self):
        return self._max_lines

    def set_max_lines(self, max_lines):
        if max_lines != self._max_lines:
            self._max_lines = max_lines
            self._sync_scrollbar()
            self._fit()

    def _sync_scrollbar(self):
        # Without a cap the field is always as tall as its text; a scrollbar
        # flashing in while it grows would narrow it and re-wrap the text.
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if self._max_lines is None else Qt.ScrollBarAsNeeded)

    def is_capped(self):
        """True when the text is taller than the field shows."""
        return self.document().size().height() > self._content_limit() + 0.5

    def _line_height(self):
        return self.fontMetrics().lineSpacing()

    def _content_limit(self):
        if self._max_lines is None:
            return float("inf")
        return self._line_height() * self._max_lines + 2 * self.document().documentMargin()

    def _chrome(self):
        viewport = self.viewport().height()
        if self.isVisible() and 0 < viewport < self.height():
            return self.height() - viewport
        margins = self.contentsMargins()
        return 2 * self.frameWidth() + margins.top() + margins.bottom() + 4

    def _fit(self, *_args):
        margin = 2 * self.document().documentMargin()
        floor = self._line_height() * self._min_lines + margin
        content = max(floor, min(self.document().size().height(), self._content_limit()))
        target = int(content + self._chrome() + 0.999)
        if target != self.height() or self.minimumHeight() != target:
            self.setFixedHeight(target)
            self.height_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit()

    # Completion, like QLineEdit's: the whole text is the prefix
    def setCompleter(self, completer):
        if self._completer is not None:
            try:
                self._completer.activated[str].disconnect(self._insert_completion)
            except TypeError:
                pass
        self._completer = completer
        if completer is None:
            return
        completer.setWidget(self)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.activated[str].connect(self._insert_completion)

    def completer(self):
        return self._completer

    def _insert_completion(self, value):
        if self._completer is None or self._completer.widget() is not self:
            return
        self.setPlainText(value)
        self.moveCursor(QTextCursor.End)

    def insertFromMimeData(self, source):
        if self._single_line and source.hasText():
            text = source.text().replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            self.insertPlainText(text)
            return
        super().insertFromMimeData(source)

    def keyPressEvent(self, event):
        completer = self._completer
        popup_open = completer is not None and completer.popup().isVisible()
        if popup_open and event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape,
                                          Qt.Key_Tab, Qt.Key_Backtab):
            event.ignore()
            return
        if self._single_line and event.key() in (Qt.Key_Enter, Qt.Key_Return):
            event.accept()
            return
        super().keyPressEvent(event)
        if completer is None:
            return
        typed = bool(event.text()) or event.key() in (Qt.Key_Backspace, Qt.Key_Delete)
        if not typed or event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return
        prefix = self.text()
        if not prefix.strip():
            completer.popup().hide()
            return
        if prefix != completer.completionPrefix():
            completer.setCompletionPrefix(prefix)
            completer.popup().setCurrentIndex(completer.completionModel().index(0, 0))
        if completer.completionCount() == 0:
            completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(160, self.viewport().width() - rect.x()))
        completer.complete(rect)


class WheelGuard(QObject):
    """Wheel turns over a value control change it only when it has focus.

    Qt gives combo boxes, spin boxes and date edits the wheel as soon as the
    pointer is over them (focus policy WheelFocus), so scrolling the
    metadata form through Độ khẩn / Độ mật changed them by accident. Installed
    on the application: an unfocused control ignores the wheel, which Qt then
    passes to its parent, so the form keeps scrolling. A click or Tab focuses
    the control and the wheel works on it again.
    """

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Polish, QEvent.Wheel):
            return False
        if not isinstance(watched, (QComboBox, QAbstractSpinBox)):
            return False
        if kind == QEvent.Polish:
            # Qt focuses a WheelFocus control before any filter sees the
            # wheel, so the policy itself has to drop to click/Tab.
            if watched.focusPolicy() == Qt.WheelFocus:
                watched.setFocusPolicy(Qt.StrongFocus)
            return False
        if not watched.hasFocus():
            event.ignore()
            return True
        return False


def install_wheel_guard(app=None):
    """Install one WheelGuard on the application (idempotent); returns it."""
    app = app or QApplication.instance()
    guard = getattr(app, "_ocr_wheel_guard", None)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
        app._ocr_wheel_guard = guard
    return guard


class InlineSelector(QComboBox):
    """A quiet combo as wide as its current choice, not its widest one, so
    its arrow sits next to the word it changes."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.addItems(items)
        self.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.currentIndexChanged.connect(lambda _index: self.updateGeometry())

    def _widest(self):
        metrics = self.fontMetrics()
        return max((metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())),
                   default=0)

    def sizeHint(self):
        hint = super().sizeHint()
        current = self.fontMetrics().horizontalAdvance(self.currentText())
        return QSize(hint.width() - self._widest() + current, hint.height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def showPopup(self):
        self.view().setMinimumWidth(self._widest() + 32)
        super().showPopup()


# ── Labels ─────────────────────────────────────────────────────
class ElidedLabel(QLabel):
    """One line of text that ends in "…" when the label is too narrow."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setMinimumWidth(24)
        self.set_full_text(text)

    def full_text(self):
        return self._full

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, hint.height())

    def _elide(self):
        width = max(0, self.contentsRect().width())
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, width) if width else self._full
        if shown != super().text():
            super().setText(shown)


class SectionHeader(QFrame):
    """Title row of an editor section: a short name and one expand button."""

    toggled = pyqtSignal()

    def __init__(self, title, expand_tip, collapse_tip, parent=None):
        super().__init__(parent)
        self.setObjectName("editorSectionHeader")
        self._expand_tip = expand_tip
        self._collapse_tip = collapse_tip
        self._expanded = False
        self._theme = "light"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 6, 2)
        layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setProperty("role", "editorSectionTitle")
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.button = QToolButton()
        self.button.setObjectName("editorExpandButton")
        self.button.setAutoRaise(True)
        self.button.setIconSize(QSize(16, 16))
        self.button.setFixedSize(26, 26)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self.toggled)
        layout.addWidget(self.button)
        self.setFixedHeight(34)
        self._sync()

    def is_expanded(self):
        return self._expanded

    def set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self._sync()

    def apply_theme(self, theme):
        self._theme = theme
        self._sync()

    def _sync(self):
        colour = theme_tokens.colors_for(self._theme)["text_secondary"]
        name = "collapse" if self._expanded else "expand"
        tip = self._collapse_tip if self._expanded else self._expand_tip
        self.button.setIcon(icon(name, size=16, color=colour))
        self.button.setToolTip(tip)
        self.button.setAccessibleName(tip.split("\n", 1)[0])


# ── Date popover ──────────────────────────────────────────────
_WEEKDAYS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


class DatePopup(QWidget):
    """Calendar popover anchored under a date field.

    Three levels, like the Windows calendar: days of a month, the months of a
    year, the years around a decade. The title goes up a level, a month or a
    year goes back down, the arrows step by month, year or decade. A click,
    Enter or a double click on a day chooses it; Esc closes without a change.

    The window itself is transparent; the panel inside it (QFrame#datePopup)
    draws the surface, so its rounded corners stay clean.
    """

    date_chosen = pyqtSignal(QDate)

    DAYS, MONTHS, YEARS = range(3)
    MINIMUM = QDate(1900, 1, 1)
    MAXIMUM = QDate(2100, 12, 31)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._theme = "light"
        self._level = self.DAYS
        self._year = QDate.currentDate().year()
        self._month = QDate.currentDate().month()
        self._anchor = None

        window = QVBoxLayout(self)
        window.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setObjectName("datePopup")
        window.addWidget(self.panel)

        outer = QVBoxLayout(self.panel)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(2)
        self.title_button = QToolButton()
        self.title_button.setObjectName("datePopupTitle")
        self.title_button.setCursor(Qt.PointingHandCursor)
        self.title_button.clicked.connect(self._zoom_out)
        self.prev_button = QToolButton()
        self.next_button = QToolButton()
        for button, step in ((self.prev_button, -1), (self.next_button, 1)):
            button.setObjectName("datePopupNav")
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(28, 28)
            button.clicked.connect(lambda _checked=False, s=step: self._step(s))
        nav.addWidget(self.title_button)
        nav.addStretch(1)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        outer.addLayout(nav)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        days = QWidget()
        days_layout = QVBoxLayout(days)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setSpacing(2)
        weekdays = QHBoxLayout()
        weekdays.setContentsMargins(0, 0, 0, 0)
        weekdays.setSpacing(0)
        for name in _WEEKDAYS:
            label = QLabel(name)
            label.setAlignment(Qt.AlignCenter)
            label.setProperty("role", "datePopupWeekday")
            weekdays.addWidget(label, 1)
        days_layout.addLayout(weekdays)

        self.calendar = QCalendarWidget()
        self.calendar.setObjectName("datePopupCalendar")
        self.calendar.setNavigationBarVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.NoHorizontalHeader)
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.setGridVisible(False)
        self.calendar.setLocale(QLocale(QLocale.Vietnamese, QLocale.Vietnam))
        self.calendar.setDateRange(self.MINIMUM, self.MAXIMUM)
        self.calendar.setFixedSize(7 * 36, 6 * 30)
        self.calendar.clicked.connect(self._choose)
        self.calendar.activated.connect(self._choose)
        self.calendar.currentPageChanged.connect(self._page_changed)
        days_layout.addWidget(self.calendar)
        self._stack.addWidget(days)

        grid_page = QWidget()
        grid = QGridLayout(grid_page)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setSpacing(4)
        self._cells = []
        for index in range(12):
            cell = QToolButton()
            cell.setProperty("role", "dateCell")
            cell.setCheckable(True)
            cell.setAutoRaise(True)
            cell.setCursor(Qt.PointingHandCursor)
            cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            cell.clicked.connect(lambda _checked=False, i=index: self._cell_clicked(i))
            grid.addWidget(cell, index // 3, index % 3)
            self._cells.append(cell)
        self._stack.addWidget(grid_page)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        self.today_button = QPushButton("Hôm nay")
        self.today_button.setProperty("variant", "ghost")
        self.today_button.setCursor(Qt.PointingHandCursor)
        self.today_button.clicked.connect(lambda: self._choose(QDate.currentDate()))
        footer.addWidget(self.today_button)
        footer.addStretch(1)
        outer.addLayout(footer)

        self.apply_theme("light")
        self._show_level(self.DAYS)

    # Public
    def level(self):
        return self._level

    def open_for(self, anchor, current=None):
        """Show under `anchor` (right edges aligned), on `current` or today."""
        self._anchor = anchor
        current = current if current is not None and current.isValid() else QDate.currentDate()
        self.calendar.setSelectedDate(current)
        self.calendar.setCurrentPage(current.year(), current.month())
        self._year, self._month = current.year(), current.month()
        self._mark_today()
        self._show_level(self.DAYS)
        self.adjustSize()
        self.move(self._position_for(anchor))
        self.show()
        self.raise_()
        self.activateWindow()
        self.calendar.setFocus(Qt.PopupFocusReason)

    def apply_theme(self, theme):
        self._theme = theme
        tokens = theme_tokens.colors_for(theme)
        for name, button in (("chevron-left", self.prev_button), ("chevron-right", self.next_button)):
            button.setIcon(icon(name, size=16, color=tokens["text_secondary"]))
        plain = QTextCharFormat()
        plain.setForeground(QColor(tokens["text"]))
        for day in (Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday,
                    Qt.Friday, Qt.Saturday, Qt.Sunday):
            self.calendar.setWeekdayTextFormat(day, plain)
        view = self.calendar.findChild(QTableView, "qt_calendar_calendarview")
        if view is not None:
            palette = view.palette()
            palette.setColor(QPalette.Base, QColor(tokens["surface"]))
            palette.setColor(QPalette.AlternateBase, QColor(tokens["surface"]))
            palette.setColor(QPalette.Text, QColor(tokens["text"]))
            palette.setColor(QPalette.Disabled, QPalette.Text, QColor(tokens["text_muted"]))
            palette.setColor(QPalette.Highlight, QColor(tokens["primary"]))
            palette.setColor(QPalette.HighlightedText, QColor(tokens["text_on_primary"]))
            view.setPalette(palette)
        self._mark_today()

    # Internals
    def _mark_today(self):
        tokens = theme_tokens.colors_for(self._theme)
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        today = QTextCharFormat()
        today.setForeground(QColor(tokens["accent_gold"]))
        today.setFontWeight(QFont.Bold)
        today.setFontUnderline(True)
        self.calendar.setDateTextFormat(QDate.currentDate(), today)

    def _position_for(self, anchor):
        size = self.sizeHint()
        below = anchor.mapToGlobal(QPoint(anchor.width() - size.width(), anchor.height() + 4))
        screen = QApplication.screenAt(below) or QApplication.primaryScreen()
        if screen is None:
            return below
        area = screen.availableGeometry()
        x = max(area.left(), min(below.x(), area.right() - size.width()))
        y = below.y()
        if y + size.height() > area.bottom():
            y = anchor.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4
        return QPoint(x, max(area.top(), y))

    def _choose(self, chosen):
        if not chosen.isValid():
            return
        self.calendar.setSelectedDate(chosen)
        self.date_chosen.emit(chosen)
        self.close()

    def _page_changed(self, year, month):
        self._year, self._month = year, month
        if self._level == self.DAYS:
            self._refresh()

    def _show_level(self, level):
        self._level = level
        self._stack.setCurrentIndex(0 if level == self.DAYS else 1)
        self._refresh()

    def _decade_start(self):
        return (self._year // 10) * 10 - 1

    def _refresh(self):
        if self._level == self.DAYS:
            self.title_button.setText("Tháng {} năm {}".format(self._month, self._year))
            self.title_button.setToolTip("Chọn tháng, năm")
            self.title_button.setEnabled(True)
            self.prev_button.setToolTip("Tháng trước")
            self.next_button.setToolTip("Tháng sau")
            return
        if self._level == self.MONTHS:
            self.title_button.setText("Năm {}".format(self._year))
            self.title_button.setToolTip("Chọn năm")
            self.title_button.setEnabled(True)
            self.prev_button.setToolTip("Năm trước")
            self.next_button.setToolTip("Năm sau")
            for index, cell in enumerate(self._cells):
                cell.setText("Tháng {}".format(index + 1))
                cell.setChecked(index + 1 == self._month)
                cell.setProperty("outside", False)
                cell.setEnabled(True)
            return
        start = self._decade_start()
        self.title_button.setText("{} – {}".format(start + 1, start + 10))
        self.title_button.setToolTip("")
        self.title_button.setEnabled(False)
        self.prev_button.setToolTip("Mười năm trước")
        self.next_button.setToolTip("Mười năm sau")
        for index, cell in enumerate(self._cells):
            year = start + index
            cell.setText(str(year))
            cell.setChecked(year == self._year)
            cell.setEnabled(self.MINIMUM.year() <= year <= self.MAXIMUM.year())
            outside = index in (0, 11)
            if cell.property("outside") != outside:
                cell.setProperty("outside", outside)
                cell.style().unpolish(cell)
                cell.style().polish(cell)

    def _zoom_out(self):
        if self._level < self.YEARS:
            self._show_level(self._level + 1)

    def _step(self, direction):
        if self._level == self.DAYS:
            if direction < 0:
                self.calendar.showPreviousMonth()
            else:
                self.calendar.showNextMonth()
            return
        span = 1 if self._level == self.MONTHS else 10
        self._year = max(self.MINIMUM.year(), min(self.MAXIMUM.year(), self._year + direction * span))
        self._refresh()

    def _cell_clicked(self, index):
        if self._level == self.MONTHS:
            self._month = index + 1
            self.calendar.setCurrentPage(self._year, self._month)
            self._show_level(self.DAYS)
            self.calendar.setFocus(Qt.OtherFocusReason)
        elif self._level == self.YEARS:
            self._year = self._decade_start() + index
            self._show_level(self.MONTHS)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        anchor = self._anchor
        if anchor is not None and anchor.isVisible():
            anchor.setFocus(Qt.PopupFocusReason)
