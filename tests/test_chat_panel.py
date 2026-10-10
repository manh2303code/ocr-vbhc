import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMainWindow

from rag_agent import AgentResult, Citation
from chat_panel import ChatPanel


class _FakeServer:
    def __init__(self, backend="cpu", running=True):
        self.backend = backend
        self.url = "http://127.0.0.1:9998"
        self.is_running = running
        self.started = 0
        self.stopped = 0

    def start_chat(self):
        self.started += 1
        self.is_running = True

    def stop(self):
        self.stopped += 1
        self.is_running = False


class _FakeWorker(QObject):
    step = pyqtSignal(str)
    token = pyqtSignal(str)
    finished_result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, agent, question, history=None):
        super().__init__()
        self.agent = agent
        self.question = question
        self.history = history
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return self.started and not self.cancelled

    def wait(self, _timeout=0):
        return True


class _FakeServerWorker(QObject):
    ready = pyqtSignal()
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, server):
        super().__init__()
        self.server = server
        self.started = False
        self.interrupted = False

    def start(self):
        self.started = True

    def requestInterruption(self):
        self.interrupted = True

    def wait(self, _timeout=0):
        return True


class _FakeAgent:
    def __init__(self, client, executor, max_rounds):
        self.client = client
        self.executor = executor
        self.max_rounds = max_rounds


class _FakeHistory:
    def __init__(self, session_id=None, messages=None, fail=False):
        self.session_id = session_id
        self.messages = list(messages or [])
        self.fail = fail
        self.created = []
        self.appended = []
        self.deleted = []

    def load_last_session(self):
        if self.fail:
            raise RuntimeError("history unavailable")
        return self.session_id, list(self.messages)

    def load_session(self, session_id):
        if self.fail:
            raise RuntimeError("history unavailable")
        return session_id, list(self.messages)

    def list_sessions(self, limit=20):
        del limit
        if self.fail:
            raise RuntimeError("history unavailable")
        return ([{"id": self.session_id, "title": "Phiên kiểm thử", "created_at": ""}]
                if self.session_id is not None else [])

    def delete_session(self, session_id):
        if self.fail:
            raise RuntimeError("history unavailable")
        self.deleted.append(session_id)
        if self.session_id == session_id:
            self.session_id = None
            self.messages = []
        return True

    def new_session(self, title):
        if self.fail:
            raise RuntimeError("history unavailable")
        self.created.append(title)
        self.session_id = 77
        return self.session_id

    def append(self, session_id, role, content, citations=None):
        if self.fail:
            raise RuntimeError("history unavailable")
        self.appended.append((session_id, role, content, list(citations or [])))


class ChatPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "documents.db")
        self.scope_calls = []
        self.server = _FakeServer()
        self.history = _FakeHistory()
        self.window = QMainWindow()
        self.panel = ChatPanel(
            self.window,
            self.db_path,
            embedding_model=object(),
            reranker=object(),
            server_manager=self.server,
            executor_factory=self._make_executor,
            client_factory=lambda _url: object(),
            agent_factory=_FakeAgent,
            worker_factory=_FakeWorker,
            history_store=self.history,
        )

    def tearDown(self):
        self.panel.shutdown()
        self.panel.deleteLater()
        self.window.deleteLater()
        self.temp_dir.cleanup()

    def _make_executor(self, scope_doc_ids):
        self.scope_calls.append(scope_doc_ids)
        return {"scope": scope_doc_ids}

    def test_construct_with_fakes_does_not_start_server(self):
        self.assertEqual("Trợ lý pháp luật", self.panel.windowTitle())
        self.assertEqual(0, self.server.started)
        self.assertIn("không thay thế tư vấn pháp lý", self.panel.disclaimer_label.text())

    def test_render_citations_creates_clickable_anchors_and_escapes_text(self):
        citations = [
            Citation(1, 11, 3, "01/QĐ", 7),
            Citation(2, 12, None, "02/QĐ", 8),
        ]

        rendered = self.panel._render_citations(
            "Theo [1] và [2]\n<script>x</script>", citations
        )

        self.assertIn('href="cite:1"', rendered)
        self.assertIn('href="cite:2"', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_set_scope_doc_ids_rebuilds_executor_for_current_document_scope(self):
        self.panel.scope_combo.setCurrentText("Tài liệu đang mở")
        self.panel.set_scope_doc_ids([9, 9, 4])

        self.assertEqual([9, 4], self.scope_calls[-1])

        self.panel.scope_combo.setCurrentText("Toàn kho")
        self.assertIsNone(self.scope_calls[-1])

    def test_citation_click_emits_document_and_default_page(self):
        citations = [Citation(2, 12, None, "02/QĐ", 8)]
        self.panel._render_citations("Theo [2]", citations)
        opened = []
        self.panel.open_document_requested.connect(
            lambda doc_id, page: opened.append((doc_id, page))
        )

        self.panel._on_anchor_clicked("cite:2")

        self.assertEqual([(12, 1)], opened)

    def test_agent_round_budget_and_send_stop_states(self):
        self.panel.question_input.setPlainText("Quyết định này còn hiệu lực không?")
        self.panel.send_question()

        worker = self.panel._agent_worker
        self.assertTrue(worker.started)
        self.assertFalse(self.panel.send_button.isEnabled())
        self.assertTrue(self.panel.stop_button.isEnabled())
        self.assertEqual(4, worker.agent.max_rounds)

        worker.step.emit("check_validity")
        self.assertIn("Đang kiểm tra hiệu lực", self.panel.steps_label.text())
        worker.token.emit("Đang trả lời")
        result = AgentResult(
            "Văn bản còn hiệu lực [1]",
            [Citation(1, 7, 2, "01/QĐ", 3)],
            ["check_validity"],
        )
        worker.finished_result.emit(result)
        worker.finished.emit()

        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertIn("Đã hoàn tất", self.panel.steps_label.text())
        self.assertIn('href="cite:1:1"', self.panel.chat_browser.toHtml())

    def test_stream_hides_reasoning_even_when_tag_arrives_in_fragments(self):
        self.panel._on_token("<thi")
        self.panel._on_token("nk>phân tích nội bộ")
        self.assertNotIn("phân tích nội bộ", self.panel.chat_browser.toPlainText())
        self.panel._on_token("</think>Kết luận hiển thị")
        self.assertIn("Kết luận hiển thị", self.panel.chat_browser.toPlainText())
        self.assertNotIn("phân tích nội bộ", self.panel.chat_browser.toPlainText())

    def test_cuda_uses_eight_rounds_and_stop_cancels_worker(self):
        self.server.backend = "cuda"
        self.panel.question_input.setPlainText("Tìm căn cứ")
        self.panel.send_question()
        worker = self.panel._agent_worker

        self.assertEqual(8, worker.agent.max_rounds)
        self.panel.stop_generation()

        self.assertTrue(worker.cancelled)
        self.assertFalse(self.panel.send_button.isEnabled())
        self.assertFalse(self.panel.question_input.isEnabled())
        worker.finished.emit()
        self.assertTrue(self.panel.send_button.isEnabled())

    def test_first_send_starts_server_lazily_and_missing_model_is_friendly(self):
        self.server.is_running = False
        starter = _FakeServerWorker(self.server)
        self.panel._server_worker_factory = lambda _manager: starter
        self.panel.question_input.setPlainText("Tìm văn bản")

        self.panel.send_question()

        self.assertTrue(starter.started)
        self.assertEqual(0, self.server.started)
        self.assertIn("30-60s", self.panel.steps_label.text())
        starter.error.emit("Chat model GGUF not found")
        self.assertIn("Chưa có model trợ lý", self.panel.steps_label.text())
        starter.finished.emit()
        self.assertTrue(self.panel.send_button.isEnabled())

    def test_loads_last_session_and_keeps_old_citations_bound_to_their_turn(self):
        history = _FakeHistory(12, [
            {"role": "assistant", "content": "Nguồn cũ [1]", "citations": [
                Citation(1, 101, 2, "CŨ", 1)
            ]},
            {"role": "user", "content": "Câu tiếp", "citations": []},
            {"role": "assistant", "content": "Nguồn mới [1]", "citations": [
                Citation(1, 202, 5, "MỚI", 2)
            ]},
        ])
        panel = ChatPanel(
            self.window, self.db_path, object(), object(),
            server_manager=self.server,
            executor_factory=self._make_executor,
            client_factory=lambda _url: object(),
            agent_factory=_FakeAgent,
            worker_factory=_FakeWorker,
            history_store=history,
        )
        opened = []
        panel.open_document_requested.connect(lambda doc, page: opened.append((doc, page)))

        html = panel.chat_browser.toHtml()
        self.assertIn("cite:0:1", html)
        self.assertIn("cite:2:1", html)
        panel._on_anchor_clicked("cite:0:1")
        panel._on_anchor_clicked("cite:2:1")

        self.assertEqual([(101, 2), (202, 5)], opened)
        panel.shutdown()
        panel.deleteLater()

    def test_first_send_creates_session_persists_messages_and_passes_plain_history(self):
        self.panel.question_input.setPlainText("Câu hỏi đầu tiên")
        self.panel.send_question()
        worker = self.panel._agent_worker

        self.assertEqual(["Câu hỏi đầu tiên"], self.history.created)
        self.assertEqual((77, "user", "Câu hỏi đầu tiên", []), self.history.appended[0])
        self.assertEqual([], worker.history)

        result = AgentResult(
            "Câu trả lời [1]", [Citation(1, 7, 3, "01/QĐ", 8)], []
        )
        worker.finished_result.emit(result)
        self.assertEqual("assistant", self.history.appended[-1][1])
        self.assertEqual("Câu trả lời [1]", self.history.appended[-1][2])
        self.assertEqual(1, len(self.history.appended[-1][3]))

    def test_history_context_is_bounded_and_current_session_can_be_deleted(self):
        self.panel._messages = [
            ("user" if index % 2 == 0 else "assistant", "lượt {}".format(index), [])
            for index in range(30)
        ]
        context = self.panel._agent_history()
        self.panel._session_id = 77
        self.panel.delete_current_session(confirm=False)

        self.assertLessEqual(len(context), 12)
        self.assertEqual("lượt 18", context[0]["content"])
        self.assertEqual([77], self.history.deleted)
        self.assertEqual([], self.panel._messages)
        self.assertIsNone(self.panel._session_id)

    def test_history_failure_does_not_block_chat(self):
        panel = ChatPanel(
            self.window, self.db_path, object(), object(),
            server_manager=self.server,
            executor_factory=self._make_executor,
            client_factory=lambda _url: object(),
            agent_factory=_FakeAgent,
            worker_factory=_FakeWorker,
            history_store=_FakeHistory(fail=True),
        )
        panel.question_input.setPlainText("Vẫn hỏi được")
        panel.send_question()

        self.assertTrue(panel._agent_worker.started)
        self.assertIn("Lịch sử", panel.history_status_label.text())
        panel.shutdown()
        panel.deleteLater()

    def test_shutdown_reports_worker_that_did_not_stop(self):
        class _CloseRecorder:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        worker = _FakeWorker(None, "")
        worker.started = True
        worker.wait = lambda _timeout=0: False
        http = _CloseRecorder()
        database = _CloseRecorder()
        self.panel._client = type("Client", (), {"client": http})()
        self.panel._db_conn = database
        self.panel._agent_worker = worker

        self.assertIs(False, self.panel.shutdown())
        self.assertFalse(http.closed)
        self.assertFalse(database.closed)

        # Worker finishes after the first close attempt. A repeated shutdown
        # must finish dependency cleanup instead of returning early.
        self.panel._agent_worker = None
        self.assertIs(True, self.panel.shutdown())
        self.assertTrue(http.closed)
        self.assertTrue(database.closed)


if __name__ == "__main__":
    unittest.main()
