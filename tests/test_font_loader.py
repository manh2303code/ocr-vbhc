import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QFontDatabase, QImage, QPainter
from PyQt5.QtWidgets import QApplication

from font_loader import FONT_FAMILY, load_application_fonts


class FontLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bundled_font_registers_and_renders_vietnamese(self):
        loaded = load_application_fonts(self.app)
        self.assertIn(FONT_FAMILY, loaded)
        self.assertIn(FONT_FAMILY, QFontDatabase().families())

        image = QImage(520, 90, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setPen(QColor("black"))
        painter.setFont(self.app.font())
        painter.drawText(10, 55, "Văn bản hành chính")
        painter.end()

        dark_pixels = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if QColor(image.pixel(x, y)).lightness() < 100
        )
        self.assertGreater(dark_pixels, 100)


if __name__ == "__main__":
    unittest.main()
