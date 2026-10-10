import sqlite3
import unittest

import numpy as np

from vector_index import VectorIndex, init_vec


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding BLOB
        )
    """)
    return conn


def _vec(*vals):
    v = np.zeros(1024, dtype=np.float32)
    v[: len(vals)] = vals
    n = np.linalg.norm(v)
    return v / n if n else v


class VectorIndexTests(unittest.TestCase):
    def _seed(self, conn):
        rows = [
            (1, 0, "chunk doc1", _vec(1.0, 0.0).tobytes()),
            (2, 0, "chunk doc2", _vec(0.0, 1.0).tobytes()),
        ]
        conn.executemany(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text, embedding) "
            "VALUES (?, ?, ?, ?)", rows
        )
        conn.commit()

    def test_sync_and_search_returns_nearest_first(self):
        conn = _make_db()
        init_vec(conn)
        self._seed(conn)
        idx = VectorIndex(conn)
        loaded = idx.sync_from_chunks(conn)
        self.assertEqual(loaded, 2)
        hits = idx.search(conn, _vec(1.0, 0.05), top_n=2)
        self.assertEqual(hits[0][1], 1)
        self.assertGreater(hits[0][2], hits[1][2])

    def test_doc_filter(self):
        conn = _make_db()
        init_vec(conn)
        self._seed(conn)
        idx = VectorIndex(conn)
        idx.sync_from_chunks(conn)
        hits = idx.search(conn, _vec(1.0), top_n=5, doc_ids=[2])
        self.assertTrue(all(h[1] == 2 for h in hits))

    def test_remove_chunks(self):
        conn = _make_db()
        init_vec(conn)
        self._seed(conn)
        idx = VectorIndex(conn)
        idx.sync_from_chunks(conn)
        idx.remove_chunks(conn, [1])
        hits = idx.search(conn, _vec(1.0), top_n=5)
        self.assertTrue(all(h[0] != 1 for h in hits))

    def test_fallback_mode_still_works(self):
        conn = _make_db()
        self._seed(conn)
        idx = VectorIndex(conn, force_fallback=True)
        self.assertFalse(idx.is_native())
        idx.sync_from_chunks(conn)
        hits = idx.search(conn, _vec(0.0, 1.0), top_n=1)
        self.assertEqual(hits[0][1], 2)


if __name__ == "__main__":
    unittest.main()
