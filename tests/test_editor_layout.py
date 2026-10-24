"""The document editor after the 2026-10 redesign.

The user's complaints, each pinned here: long metadata was hidden inside
fields that neither grew nor fit; the show/hide-metadata button was hidden
and confusing, so each section now has an expand button instead (the full
text, or every metadata value in full); the "Chọn ngày" window looked
foreign, so the date picker is a popover under the field; "Lưu thay đổi"
was cut to "Lưu thay đổ".
"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QDate, QMimeData, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication, QDialog, QPushButton, QTableView, QTextEdit, QToolButton,
)

from main_window_1 import OCRResultEditor
from theme_manager_v2 import ThemeManager
from theme_tokens import colors_for

ROOT = Path(__file__).resolve().parents[1]
LONG_NUMBER = "1234/QĐ-UBND-KTTH-TCKH-QLĐT-VP-2026-BỔ-SUNG-ĐIỀU-CHỈNH"
RECEIVERS = "\n".join("- Đơn vị nhận số {}".format(i) for i in range(1, 11))


def _row(**overrides):
    values = {
        "cqbh_tren": "HĐND TỈNH HƯNG YÊN", "cqbh_duoi": "BAN KINH TẾ NGÂN SÁCH",
        "so": "336/KH-KTNS", "ngay": "02/08/2017", "khan": "Không", "mat": "Không",
        "loai": "Kế hoạch", "nd": "Việc trích lập, quản lý và sử dụng Quỹ phát triển đất",
        "noi_nhan": "- Chủ tịch\n- Lưu VT", "chuc_vu": "TM. BAN KINH TẾ NGÂN SÁCH\nTRƯỞNG BAN",
        "ky": "Trần Quốc Toàn",
        "text": "Plain OCR body long enough for the loader to recognize as content.",
    }
    values.update(overrides)
    return (
        1, 9, 1, values["cqbh_tren"], values["cqbh_duoi"], values["so"], values["ngay"],
        values["khan"], values["mat"], values["loai"], values["nd"], values["noi_nhan"],
        values["chuc_vu"], values["ky"], values["text"], "OCR System",
        "2026-07-11 10:00:00", "chandra-q4",
    )


class _EditorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        ThemeManager.instance().apply(self.app, "light")
        self.editor = OCRResultEditor(db=None)
        self.editor.resize(420, 900)
        self.editor.show()
        self.settle()

    def tearDown(self):
        popup = self.editor._date_popup
        if popup is not None:
            popup.close()
        self.editor.close()
        self.editor.deleteLater()
        self.settle()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def load(self, **overrides):
        self.editor.load_data(_row(**overrides))
        self.settle()


class SectionExpandTests(_EditorCase):
    def test_the_hidden_metadata_toggle_is_gone(self):
        self.assertFalse(hasattr(self.editor, "_toggle_meta_btn"))
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8")
        # The command palette no longer reaches for it (F10 keeps toggling
        # the whole details panel through its toolbar action).
        self.assertNotIn("ocr_editor._toggle_meta_btn", source)
        self.assertNotIn("Ẩn metadata", source)

    def test_each_section_has_one_expand_button_with_a_tooltip(self):
        buttons = self.editor.findChildren(QToolButton, "editorExpandButton")
        self.assertEqual(2, len(buttons))
        for button in buttons:
            self.assertTrue(button.toolTip().startswith("Mở rộng"))
            self.assertFalse(button.icon().isNull())
        self.assertEqual("Toàn văn", self.editor._text_header.title.text())
        self.assertEqual("Thông tin", self.editor._meta_header.title.text())

    def test_expanding_the_full_text_folds_the_metadata_to_its_title(self):
        self.load()
        text = self.editor.fields["van_ban_dang_txt"]
        split_height = text.height()

        self.editor._text_header.button.click()
        self.settle()
        self.assertEqual(OCRResultEditor.TEXT, self.editor.expanded_section())
        self.assertFalse(self.editor.scroll_area.isVisible())
        self.assertTrue(self.editor._meta_header.isVisible())
        self.assertGreater(text.height(), split_height + 200)
        self.assertTrue(self.editor._text_header.button.toolTip().startswith("Thu gọn"))

        self.editor._text_header.button.click()
        self.settle()
        self.assertIsNone(self.editor.expanded_section())
        self.assertTrue(self.editor.scroll_area.isVisible())
        self.assertTrue(self.editor._text_header.button.toolTip().startswith("Mở rộng"))

    def test_expanding_the_metadata_shows_every_value_in_full(self):
        self.load(noi_nhan=RECEIVERS)
        receivers = self.editor.fields["noi_nhan"]
        self.assertTrue(receivers.is_capped())  # split: a few lines, then it scrolls

        self.editor._meta_header.button.click()
        self.settle()
        self.assertEqual(OCRResultEditor.META, self.editor.expanded_section())
        self.assertFalse(self.editor.fields["van_ban_dang_txt"].isVisible())
        self.assertFalse(receivers.is_capped())
        self.assertGreaterEqual(receivers.height(), 10 * receivers.fontMetrics().lineSpacing())
        self.assertGreater(self.editor.scroll_area.height(), self.editor.height() // 2)

        # The other section's button switches straight over.
        self.editor._text_header.button.click()
        self.settle()
        self.assertEqual(OCRResultEditor.TEXT, self.editor.expanded_section())
        self.assertTrue(receivers.is_capped())

    def test_split_layout_keeps_the_larger_share_for_the_text(self):
        self.load(noi_nhan=RECEIVERS)
        self.assertLessEqual(self.editor.scroll_area.height(), self.editor.height() * 0.5 + 1)
        self.assertGreater(self.editor.fields["van_ban_dang_txt"].height(), 250)

    def test_long_metadata_never_stops_the_window_getting_shorter(self):
        self.load(noi_nhan=RECEIVERS, nd="Trích yếu " * 40)
        self.assertLess(self.editor.minimumSizeHint().height(), 420)
        self.editor.resize(420, 520)
        self.settle()
        self.assertEqual(520, self.editor.height())

    def test_the_layout_choice_survives_opening_another_document(self):
        self.editor.set_expanded(OCRResultEditor.META)
        self.load(so="02/CV")
        self.assertEqual(OCRResultEditor.META, self.editor.expanded_section())


class MetadataValueTests(_EditorCase):
    def test_a_long_single_value_wraps_instead_of_hiding_its_end(self):
        self.load(so="01/CV")
        field = self.editor.fields["so_ki_hieu"]
        one_line = field.height()
        self.load(so=LONG_NUMBER)
        self.assertEqual(LONG_NUMBER, field.text())
        self.assertGreater(field.height(), one_line)
        self.assertFalse(field.is_capped())
        self.assertEqual(0, field.textCursor().position())

    def test_a_single_value_stays_one_line_when_typed_or_pasted(self):
        self.load()
        field = self.editor.fields["so_ki_hieu"]
        field.setFocus()
        field.moveCursor(QTextCursor.End)
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual("336/KH-KTNS", field.text())
        mime = QMimeData()
        mime.setText("A\nB")
        field.insertFromMimeData(mime)
        self.assertEqual("336/KH-KTNSA B", field.text())

    def test_a_long_worded_date_shows_its_start_and_its_whole_as_tooltip(self):
        worded = "Hưng Yên, ngày 02 tháng 8 năm 2017, theo dấu bưu điện của văn thư"
        self.load(ngay=worded)
        field = self.editor.fields["ngay_bh"]
        self.assertEqual(0, field.cursorPosition())
        self.assertEqual(worded, field.toolTip())
        # A date that fits needs no tooltip (wide, whatever the test font).
        self.editor.resize(1000, 900)
        self.load(ngay="02/08/2017")
        self.assertEqual("", field.toolTip())

    def test_two_line_values_are_shown_on_two_lines(self):
        # Wide enough that neither line wraps, whatever font the test
        # platform substitutes for Times New Roman.
        self.editor.resize(760, 900)
        self.load()
        for key, expected in (("cqbh", "HĐND TỈNH HƯNG YÊN\nBAN KINH TẾ NGÂN SÁCH"),
                              ("chuc_vu", "TM. BAN KINH TẾ NGÂN SÁCH\nTRƯỞNG BAN")):
            field = self.editor.fields[key]
            self.assertEqual(expected, field.text())
            self.assertFalse(field.is_capped(), key)
            self.assertGreaterEqual(field.height(), 2 * field.fontMetrics().lineSpacing(), key)

    def test_long_content_wraps_instead_of_running_out_of_the_field(self):
        summary = "Việc trích lập, quản lý và sử dụng Quỹ phát triển đất " * 4
        self.load(nd=summary)
        field = self.editor.fields["nd_chinh"]
        self.assertEqual(Qt.ScrollBarAlwaysOff, field.horizontalScrollBarPolicy())
        self.assertEqual(QTextEdit.WidgetWidth, field.lineWrapMode())
        self.assertGreaterEqual(field.height(), 3 * field.fontMetrics().lineSpacing())

    def test_tab_walks_the_rows_in_the_order_they_are_shown(self):
        fields = self.editor.fields
        expected = [fields["cqbh"], self.editor.cqbh_selector, fields["so_ki_hieu"],
                    fields["ngay_bh"], fields["do_khan"], fields["do_mat"], fields["loai_vb"],
                    fields["nd_chinh"], fields["noi_nhan"], fields["chuc_vu"],
                    self.editor.chuc_vu_selector, fields["chu_ky"]]
        walked, widget = [], fields["cqbh"]
        while len(walked) < len(expected):
            if widget in expected and widget not in walked:
                walked.append(widget)
            widget = widget.nextInFocusChain()
            if widget is fields["cqbh"]:
                break
        self.assertEqual(expected, walked)

    def test_editing_the_issuing_body_marks_the_document_dirty(self):
        self.load()
        self.assertFalse(self.editor.save_btn.isEnabled())
        self.assertFalse(self.editor.reset_btn.isEnabled())
        self.editor.fields["cqbh"].setText("UBND TỈNH\nSỞ TÀI CHÍNH")
        self.assertTrue(self.editor.save_btn.isEnabled())
        self.assertTrue(self.editor.reset_btn.isEnabled())
        saved = self.editor.get_current_data()
        self.assertEqual(("UBND TỈNH", "SỞ TÀI CHÍNH"), (saved["cqbh_tren"], saved["cqbh_duoi"]))

    def test_rerendering_the_full_text_is_not_an_edit(self):
        self.load()
        ThemeManager.instance().apply(self.app, "dark")
        self.editor.highlight_keywords(["OCR"])
        self.settle()
        self.assertFalse(self.editor.save_btn.isEnabled())

    def test_search_marks_keep_the_chandra_formatting(self):
        html = ('<div data-label="Section-Header"><p><b>THÔNG BÁO</b></p></div>'
                '<div data-label="Table"><table><tr><td>Ô A</td><td>Ô B</td></tr></table></div>')
        self.load(text=html)
        self.editor.highlight_keywords(["Ô A"])
        document = self.editor.fields["van_ban_dang_txt"].document()
        cursor = document.find("Ô A")
        self.assertIsNotNone(cursor.currentTable())
        self.assertEqual(colors_for("light")["accent_gold_soft"].lower(),
                         cursor.charFormat().background().color().name())
        self.assertEqual(html, self.editor.get_current_data()["van_ban_dang_txt"])

    def test_level_dots_use_the_nd30_tokens(self):
        combo = self.editor.fields["do_khan"]
        self.assertTrue(combo.itemIcon(0).isNull())  # "Không" lines up with the values
        self.assertFalse(combo.itemIcon(combo.findText("Hỏa Tốc")).isNull())


class HeaderAndActionTests(_EditorCase):
    def test_header_shows_the_type_then_the_number_without_a_dot_string(self):
        self.load()
        editor = self.editor
        self.assertEqual("Kế hoạch", editor._doc_type_label.full_text())
        self.assertEqual("336/KH-KTNS", editor._doc_number_label.full_text())
        self.assertNotIn("·", editor._doc_type_label.text() + editor._doc_number_label.text())
        editor.fields["so_ki_hieu"].setText("337/KH-KTNS")
        self.assertEqual("337/KH-KTNS", editor._doc_number_label.full_text())
        editor.clear()
        self.assertEqual("Chưa mở văn bản", editor._doc_type_label.full_text())
        self.assertFalse(editor._doc_number_label.isVisible())

    def test_a_long_value_never_widens_the_panel(self):
        """Content must not set the editor's minimum width: the splitter
        rules in workspace_layout decide it."""
        narrow = self.editor.minimumSizeHint().width()
        self.load(so=LONG_NUMBER, loai=LONG_NUMBER, ky=LONG_NUMBER, ngay=LONG_NUMBER)
        self.assertLessEqual(self.editor.minimumSizeHint().width(), narrow)
        self.assertIn(LONG_NUMBER, self.editor._doc_number_label.toolTip())

    def test_one_primary_button_and_its_label_is_never_cut(self):
        self.editor.resize(330, 800)
        self.settle()
        primaries = [button for button in self.editor.findChildren(QPushButton)
                     if button.property("variant") == "primary"]
        self.assertEqual([self.editor.save_btn], primaries)
        save = self.editor.save_btn
        self.assertGreaterEqual(save.width(), save.sizeHint().width())
        self.assertGreater(save.width(), save.fontMetrics().horizontalAdvance(save.text()) + 8)
        self.assertNotEqual("gold", self.editor.reocr_btn.property("variant"))

    def test_icon_only_tools_explain_themselves(self):
        for button in (self.editor.reocr_btn, self.editor._copy_btn, self.editor._export_word_btn):
            self.assertEqual("", button.text())
            self.assertTrue(button.toolTip())
            self.assertFalse(button.icon().isNull())
        self.assertIn("Ctrl+S", self.editor.save_btn.toolTip())


class DatePickerTests(_EditorCase):
    def open_picker(self):
        popup = self.editor.show_date_picker()
        self.settle()
        view = popup.calendar.findChild(QTableView, "qt_calendar_calendarview")
        return popup, view

    def test_the_picker_is_a_popover_under_the_field_not_a_window(self):
        self.load()
        popup, _view = self.open_picker()
        self.assertTrue(popup.isVisible())
        self.assertFalse(isinstance(popup, QDialog))
        self.assertTrue(popup.windowFlags() & Qt.Popup)
        self.assertEqual(QDate(2017, 8, 2), popup.calendar.selectedDate())
        self.assertEqual("Tháng 8 năm 2017", popup.title_button.text())
        self.assertEqual(Qt.Monday, popup.calendar.firstDayOfWeek())
        field = self.editor.fields["ngay_bh"]
        top = field.mapToGlobal(field.rect().topLeft()).y()
        bottom = field.mapToGlobal(field.rect().bottomLeft()).y()
        frame = popup.frameGeometry()
        self.assertTrue(frame.top() > bottom or frame.bottom() < top)

    def test_enter_chooses_the_day_as_dd_mm_yyyy(self):
        self.load()
        popup, view = self.open_picker()
        view.setFocus()
        QTest.keyClick(view, Qt.Key_Right)
        QTest.keyClick(view, Qt.Key_Return)
        self.settle()
        self.assertFalse(popup.isVisible())
        self.assertEqual("03/08/2017", self.editor.fields["ngay_bh"].text())
        self.assertTrue(self.editor.save_btn.isEnabled())

    def test_a_click_chooses_the_day(self):
        self.load()
        popup, _view = self.open_picker()
        popup.calendar.clicked.emit(QDate(2017, 8, 15))
        self.settle()
        self.assertFalse(popup.isVisible())
        self.assertEqual("15/08/2017", self.editor.fields["ngay_bh"].text())

    def test_escape_closes_without_a_change(self):
        self.load()
        popup, view = self.open_picker()
        QTest.keyClick(view, Qt.Key_Escape)
        self.settle()
        self.assertFalse(popup.isVisible())
        self.assertEqual("02/08/2017", self.editor.fields["ngay_bh"].text())
        self.assertFalse(self.editor.save_btn.isEnabled())

    def test_the_title_climbs_to_months_and_years(self):
        self.load()
        popup, _view = self.open_picker()
        popup.title_button.click()
        self.assertEqual(popup.MONTHS, popup.level())
        self.assertEqual("Năm 2017", popup.title_button.text())
        self.assertTrue(popup._cells[7].isChecked())
        popup.title_button.click()
        self.assertEqual(popup.YEARS, popup.level())
        self.assertEqual("2010 – 2019", popup.title_button.text())
        popup.prev_button.click()
        self.assertEqual("2000 – 2009", popup.title_button.text())
        popup._cells[6].click()  # 2005
        self.assertEqual(popup.MONTHS, popup.level())
        self.assertEqual("Năm 2005", popup.title_button.text())
        popup._cells[2].click()  # Tháng 3
        self.assertEqual(popup.DAYS, popup.level())
        self.assertEqual((2005, 3), (popup.calendar.yearShown(), popup.calendar.monthShown()))

    def test_today_is_marked_in_the_gold_of_the_theme(self):
        popup, _view = self.open_picker()
        for theme in ("light", "dark"):
            self.editor.apply_theme(theme)
            mark = popup.calendar.dateTextFormat(QDate.currentDate())
            self.assertEqual(colors_for(theme)["accent_gold"].lower(),
                             mark.foreground().color().name())
            weekday = popup.calendar.weekdayTextFormat(Qt.Sunday)
            self.assertEqual(colors_for(theme)["text"].lower(), weekday.foreground().color().name())

    def test_alt_down_opens_the_picker_from_the_field(self):
        self.load()
        field = self.editor.fields["ngay_bh"]
        field.setFocus()
        QTest.keyClick(field, Qt.Key_Down, Qt.AltModifier)
        self.settle()
        self.assertTrue(self.editor._date_popup.isVisible())

    def test_a_typed_date_is_stored_as_dd_mm_yyyy(self):
        self.load()
        field = self.editor.fields["ngay_bh"]
        for typed, stored in (("2/8/2017", "02/08/2017"), ("05-09-2018", "05/09/2018"),
                              ("ngày 5 tháng 9 năm 2018", "05/09/2018"), ("không rõ", "không rõ")):
            field.setText(typed)
            field.setModified(True)
            field.editingFinished.emit()
            self.assertEqual(stored, field.text(), typed)

    def test_a_loaded_legacy_date_is_left_alone_until_edited(self):
        self.load(ngay="ngày 02 tháng 8 năm 2017")
        field = self.editor.fields["ngay_bh"]
        field.editingFinished.emit()
        self.assertEqual("ngày 02 tháng 8 năm 2017", field.text())
        self.assertFalse(self.editor.save_btn.isEnabled())
        popup, _view = self.open_picker()
        self.assertEqual(QDate(2017, 8, 2), popup.calendar.selectedDate())


if __name__ == "__main__":
    unittest.main()
