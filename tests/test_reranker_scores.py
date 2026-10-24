import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from reranker import RERANK_MAX_TOKENS, RerankerModel
from torch_compat import preferred_torch_device


class _Model:
    def predict(self, _pairs, show_progress_bar=False):
        return np.array([-2.0, 2.0], dtype=np.float32)


class RerankerScoreTests(unittest.TestCase):
    def test_scores_use_independent_sigmoid_not_per_result_minmax(self):
        reranker = RerankerModel()
        reranker.model = _Model()
        reranker._is_loaded = True

        scores = reranker.rerank("query", ["first", "second"])

        self.assertAlmostEqual(0.1192, scores[0], places=3)
        self.assertAlmostEqual(0.8808, scores[1], places=3)
        self.assertNotEqual([0.0, 1.0], scores)

    def test_probabilities_from_the_cross_encoder_are_not_squashed_again(self):
        # sentence-transformers ends a one-label CrossEncoder's predict() in a
        # sigmoid; a second one put every score in [0.5, 0.731].
        import torch

        class _ProbabilityModel:
            activation_fn = torch.nn.Sigmoid()

            def predict(self, _pairs, show_progress_bar=False):
                return np.array([0.02, 0.97], dtype=np.float32)

        reranker = RerankerModel()
        reranker.model = _ProbabilityModel()
        reranker._is_loaded = True

        scores = reranker.rerank("query", ["first", "second"])

        self.assertAlmostEqual(0.02, scores[0], places=5)
        self.assertAlmostEqual(0.97, scores[1], places=5)
class RerankerLoadTests(unittest.TestCase):
    def _load(self):
        created = {}

        class _CrossEncoder:
            def __init__(self, path, **kwargs):
                created.update(kwargs, path=path)

            def predict(self, pairs, show_progress_bar=False):
                return np.zeros(len(pairs), dtype=np.float32)

        fake = types.ModuleType("sentence_transformers")
        fake.CrossEncoder = _CrossEncoder
        model = RerankerModel()
        # Swap only this one module: torch, imported by load(), must stay
        # loaded (it cannot be imported twice in one process).
        saved = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = fake
        try:
            with patch.object(RerankerModel, "is_available", return_value=True):
                self.assertTrue(model.load())
        finally:
            if saved is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = saved
        return created

    def test_reads_256_tokens(self):
        # 512 tokens over wider windows was measured and showed the model
        # no more answers at twice the CPU time (token-limits_results.md).
        self.assertEqual(RERANK_MAX_TOKENS, 256)
        self.assertEqual(self._load()["max_length"], 256)

    def test_stays_on_the_cpu_when_torch_supports_the_gpu(self):
        # It loads at startup, before Chandra and the chat server take the
        # GPU: on the cards the app's torch supports (RTX 20/30) its fp32
        # weights would hold ~2.6 GB of VRAM that llama-server needs, and the
        # speed-up was never measured in the app's own runtime.
        with patch("torch.cuda.is_available", return_value=True), \
                patch("torch.cuda.get_device_capability", return_value=(8, 6)), \
                patch("torch.cuda.get_arch_list", return_value=["sm_80", "sm_86"]):
            self.assertEqual(preferred_torch_device(), "cuda")
            self.assertEqual(self._load()["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
