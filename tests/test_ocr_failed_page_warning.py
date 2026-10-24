"""Pages the OCR gate could not recover are shown to the user, not hidden."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main_window_1
from main_window_1 import MainWindow


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *_args):
        self.messages.append(message)


class _Window:
    def __init__(self):
        self.bar = _StatusBar()

    def statusBar(self):
        return self.bar


WARNING = "Chưa nhận dạng được trọn vẹn trang 3 (mô hình lặp vòng)."


class FailedPageWarningTests(unittest.TestCase):
    def test_single_document_warning_is_modal(self):
        window = _Window()
        with patch.object(main_window_1.QMessageBox, "warning") as box:
            shown = MainWindow._warn_incomplete_ocr(
                window, "C:/docs/a.pdf", {"OCR_Warning": WARNING}, modal=True
            )

        self.assertTrue(shown)
        self.assertIn(WARNING, box.call_args[0][2])
        self.assertIn("a.pdf", box.call_args[0][2])

    def test_batch_warning_does_not_block(self):
        window = _Window()
        with patch.object(main_window_1.QMessageBox, "warning") as box, \
             patch("toast_widget.toast_warning") as toast:
            shown = MainWindow._warn_incomplete_ocr(
                window, "C:/docs/b.pdf", {"OCR_Warning": WARNING}, modal=False
            )

        self.assertTrue(shown)
        box.assert_not_called()
        self.assertIn("b.pdf", toast.call_args[0][1])
        self.assertTrue(window.bar.messages)

    def test_complete_document_shows_nothing(self):
        window = _Window()
        with patch.object(main_window_1.QMessageBox, "warning") as box:
            shown = MainWindow._warn_incomplete_ocr(
                window, "C:/docs/c.pdf", {"OCR_Warning": ""}, modal=True
            )

        self.assertFalse(shown)
        box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
