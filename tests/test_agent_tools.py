import sqlite3
import unittest

import numpy as np

from agent_tools import TOOLS_SCHEMA, ToolExecutor, ToolResult
from legal_graph import init_tables
from search_store import init_fts


class _EmbeddingModel:
    def encode(self, _text):
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        return vector


class _VectorIndex:
    def search(self, _conn, _query_vec, top_n=50, doc_ids=None):
        del top_n
        rows = [(1, 1, 0.95), (2, 2, 0.80), (3, 3, 0.20)]
        allowed = set(doc_ids) if doc_ids is not None else None
        return [row for row in rows if allowed is None or row[1] in allowed]


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            file_path TEXT,
            file_name TEXT
        );
        CREATE TABLE document_versions (
            id INTEGER PRIMARY KEY,
            document_id INTEGER,
            version_number INTEGER,
            cqbh_tren TEXT,
            cqbh_duoi TEXT,
            so_ki_hieu TEXT,
            ngay_bh TEXT,
            loai_vb TEXT,
            nd_chinh TEXT,
            van_ban_dang_txt TEXT
        );
        CREATE TABLE document_chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER,
            chunk_index INTEGER,
            chunk_text TEXT,
            embedding BLOB,
            page_number INTEGER
        );
        """
    )
    init_fts(conn.cursor())
    init_tables(conn.cursor())
    conn.executemany(
        "INSERT INTO documents VALUES (?, ?, ?)",
        [
            (1, "doc1.pdf", "doc1.pdf"),
            (2, "doc2.pdf", "doc2.pdf"),
            (3, "doc3.pdf", "doc3.pdf"),
        ],
    )
    conn.executemany(
        "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "Bộ Công an", "", "01/2020/TT-BCA", "01/01/2020",
             "Thông tư", "Quy định hộ chiếu", "Toàn văn cũ"),
            (2, 2, 1, "Chính phủ", "", "02/2024/NĐ-CP", "02/02/2024",
             "Nghị định", "Thay thế quy định cũ", "Toàn văn mới"),
            (3, 3, 1, "Bộ Tài chính", "", "03/2023/TT-BTC", "2023-03-03",
             "Thông tư", "Quy định ngân sách", "Toàn văn ngân sách"),
        ],
    )
    long_text = "Quy định cấp hộ chiếu ngoại giao. " + ("x" * 800)
    conn.executemany(
        "INSERT INTO document_chunks VALUES (?, ?, 0, ?, NULL, ?)",
        [
            (1, 1, long_text, 4),
            (2, 2, "Nghị định thay thế quy định hộ chiếu cũ", 2),
            (3, 3, "Hướng dẫn lập dự toán ngân sách", 7),
        ],
    )
    conn.execute(
        "INSERT INTO doc_relations "
        "(source_doc_id, target_so_ki_hieu, target_doc_id, relation_type, evidence) "
        "VALUES (2, '01/2020/TT-BCA', 1, 'thay_the', 'Thay thế Thông tư cũ')"
    )
    conn.execute(
        "INSERT INTO doc_validity(doc_id, status, reason_doc_id) "
        "VALUES (1, 'het_hieu_luc', 2)"
    )
    conn.commit()
    return conn


class ToolSchemaTests(unittest.TestCase):
    def test_exactly_five_openai_function_schemas(self):
        self.assertEqual(len(TOOLS_SCHEMA), 5)
        self.assertEqual(
            {item["function"]["name"] for item in TOOLS_SCHEMA},
            {
                "search_documents",
                "get_document_info",
                "get_related_documents",
                "check_validity",
                "read_document_section",
            },
        )
        self.assertTrue(all(item["type"] == "function" for item in TOOLS_SCHEMA))


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        self.executor = ToolExecutor(
            self.conn, _VectorIndex(), _EmbeddingModel(), reranker=None
        )

    def tearDown(self):
        self.conn.close()

    def test_search_documents_returns_compact_chunks_and_sources(self):
        result = self.executor.execute("search_documents", {"query": "hộ chiếu"})

        self.assertIsInstance(result, ToolResult)
        self.assertIn("Quy định cấp hộ chiếu", result.text)
        self.assertIn("【01/2020/TT-BCA · Thông tư · trang 4】", result.text)
        self.assertIn(1, [source["doc_id"] for source in result.sources])
        rendered_chunk = result.text.split("\n", 1)[1].split("\n\n", 1)[0]
        self.assertLessEqual(len(rendered_chunk), 600)

    def test_prefetch_uses_unfiltered_query_and_builds_grounded_overview(self):
        targeted = self.executor.prefetch("Quy định hộ chiếu của cơ quan nào?")
        overview = self.executor.prefetch(
            "Kho hiện có bao nhiêu văn bản, gồm những loại nào?"
        )

        self.assertIn(1, [source["doc_id"] for source in targeted.sources])
        self.assertIn("Tổng số văn bản: 3", overview.text)
        self.assertIn("Thông tư: 2", overview.text)
        self.assertIn("Nghị định: 1", overview.text)
        self.assertTrue(overview.sources)

    def test_search_filters_type_agency_and_dates(self):
        matching = self.executor.execute(
            "search_documents",
            {
                "query": "hộ chiếu",
                "loai_vb": "Thông tư",
                "co_quan": "Bộ Công an",
                "from_date": "2019-01-01",
                "to_date": "2021-12-31",
            },
        )
        excluded = self.executor.execute(
            "search_documents", {"query": "hộ chiếu", "co_quan": "Bộ Tài chính"}
        )

        self.assertEqual([source["doc_id"] for source in matching.sources], [1])
        self.assertNotIn(1, [source["doc_id"] for source in excluded.sources])

    def test_scope_applies_to_search_and_read_section(self):
        scoped = ToolExecutor(
            self.conn,
            _VectorIndex(),
            _EmbeddingModel(),
            reranker=None,
            scope_doc_ids=[3],
        )

        search = scoped.execute("search_documents", {"query": "hộ chiếu"})
        read = scoped.execute(
            "read_document_section", {"doc_id": 1, "query": "hộ chiếu"}
        )

        self.assertNotIn(1, [source["doc_id"] for source in search.sources])
        self.assertNotIn(2, [source["doc_id"] for source in search.sources])
        self.assertEqual(read.sources, [])

    def test_get_document_info_by_number_or_id(self):
        by_number = self.executor.execute(
            "get_document_info", {"so_ki_hieu": " 01/2020/tt-bca "}
        )
        by_id = self.executor.execute("get_document_info", {"doc_id": 2})

        self.assertIn("Bộ Công an", by_number.text)
        self.assertEqual(by_number.sources[0]["doc_id"], 1)
        self.assertIn("02/2024/NĐ-CP", by_id.text)

    def test_check_validity_explains_reason_document(self):
        result = self.executor.execute("check_validity", {"doc_id": 1})

        self.assertIn("hết hiệu lực", result.text.lower())
        self.assertIn("02/2024/NĐ-CP", result.text)

    def test_get_related_documents_lists_outgoing_and_incoming(self):
        outgoing = self.executor.execute("get_related_documents", {"doc_id": 2})
        incoming = self.executor.execute(
            "get_related_documents",
            {"doc_id": 1, "relation_types": ["thay_the"]},
        )

        self.assertIn("thay thế", outgoing.text.lower())
        self.assertIn("01/2020/TT-BCA", outgoing.text)
        self.assertIn("02/2024/NĐ-CP", incoming.text)

    def test_read_document_section_searches_only_requested_document(self):
        result = self.executor.execute(
            "read_document_section", {"doc_id": 1, "query": "hộ chiếu"}
        )

        self.assertTrue(result.sources)
        self.assertTrue(all(source["doc_id"] == 1 for source in result.sources))

    def test_unknown_and_malformed_calls_fail_softly(self):
        unknown = self.executor.execute("not_a_tool", {})
        malformed = self.executor.execute("search_documents", "not-an-object")
        missing = self.executor.execute("check_validity", {})
        bad_date = self.executor.execute(
            "search_documents", {"query": "x", "from_date": "not-a-date"}
        )

        self.assertIn("Công cụ không tồn tại", unknown.text)
        self.assertEqual(unknown.sources, [])
        for result in (malformed, missing, bad_date):
            self.assertIsInstance(result, ToolResult)
            self.assertEqual(result.sources, [])
            self.assertIn("Lỗi", result.text)


if __name__ == "__main__":
    unittest.main()
