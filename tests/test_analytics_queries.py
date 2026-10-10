import sqlite3
import unittest

from analytics_queries import fetch_latest_documents


class AnalyticsLatestVersionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY, file_path TEXT, file_name TEXT, created_at TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE document_versions (
                id INTEGER PRIMARY KEY, document_id INTEGER, version_number INTEGER,
                so_ki_hieu TEXT, ngay_bh TEXT, loai_vb TEXT, cqbh_tren TEXT,
                nd_chinh TEXT, do_khan TEXT
            )
        """)
        self.conn.execute(
            "INSERT INTO documents VALUES (1, 'doc.pdf', 'doc.pdf', '2026-07-11 09:00:00')"
        )
        self.conn.executemany(
            "INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, 1, 1, "OLD-01", "10/07/2026", "Công văn", "Cơ quan cũ", "Nội dung cũ", "Không"),
                (2, 1, 2, "NEW-02", "11/07/2026", "Thông báo", "Cơ quan mới", "Nội dung mới", "Khẩn"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_number_search_ignores_matching_old_version(self):
        self.assertEqual([], fetch_latest_documents(self.conn, number="OLD-01"))
        rows = fetch_latest_documents(self.conn, number="NEW-02")
        self.assertEqual(1, len(rows))
        self.assertEqual("NEW-02", rows[0][4])

    def test_content_filters_apply_only_to_latest_version(self):
        self.assertEqual([], fetch_latest_documents(self.conn, keyword="Nội dung cũ"))
        rows = fetch_latest_documents(
            self.conn, keyword="Nội dung mới", doc_type="Thông báo", org="Cơ quan mới"
        )
        self.assertEqual(1, len(rows))

    def test_date_search_returns_one_row_per_document(self):
        rows = fetch_latest_documents(
            self.conn, date_from="2026-07-11", date_to="2026-07-11"
        )
        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
