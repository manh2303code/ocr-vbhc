import sqlite3
import unittest

from legal_graph import GraphStore, compute_validity, get_validity, init_tables
from legal_relations import Relation


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            file_name TEXT
        );
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
        [(1, "nguon.pdf"), (2, "dich.pdf"), (3, "cu.pdf")],
    )
    conn.executemany(
        "INSERT INTO document_versions"
        "(id, document_id, version_number, so_ki_hieu) VALUES (?, ?, ?, ?)",
        [
            (1, 2, 1, "04/2017/QĐ-UBND"),
            (2, 2, 2, " 05/2018/qđ-ubnd "),
            (3, 3, 1, "01/2010/QD-UBND"),
        ],
    )
    conn.commit()
    return conn


class GraphSchemaTests(unittest.TestCase):
    def test_init_tables_is_idempotent_and_creates_required_indexes(self):
        conn = _make_db()
        init_tables(conn.cursor())
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {"doc_relations", "doc_entities", "doc_validity"}.issubset(tables)
        )
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertTrue(
            {
                "idx_relations_source_doc_id",
                "idx_relations_target_doc_id",
                "idx_relations_target_so_ki_hieu",
            }.issubset(indexes)
        )


class GraphStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        self.store = GraphStore(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_replace_and_get_outgoing_relations(self):
        old = Relation("can_cu", "01/2010/QD-UBND", "Căn cứ số cũ", 3)
        replacement = Relation(
            "thay_the", "05/2018/QĐ-UBND", "Thay thế số mới", 20
        )
        self.store.replace_relations(1, [old])
        self.store.replace_relations(1, [replacement])

        rows = self.store.get_relations(1, "out")

        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], sqlite3.Row)
        self.assertEqual(rows[0]["relation_type"], "thay_the")
        self.assertEqual(rows[0]["target_so_ki_hieu"], "05/2018/QĐ-UBND")
        self.assertEqual(rows[0]["char_offset"], 20)

    def test_resolve_targets_normalizes_latest_document_number(self):
        self.store.replace_relations(
            1,
            [Relation("can_cu", "05/2018/QĐ-UBND", "Căn cứ...", 0)],
        )

        resolved = self.store.resolve_targets()

        self.assertEqual(resolved, 1)
        row = self.store.get_relations(1)[0]
        self.assertEqual(row["target_doc_id"], 2)
        incoming = self.store.get_relations(2, "in")
        self.assertEqual([item["source_doc_id"] for item in incoming], [1])

    def test_upsert_entities_is_idempotent_and_normalizes_values(self):
        version = {
            "cqbh_tren": "Ủy ban nhân dân",
            "cqbh_duoi": "Sở Tư pháp",
            "chu_ky": "Đặng Văn An",
            "chuc_vu": "Giám đốc",
            "loai_vb": "Quyết định",
        }

        self.store.upsert_entities_from_version(1, version)
        self.store.upsert_entities_from_version(1, version)

        rows = self.conn.execute(
            "SELECT entity_type, value_norm, value_raw FROM doc_entities "
            "WHERE doc_id = 1 ORDER BY entity_type, value_norm"
        ).fetchall()
        self.assertEqual(len(rows), 5)
        self.assertIn(
            ("nguoi_ky", "DANG VAN AN", "Đặng Văn An"),
            [tuple(row) for row in rows],
        )
        self.assertEqual(
            sum(row["entity_type"] == "co_quan" for row in rows), 2
        )

    def test_delete_doc_removes_owned_graph_data_and_unresolves_targets(self):
        self.store.replace_relations(
            1, [Relation("thay_the", "05/2018/QĐ-UBND", "Thay thế...", 0)]
        )
        self.store.resolve_targets()
        self.store.upsert_entities_from_version(
            2, {"loai_vb": "Quyết định"}
        )
        self.conn.execute(
            "INSERT INTO doc_validity(doc_id, status, reason_doc_id) "
            "VALUES (2, 'het_hieu_luc', 1)"
        )
        self.conn.commit()

        self.store.delete_doc(2)

        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM doc_entities WHERE doc_id = 2"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM doc_validity WHERE doc_id = 2"
            ).fetchone()[0],
            0,
        )
        relation = self.store.get_relations(1)[0]
        self.assertIsNone(relation["target_doc_id"])

    def test_get_relations_rejects_invalid_direction(self):
        with self.assertRaises(ValueError):
            self.store.get_relations(1, "sideways")


class ValidityTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        self.conn.executemany(
            "INSERT INTO documents(id, file_name) VALUES (?, ?)",
            [(4, "sua-doi.pdf"), (5, "chuoi.pdf")],
        )

    def tearDown(self):
        self.conn.close()

    def _relation(self, source, target, relation_type):
        self.conn.execute(
            "INSERT INTO doc_relations "
            "(source_doc_id, target_so_ki_hieu, target_doc_id, relation_type) "
            "VALUES (?, '', ?, ?)",
            (source, target, relation_type),
        )
        self.conn.commit()

    def test_compute_all_infers_replaced_modified_and_current_documents(self):
        self._relation(1, 2, "thay_the")
        self._relation(3, 4, "sua_doi")
        self._relation(3, 3, "bai_bo")

        statuses = compute_validity(self.conn)

        self.assertEqual(statuses[2], "het_hieu_luc")
        self.assertEqual(get_validity(self.conn, 2), ("het_hieu_luc", 1))
        self.assertEqual(statuses[4], "sua_doi_mot_phan")
        self.assertEqual(get_validity(self.conn, 4), ("sua_doi_mot_phan", 3))
        self.assertEqual(statuses[1], "con_hieu_luc")
        self.assertEqual(statuses[3], "con_hieu_luc")

    def test_replacement_chain_keeps_target_expired_when_source_is_expired(self):
        self._relation(1, 2, "thay_the")
        self._relation(2, 5, "thay_the")

        compute_validity(self.conn)

        self.assertEqual(get_validity(self.conn, 2), ("het_hieu_luc", 1))
        self.assertEqual(get_validity(self.conn, 5), ("het_hieu_luc", 2))

    def test_affected_scope_only_recomputes_requested_documents(self):
        self._relation(1, 2, "bai_bo")
        self.conn.execute(
            "INSERT INTO doc_validity(doc_id, status) "
            "VALUES (1, 'khong_xac_dinh')"
        )
        self.conn.commit()

        statuses = compute_validity(self.conn, affected_doc_ids=[2])

        self.assertEqual(statuses, {2: "het_hieu_luc"})
        self.assertEqual(get_validity(self.conn, 2), ("het_hieu_luc", 1))
        self.assertEqual(get_validity(self.conn, 1), ("khong_xac_dinh", None))


if __name__ == "__main__":
    unittest.main()
