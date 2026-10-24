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


class FoldedSearchTests(unittest.TestCase):
    """Accent-insensitive retrieval: unaccented queries and OCR mark errors."""

    def setUp(self):
        self.conn = _make_db()
        self.conn.executemany(
            "INSERT INTO document_chunks (document_id, chunk_index, chunk_text) VALUES (?, 0, ?)",
            [
                (1, "Đối tượng: đồng chí Đỗ Xuân Chương, Trưởng phòng Công tác Quốc hội."),
                (2, "Dự thảo Quyết định quy định về khoản kinh phí sử dụng xe ô tô."),
                (3, "Kế hoạch tiếp xúc cử tri sau kỳ họp thứ Ba."),
            ],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_fold_strips_tone_vowel_marks_and_d_bar(self):
        from search_store import fold_vietnamese

        self.assertEqual(fold_vietnamese("Đỗ Xuân Chương"), "do xuan chuong")
        self.assertEqual(fold_vietnamese("khoán"), fold_vietnamese("khoản"))

    def test_unaccented_query_finds_accented_chunk(self):
        from search_store import fts_search_folded

        hits = fts_search_folded(self.conn, "do xuan chuong chuc vu gi")
        self.assertEqual(hits[0][1], 1)

    def test_correct_spelling_finds_chunk_with_ocr_mark_error(self):
        from search_store import fts_search_folded

        hits = fts_search_folded(self.conn, "khoán kinh phí xe ô tô")
        self.assertEqual(hits[0][1], 2)

    def test_folded_index_follows_chunk_rewrites(self):
        from search_store import fts_search_folded

        self.assertTrue(fts_search_folded(self.conn, "tiep xuc cu tri"))
        self.conn.execute("DELETE FROM document_chunks WHERE document_id = 3")
        self.conn.execute(
            "INSERT INTO document_chunks (document_id, chunk_index, chunk_text) "
            "VALUES (4, 0, 'Lịch khảo sát Công ty Linea Aqua')"
        )
        self.conn.commit()

        self.assertEqual(fts_search_folded(self.conn, "tiep xuc cu tri"), [])
        self.assertEqual(fts_search_folded(self.conn, "linea aqua")[0][1], 4)

    def test_doc_ids_filter_applies(self):
        from search_store import fts_search_folded

        self.assertEqual(fts_search_folded(self.conn, "do xuan chuong", doc_ids=[2]), [])


class BestPassageTests(unittest.TestCase):
    def test_short_text_is_returned_whole(self):
        from search_store import best_passage

        self.assertEqual(best_passage("Ngắn gọn.", "bất kỳ", size=100), "Ngắn gọn.")

    def test_long_text_returns_the_window_that_answers_the_query(self):
        from search_store import best_passage

        filler = "Nội dung mục đích yêu cầu chung của kế hoạch. " * 40
        text = filler + "2. Đối tượng: đồng chí Đỗ Xuân Chương, Trưởng phòng Công tác Quốc hội. " + filler
        passage = best_passage(text, "Đỗ Xuân Chương có chức vụ là gì?", size=300)

        self.assertIn("Trưởng phòng Công tác Quốc hội", passage)
        self.assertLessEqual(len(passage), 300)

    def test_matching_ignores_marks_and_never_cuts_a_word(self):
        from search_store import best_passage

        text = ("mở đầu " * 120) + "tổng mức đầu tư dự kiến 180.500 triệu đồng" + (" kết thúc" * 120)
        passage = best_passage(text, "tong muc dau tu", size=120)

        self.assertIn("180.500", passage)
        self.assertFalse(passage[0].isspace() or passage[-1].isspace())
        self.assertTrue(text.find(passage) >= 0)
