import unittest

import numpy as np

from reranker import RerankerModel


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


if __name__ == "__main__":
    unittest.main()
