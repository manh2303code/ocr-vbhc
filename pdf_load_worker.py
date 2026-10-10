"""
Background PDF loading worker.

2026-04-18 fix: previous PDFViewer.load_pdf() called pdf2image's
convert_from_path() synchronously on the main thread. Large PDFs took
1-5s, freezing UI (click selection lagged, cursor juddered). Worker
moves the poppler call off the GUI thread and emits results when ready.

Loading protocol:
    worker = PDFLoadWorker(path, dpi, poppler_path)
    worker.pages_ready.connect(on_pages)     # list[PIL.Image]
    worker.load_failed.connect(on_failed)    # str error
    worker.start()
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger("PDFLoader")


class PDFLoadWorker(QThread):
    """Load PDF pages in the background. Never call GUI methods from run()."""

    pages_ready = pyqtSignal(str, list)  # (pdf_path, list[PIL.Image])
    load_failed = pyqtSignal(str, str)   # (pdf_path, error_message)

    # Image file extensions handled here (load as single-page).
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.webp'}

    def __init__(self, pdf_path: str, dpi: int = 150,
                 poppler_path: Optional[str] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.dpi = dpi
        self.poppler_path = poppler_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if not os.path.exists(self.pdf_path):
                self.load_failed.emit(self.pdf_path, "file not found")
                return

            ext = os.path.splitext(self.pdf_path)[1].lower()

            if ext in self.IMAGE_EXTENSIONS:
                from PIL import Image, ImageOps
                img = Image.open(self.pdf_path).convert('RGB')
                img = ImageOps.exif_transpose(img)
                if self._cancelled:
                    return
                self.pages_ready.emit(self.pdf_path, [img])
                return

            # PDF path — use pdf2image via poppler.
            from pdf2image import convert_from_path
            pages = convert_from_path(
                self.pdf_path,
                dpi=self.dpi,
                poppler_path=self.poppler_path,
            )
            if self._cancelled:
                return
            self.pages_ready.emit(self.pdf_path, pages)

        except Exception as e:
            logger.error(f"PDFLoadWorker failed for {self.pdf_path}: {e}")
            if not self._cancelled:
                self.load_failed.emit(self.pdf_path, str(e))
