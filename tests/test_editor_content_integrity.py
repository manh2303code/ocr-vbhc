import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from main_window_1 import OCRResultEditor


class EditorContentIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_metadata_save_preserves_raw_chandra_html(self):
        raw_html = (
            '<div data-label="Table"><table><tr><td>Ô A</td>'
            '<td>Ô B</td></tr></table></div>'
        )
        row = (
            1, 9, 1, "CQBH", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Trích yếu", "Nơi nhận", "Chức vụ", "Người ký",
            raw_html, "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        )
        editor = OCRResultEditor(db=None)
        editor.load_data(row)
        editor.fields["so_ki_hieu"].setText("02/CV")

        saved = editor.get_current_data()

        self.assertEqual(raw_html, saved["van_ban_dang_txt"])
        editor.deleteLater()

    def test_changing_urgency_marks_editor_dirty(self):
        editor = OCRResultEditor(db=None)
        editor.load_data((
            1, 9, 1, "CQBH", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Trích yếu", "Nơi nhận", "Chức vụ", "Người ký",
            "Plain OCR body long enough for the loader to recognize as content.",
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))
        self.assertFalse(editor.save_btn.isEnabled())

        editor.fields["do_khan"].setCurrentText("Khẩn")

        self.assertTrue(editor.save_btn.isEnabled())
        editor.deleteLater()

    def test_position_selector_reads_chuc_vu_not_nd_chinh(self):
        editor = OCRResultEditor(db=None)
        editor.load_data((
            1, 9, 1, "CQBH", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "NOI_DUNG_CHINH_KHONG_PHAI_CHUC_VU", "Nơi nhận",
            "KT. GIÁM ĐỐC\nPHÓ GIÁM ĐỐC", "Người ký",
            "Plain OCR body long enough for the loader to recognize as content.",
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))

        editor.chuc_vu_selector.setCurrentText("Chức danh")

        self.assertEqual("PHÓ GIÁM ĐỐC", editor.fields["chuc_vu"].text())
        self.assertNotIn("NOI_DUNG_CHINH", editor.fields["chuc_vu"].text())
        editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
