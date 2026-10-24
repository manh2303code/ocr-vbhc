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


def _vector_ids(conn):
    return {row[0] for row in conn.execute("SELECT rowid FROM chunk_vectors")}


def _chunk_ids(conn):
    return {row[0] for row in conn.execute("SELECT id FROM document_chunks")}


class VectorIndexSyncTests(unittest.TestCase):
    """chunk_vectors must mirror document_chunks, not merely match its size."""

    def setUp(self):
        self.conn = _make_db()
        if not init_vec(self.conn):
            self.skipTest("sqlite-vec unavailable")
        self.conn.executemany(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text, embedding) "
            "VALUES (?, ?, ?, ?)",
            [(1, 0, "a", _vec(1.0).tobytes()), (2, 0, "b", _vec(0.0, 1.0).tobytes())],
        )
        self.index = VectorIndex(self.conn)
        self.index.sync_from_chunks(self.conn)

    def _rewrite_doc_chunks_without_vectors(self, doc_id):
        # What VectorStore.upsert_chunks used to do: new rowids, no vectors.
        self.conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (doc_id,))
        self.conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text, embedding) "
            "VALUES (?, 0, 'new', ?)",
            (doc_id, _vec(1.0, 1.0).tobytes()),
        )

    def test_replace_chunks_drops_old_ids_and_indexes_new_ones(self):
        old_ids = [row[0] for row in self.conn.execute(
            "SELECT id FROM document_chunks WHERE document_id = 1")]
        self._rewrite_doc_chunks_without_vectors(1)
        new_rows = list(self.conn.execute(
            "SELECT id, embedding FROM document_chunks WHERE document_id = 1"))

        self.index.replace_chunks(self.conn, old_ids, new_rows)

        self.assertEqual(_chunk_ids(self.conn), _vector_ids(self.conn))

    def test_out_of_sync_detects_orphans_even_when_counts_match(self):
        # The live DB state: same number of vectors and chunks, different ids.
        self._rewrite_doc_chunks_without_vectors(1)
        count_vectors = self.conn.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
        count_chunks = self.conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
        self.assertEqual(count_vectors, count_chunks)

        self.assertTrue(self.index.out_of_sync(self.conn))

    def test_ensure_synced_rebuilds_a_desynced_index(self):
        self._rewrite_doc_chunks_without_vectors(2)

        rebuilt = self.index.ensure_synced(self.conn)

        self.assertEqual(2, rebuilt)
        self.assertFalse(self.index.out_of_sync(self.conn))
        self.assertEqual(_chunk_ids(self.conn), _vector_ids(self.conn))

    def test_ensure_synced_leaves_a_synced_index_alone(self):
        self.assertIsNone(self.index.ensure_synced(self.conn))

    def test_chunks_with_unusable_embeddings_do_not_force_a_resync(self):
        self.conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text, embedding) "
            "VALUES (3, 0, 'bad', ?)",
            (b"vector",),
        )

        self.assertFalse(self.index.out_of_sync(self.conn))


if __name__ == "__main__":
    unittest.main()
