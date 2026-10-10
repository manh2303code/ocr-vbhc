import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from ocr_pipeline import DocumentOCR
from hardware_detect import HardwareInfo


class _TokenAwareOCR:
    def __init__(self):
        self.callback_results = []

    def ocr_full_page_with_html(self, _image, on_token=None):
        for token_count in (5, 10):
            result = on_token(token_count, "chunk")
            self.callback_results.append(result)
            if result is False:
                break
        return "text", '<div data-label="Text"><p>OCR content</p></div>'


class OCRCancellationTests(unittest.TestCase):
    @staticmethod
    def _engine(fake_ocr):
        engine = DocumentOCR.__new__(DocumentOCR)
        engine.fullpage_ocr = fake_ocr
        engine.ocr_model = fake_ocr
        engine.num_processes = 1
        engine.current_images = []
        engine.processed_images = []
        return engine

    def test_token_callback_propagates_cancel_decision_to_http_stream(self):
        fake_ocr = _TokenAwareOCR()
        engine = self._engine(fake_ocr)

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            Image.new("RGB", (100, 140), "white").save(image_path)

            def progress(_fraction, _total, message=""):
                return False if "tokens" in message else True

            engine.process_document_chandra(str(image_path), progress_callback=progress)

        self.assertEqual([False], fake_ocr.callback_results)

    def test_parallel_cancel_discards_pages_that_have_not_started(self):
        fake_ocr = _TokenAwareOCR()
        engine = self._engine(fake_ocr)
        hardware = HardwareInfo(8, 16, 16000, 12000, "GPU", 24000, 20000, True)

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "document.pdf"
            pdf_path.write_bytes(b"pdf fixture")
            pages = [Image.new("RGB", (100, 140), "white") for _ in range(3)]

            def progress(_fraction, _total, message=""):
                return False if "tokens" in message else True

            with patch("ocr_pipeline.render_pdf_adaptive", return_value=pages), \
                 patch("hardware_detect.detect_hardware", return_value=hardware), \
                 patch("hardware_detect.calculate_optimal_workers", return_value=2):
                result = engine.process_document_chandra(
                    str(pdf_path), progress_callback=progress
                )

        self.assertEqual((None, None), result)
        self.assertIn(False, fake_ocr.callback_results)
        self.assertLess(len(fake_ocr.callback_results), 6)


if __name__ == "__main__":
    unittest.main()
