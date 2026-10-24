"""DocumentOCR retries a rejected page once, sequentially, then marks it.

A page that still fails is reported in the result (so the UI can warn) and
its reasoning prose never reaches Van_ban_dang_txt, chunks or vectors. One
bad page no longer throws away the other pages of the document.
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from llama_ocr import OCRServerError, PageOCR
from ocr_pipeline import DocumentOCR


def _good(page):
    return (
        '<div data-bbox="10 10 500 40" data-label="Text"><p>Trang {} Số: 12/QĐ-UBND</p></div>'
        .format(page)
    )


PROSE = (
    "The user wants me to complete the document, specifically the table at the "
    "bottom. I need to fill in the table with the appropriate data and then I will "
    "format it as HTML."
)


class _ScriptedOCR:
    """Returns scripted attempts per page; records call order and overlap."""

    def __init__(self, script, delay=0.0):
        self.script = {page: list(attempts) for page, attempts in script.items()}
        self.calls = []
        self.delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self.max_active_during_retry = 0

    def ocr_page(self, image, on_token=None, retry=False):
        page = image.info["page"]
        with self._lock:
            self._active += 1
            self.calls.append((page, retry))
            if retry:
                self.max_active_during_retry = max(
                    self.max_active_during_retry, self._active
                )
        try:
            if self.delay:
                time.sleep(self.delay)
            outcome = self.script[page].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            html, finish = outcome
            return PageOCR(html=html, finish_reason=finish, n_tokens=10)
        finally:
            with self._lock:
                self._active -= 1


def _pages(count):
    images = []
    for number in range(1, count + 1):
        image = Image.new("RGB", (100, 140), "white")
        image.info["page"] = number
        images.append(image)
    return images


def _engine(fake, workers=1):
    engine = DocumentOCR.__new__(DocumentOCR)
    engine.fullpage_ocr = fake
    engine.ocr_model = fake
    engine.num_processes = 1
    engine.current_images = []
    engine.processed_images = []
    engine.ocr_workers = workers
    return engine


def _run(engine, count):
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "document.pdf"
        pdf_path.write_bytes(b"pdf fixture")
        with patch("ocr_pipeline.render_pdf_adaptive", return_value=_pages(count)):
            return engine.process_document_chandra(str(pdf_path))


class PageGatePipelineTests(unittest.TestCase):
    def test_rejected_page_is_retried_once_and_the_retry_is_used(self):
        fake = _ScriptedOCR({
            1: [(_good(1), "stop")],
            2: [(PROSE, "length"), (_good(2), "stop")],
        })

        result, _ = _run(_engine(fake), 2)

        self.assertEqual([(1, False), (2, False), (2, True)], fake.calls)
        self.assertIn("Trang 2", result["Van_ban_dang_txt"])
        self.assertFalse(result.get("OCR_Failed_Pages"))
        self.assertFalse(result.get("OCR_Warning"))

    def test_page_that_fails_twice_is_marked_and_its_prose_is_not_kept(self):
        fake = _ScriptedOCR({
            1: [(_good(1), "stop")],
            2: [(PROSE, "length"), (PROSE, "length")],
            3: [(_good(3), "stop")],
        })

        result, _ = _run(_engine(fake), 3)

        self.assertIsNotNone(result)
        self.assertEqual([2], [item["page"] for item in result["OCR_Failed_Pages"]])
        self.assertEqual("no_html", result["OCR_Failed_Pages"][0]["problem"])
        self.assertIn("2", result["OCR_Warning"])
        text = result["Van_ban_dang_txt"]
        self.assertNotIn("The user wants", text)
        self.assertIn("Trang 1", text)
        self.assertIn("Trang 3", text)
        # Page boundaries survive, so chunks keep their page numbers.
        self.assertEqual(3, text.count("<!--page-orient:"))

    def test_server_error_is_retried(self):
        fake = _ScriptedOCR({
            1: [OCRServerError("Context size has been exceeded."), (_good(1), "stop")],
        })

        result, _ = _run(_engine(fake), 1)

        self.assertIn("Trang 1", result["Van_ban_dang_txt"])
        self.assertEqual([(1, False), (1, True)], fake.calls)

    def test_document_with_no_usable_page_fails(self):
        fake = _ScriptedOCR({1: [(PROSE, "length"), (PROSE, "length")]})

        self.assertEqual((None, None), _run(_engine(fake), 1))

    def test_retries_run_after_the_parallel_pass_one_at_a_time(self):
        script = {page: [(_good(page), "stop")] for page in range(1, 7)}
        script[2] = [(PROSE, "length"), (_good(2), "stop")]
        script[5] = [(PROSE, "length"), (_good(5), "stop")]
        fake = _ScriptedOCR(script, delay=0.02)

        result, _ = _run(_engine(fake, workers=2), 6)

        first_pass = [call for call in fake.calls if not call[1]]
        retries = [call for call in fake.calls if call[1]]
        self.assertEqual(6, len(first_pass))
        self.assertEqual([(2, True), (5, True)], retries)
        self.assertEqual(fake.calls[-2:], retries)
        self.assertEqual(1, fake.max_active_during_retry)
        self.assertFalse(result.get("OCR_Failed_Pages"))


class _StreamingOCR(_ScriptedOCR):
    """Streams a few token callbacks per page, so parallel pages interleave."""

    def ocr_page(self, image, on_token=None, retry=False):
        for step in range(1, 6):
            if on_token is not None:
                on_token(step * 100, "x")
            time.sleep(0.005)
        return super().ocr_page(image, on_token=None, retry=retry)


class ParallelProgressTests(unittest.TestCase):
    """With two workers, pages 3 and 5 stream at the same time; the status
    used to name whichever page sent the last token ("Trang 3/9: 955 tokens",
    "Trang 5/9: 510 tokens", ...) and flickered in the batch window."""

    def test_status_counts_finished_pages_and_only_moves_forward(self):
        import re
        script = {page: [(_good(page), "stop")] for page in range(1, 10)}
        engine = _engine(_StreamingOCR(script), workers=2)
        statuses = []

        def progress(_fraction, _step, message):
            statuses.append(message)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "document.pdf"
            pdf_path.write_bytes(b"pdf fixture")
            with patch("ocr_pipeline.render_pdf_adaptive", return_value=_pages(9)):
                engine.process_document_chandra(str(pdf_path), progress_callback=progress)

        counts = [int(m.group(1)) for m in (re.match(r"Đã xong (\d+)/9 trang$", s) for s in statuses) if m]
        self.assertTrue(counts)
        self.assertEqual(counts, sorted(counts))
        self.assertFalse([s for s in statuses if "token" in s.lower()])


if __name__ == "__main__":
    unittest.main()
