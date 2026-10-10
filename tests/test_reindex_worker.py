import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from background_workers import ReindexWorker, reindex_documents
from document_repository import DocumentDatabase
from legal_graph import get_validity


THAY_THE = (
    "Quyết định này thay thế Quyết định số 05/2018/QĐ-UBND "
    "ngày 10/01/2018 của UBND tỉnh."
)


class _EmbeddingModel:
    def encode(self, text):
        vector = np.zeros(1024, dtype=np.float32)
        vector[hash(text) % 1024] = 1.0
        return vector


class ReindexWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "documents.db"
        self.db = DocumentDatabase(self.db_path)
        self.target_id = self._add_doc(
            "cu.pdf", "Nội dung văn bản cũ", "05/2018/QĐ-UBND"
        )
        self.source_id = self._add_doc("moi.pdf", THAY_THE, "09/2026/QĐ-UBND")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _add_doc(self, name, text, number):
        path = self.root / name
        path.write_bytes(name.encode("utf-8"))
        return self.db.add_document(
            str(path),
            {
                "Van_ban_dang_txt": text,
                "So_Ki_Hieu": number,
                "Loai_VB": "Quyết định",
                "CQBH_tren": "Ủy ban nhân dân",
            },
        )

    def _clear_indexes(self):
        conn = self.db.conn_pool.get_connection()
        conn.executescript(
            "DELETE FROM document_chunks; DELETE FROM doc_relations; "
            "DELETE FROM doc_entities; DELETE FROM doc_validity;"
        )
        try:
            conn.execute("DELETE FROM chunk_vectors")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    @staticmethod
    def _load_sqlite_vec(conn):
        try:
            import sqlite_vec
        except ImportError:
            return False
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        return True

    def test_reindexes_chunks_relations_validity_and_reports_progress(self):
        self._clear_indexes()
        progress = []

        count = reindex_documents(
            self.db_path,
            _EmbeddingModel(),
            progress_cb=lambda current, total, name: progress.append(
                (current, total, name)
            ),
        )

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(2, count)
            self.assertEqual(
                2,
                conn.execute(
                    "SELECT COUNT(DISTINCT document_id) FROM document_chunks"
                ).fetchone()[0],
            )
            self.assertEqual(
                ("thay_the", self.target_id),
                conn.execute(
                    "SELECT relation_type, target_doc_id FROM doc_relations "
                    "WHERE source_doc_id = ?",
                    (self.source_id,),
                ).fetchone(),
            )
            self.assertEqual(
                ("het_hieu_luc", self.source_id),
                get_validity(conn, self.target_id),
            )
            if self._load_sqlite_vec(conn):
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0],
                )
        self.assertEqual((2, 2), progress[-1][:2])

    def test_cancel_check_stops_cleanly_between_documents(self):
        self._clear_indexes()
        cancel = {"value": False}

        def on_progress(current, _total, _name):
            if current == 1:
                cancel["value"] = True

        count = reindex_documents(
            self.db_path,
            _EmbeddingModel(),
            cancel_check=lambda: cancel["value"],
            progress_cb=on_progress,
        )

        with sqlite3.connect(self.db_path) as conn:
            indexed = conn.execute(
                "SELECT DISTINCT document_id FROM document_chunks ORDER BY document_id"
            ).fetchall()
        self.assertEqual(1, count)
        self.assertEqual([(self.target_id,)], indexed)

    def test_worker_emits_error_for_unknown_requested_document(self):
        errors = []
        successes = []
        worker = ReindexWorker(self.db_path, _EmbeddingModel(), [999])
        worker.error.connect(errors.append)
        worker.finished_ok.connect(successes.append)

        worker.run()

        self.assertTrue(errors)
        self.assertIn("999", errors[0])
        self.assertEqual([], successes)

    def test_failed_reindex_leaves_no_open_handle_on_the_database(self):
        """The error path used to abandon an open connection, which kept
        documents.db locked and made every later delete or backup fail."""
        worker = ReindexWorker(self.db_path, _EmbeddingModel(), [999])
        worker.error.connect(lambda _message: None)

        worker.run()

        self.db.close()
        copy_path = self.root / "copy.db"
        self.db_path.replace(copy_path)
        self.assertFalse(self.db_path.exists())
        copy_path.replace(self.db_path)
        self.db = DocumentDatabase(self.db_path)

    def test_worker_emits_progress_and_finished_ok(self):
        progress = []
        successes = []
        worker = ReindexWorker(
            self.db_path, _EmbeddingModel(), [self.source_id]
        )
        worker.progress.connect(
            lambda current, total, name: progress.append((current, total, name))
        )
        worker.finished_ok.connect(successes.append)

        worker.run()

        self.assertEqual([1], successes)
        self.assertEqual((1, 1), progress[-1][:2])

    def test_delete_cleans_graph_vectors_and_recomputes_affected_validity(self):
        reindex_documents(self.db_path, _EmbeddingModel())
        with sqlite3.connect(self.db_path) as conn:
            source_chunks = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM document_chunks WHERE document_id = ?",
                    (self.source_id,),
                )
            ]
        self.db.create_backup = lambda *_args, **_kwargs: None

        self.db.delete_document(self.source_id, keep_file=True)

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
                    (self.source_id,),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM doc_relations WHERE source_doc_id = ?",
                    (self.source_id,),
                ).fetchone()[0],
            )
            self.assertEqual(
                ("con_hieu_luc", None), get_validity(conn, self.target_id)
            )
            if self._load_sqlite_vec(conn):
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM chunk_vectors WHERE rowid IN ({})".format(
                        ",".join("?" for _ in source_chunks)
                    ),
                    source_chunks,
                ).fetchone()[0]
                self.assertEqual(0, remaining)


if __name__ == "__main__":
    unittest.main()
