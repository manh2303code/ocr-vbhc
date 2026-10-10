"""
Adaptive PDF rendering for OCR pipeline.

Purpose: landscape pages (common for wide tables with many columns) have
inherently lower char-per-pixel density at the default DPI. This module
detects landscape orientation via image aspect ratio and re-renders only
those pages at a higher DPI (200) so the OCR model sees enough detail.

Design rules (from user feedback 2026-04-17):
  1. Edge-case fix only — portrait pages get IDENTICAL behavior as before
     (single convert_from_path call at 150 DPI, zero extra latency).
  2. Landscape pages re-rendered at 200 DPI per-page (via first_page/last_page).
  3. Preserve list order. Caller gets a list[PIL.Image] matching PDF page order.

No PyQt5 import. No hidden state. Pure function — testable via mocks.
"""
import logging
from pathlib import Path
from typing import List, Optional

from PIL import Image
from pdf2image import convert_from_path

logger = logging.getLogger("PdfRendering")

DEFAULT_DPI = 150
LANDSCAPE_DPI = 200


def is_landscape_image(img: Image.Image) -> bool:
    """True iff image width is strictly greater than height.

    Square images treated as portrait (default behavior — no re-render).
    """
    w, h = img.size
    return w > h


def render_pdf_adaptive(
    pdf_path: str,
    poppler_path: Optional[str] = None,
    thread_count: int = 1,
) -> List[Image.Image]:
    """Render PDF pages with adaptive DPI for landscape pages.

    Portrait-only docs get exactly 1 convert_from_path call at 150 DPI
    (no regression vs old behavior). Landscape pages trigger one additional
    per-page convert_from_path call at 200 DPI; only those pages are upgraded.

    Args:
        pdf_path: path to PDF file
        poppler_path: path to poppler bin dir (Windows)
        thread_count: poppler thread count for batch render

    Returns:
        list[PIL.Image] in original page order
    """
    # Pass 1: render ALL pages at default DPI
    pages = convert_from_path(
        pdf_path,
        dpi=DEFAULT_DPI,
        poppler_path=poppler_path,
        thread_count=thread_count,
    )
    if not pages:
        return []

    # Pass 2: detect landscape, re-render only those at higher DPI
    any_landscape = False
    for i, img in enumerate(pages):
        if is_landscape_image(img):
            any_landscape = True
            page_num = i + 1  # pdf2image uses 1-based page indexing
            hi_res = convert_from_path(
                pdf_path,
                dpi=LANDSCAPE_DPI,
                first_page=page_num,
                last_page=page_num,
                poppler_path=poppler_path,
                thread_count=thread_count,
            )
            if hi_res:
                pages[i] = hi_res[0]
                logger.info(
                    f"[landscape] re-rendered page {page_num}/{len(pages)} "
                    f"at {LANDSCAPE_DPI} DPI ({img.size} → {hi_res[0].size})"
                )

    if not any_landscape:
        logger.debug(f"[portrait] {len(pages)} pages, single-pass {DEFAULT_DPI} DPI")
    return pages
