import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from retrieval import RetrievedChunk, hybrid_search, rrf_fuse


class DialogSearchTests(unittest.TestCase):
    def test_dialog_maps_hybrid_chunks_to_existing_result_shape(self):
        from ai_search_dialog import _hybrid_or_legacy_search

        model = MagicMock()
        store = MagicMock(db_path="documents.db")
        vindex = object()
        chunk = RetrievedChunk(7, 3, "đoạn phù hợp", 0.75)
        conn = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = conn

        with patch("ai_search_dialog.sqlite3.connect", return_value=context), \
                patch("ai_search_dialog.hybrid_search", return_value=[chunk]) as search:
            results = _hybrid_or_legacy_search(
                model, store, vindex, "hộ chiếu", reranker=None
            )

        self.assertEqual(results, [(3, 0.75, "đoạn phù hợp")])
        self.assertIs(search.call_args.kwargs["doc_ids"], None)

    def test_dialog_keeps_only_best_chunk_per_document(self):
        from ai_search_dialog import _hybrid_or_legacy_search

        chunks = [
            RetrievedChunk(1, 10, "yếu", 0.4),
            RetrievedChunk(2, 20, "khác", 0.7),
            RetrievedChunk(3, 10, "tốt", 0.9),
        ]
        store = MagicMock(db_path="documents.db")
        context = MagicMock()
        context.__enter__.return_value = MagicMock()

        with patch("ai_search_dialog.sqlite3.connect", return_value=context), \
                patch("ai_search_dialog.hybrid_search", return_value=chunks):
            results = _hybrid_or_legacy_search(
                MagicMock(), store, object(), "hộ chiếu"
            )

        self.assertEqual(results, [(10, 0.9, "tốt"), (20, 0.7, "khác")])

    def test_dialog_falls_back_to_legacy_search_on_hybrid_error(self):
        from ai_search_dialog import _hybrid_or_legacy_search

        model = MagicMock()
        model.encode.return_value = "query-vector"
        store = MagicMock(db_path="documents.db")
        store.search_chunks.return_value = [(4, 0.6, "legacy")]

        with patch("ai_search_dialog.sqlite3.connect", side_effect=RuntimeError("boom")):
            results = _hybrid_or_legacy_search(
                model, store, object(), "hộ chiếu", reranker="reranker"
            )

        self.assertEqual(results, [(4, 0.6, "legacy")])
        store.search_chunks.assert_called_once_with(
            "query-vector", reranker="reranker", query_text="hộ chiếu"
        )


class RrfTests(unittest.TestCase):
    def test_rrf_math_k60(self):
        scores = rrf_fuse([[10, 20], [20, 30]], k=60)

        self.assertAlmostEqual(scores[20], 1 / 62 + 1 / 61)
        self.assertAlmostEqual(scores[10], 1 / 61)
        self.assertAlmostEqual(scores[30], 1 / 62)
        self.assertEqual(max(scores, key=scores.get), 20)


class _FakeEmbed:
    def encode(self, _text):
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        return vector


class _FakeReranker:
    _is_loaded = True

    def is_available(self):
        return True

    def rerank(self, _query, texts):
        count = len(texts)
        return [index / count for index in range(count)][::-1]


class HybridTests(unittest.TestCase):
    def _db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE document_chunks (
                id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
                chunk_text TEXT, embedding BLOB, page_number INTEGER);
            CREATE TABLE documents (id INTEGER PRIMARY KEY, file_name TEXT);
            CREATE TABLE document_versions (
                id INTEGER PRIMARY KEY, document_id INTEGER, version_number INTEGER,
                so_ki_hieu TEXT, loai_vb TEXT, ngay_bh TEXT);
        """)
        import search_store

        search_store.init_fts(conn.cursor())
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        conn.execute(
            "INSERT INTO document_chunks VALUES "
            "(1, 1, 0, 'cấp hộ chiếu ngoại giao', ?, 2)",
            (vector.tobytes(),),
        )
        conn.execute("INSERT INTO documents VALUES (1, 'a.pdf')")
        conn.execute(
            "INSERT INTO document_versions VALUES "
            "(1, 1, 1, '12/QĐ-UBND', 'Quyết định', '01/02/2024')"
        )
        conn.commit()
        return conn

    def test_hybrid_returns_metadata_and_page(self):
        conn = self._db()
        from vector_index import VectorIndex

        vindex = VectorIndex(conn, force_fallback=True)
        vindex.sync_from_chunks(conn)
        out = hybrid_search(
            conn, vindex, _FakeEmbed(), _FakeReranker(), "hộ chiếu", top_k=5
        )

        self.assertEqual(len(out), 1)
        result = out[0]
        self.assertIsInstance(result, RetrievedChunk)
        self.assertEqual(
            (result.doc_id, result.so_ki_hieu, result.page_number),
            (1, "12/QĐ-UBND", 2),
        )

    def test_unaccented_query_still_finds_the_chunk(self):
        conn = self._db()
        from vector_index import VectorIndex

        class _NoVector(_FakeEmbed):
            def encode(self, _text):
                return None

        vindex = VectorIndex(conn, force_fallback=True)
        vindex.sync_from_chunks(conn)
        out = hybrid_search(conn, vindex, _NoVector(), None, "ho chieu", top_k=5)

        self.assertEqual([chunk.doc_id for chunk in out], [1])

    def test_reranker_reads_the_part_of_the_chunk_the_question_is_about(self):
        conn = self._db()
        long_text = ("Phần mở đầu chung. " * 80) + "Hạn nộp hồ sơ hộ chiếu là ngày 30/4." + (" Phụ lục." * 60)
        conn.execute("UPDATE document_chunks SET chunk_text = ? WHERE id = 1", (long_text,))
        conn.commit()
        from vector_index import VectorIndex

        seen = []

        class _Recorder(_FakeReranker):
            def rerank(self, query, texts):
                seen.extend(texts)
                return super().rerank(query, texts)

        vindex = VectorIndex(conn, force_fallback=True)
        vindex.sync_from_chunks(conn)
        hybrid_search(conn, vindex, _FakeEmbed(), _Recorder(), "hạn nộp hồ sơ hộ chiếu", top_k=5)

        self.assertTrue(seen)
        self.assertIn("30/4", seen[0])
        self.assertLessEqual(len(seen[0]), 500)


if __name__ == "__main__":
    unittest.main()
