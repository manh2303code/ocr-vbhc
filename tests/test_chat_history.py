import sqlite3
import tempfile
import unittest
from pathlib import Path

from chat_history import ChatHistory, init_tables
from document_repository import DocumentDatabase
from rag_agent import Citation


class ChatHistorySchemaTests(unittest.TestCase):
    def test_init_tables_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        try:
            init_tables(conn.cursor())
            init_tables(conn.cursor())

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({"chat_sessions", "chat_messages"} <= tables)
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            self.assertIn("idx_chat_messages_session", indexes)
        finally:
            conn.close()

    def test_document_database_initializes_chat_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DocumentDatabase(Path(tmp) / "documents.db")
            try:
                conn = db.conn_pool.get_connection()
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue({"chat_sessions", "chat_messages"} <= tables)
            finally:
                db.close()


class ChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"
        with sqlite3.connect(self.db_path) as conn:
            init_tables(conn.cursor())
        self.history = ChatHistory(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_session_returns_none_and_empty_messages(self):
        self.assertEqual((None, []), self.history.load_last_session())
        self.assertEqual([], self.history.list_sessions())

    def test_unicode_messages_and_citations_round_trip_as_citation_objects(self):
        session_id = self.history.new_session("Hỏi về hộ chiếu 🇻🇳")
        citations = [
            Citation(1, 7, 3, "01/2026/TT-BCA", 11),
            Citation(2, 8, None, "Nghị định 42/2025/NĐ-CP", None),
        ]

        self.history.append(session_id, "user", "Văn bản này còn hiệu lực không?")
        self.history.append(
            session_id,
            "assistant",
            "Còn hiệu lực theo [1] và [2].",
            citations,
        )

        loaded_id, messages = self.history.load_last_session()
        self.assertEqual(session_id, loaded_id)
        self.assertEqual(["user", "assistant"], [item["role"] for item in messages])
        self.assertEqual("Văn bản này còn hiệu lực không?", messages[0]["content"])
        self.assertEqual([], messages[0]["citations"])
        self.assertEqual(citations, messages[1]["citations"])

    def test_last_session_and_list_sessions_use_newest_id_order_for_timestamp_ties(self):
        first = self.history.new_session("Phiên đầu")
        second = self.history.new_session("Phiên sau")
        self.history.append(first, "user", "cũ")
        self.history.append(second, "user", "mới")

        loaded_id, messages = self.history.load_last_session()

        self.assertEqual(second, loaded_id)
        self.assertEqual("mới", messages[0]["content"])
        sessions = self.history.list_sessions(limit=1)
        self.assertEqual([second], [item["id"] for item in sessions])
        self.assertEqual("Phiên sau", sessions[0]["title"])

    def test_load_and_delete_one_session_without_touching_others(self):
        first = self.history.new_session("Phiên đầu")
        second = self.history.new_session("Phiên sau")
        self.history.append(first, "user", "giữ lại")
        self.history.append(second, "user", "xóa đi")

        loaded_id, messages = self.history.load_session(first)
        deleted = self.history.delete_session(second)

        self.assertEqual(first, loaded_id)
        self.assertEqual("giữ lại", messages[0]["content"])
        self.assertTrue(deleted)
        self.assertFalse(self.history.delete_session(second))
        self.assertEqual([first], [item["id"] for item in self.history.list_sessions()])

    def test_malformed_citations_json_degrades_to_empty_list(self):
        session_id = self.history.new_session("Dữ liệu cũ")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, citations_json) VALUES (?, ?, ?, ?)",
                (session_id, "assistant", "Không làm ứng dụng lỗi", "{broken"),
            )

        _, messages = self.history.load_last_session()

        self.assertEqual([], messages[0]["citations"])

    def test_append_requires_an_existing_session(self):
        with self.assertRaises(ValueError):
            self.history.append(999, "user", "Không có phiên")

    def test_list_sessions_validates_limit(self):
        with self.assertRaises(ValueError):
            self.history.list_sessions(0)


if __name__ == "__main__":
    unittest.main()
