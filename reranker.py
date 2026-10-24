"""
Reranker Module - Cross-encoder reranking using ViRanker
Uses namdp-ptit/ViRanker (0.6B, BGE-M3 based) for Vietnamese document reranking.
Designed for 100% offline, CPU-only inference with FP16 optimization.
"""
import os
import gc
import logging
from pathlib import Path
from typing import List

import numpy as np

# Offline mode
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

logger = logging.getLogger("Reranker")

RERANKER_MODEL_DIR = Path(__file__).parent / "model_reranker"
# Tokens of (question, passage) the cross-encoder reads. A 480-character
# window (retrieval.RERANK_PASSAGE_CHARS) plus the question is at most 233.
# Measured on the 41-question set (research/token-limits_results.md): 512
# tokens with 1600- or 1200-character windows saw no more answers (35/39
# against 36/39 shown to the model) and took 1.4-2x the CPU prefetch time.
RERANK_MAX_TOKENS = 256


class RerankerModel:
    """Cross-encoder reranker for improving search result quality."""

    def __init__(self, model_dir: str = None):
        self.model_dir = Path(model_dir) if model_dir else RERANKER_MODEL_DIR
        self.model = None
        self.tokenizer = None
        self._is_loaded = False

    def is_available(self) -> bool:
        """Check if model files exist on disk."""
        if not self.model_dir.exists():
            return False
        has_config = (self.model_dir / "config.json").exists()
        has_weights = (
            (self.model_dir / "model.safetensors").exists()
            or (self.model_dir / "pytorch_model.bin").exists()
        )
        return has_config and has_weights

    def load(self) -> bool:
        """Lazy-load model via sentence-transformers CrossEncoder (optimized).

        2026-04-17 perf fix: manual AutoModel path was 33x slower on CPU
        (32s for 10 pairs vs benchmark 961ms). CrossEncoder.predict() uses
        batched tokenization + torch.no_grad() + optimized collate.
        """
        if self._is_loaded and self.model is not None:
            return True

        if not self.is_available():
            logger.warning(
                "Reranker model not found at %s. "
                "Download namdp-ptit/ViRanker to this directory.",
                self.model_dir,
            )
            return False

        try:
            import os as _os
            import torch as _torch
            from sentence_transformers import CrossEncoder
        except ImportError:
            logger.error("sentence_transformers not installed")
            return False

        try:
            # Limit torch CPU threads — rerank is spawned on-demand while
            # llama-server already holds 8 threads for Chandra OCR. Without
            # this cap, PyTorch greedily takes all cores → 50× slowdown from
            # context-switching. Benchmark 2026-04-17: 505ms isolated vs
            # 26000ms production (llama-server competing).
            cpu_count = _os.cpu_count() or 8
            rerank_threads = max(2, min(4, cpu_count // 4))
            _torch.set_num_threads(rerank_threads)
            try:
                _torch.set_num_interop_threads(2)
            except RuntimeError:
                pass  # already set elsewhere — fine
            logger.info(
                f"Reranker torch threads: {_torch.get_num_threads()} intra "
                f"(of {cpu_count} cores — leaving rest for llama-server)"
            )

            # CPU only. The model loads at startup, before Chandra and the
            # chat server take the GPU; on the cards the app's torch supports
            # (RTX 20/30) its fp32 weights would hold about 2.6 GB of VRAM
            # that llama-server needs, and the speed-up (0.62 s against 9.8 s
            # for 20 pairs) was measured only in a newer torch outside the
            # app (research/token-limits_results.md).
            logger.info("Loading reranker (CrossEncoder) from %s...", self.model_dir)
            self.model = CrossEncoder(
                str(self.model_dir),
                device="cpu",
                max_length=RERANK_MAX_TOKENS,
            )
            self.tokenizer = None

            # Warmup: first predict() has JIT overhead — run tiny pair now
            # so first user search doesn't pay this cost.
            try:
                _ = self.model.predict([("warmup", "warmup")], show_progress_bar=False)
            except Exception as we:
                logger.debug(f"Reranker warmup skipped: {we}")

            self._is_loaded = True
            logger.info("Reranker loaded successfully (CrossEncoder path, warmed)")
            return True

        except Exception as e:
            import traceback
            logger.error("Error loading reranker: %s", e)
            logger.error(traceback.format_exc())
            self._is_loaded = False
            self.model = None
            self.tokenizer = None
            return False

    def rerank(self, query: str, texts: List[str]) -> List[float]:
        """
        Rerank texts against query using cross-encoder.

        Args:
            query: search query string
            texts: list of document texts to rerank

        Returns:
            List of scores (0.0 - 1.0), one per text. Higher is more relevant.
        """
        if not self._is_loaded:
            if not self.load():
                return [0.5] * len(texts)

        if not texts:
            return []

        try:
            import numpy as np

            # Truncate texts to first 500 chars for better reranking signal
            truncated = [t[:500] if len(t) > 500 else t for t in texts]
            pairs = [(query, text) for text in truncated]

            # CrossEncoder.predict handles batching + no_grad internally — fast
            raw_scores = self.model.predict(pairs, show_progress_bar=False)
            raw_scores = np.asarray(raw_scores, dtype=np.float64).reshape(-1)

            # Each score on its own, never min-max over the results: that made
            # the first item 100% and the last 0% regardless of relevance.
            # A one-label CrossEncoder already applies a sigmoid in predict();
            # a second one squeezed every score into [0.5, 0.731].
            if self._predicts_probabilities():
                return np.clip(raw_scores, 0.0, 1.0).tolist()
            clipped = np.clip(raw_scores, -60.0, 60.0)
            return (1.0 / (1.0 + np.exp(-clipped))).tolist()

        except Exception as e:
            logger.error("Reranking error: %s", e)
            return [0.5] * len(texts)

    def _predicts_probabilities(self) -> bool:
        """True when the model's predict() ends in a sigmoid (sentence-
        transformers does so for a one-label cross-encoder such as ViRanker);
        otherwise it returns logits and rerank applies the sigmoid itself."""
        activation = getattr(self.model, "activation_fn", None)
        if activation is None:  # sentence-transformers < 4
            activation = getattr(self.model, "default_activation_function", None)
        return type(activation).__name__ == "Sigmoid"

    def unload(self):
        """Release model from memory."""
        if self.model is not None:
            del self.model
            self.model = None
        if self.tokenizer is not None:
            del self.tokenizer
            self.tokenizer = None
        self._is_loaded = False

        gc.collect()

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        logger.info("Reranker model unloaded")
