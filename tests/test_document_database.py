import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from document_repository import DocumentDatabase, SCHEMA_VERSION


class DocumentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = DocumentDatabase(self.root / "documents.db")
        self.source = self.root / "source.png"
        self.source.write_bytes(b"image fixture")
        self.doc_id = self.db.add_document(
            str(self.source), {"Van_ban_dang_txt": "OCR body"}, model_key="chandra-q4"
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pre_serialized_detection_is_not_double_encoded(self):
        detection = {"box": [1, 2, 3, 4], "label": "field"}

        self.db.add_page_detections(self.doc_id, 1, json.dumps(detection))

        self.assertEqual(detection, self.db.get_document_detections(self.doc_id, 1))

    def test_delete_removes_related_versions_and_chunks_but_can_keep_source(self):
        self.db.conn_pool.get_connection().execute(
            "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES (?,?,?,?)",
            (self.doc_id, 0, "chunk", b"vector"),
        )
        self.db.conn_pool.get_connection().commit()

        self.db.create_backup = lambda *_args, **_kwargs: None
        self.db.delete_document(self.doc_id, keep_file=True)

        conn = self.db.conn_pool.get_connection()
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0])
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0])
        self.assertTrue(self.source.exists())

    def test_legacy_schema_is_migrated_and_versioned(self):
        self.db.close()
        legacy_path = self.root / "legacy.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, file_path TEXT NOT NULL, file_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_hash TEXT, file_size INTEGER, page_count INTEGER
                );
                CREATE TABLE document_versions (
                    id INTEGER PRIMARY KEY, document_id INTEGER, version_number INTEGER,
                    cqbh_tren TEXT, cqbh_duoi TEXT, so_ki_hieu TEXT, ngay_bh TEXT,
                    do_khan TEXT, do_mat TEXT, loai_vb TEXT, nd_chinh TEXT, noi_nhan TEXT,
                    chuc_vu TEXT, chu_ky TEXT, van_ban_dang_txt TEXT, modified_by TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO documents (id, file_path, file_name) VALUES (1, 'legacy.pdf', 'legacy.pdf');
                INSERT INTO document_versions (document_id, version_number) VALUES (1, 1);
            """)

        legacy = DocumentDatabase(legacy_path)
        try:
            conn = legacy.conn_pool.get_connection()
            columns = {row[1] for row in conn.execute("PRAGMA table_info(document_versions)")}
            self.assertIn("model_used", columns)
            self.assertEqual("chandra-q4", legacy.get_latest_version(1)["model_used"])
            self.assertEqual(SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            legacy.close()

    def test_database_backup_is_consistent_without_closing_repository(self):
        backup_path = self.root / "backups" / "documents.db"

        self.db.backup_database(backup_path)

        with sqlite3.connect(backup_path) as backup:
            self.assertEqual("ok", backup.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual(1, backup.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        self.assertIsNotNone(self.db.get_document_info(self.doc_id))


if __name__ == "__main__":
    unittest.main()
