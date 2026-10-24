import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPalette, QTextFormat
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QSplitter, QWidget

from analytics_dialog import AnalyticsDialog
from display_html_renderer import build_display_html
from main_window_1 import OCRResultEditor
from theme_manager_v2 import ThemeManager
from theme_tokens import COLORS, DARK_COLORS, colors_for


class FrontendDensityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_sets_document_content_in_times_and_chrome_in_sans(self):
        """2026-10 brief: the full text and the metadata values read like the
        paper (Times New Roman, 10.5-11pt); labels and controls stay sans.
        Still compact: a one-line value is one line high, the action bar thin."""
        ThemeManager.instance().apply(self.app, "light")
        editor = OCRResultEditor(db=None)
        editor.resize(420, 900)
        editor.show()
        self.app.processEvents()
        rendered = build_display_html('<div data-label="Text"><p>Nội dung OCR</p></div>')

        self.assertIn("Times New Roman", rendered)
        self.assertNotIn("Be Vietnam Pro", rendered)
        for key in ("so_ki_hieu", "cqbh", "nd_chinh", "do_khan", "van_ban_dang_txt"):
            font = editor.fields[key].font()
            self.assertEqual("Times New Roman", font.family(), key)
            self.assertGreaterEqual(font.pointSizeF(), 10.5, key)
            self.assertLessEqual(font.pointSizeF(), 11.0, key)
        label = editor._metadata_labels["Số hiệu"]
        self.assertEqual("Be Vietnam Pro", label.font().family())
        self.assertLessEqual(label.font().pointSizeF(), 9.0)
        self.assertLessEqual(editor.fields["so_ki_hieu"].height(), 30)
        self.assertLessEqual(editor.findChild(QFrame, "ocr_action_bar").height(), 46)
        editor.close()

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
        """The cached OCR HTML carries no text colour of its own, so the text
        takes the widget's token colour in either theme; the header number is
        the gold mark of the theme."""
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        editor = OCRResultEditor(db=None)
        editor.show()
        editor.load_data((
            1, 9, 1, "Cơ quan", "", "01/CV", "11/07/2026", "Không", "Không",
            "Công văn", "Nội dung chính", "Nơi nhận", "Chức vụ", "Người ký",
            '<div data-label="Text"><p>Nội dung OCR tiếng Việt</p></div>',
            "OCR System", "2026-07-11 10:00:00", "chandra-q4",
        ))
        ocr = editor.fields["van_ban_dang_txt"]

        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            cursor = ocr.document().find("Nội dung OCR")
            self.assertFalse(cursor.isNull())
            self.assertFalse(cursor.charFormat().hasProperty(QTextFormat.ForegroundBrush))
            self.assertEqual(tokens["text"].lower(), ocr.palette().color(QPalette.Text).name())
            number = editor._doc_number_label
            self.assertEqual("01/CV", number.full_text())
            self.assertEqual(tokens["accent_gold"].lower(),
                             number.palette().color(QPalette.WindowText).name())
        rendered = build_display_html('<div data-label="Text"><p>Nội dung OCR</p></div>').lower()
        self.assertNotIn(COLORS["text"].lower(), rendered)
        self.assertNotIn(DARK_COLORS["text"].lower(), rendered)
        editor.close()

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
        self.assertLessEqual(editor.fields["so_ki_hieu"].height(), 30)
        # Multi-line values are as tall as their lines, all of them visible.
        self.assertLess(editor.fields["nd_chinh"].height(), editor.fields["noi_nhan"].height())
        self.assertFalse(editor.fields["noi_nhan"].is_capped())

        labels = {
            label.text(): label for label in form.findChildren(QLabel)
            if label.property("role") == "metadataLabel"
        }
        # Rows never overlap, with air between them, in the order of the paper.
        rows = ("cqbh", "so_ki_hieu", "ngay_bh", "do_khan", "do_mat", "loai_vb",
                "nd_chinh", "noi_nhan", "chuc_vu", "chu_ky")
        for upper, lower in zip(rows, rows[1:]):
            gap = (editor.fields[lower].geometry().top()
                   - editor.fields[upper].geometry().bottom())
            self.assertGreaterEqual(gap, 3, (upper, lower))
        # A label sits on the line of its value, never on another row.
        for text, key in (("Số hiệu", "so_ki_hieu"), ("Độ khẩn", "do_khan")):
            value = editor.fields[key].geometry()
            label_top = labels[text].mapTo(form, labels[text].rect().topLeft()).y()
            self.assertLessEqual(abs(label_top - value.top()), 6, text)
        self.assertEqual("", labels["Số hiệu"].styleSheet())
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
        """Opened in the dark theme, then switched live to the light one.
        The switch goes through the ThemeManager as in the app: the colours
        come from the generated stylesheet now, not from a palette the
        dialog used to set on every child by itself."""
        manager = ThemeManager.instance()
        manager.apply(self.app, "dark")
        dialog = AnalyticsDialog(db=None)
        dialog.show()
        self.app.processEvents()

        dark_base = dialog.results_table.palette().color(QPalette.Base)
        dark_text = dialog.results_table.palette().color(QPalette.Text)
        self.assertLess(self._luminance(dark_base), 0.2)
        self.assertGreaterEqual(self._contrast(dark_base, dark_text), 4.5)

        manager.apply(self.app, "light")
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
