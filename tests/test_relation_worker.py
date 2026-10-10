import sqlite3
import tempfile
import unittest
from pathlib import Path

from legal_graph import get_validity, init_tables
from background_workers import RelationWorker, index_document_relations


THAY_THE_LINE = (
    "Quyết định này có hiệu lực kể từ ngày ký và thay thế "
    "Quyết định số 05/2018/QĐ-UBND ngày 10/01/2018 của UBND tỉnh."
)


class RelationIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "documents.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE documents (id INTEGER PRIMARY KEY, file_name TEXT);
                CREATE TABLE document_versions (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL,
                    version_number INTEGER NOT NULL,
                    cqbh_tren TEXT,
                    cqbh_duoi TEXT,
                    so_ki_hieu TEXT,
                    loai_vb TEXT,
                    chuc_vu TEXT,
                    chu_ky TEXT
                );
                """
            )
            init_tables(conn.cursor())
            conn.executemany(
                "INSERT INTO documents(id, file_name) VALUES (?, ?)",
                [(1, "nguon.pdf"), (2, "dich.pdf")],
            )
            conn.execute(
                "INSERT INTO document_versions"
                "(id, document_id, version_number, so_ki_hieu) VALUES (1, 2, 1, ?)",
                ("05/2018/QĐ-UBND",),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_indexes_html_relations_and_updates_target_validity(self):
        count = index_document_relations(
            self.db_path,
            1,
            {
                "van_ban_dang_txt": f"<div><p>{THAY_THE_LINE}</p></div>",
                "cqbh_tren": "Ủy ban nhân dân",
                "loai_vb": "Quyết định",
            },
        )

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT relation_type, target_doc_id FROM doc_relations "
                "WHERE source_doc_id = 1"
            ).fetchone()
            self.assertEqual(count, 1)
            self.assertEqual(row, ("thay_the", 2))
            self.assertEqual(get_validity(conn, 2), ("het_hieu_luc", 1))
            self.assertEqual(
                conn.execute(
                    "SELECT value_raw FROM doc_entities "
                    "WHERE doc_id = 1 AND entity_type = 'co_quan'"
                ).fetchone()[0],
                "Ủy ban nhân dân",
            )

    def test_worker_emits_document_id_and_relation_count(self):
        emitted = []
        worker = RelationWorker(
            self.db_path,
            1,
            {"van_ban_dang_txt": THAY_THE_LINE},
        )
        worker.done.connect(lambda doc_id, count: emitted.append((doc_id, count)))

        worker.run()

        self.assertEqual(emitted, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
