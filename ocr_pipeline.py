"""Local Chandra OCR pipeline, independent from the Qt presentation layer."""
import logging
import multiprocessing as mp
import time
from pathlib import Path

from PIL import Image

from app_config import (
    ACTIVE_OCR_MODEL, CHANDRA_PORT, LLAMA_SERVER_HOST, get_poppler_path,
    select_backend,
)
from llama_ocr import (
    PAGE_PROBLEM_LABELS, PAGE_PROBLEM_SEVERITY, LlamaOCR, PageOCR,
    assess_page, salvage_page,
)
from llama_server_manager import LlamaServerManager
from pdf_rendering import render_pdf_adaptive


logger = logging.getLogger("OCRPipeline")
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
POPPLER_PATH = str(get_poppler_path() or "")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
LLAMA_AVAILABLE = True


def has_complete_page_ocr(page_htmls):
    """A document is valid only when every rendered page returned content."""
    return bool(page_htmls) and all(str(page or "").strip() for page in page_htmls)


def describe_failed_pages(failed):
    """User-facing warning for pages the quality gate could not recover."""
    if not failed:
        return ""
    parts = [
        "trang {} ({})".format(item["page"], item["label"]) for item in failed
    ]
    return (
        "Chưa nhận dạng được trọn vẹn {}. Nội dung lỗi của các trang này không "
        "được lưu vào văn bản và chỉ mục tìm kiếm; hãy kiểm tra lại và bấm "
        "\"OCR lại\".".format(", ".join(parts))
    )


class DocumentOCR:
    """Chandra OCR 2 pipeline — Q4 full-page OCR via llama-server + HTML parser.

    Replaces the 2026-02 YOLO+Vintern+Chandra hybrid with a single-engine
    flow. chandra_field_parser extracts all 12 NĐ30 fields from the Chandra
    HTML output directly — no per-region detection needed.

    Legacy `process_document` (YOLO region-detect → Vintern per-region →
    Chandra full-page) was removed 2026-04-17 along with the supporting
    modules (vintern_ocr, model_manager, ocr_postprocess). All OCR traffic
    now flows through `process_document_chandra` below.
    """

    def __init__(self, model_path=None):
        # model_path retained only for signature compat with older callers;
        # the Chandra pipeline needs no YOLO model file. Safe to pass None.
        self.fullpage_ocr = None
        self._chandra_server = None
        # Poppler thread count for PDF-to-image rendering (used by
        # render_pdf_adaptive in process_document_chandra). Cap at cpu-1 to
        # leave 1 core for the GUI.
        self.num_processes = max(1, mp.cpu_count() - 1)
        self._init_hybrid()
        self.ocr_model = self.fullpage_ocr  # back-compat alias for workers
        self.lang = 'vie'
        # Filled per-OCR-run by process_document_chandra; init empty so any
        # early read before OCR doesn't AttributeError.
        self.current_images = []
        self.processed_images = []
        self.image_save_dir = OUTPUT_DIR / 'ocr_images'
        self.image_save_dir.mkdir(exist_ok=True, parents=True)
        (self.image_save_dir / 'original').mkdir(exist_ok=True)
        (self.image_save_dir / 'processed').mkdir(exist_ok=True)

    def _init_hybrid(self):
        """Start Chandra Q4 llama-server. No fallback — Vintern legacy removed.

        The worker count is decided once, here, and the server gets exactly
        that many slots: each page in flight owns its slot's context instead
        of sharing one pool with the others.
        """
        if not LLAMA_AVAILABLE:
            raise RuntimeError(
                "llama-server modules unavailable. Required: "
                "llama_ocr, llama_server_manager, app_config."
            )
        from hardware_detect import detect_hardware, calculate_optimal_workers

        backend = select_backend()
        # CPU: llama.cpp already uses every thread; parallel pages only thrash.
        workers = calculate_optimal_workers(detect_hardware()) if backend == "cuda" else 1
        self.ocr_workers = max(1, int(workers))
        chandra_url = f"http://{LLAMA_SERVER_HOST}:{CHANDRA_PORT}"
        logger.info(
            f"Khởi tạo Chandra OCR 2 Q4 (port {CHANDRA_PORT}, {backend}, "
            f"{self.ocr_workers} workers)..."
        )
        self._chandra_server = LlamaServerManager(port=CHANDRA_PORT)
        self._chandra_server.start(
            timeout=120, backend=backend, model_key=ACTIVE_OCR_MODEL,
            parallel_slots=self.ocr_workers,
        )
        self.fullpage_ocr = LlamaOCR(base_url=chandra_url)
        logger.info(f"✓ Chandra OCR 2 Q4 sẵn sàng (port {CHANDRA_PORT})")

    def process_document_chandra(self, pdf_path, progress_callback=None):
        """Process PDF using Chandra-only pipeline — no YOLO, no Vintern.

        Smart parallel OCR: detects hardware, calculates optimal worker count,
        OCRs multiple pages in parallel using llama-server slots.

        2026-04-18 latency fix: emit progress events BEFORE each heavy
        step (PDF render, image encode, HTTP upload, server prompt eval).
        Previously user saw no progress for 2-6s between click and first
        token. Now they see "Đang tải PDF..." immediately.
        """
        import traceback
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from PIL import ImageOps
        from chandra_field_parser import parse_chandra_html
        from hardware_detect import detect_hardware, calculate_optimal_workers

        start_time = time.time()

        try:
            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                logger.error(f"File not found: {pdf_path}")
                return None, None

            # EARLY PROGRESS: tell user app is responding RIGHT NOW.
            # Prevents "app looks frozen" perception during PDF render.
            if progress_callback:
                progress_callback(0.005, 1, f"Đang tải {pdf_path.name}...")

            # Load file → images
            if pdf_path.suffix.lower() in IMAGE_EXTENSIONS:
                logger.info(f"Đang tải hình ảnh: {pdf_path.name}")
                img = Image.open(str(pdf_path)).convert('RGB')
                img = ImageOps.exif_transpose(img)
                MAX_PIXELS = 25_000_000
                w, h = img.size
                if w * h > MAX_PIXELS:
                    scale = (MAX_PIXELS / (w * h)) ** 0.5
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                images = [img]
            else:
                logger.info(f"Đang chuyển đổi PDF sang ảnh...")
                if progress_callback:
                    progress_callback(0.01, 1, "Đang chuyển PDF → ảnh...")
                # Use adaptive DPI: landscape pages (wide tables) get 200 DPI
                # for better char detail; portrait pages stay at 150 (no regression).
                # Import moved to module-top (perf fix 2026-04-18: was in hot
                # function, saved 1-2ms per OCR via avoiding sys.path rescan).
                images = render_pdf_adaptive(
                    str(pdf_path),
                    poppler_path=POPPLER_PATH,
                    thread_count=self.num_processes,
                )

            self.current_images = images
            self.processed_images = images
            pages_count = len(images)

            # Workers = server slots, fixed when the server started; never
            # send more pages at once than there are slots.
            n_workers = getattr(self, "ocr_workers", None)
            if not n_workers:
                n_workers = calculate_optimal_workers(detect_hardware())
            # Single page → always 1 worker
            n_workers = max(1, min(n_workers, pages_count))
            logger.info(f"OCR {pages_count} trang, {n_workers} workers (Chandra Q4)")

            _fp_model = self.fullpage_ocr or self.ocr_model
            page_widths = [img.size[0] for img in images]
            total_steps = pages_count + 1

            # ── Progress tracking (thread-safe) ──
            import threading
            _progress_lock = threading.Lock()
            _cancel_event = threading.Event()
            _tokens_per_page = {}  # page_num → n_tokens
            _done_pages = set()

            def _status(page_num):
                """One line that only moves forward. With several workers the
                pages stream at the same time, and naming the page whose token
                arrived last made the text flick between them ("Trang 3/9:
                955 tokens", "Trang 5/9: 510 tokens", ...)."""
                if n_workers > 1:
                    return "Đã xong {}/{} trang".format(len(_done_pages), pages_count)
                return "Đang đọc trang {}/{}".format(page_num, pages_count)

            def _make_token_cb(page_num):
                est_tokens = 1200
                def _on_token(n_tok, _chunk):
                    if _cancel_event.is_set():
                        return False
                    with _progress_lock:
                        _tokens_per_page[page_num] = n_tok
                    if progress_callback:
                        with _progress_lock:
                            # Sum progress across all pages
                            total_frac = sum(
                                min(0.95, t / est_tokens)
                                for t in _tokens_per_page.values()
                            )
                            status = _status(page_num)
                        overall = min(0.95, total_frac / total_steps)
                        keep_going = progress_callback(overall, 1, status)
                        if keep_going is False:
                            _cancel_event.set()
                        return keep_going
                    return True
                return _on_token

            def _ocr_page(page_num, pil_img, retry=False):
                """OCR a single page and judge it — called from thread pool.

                Returns (page_num, PageOCR); problem is set when the page
                fails the quality gate (llama_ocr.assess_page).
                """
                if _cancel_event.is_set():
                    return page_num, PageOCR("")
                try:
                    on_token = _make_token_cb(page_num)
                    if hasattr(_fp_model, 'ocr_page'):
                        page = _fp_model.ocr_page(pil_img, on_token=on_token, retry=retry)
                    elif hasattr(_fp_model, 'ocr_full_page_with_html'):
                        _, raw_html = _fp_model.ocr_full_page_with_html(pil_img, on_token=on_token)
                        page = PageOCR(raw_html or "")
                    else:
                        page = PageOCR(_fp_model.ocr_full_page_like_manual(pil_img) or "")
                    if page.problem is None and not _cancel_event.is_set():
                        page.problem = assess_page(page.html, page.finish_reason)
                except Exception as e:
                    logger.error(f"Chandra trang {page_num} lỗi: {e}")
                    page = PageOCR("", problem="error")
                logger.info(
                    f"Trang {page_num}{' (thử lại)' if retry else ''}: "
                    f"{len(page.html or '')} chars HTML, finish={page.finish_reason}, "
                    f"problem={page.problem}"
                )
                with _progress_lock:
                    _done_pages.add(page_num)
                return page_num, page

            # ── Execute OCR ──
            # Show "server warming up" message — user sees activity during
            # 1-2s prompt eval / image encoding window before first token.
            if progress_callback:
                progress_callback(
                    0.02, 1,
                    f"Đang gửi ảnh trang 1/{pages_count} đến server Chandra..."
                )

            if n_workers <= 1:
                # Sequential — simple loop (1 worker or 1 page).
                # Check progress_callback return value to respect user cancel —
                # previously ignored, causing worker to run to completion even
                # after user hit "Hủy", which then triggered terminate() that
                # corrupted the shared httpx.Client → next OCR returned "".
                page_results = []
                for page_num, pil_img in enumerate(images, 1):
                    if progress_callback:
                        keep_going = progress_callback(
                            (page_num - 1) / total_steps, 1,
                            f"Chandra OCR trang {page_num}/{pages_count}..."
                        )
                        if keep_going is False:
                            logger.info(f"OCR canceled before page {page_num} — exiting")
                            return None, None
                    _, page = _ocr_page(page_num, pil_img)
                    if _cancel_event.is_set():
                        logger.info(f"OCR canceled during page {page_num} — exiting")
                        return None, None
                    page_results.append(page)
            else:
                # Parallel — ThreadPoolExecutor with N workers
                logger.info(f"Parallel OCR: {n_workers} workers for {pages_count} pages")
                page_results = [PageOCR("", problem="empty")] * pages_count
                with ThreadPoolExecutor(max_workers=n_workers) as executor:
                    futures = {
                        executor.submit(_ocr_page, i + 1, img): i
                        for i, img in enumerate(images)
                    }
                    for future in as_completed(futures):
                        page_num, page = future.result()
                        page_results[page_num - 1] = page
                        if _cancel_event.is_set():
                            for pending in futures:
                                pending.cancel()
                            logger.info("Parallel OCR canceled — pending pages discarded")
                            return None, None

            # ── Quality gate: retry rejected pages once, one at a time ──
            # Sequential so the retry has the server to itself (a page that
            # overflowed a shared context or timed out under load gets a
            # clean second chance).
            rejected = [n for n, page in enumerate(page_results, 1) if page.problem]
            for page_num in rejected:
                first = page_results[page_num - 1]
                if progress_callback:
                    keep_going = progress_callback(
                        pages_count / total_steps, 1,
                        f"Thử lại trang {page_num}/{pages_count} "
                        f"({PAGE_PROBLEM_LABELS.get(first.problem, first.problem)})..."
                    )
                    if keep_going is False:
                        _cancel_event.set()
                if _cancel_event.is_set():
                    logger.info("OCR canceled before retrying page %d — exiting", page_num)
                    return None, None
                logger.warning(
                    "Trang %d bị loại (%s, finish=%s) — thử lại một lần",
                    page_num, first.problem, first.finish_reason,
                )
                _, second = _ocr_page(page_num, images[page_num - 1], retry=True)
                if _cancel_event.is_set():
                    logger.info(f"OCR canceled during retry of page {page_num} — exiting")
                    return None, None
                # Keep the more usable attempt; ties go to the retry.
                if (PAGE_PROBLEM_SEVERITY.get(second.problem, 5)
                        <= PAGE_PROBLEM_SEVERITY.get(first.problem, 5)):
                    page_results[page_num - 1] = second

            failed_pages = []
            all_page_htmls = []
            for page_num, page in enumerate(page_results, 1):
                if page.problem:
                    label = PAGE_PROBLEM_LABELS.get(page.problem, page.problem)
                    failed_pages.append(
                        {"page": page_num, "problem": page.problem, "label": label}
                    )
                    logger.error(
                        "Trang %d vẫn lỗi sau khi thử lại: %s (finish=%s)",
                        page_num, page.problem, page.finish_reason,
                    )
                    # Never index reasoning prose or a loop as document text.
                    all_page_htmls.append(salvage_page(page.html, page.problem))
                else:
                    all_page_htmls.append(page.html or "")

            if not any(html.strip() for html in all_page_htmls):
                logger.error("OCR không trang nào dùng được (%d trang)", pages_count)
                return None, None

            # ── Parse HTML → 12 NĐ30 fields ──
            if progress_callback:
                progress_callback(0.95, 1, "Đang trích xuất trường thông tin...")

            logger.info("=" * 60)
            logger.info(f"PARSING {len(all_page_htmls)} pages → 12 NĐ30 fields")
            # Compute per-page orientation — landscape pages get <!--page-orient:landscape-->
            # markers embedded in van_ban_dang_txt so DOCX export emits section breaks.
            orientations = [
                ("landscape" if img.size[0] > img.size[1] else "portrait")
                for img in images
            ]
            final_results = parse_chandra_html(
                all_page_htmls, page_widths=page_widths, orientations=orientations
            )
            filled = sum(1 for v in final_results.values() if v)
            logger.info(f"Parsed fields: {filled}/12 non-empty | orient: {''.join('L' if o=='landscape' else 'P' for o in orientations)}")
            # Visible to the caller (UI warning); not a document field.
            final_results["OCR_Failed_Pages"] = failed_pages
            final_results["OCR_Warning"] = describe_failed_pages(failed_pages)
            if failed_pages:
                logger.warning(final_results["OCR_Warning"])

            if progress_callback:
                progress_callback(1.0, 1, "Hoàn thành!")

            total_time = time.time() - start_time
            logger.info(f"OCR hoàn thành trong {total_time:.1f}s "
                       f"({pages_count} trang, {n_workers} workers)")

            # Clear cached images to free RAM (bug fix 2026-04-18).
            # Previously `self.current_images = images` held references
            # indefinitely → 10 PDFs × 5 pages × 3MB = 150MB leak.
            # Caller gets the final_results; raw images no longer needed.
            self.current_images = []
            self.processed_images = []

            return final_results, [[] for _ in range(pages_count)]

        except Exception as e:
            logger.error(f"Document processing error: {e}")
            logger.error(traceback.format_exc())
            return None, None
