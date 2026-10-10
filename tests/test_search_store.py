import sqlite3
import unittest

from search_store import init_fts, rebuild_fts, fts_search, build_match_query


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding BLOB,
            text_hash TEXT,
            created_at TIMESTAMP
        )
    """)
    init_fts(conn.cursor())
    conn.commit()
    return conn


class FtsSearchTests(unittest.TestCase):
    def test_match_query_escapes_and_quotes_tokens(self):
        # Ký tự đặc biệt FTS5 không được gây syntax error
        q = build_match_query('quyết định "số 5" AND (test)')
        self.assertNotIn("(", q)
        self.assertIn('"quyết"', q)

    def test_search_finds_vietnamese_with_diacritics(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
            "VALUES (1, 0, 'Quyết định về việc cấp hộ chiếu ngoại giao')"
        )
        conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
            "VALUES (2, 0, 'Báo cáo tổng kết năm 2024')"
        )
        conn.commit()
        hits = fts_search(conn, "hộ chiếu")
        self.assertEqual(len(hits), 1)
        chunk_id, doc_id, score = hits[0]
        self.assertEqual(doc_id, 1)
        self.assertGreater(score, 0)

    def test_diacritics_are_significant(self):
        # remove_diacritics 0: 'hòa' KHÔNG match 'hoa'
        conn = _make_db()
        conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
            "VALUES (1, 0, 'phong trào thi đua hòa bình')"
        )
        conn.commit()
        self.assertEqual(fts_search(conn, "hoa binh"), [])

    def test_delete_syncs_via_trigger(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
            "VALUES (1, 0, 'văn bản sẽ bị xóa')"
        )
        conn.commit()
        conn.execute("DELETE FROM document_chunks WHERE document_id = 1")
        conn.commit()
        self.assertEqual(fts_search(conn, "xóa"), [])

    def test_doc_ids_filter(self):
        conn = _make_db()
        for doc in (1, 2):
            conn.execute(
                "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
                "VALUES (?, 0, 'kế hoạch công tác')", (doc,)
            )
        conn.commit()
        hits = fts_search(conn, "kế hoạch", doc_ids=[2])
        self.assertEqual([h[1] for h in hits], [2])

    def test_rebuild_indexes_rows_created_before_fts(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                embedding BLOB,
                text_hash TEXT,
                created_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO document_chunks(document_id, chunk_index, chunk_text) "
            "VALUES (1, 0, 'hộ chiếu ngoại giao')"
        )
        init_fts(conn.cursor())
        self.assertEqual(fts_search(conn, "hộ chiếu"), [])
        rebuild_fts(conn)
        self.assertEqual(fts_search(conn, "hộ chiếu")[0][1], 1)


if __name__ == "__main__":
    unittest.main()
