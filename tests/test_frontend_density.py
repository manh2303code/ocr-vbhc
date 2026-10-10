import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QSplitter, QWidget

from analytics_dialog import AnalyticsDialog
from display_html_renderer import build_display_html
from main_window_1 import OCRResultEditor
from theme_manager_v2 import ThemeManager


class FrontendDensityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_uses_one_compact_typography_system(self):
        editor = OCRResultEditor(db=None)
        rendered = build_display_html('<div data-label="Text"><p>Nội dung OCR</p></div>')

        self.assertIn("Be Vietnam Pro", rendered)
        self.assertNotIn("Times New Roman", rendered)
        self.assertEqual("Be Vietnam Pro", editor.fields["so_ki_hieu"].font().family())
        self.assertEqual("Be Vietnam Pro", editor.fields["van_ban_dang_txt"].font().family())
        self.assertLessEqual(editor.fields["so_ki_hieu"].font().pointSizeF(), 9.0)
        self.assertLessEqual(editor.fields["so_ki_hieu"].maximumHeight(), 26)
        self.assertLessEqual(editor.scroll_area.maximumHeight(), 370)
        self.assertGreaterEqual(editor.scroll_area.minimumHeight(), 230)
        self.assertLessEqual(editor.findChild(QFrame, "ocr_action_bar").height(), 46)
        editor.deleteLater()

    def test_loading_document_returns_metadata_to_top(self):
        editor = OCRResultEditor(db=None)
        editor.resize(720, 900)
        editor.show()
        self.app.processEvents()
        editor.scroll_area.verticalScrollBar().setValue(
            editor.scroll_area.verticalScrollBar().maximum()
        )
        editor.load_data((
            1, 9, 1, "Cơ quan", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Nội dung chính", "Nơi nhận", "Chức vụ", "Người ký",
            "Plain OCR body long enough for the loader to recognize as content.",
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))
        self.app.processEvents()
        self.assertEqual(0, editor.scroll_area.verticalScrollBar().value())
        editor.close()

    def test_dark_theme_recolors_cached_ocr_html(self):
        editor = OCRResultEditor(db=None)
        editor.load_data((
            1, 9, 1, "Cơ quan", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Nội dung chính", "Nơi nhận", "Chức vụ", "Người ký",
            '<div data-label="Text"><p>Nội dung OCR tiếng Việt</p></div>',
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))

        editor.apply_theme("dark")
        cursor = editor.fields["van_ban_dang_txt"].document().find("Nội dung OCR")
        self.assertFalse(cursor.isNull())
        self.assertEqual("#e8ebf5", cursor.charFormat().foreground().color().name())
        self.assertIn("#E5C466", editor._file_label.text())
        self.assertNotIn("#17231b", build_display_html(
            '<div data-label="Text"><p>Nội dung OCR</p></div>'
        ).lower())
        editor.apply_theme("light")
        cursor = editor.fields["van_ban_dang_txt"].document().find("Nội dung OCR")
        self.assertEqual("#17231b", cursor.charFormat().foreground().color().name())
        editor.deleteLater()

    def test_metadata_form_spacing_and_text_areas_fit_content(self):
        ThemeManager.instance().apply(self.app, "light")
        editor = OCRResultEditor(db=None)
        editor.resize(760, 1050)
        editor.show()
        editor.load_data((
            1, 9, 1, "Cơ quan", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Một dòng", "Dòng một\nDòng hai\nDòng ba", "Chức vụ", "Người ký",
            "Plain OCR body long enough for the loader to recognize as content.",
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))
        self.app.processEvents()

        form = editor.findChild(QWidget, "ocrMetadataForm")
        self.assertIsNotNone(form)
        self.assertEqual("ocrMetadataScroll", editor.scroll_area.objectName())
        self.assertLessEqual(editor.fields["so_ki_hieu"].height(), 24)
        self.assertLess(editor.fields["nd_chinh"].height(), editor.fields["noi_nhan"].height())
        self.assertLessEqual(editor.fields["noi_nhan"].height(), 68)

        labels = {
            label.text(): label for label in form.findChildren(QLabel)
            if label.property("role") == "metadataLabel"
        }
        first_bottom = editor.fields["so_ki_hieu"].geometry().bottom()
        self.assertGreaterEqual(labels["Độ khẩn"].geometry().top() - first_bottom, 5)
        self.assertNotIn("color:", labels["Số hiệu"].styleSheet().lower())
        editor.close()

    def test_analytics_sidebar_and_rows_are_laptop_compact(self):
        dialog = AnalyticsDialog(db=None)
        splitter = dialog.findChild(QSplitter, "analyticsSplitter")
        sidebar = dialog.findChild(QWidget, "analyticsSearchPanel")

        self.assertIsNotNone(splitter)
        self.assertIsNotNone(sidebar)
        self.assertLessEqual(sidebar.maximumWidth(), 340)
        self.assertGreaterEqual(sidebar.minimumWidth(), 280)
        self.assertEqual("dd/MM/yyyy", dialog.date_from.displayFormat())
        self.assertLessEqual(dialog.results_table.verticalHeader().defaultSectionSize(), 26)
        self.assertLessEqual(dialog.font().pointSizeF(), 9.0)
        dialog.deleteLater()

    def test_analytics_palette_remains_readable_in_both_themes(self):
        manager = ThemeManager.instance()
        manager.apply(self.app, "dark")
        dialog = AnalyticsDialog(db=None)
        dialog.show()
        self.app.processEvents()

        dark_base = dialog.results_table.palette().color(QPalette.Base)
        dark_text = dialog.results_table.palette().color(QPalette.Text)
        self.assertLess(self._luminance(dark_base), 0.2)
        self.assertGreaterEqual(self._contrast(dark_base, dark_text), 4.5)

        dialog.apply_theme("light")
        self.app.processEvents()
        light_base = dialog.results_table.palette().color(QPalette.Base)
        light_text = dialog.results_table.palette().color(QPalette.Text)
        self.assertGreater(self._luminance(light_base), 0.8)
        self.assertGreaterEqual(self._contrast(light_base, light_text), 4.5)
        dialog.close()

    @staticmethod
    def _luminance(color):
        channels = []
        for value in (color.redF(), color.greenF(), color.blueF()):
            channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    @classmethod
    def _contrast(cls, first, second):
        high, low = sorted((cls._luminance(first), cls._luminance(second)), reverse=True)
        return (high + 0.05) / (low + 0.05)


if __name__ == "__main__":
    unittest.main()
