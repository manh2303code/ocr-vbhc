import sqlite3
import unittest

import numpy as np

from agent_tools import (
    RESULT_BUDGET_CHARS, TOOLS_SCHEMA, ToolExecutor, ToolResult, result_budget_for_context,
)
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
            van_ban_dang_txt TEXT,
            chuc_vu TEXT,
            chu_ky TEXT
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
        "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, "Bộ Công an", "", "01/2020/TT-BCA", "01/01/2020",
             "Thông tư", "Quy định hộ chiếu", "Toàn văn cũ", "BỘ TRƯỞNG", "Tô Lâm"),
            (2, 2, 1, "Chính phủ", "", "02/2024/NĐ-CP", "02/02/2024",
             "Nghị định", "Thay thế quy định cũ", "Toàn văn mới", "TM. CHÍNH PHỦ\nTHỦ TƯỚNG", ""),
            (3, 3, 1, "Bộ Tài chính", "", "03/2023/TT-BTC", "2023-03-03",
             "Thông tư", "Quy định ngân sách", "Toàn văn ngân sách", "", ""),
        ],
    )
    long_text = (
        "Quy định cấp hộ chiếu ngoại giao. " + ("Điều khoản chung về thủ tục. " * 60)
        + "Thời hạn hộ chiếu ngoại giao là 05 năm kể từ ngày cấp. " + ("Phụ lục mẫu. " * 40)
    )
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
    def test_six_openai_function_schemas(self):
        self.assertEqual(len(TOOLS_SCHEMA), 6)
        self.assertEqual(
            {item["function"]["name"] for item in TOOLS_SCHEMA},
            {
                "search_documents",
                "get_document_info",
                "get_related_documents",
                "check_validity",
                "read_document_section",
                "list_documents",
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
        self.assertIn("【01/2020/TT-BCA · Thông tư · 01/01/2020 · trang 4】", result.text)
        self.assertIn(1, [source["doc_id"] for source in result.sources])
        blocks = result.text.split("\n\n")
        self.assertEqual(len(blocks), len(result.sources))
        for block in blocks:
            self.assertLessEqual(len(block), 1800)

    def test_long_chunk_shows_the_passage_that_answers_the_question(self):
        result = self.executor.execute(
            "search_documents", {"query": "thời hạn hộ chiếu ngoại giao là bao lâu"}
        )

        self.assertIn("05 năm kể từ ngày cấp", result.text)

    def test_source_label_names_issuer_and_signer(self):
        result = self.executor.execute("search_documents", {"query": "hộ chiếu"})
        # Documents 1 and 2 both match; the label is what is under test here.
        block = next(b for b in result.text.split("\n\n") if "01/2020/TT-BCA" in b)

        self.assertIn("Bộ Công an", block)
        self.assertIn("Ký: Tô Lâm (BỘ TRƯỞNG)", block)

    def test_source_label_reads_the_signature_block_when_switched_on(self):
        import agent_tools

        agent_tools.READABLE_SIGNER = True
        try:
            result = self.executor.execute("search_documents", {"query": "hộ chiếu"})
        finally:
            agent_tools.READABLE_SIGNER = False
        block = next(b for b in result.text.split("\n\n") if "01/2020/TT-BCA" in b)
        self.assertIn("Ký: Tô Lâm, Bộ trưởng", block)

    def test_a_metadata_question_gets_a_table_one_block_per_source(self):
        result = self.executor.aggregate("Liệt kê các thông tư")
        self.assertEqual("documents", result.aggregate.kind)
        self.assertEqual([1, 3], [source["doc_id"] for source in result.sources])
        self.assertEqual(len(result.sources), len(result.text.split("\n\n")))
        self.assertIn("KẾT QUẢ: 2 văn bản", result.text)
        people = self.executor.aggregate("Thống kê những người ký văn bản trong kho")
        self.assertIn("Tô Lâm — ký 1 văn bản. Bộ trưởng", people.text)
        # Content questions and a scope of one document are not tables.
        self.assertIsNone(self.executor.aggregate("Thông tư nào quy định hộ chiếu ngoại giao?"))
        scoped = ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), reranker=None, scope_doc_ids=[1])
        self.assertIsNone(scoped.aggregate("Liệt kê các thông tư"))

    def test_prefetch_within_the_documents_the_last_turn_listed(self):
        result = self.executor.prefetch("quy định nào thay thế hộ chiếu?", within=[2, 3])
        self.assertTrue(result.sources)
        self.assertTrue({source["doc_id"] for source in result.sources} <= {2, 3})
        self.assertIsNone(result.relevance)

    def test_prefetch_puts_the_numbered_document_first(self):
        result = self.executor.prefetch("Văn bản 03/2023/TT-BTC quy định gì?")

        self.assertEqual(result.sources[0]["doc_id"], 3)
        self.assertIn("Quy định ngân sách", result.text)

    def test_list_documents_filters_type_year_and_agency_ignoring_marks(self):
        by_type = self.executor.execute("list_documents", {"loai_vb": "thong tu"})
        by_year = self.executor.execute("list_documents", {"nam": 2024})
        by_agency = self.executor.execute("list_documents", {"co_quan": "bo tai chinh"})

        self.assertEqual(sorted(s["doc_id"] for s in by_type.sources), [1, 3])
        self.assertEqual([s["doc_id"] for s in by_year.sources], [2])
        self.assertEqual([s["doc_id"] for s in by_agency.sources], [3])
        self.assertIn("03/2023/TT-BTC", by_agency.text)

    def test_prefetch_shows_a_few_selected_chunks_whole(self):
        result = self.executor.prefetch("Thời hạn hộ chiếu ngoại giao")
        block = next(b for b in result.text.split("\n\n") if "01/2020/TT-BCA" in b)

        self.assertIn("Quy định cấp hộ chiếu ngoại giao.", block)
        self.assertTrue(block.endswith("Phụ lục mẫu."))
        self.assertGreaterEqual(len(result.sources), 3)
        self.assertLessEqual(len(result.text), 9000)

    def test_result_budget_leaves_room_for_the_answer_in_a_small_context(self):
        self.assertEqual(result_budget_for_context(None), RESULT_BUDGET_CHARS)
        for gpu_context in (16384, 24576, 32768):
            self.assertEqual(result_budget_for_context(gpu_context), RESULT_BUDGET_CHARS)
        self.assertLess(result_budget_for_context(12288), RESULT_BUDGET_CHARS)
        # 8192 is the CPU context. Prompt processing there runs at tens of
        # tokens per second, so its sources stay as small as they were at 4096.
        self.assertLessEqual(result_budget_for_context(8192), 3824)  # the old prefetch maximum
        self.assertLessEqual(result_budget_for_context(4096), 3824)

        small = ToolExecutor(
            self.conn, _VectorIndex(), _EmbeddingModel(), reranker=None,
            result_budget_chars=1500,
        )
        full = self.executor.execute("search_documents", {"query": "hộ chiếu"})
        limited = small.execute("search_documents", {"query": "hộ chiếu"})

        self.assertLess(len(limited.sources), len(full.sources))
        self.assertEqual(len(limited.text.split("\n\n")), len(limited.sources))

    def test_prefetch_tolerates_a_recognition_slip_in_the_number(self):
        result = self.executor.prefetch("Thông tư 01/2020/TT-BCAA quy định gì?")

        self.assertEqual(result.sources[0]["doc_id"], 1)
        self.assertIn("Quy định hộ chiếu", result.text)

    def test_prefetch_does_not_pin_a_date_or_a_different_number(self):
        dated = self.executor.prefetch("Văn bản ngày 01/01/2020 về hộ chiếu")
        other = self.executor.prefetch("Thông tư 07/2020/TT-BCA quy định gì về hộ chiếu?")

        for result in (dated, other):
            self.assertNotIn("thông tin văn bản", result.text)
            self.assertTrue(all(s["chunk_id"] is not None for s in result.sources))

    def test_content_question_naming_a_type_is_not_a_listing(self):
        result = self.executor.prefetch("Nghị định thay thế các quy định hộ chiếu nào?")

        self.assertTrue(result.sources)
        self.assertTrue(all(s["chunk_id"] is not None for s in result.sources))

    def test_prefetch_reranks_only_the_candidates_it_can_use(self):
        # Prefetch keeps at most 12 candidates and shows at most 6; scoring
        # 20 showed the model no more answers at 1.7x the CPU time.
        import agent_tools
        from unittest.mock import patch

        calls = []
        real = agent_tools.hybrid_search

        def recording(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)

        with patch.object(agent_tools, "hybrid_search", recording):
            self.executor.prefetch("Quy định hộ chiếu của cơ quan nào?")

        self.assertEqual(calls[0]["top_k"], 12)
        self.assertEqual(calls[0]["rerank_top"], agent_tools.PREFETCH_RERANK_TOP)
        self.assertEqual(agent_tools.PREFETCH_RERANK_TOP, 12)

    def test_prefetch_answers_listing_questions_from_metadata(self):
        result = self.executor.prefetch("Liệt kê các thông tư có trong kho")

        self.assertEqual(sorted(s["doc_id"] for s in result.sources), [1, 3])
        self.assertIn("01/2020/TT-BCA", result.text)
        self.assertIn("03/2023/TT-BTC", result.text)

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




class _ScoringReranker:
    """Sigmoid-like scores: the first candidate is the most relevant."""

    def is_available(self):
        return True

    def rerank(self, _query, texts):
        return [0.42 - 0.01 * index for index in range(len(texts))]


class PrefetchRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def test_prefetch_reports_the_reranker_relevance(self):
        executor = ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), _ScoringReranker())

        result = executor.prefetch("Quy định hộ chiếu ngoại giao thế nào?")

        self.assertAlmostEqual(result.relevance, 0.42)

    def test_unaccented_question_has_no_relevance_signal(self):
        executor = ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), _ScoringReranker())

        result = executor.prefetch("quy dinh ho chieu ngoai giao the nao")

        self.assertIsNone(result.relevance)

    def test_without_a_reranker_relevance_is_unknown(self):
        executor = ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), reranker=None)

        result = executor.prefetch("Quy định hộ chiếu ngoại giao thế nào?")

        self.assertIsNone(result.relevance)

    def test_a_scope_the_user_chose_is_never_gated(self):
        executor = ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), _ScoringReranker(),
                                scope_doc_ids=[1])

        result = executor.prefetch("Quy định hộ chiếu ngoại giao thế nào?")

        self.assertIsNone(result.relevance)
        self.assertEqual({source["doc_id"] for source in result.sources}, {1})


class _KeywordReranker:
    """Scores a passage high when it shares the question's key phrase."""

    def is_available(self):
        return True

    def rerank(self, query, texts):
        def score(text):
            if "thay thế" in query and "thay thế" in text:
                return 0.8
            return 0.7 if "ngân sách" in text else 0.3
        return [score(text) for text in texts]


class DocumentScopeTests(unittest.TestCase):
    """The open document ("Tài liệu đang mở") and documents named by number."""

    def setUp(self):
        self.conn = _make_db()
        self.conn.executemany(
            "INSERT INTO document_chunks VALUES (?, 1, ?, ?, NULL, ?)",
            [(11, 1, "Phần hai: trình tự cấp hộ chiếu.", 5),
             (12, 2, "Phần ba: điều khoản thi hành.", 6)],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _executor(self, reranker=None, scope=None):
        return ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), reranker,
                            scope_doc_ids=scope)

    def test_summary_of_the_open_document_reads_it_from_the_beginning(self):
        result = self._executor(_ScoringReranker(), scope=[1]).prefetch("tóm tắt tài liệu đang mở")

        blocks = result.text.split("\n\n")
        self.assertIn("thông tin văn bản", blocks[0])
        self.assertIn("Quy định hộ chiếu", blocks[0])
        self.assertEqual([s["doc_id"] for s in result.sources], [1, 1, 1, 1])
        self.assertEqual([s["page"] for s in result.sources], [None, 4, 5, 6])
        self.assertLess(result.text.index("Phần hai"), result.text.index("Phần ba"))
        self.assertIsNone(result.relevance)

    def test_a_detail_question_in_the_open_document_leads_with_its_card(self):
        result = self._executor(_ScoringReranker(), scope=[1]).prefetch("Thời hạn hộ chiếu là bao lâu?")

        self.assertIn("thông tin văn bản", result.text.split("\n\n")[0])
        self.assertEqual({s["doc_id"] for s in result.sources}, {1})
        self.assertIsNone(result.relevance)

    def test_a_named_document_keeps_other_documents_out_unless_they_answer_better(self):
        named = self._executor(_KeywordReranker()).prefetch("Văn bản 03/2023/TT-BTC quy định gì?")
        replaced = self._executor(_KeywordReranker()).prefetch(
            "Thông tư 01/2020/TT-BCA bị văn bản nào thay thế?")

        self.assertEqual({s["doc_id"] for s in named.sources}, {3})
        self.assertEqual(replaced.sources[0]["doc_id"], 1)
        self.assertIn(2, [s["doc_id"] for s in replaced.sources])

    def test_a_summary_of_a_named_document_reads_that_document(self):
        result = self._executor().prefetch("Tóm tắt Thông tư 01/2020/TT-BCA")

        self.assertEqual({s["doc_id"] for s in result.sources}, {1})
        self.assertIn("Phần hai", result.text)


class NamedDocumentsTests(unittest.TestCase):
    """Documents a question names or points at (doc_resolver), pinned by
    RagAgent: each gets its card and chunks in its share of the budget."""

    def setUp(self):
        self.conn = _make_db()
        self.conn.executemany(
            "INSERT INTO document_chunks VALUES (?, 1, ?, ?, NULL, ?)",
            [(11, 1, "Phần hai: trình tự cấp hộ chiếu. " * 40, 5),
             (12, 2, "Phần ba: điều khoản thi hành nghị định. " * 40, 6)],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _executor(self, budget=RESULT_BUDGET_CHARS, scope=None, reranker=None):
        return ToolExecutor(self.conn, _VectorIndex(), _EmbeddingModel(), reranker,
                            scope_doc_ids=scope, result_budget_chars=budget)

    def test_two_documents_each_get_their_card_and_chunks(self):
        result = self._executor(budget=3600).prefetch(
            "so sánh hai văn bản về hộ chiếu", documents=[2, 1])

        cards = [block for block in result.text.split("\n\n") if "thông tin văn bản" in block]
        self.assertEqual(2, len(cards))
        self.assertIn("02/2024/NĐ-CP", cards[0])
        self.assertIn("01/2020/TT-BCA", cards[1])
        self.assertEqual([2, 1], result.documents)
        self.assertEqual({1, 2}, {s["doc_id"] for s in result.sources})
        self.assertEqual(len(result.text.split("\n\n")), len(result.sources))
        self.assertLessEqual(len(result.text), 3600 + 200)
        self.assertIsNone(result.relevance)

    def test_a_summary_of_two_documents_reads_each_from_its_beginning(self):
        result = self._executor().prefetch("tóm tắt hai văn bản", documents=[1, 2])

        self.assertLess(result.text.index("Quy định cấp hộ chiếu ngoại giao"),
                        result.text.index("Nghị định thay thế quy định hộ chiếu cũ"))

    def test_one_pinned_document_is_read_like_a_numbered_one(self):
        pinned = self._executor().prefetch("Văn bản này quy định gì?", documents=[3])
        numbered = self._executor().prefetch("Văn bản 03/2023/TT-BTC quy định gì?")

        self.assertEqual(3, pinned.sources[0]["doc_id"])
        self.assertIn("thông tin văn bản", pinned.text.split("\n\n")[0])
        self.assertEqual(numbered.sources[0], self._executor().prefetch(
            "Văn bản 03/2023/TT-BTC quy định gì?", documents=[3]).sources[0])

    def test_one_document_alone_skips_the_search_of_the_whole_store(self):
        import agent_tools
        from unittest.mock import patch

        calls = []
        real = agent_tools.hybrid_search

        def recording(*args, **kwargs):
            calls.append(kwargs.get("doc_ids"))
            return real(*args, **kwargs)

        with patch.object(agent_tools, "hybrid_search", recording):
            result = self._executor().prefetch("Thời hạn hộ chiếu?", documents=[2], others=False)

        self.assertEqual([[2]], calls)
        self.assertEqual({2}, {s["doc_id"] for s in result.sources})
        self.assertIn("thông tin văn bản", result.text.split("\n\n")[0])

    def test_resolve_looks_in_the_whole_store_and_reads_the_scope_as_the_open_document(self):
        executor = self._executor(scope=[1])

        self.assertEqual([3], executor.resolve("Văn bản 03/2023/TT-BTC quy định gì?").docs)
        self.assertEqual([1], executor.resolve("Văn bản này do ai ký?").docs)
        self.assertEqual("01/2020/TT-BCA", executor.catalog.label(1))


class TunedScaleTests(unittest.TestCase):
    """The reranker returns probabilities now (no second sigmoid); prefetch
    compares them on the old scale, so it selects the same chunks."""

    def test_the_selection_rule_reads_scores_on_the_old_scale(self):
        from agent_tools import _tuned_scale

        self.assertAlmostEqual(0.5, _tuned_scale(0.0))
        self.assertAlmostEqual(0.7311, _tuned_scale(1.0), places=4)
        old_best, old_close, old_far = _tuned_scale(0.98), _tuned_scale(0.40), _tuned_scale(0.30)
        tolerance = old_best * 0.20
        self.assertLessEqual(old_best - old_close, tolerance)
        self.assertGreater(old_best - old_far, tolerance)


if __name__ == "__main__":
    unittest.main()
