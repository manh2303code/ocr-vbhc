"""tools/reocr_documents.py: re-OCR, save a version, re-index those ids only."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ai_search
import background_workers
import document_repository
import ocr_pipeline
from document_repository import DocumentDatabase
from tools import reocr_documents


GOOD = '<!--page-orient:portrait-->\n<div data-label="Text"><p>Số: 12/QĐ-UBND</p></div>'


class _FakeModel:
    current_model_key = "chandra-q4"

    def close(self):
        pass


class _FakeServer:
    def stop(self):
        pass


class _FakeOCR:
    calls = []

    def __init__(self):
        self.ocr_model = self.fullpage_ocr = _FakeModel()
        self._chandra_server = _FakeServer()

    def process_document_chandra(self, path):
        _FakeOCR.calls.append(Path(path).name)
        return {"So_Ki_Hieu": "12/QĐ-UBND", "Van_ban_dang_txt": GOOD,
                "OCR_Failed_Pages": [], "OCR_Warning": ""}, [[]]


class _FakeEmbedding:
    def load(self):
        return True


class ReocrToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "documents.db"
        source = root / "a.pdf"
        source.write_bytes(b"pdf fixture")
        db = DocumentDatabase(self.db_path)
        self.doc_id = db.add_document(
            str(source), {"Van_ban_dang_txt": "The user wants me to fill the table."},
            page_count=1,
        )
        db.close()
        _FakeOCR.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra):
        reindexed = []

        def fake_reindex(db_path, _embedding, doc_ids=None, **_kwargs):
            reindexed.append(list(doc_ids))
            return len(doc_ids)

        with patch.dict(os.environ), \
             patch.object(ocr_pipeline, "DocumentOCR", _FakeOCR), \
             patch.object(ai_search, "EmbeddingModel", _FakeEmbedding), \
             patch.object(background_workers, "reindex_documents", fake_reindex):
            code = reocr_documents.main(
                ["--db", str(self.db_path), "--doc-ids", str(self.doc_id),
                 "--port", "9991", *extra]
            )
        return code, reindexed

    def _latest(self):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            return conn.execute(
                "SELECT version_number, so_ki_hieu, van_ban_dang_txt, modified_by "
                "FROM document_versions WHERE document_id = ? "
                "ORDER BY version_number DESC LIMIT 1", (self.doc_id,),
            ).fetchone()

    def test_saves_a_new_version_and_reindexes_only_that_document(self):
        code, reindexed = self._run()

        self.assertEqual(0, code)
        self.assertEqual(["a.pdf"], _FakeOCR.calls)
        self.assertEqual([[self.doc_id]], reindexed)
        version, number, text, author = self._latest()
        self.assertEqual(2, version)
        self.assertEqual("12/QĐ-UBND", number)
        self.assertNotIn("The user wants", text)
        self.assertEqual("OCR lại (chandra-q4)", author)
        self.assertTrue(Path(str(self.db_path) + ".before-reocr").is_file())

    def test_dry_run_leaves_the_database_alone(self):
        before = self.db_path.read_bytes()
        opened = []
        real_db = document_repository.DocumentDatabase

        def recording_db(path, *args, **kwargs):
            opened.append(Path(path))
            return real_db(path, *args, **kwargs)

        with patch.object(document_repository, "DocumentDatabase", recording_db):
            code, reindexed = self._run("--dry-run")

        self.assertEqual(0, code)
        self.assertEqual([], reindexed)
        self.assertEqual(["a.pdf"], _FakeOCR.calls)
        self.assertEqual(1, self._latest()[0])
        # Opening a database migrates it, so the dry run must open a copy.
        self.assertNotIn(self.db_path, opened)
        self.assertEqual(before, self.db_path.read_bytes())
        self.assertFalse(Path(str(self.db_path) + ".before-reocr").exists())

    def test_a_second_run_keeps_the_first_backup(self):
        self._run()
        first = Path(str(self.db_path) + ".before-reocr").read_bytes()

        self._run()

        self.assertEqual(first, Path(str(self.db_path) + ".before-reocr").read_bytes())
        self.assertTrue(Path(str(self.db_path) + ".before-reocr.2").is_file())

    def test_refuses_the_app_ports(self):
        with patch.dict(os.environ), self.assertRaises(SystemExit):
            reocr_documents.main(["--db", str(self.db_path), "--doc-ids", "1",
                                  "--port", "9999"])


if __name__ == "__main__":
    unittest.main()
