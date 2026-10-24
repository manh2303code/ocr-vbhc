import sqlite3
import unittest

import numpy as np

from ai_search import chunk_pages, chunk_text, embed_document_chunks


class _EmbeddingModel:
    def encode(self, _text):
        return np.ones(1024, dtype=np.float32)


class _VectorStore:
    def __init__(self):
        self.chunks = None

    def upsert_chunks(self, _doc_id, chunks):
        self.chunks = chunks


class VersionRowIndexingTests(unittest.TestCase):
    def test_chunk_pages_maps_marker_to_page_numbers(self):
        text = (
            "<!--page-orient:portrait--><div><p>Trang một nội dung</p></div>"
            "<!--page-orient:landscape--><div><p>Trang hai bảng biểu</p></div>"
        )

        pages = chunk_pages(text)

        self.assertEqual([page for _, page in pages], [1, 2])

    def test_sentence_boundary_chunking_never_drops_text(self):
        marker = "UNIQUE_GAP_MARKER"
        text = "a" * 1750 + "\n" + marker + "b" * 2500

        chunks = chunk_text(text, chunk_size=2000, overlap=200)

        self.assertTrue(any(marker in chunk for chunk in chunks))

    def test_chunk_embedding_reads_ocr_html_by_column_name(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE document_versions (
                id INTEGER, document_id INTEGER, version_number INTEGER,
                cqbh_tren TEXT, cqbh_duoi TEXT, so_ki_hieu TEXT,
                ngay_bh TEXT, do_khan TEXT, do_mat TEXT, loai_vb TEXT,
                nd_chinh TEXT, noi_nhan TEXT, chuc_vu TEXT, chu_ky TEXT,
                van_ban_dang_txt TEXT, modified_by TEXT, created_at TEXT,
                model_used TEXT
            )
        """)
        sentinel = "NOI_DUNG_TOAN_VAN_CAN_DUOC_INDEX"
        conn.execute(
            "INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 9, 1, "CQ", "", "01/CV", "", "Không", "", "Công văn",
             "Trích yếu", "", "", "", f"<div><p>{sentinel}</p></div>",
             "OCR", "2026-07-11 10:00:00", "chandra-q4"),
        )
        row = conn.execute("SELECT * FROM document_versions").fetchone()
        store = _VectorStore()

        count = embed_document_chunks(_EmbeddingModel(), store, 9, row)

        self.assertEqual(1, count)
        self.assertIn(sentinel, store.chunks[0][0])
        self.assertIsNone(store.chunks[0][2])


class EmbeddingLengthTests(unittest.TestCase):
    def test_embedding_reads_at_most_the_2048_tokens_it_was_trained_on(self):
        # model_embedding/README.md: trained with max sequence length 2048.
        # Chunks are about 470 tokens (max 816), so nothing is cut today.
        import sys
        import types
        from unittest.mock import patch

        import ai_search

        class _SentenceTransformer:
            def __init__(self, *_args, **_kwargs):
                self.max_seq_length = 8192

        fake = types.ModuleType("sentence_transformers")
        fake.SentenceTransformer = _SentenceTransformer
        saved = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = fake
        try:
            with patch.object(ai_search.EmbeddingModel, "is_available", return_value=True), \
                    patch.object(ai_search, "preferred_torch_device", return_value="cpu"):
                model = ai_search.EmbeddingModel()
                self.assertTrue(model.load())
        finally:
            if saved is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = saved

        self.assertEqual(ai_search.EMBEDDING_MAX_TOKENS, 2048)
        self.assertEqual(model.model.max_seq_length, 2048)


if __name__ == "__main__":
    unittest.main()
