"""editor_widgets: the growing metadata field, the inline selector, the
date helpers and the date popover, on their own (no editor around them)."""
import os
import unittest
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QDate, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit

from editor_widgets import (
    DatePopup, GrowingTextEdit, InlineSelector, format_document_date, parse_document_date,
)


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])


class GrowingTextEditTests(_QtCase):
    def test_text_returns_exactly_what_was_set_until_edited(self):
        field = GrowingTextEdit()
        for value in ("A\r\nB", "A B", "dòng một\ndòng hai\n", "\tthụt", ""):
            field.setText(value)
            self.assertEqual(value, field.text())
        field.setText("A")
        field.moveCursor(QTextCursor.End)
        field.insertPlainText("\nB")
        self.assertEqual("A\nB", field.text())

    def test_pasted_rich_text_stays_plain(self):
        field = GrowingTextEdit()
        field.setText("<b>Mật</b>")
        self.assertEqual("<b>Mật</b>", field.text())
        self.assertFalse(field.acceptRichText())

    def test_height_follows_lines_up_to_the_cap(self):
        field = GrowingTextEdit(min_lines=1, max_lines=3)
        field.resize(300, 30)
        field.show()
        field.setText("một")
        self.app.processEvents()
        one = field.height()
        field.setText("một\nhai")
        self.app.processEvents()
        self.assertGreater(field.height(), one)
        field.setText("\n".join("dòng {}".format(i) for i in range(8)))
        self.app.processEvents()
        capped = field.height()
        self.assertTrue(field.is_capped())
        field.set_max_lines(None)
        self.app.processEvents()
        self.assertGreater(field.height(), capped)
        self.assertFalse(field.is_capped())
        field.close()

    def test_tab_moves_on_like_in_a_form(self):
        self.assertTrue(GrowingTextEdit().tabChangesFocus())


class InlineSelectorTests(_QtCase):
    def test_is_as_wide_as_its_current_choice(self):
        selector = InlineSelector(["Tất cả", "Chức danh"])
        narrow = selector.sizeHint().width()
        selector.setCurrentText("Chức danh")
        self.assertGreater(selector.sizeHint().width(), narrow)


class DocumentDateTests(unittest.TestCase):
    def test_parses_the_forms_users_and_ocr_write(self):
        cases = {
            "02/08/2017": date(2017, 8, 2), "2/8/2017": date(2017, 8, 2),
            "02-08-2017": date(2017, 8, 2), "02.08.2017": date(2017, 8, 2),
            "02/08/17": date(2017, 8, 2), "02/08/98": date(1998, 8, 2),
            "Hưng Yên, ngày 02 tháng 8 năm 2017": date(2017, 8, 2),
        }
        for text, expected in cases.items():
            self.assertEqual(expected, parse_document_date(text), text)
        for text in ("", "31/02/2017", "abc", "2017-08-02"):
            self.assertIsNone(parse_document_date(text), text)
        self.assertEqual("02/08/2017", format_document_date(date(2017, 8, 2)))


class DatePopupTests(_QtCase):
    def test_weeks_start_on_monday_with_vietnamese_day_names(self):
        popup = DatePopup()
        weekdays = [label.text() for label in popup.findChildren(QLabel)
                    if label.property("role") == "datePopupWeekday"]
        self.assertEqual(["T2", "T3", "T4", "T5", "T6", "T7", "CN"], weekdays)
        self.assertEqual(Qt.Monday, popup.calendar.firstDayOfWeek())
        popup.deleteLater()

    def test_opens_on_the_given_date_and_reports_a_choice(self):
        anchor = QLineEdit()
        anchor.resize(200, 26)
        anchor.show()
        popup = DatePopup()
        chosen = []
        popup.date_chosen.connect(chosen.append)
        popup.open_for(anchor, QDate(2017, 8, 2))
        self.app.processEvents()
        self.assertTrue(popup.isVisible())
        self.assertEqual("Tháng 8 năm 2017", popup.title_button.text())
        popup.next_button.click()
        self.assertEqual("Tháng 9 năm 2017", popup.title_button.text())
        popup.today_button.click()
        self.assertEqual([QDate.currentDate()], chosen)
        self.assertFalse(popup.isVisible())
        anchor.close()

    def test_an_invalid_date_opens_on_today(self):
        anchor = QLineEdit()
        anchor.show()
        popup = DatePopup()
        popup.open_for(anchor, None)
        self.app.processEvents()
        today = QDate.currentDate()
        self.assertEqual((today.year(), today.month()),
                         (popup.calendar.yearShown(), popup.calendar.monthShown()))
        popup.close()
        anchor.close()


if __name__ == "__main__":
    unittest.main()
