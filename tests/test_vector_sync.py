"""Every chunk write must reach chunk_vectors.

The live database had 54/107 chunks without a vector: VectorStore.upsert_chunks
replaced a document's chunks (new rowids) after OCR, re-OCR or a manual edit,
and nothing ever indexed them, while the old rowids stayed behind as orphan
vectors. Opening the database only rebuilt the index when it was empty.
"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np

from ai_search import VectorStore
from document_repository import DocumentDatabase
from vector_index import init_vec


def _vec(seed):
    rng = np.random.default_rng(seed)
    vector = rng.standard_normal(1024).astype(np.float32)
    return vector / np.linalg.norm(vector)


def _ids(db_path):
    with closing(sqlite3.connect(str(db_path))) as conn:
        if not init_vec(conn):
            raise unittest.SkipTest("sqlite-vec unavailable")
        chunks = {r[0] for r in conn.execute("SELECT id FROM document_chunks")}
        vectors = {r[0] for r in conn.execute("SELECT rowid FROM chunk_vectors")}
    return chunks, vectors


class VectorStoreSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "documents.db"
        self.db = DocumentDatabase(self.db_path)
        self.store = VectorStore(str(self.db_path))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_new_chunks_are_indexed_when_written(self):
        self.store.upsert_chunks(1, [("a", _vec(1), 1), ("b", _vec(2), 1)])

        chunks, vectors = _ids(self.db_path)
        self.assertEqual(2, len(chunks))
        self.assertEqual(chunks, vectors)

    def test_rewriting_a_document_drops_its_old_vectors(self):
        self.store.upsert_chunks(1, [("a", _vec(1), 1), ("b", _vec(2), 1)])
        self.store.upsert_chunks(2, [("c", _vec(3), 1)])

        self.store.upsert_chunks(1, [("a2", _vec(4), 1)])

        chunks, vectors = _ids(self.db_path)
        self.assertEqual(2, len(chunks))
        self.assertEqual(chunks, vectors)


class DatabaseOpenResyncTests(unittest.TestCase):
    def test_open_rebuilds_an_index_whose_ids_drifted_from_the_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "documents.db"
            DocumentDatabase(db_path).close()
            store = VectorStore(str(db_path))
            store.upsert_chunks(1, [("a", _vec(1), 1), ("b", _vec(2), 1)])
            # Reproduce the old write path: replace the chunks with new rowids
            # and leave chunk_vectors untouched (same count, wrong ids).
            with closing(sqlite3.connect(str(db_path))) as conn, conn:
                conn.execute("DELETE FROM document_chunks")
                conn.executemany(
                    "INSERT INTO document_chunks(document_id, chunk_index, chunk_text, embedding) "
                    "VALUES (1, ?, ?, ?)",
                    [(0, "a", _vec(1).tobytes()), (1, "b", _vec(2).tobytes())],
                )
            chunks, vectors = _ids(db_path)
            self.assertEqual(len(chunks), len(vectors))
            self.assertNotEqual(chunks, vectors)

            DocumentDatabase(db_path).close()

            chunks, vectors = _ids(db_path)
            self.assertEqual(chunks, vectors)


if __name__ == "__main__":
    unittest.main()
