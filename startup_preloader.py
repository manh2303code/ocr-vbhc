"""
Startup model preloader — loads embedding + reranker weights in background
so first semantic search completes fast instead of cold-loading 7-10s.

Runs during intro / MainWindow init on a QThread. Does NOT compete with
Chandra OCR (runs in separate llama-server process) — only CPU spike 5-10%
for ~2-5s while weights unpack, zero GPU usage during load.

After preload:
  - embedding_model._is_loaded = True (or model attr set)
  - reranker._is_loaded = True
  - First user query skips cold-load → ~3-5s instead of 7-10s
"""
import logging
import time

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger("StartupPreloader")


class ModelPreloader(QThread):
    """Background preload of embedding + reranker.

    Safe to overlap with OCR because:
      1. Chandra runs in separate llama-server subprocess — HTTP isolated
      2. This thread only LOADS weights (brief CPU spike, no GPU compute)
      3. Inference happens later via signal-triggered search workers
    """

    ready = pyqtSignal(float)   # total load seconds
    progress = pyqtSignal(str)  # status message for UI/logs
    error = pyqtSignal(str)

    def __init__(self, embedding_model=None, reranker=None):
        super().__init__()
        self.embedding_model = embedding_model
        self.reranker = reranker

    def run(self):
        t0 = time.time()

        # 1) Embedding — check if already loaded
        if self.embedding_model is not None:
            try:
                self.progress.emit("Preloading embedding model...")
                te = time.time()
                already_loaded = (
                    getattr(self.embedding_model, "model", None) is not None
                    or getattr(self.embedding_model, "_is_loaded", False)
                )
                if not already_loaded:
                    # EmbeddingModel typically has .load() method
                    if hasattr(self.embedding_model, "load"):
                        self.embedding_model.load()
                    elif hasattr(self.embedding_model, "encode"):
                        # Trigger lazy load via a no-op encode
                        self.embedding_model.encode("warmup")
                dt = time.time() - te
                logger.info(f"[preload] embedding ready in {dt:.1f}s")
            except Exception as e:
                logger.warning(f"[preload] embedding failed: {e}")
                self.error.emit(f"embedding: {e}")

        # 2) Reranker
        if self.reranker is not None:
            try:
                self.progress.emit("Preloading reranker...")
                tr = time.time()
                if not getattr(self.reranker, "_is_loaded", False):
                    if hasattr(self.reranker, "load"):
                        self.reranker.load()
                dt = time.time() - tr
                logger.info(f"[preload] reranker ready in {dt:.1f}s")
            except Exception as e:
                logger.warning(f"[preload] reranker failed: {e}")
                self.error.emit(f"reranker: {e}")

        total = time.time() - t0
        logger.info(f"[preload] all models ready in {total:.1f}s")
        self.ready.emit(total)
