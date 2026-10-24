import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QAbstractButton, QApplication, QLabel, QLineEdit, QMainWindow

from chat_panel import ChatPanel
from llm_client import estimate_prompt_tokens
from rag_agent import DISCLAIMER_VI, AgentResult, Citation
from chat_widgets import AssistantTurnView, UserMessageView


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
    sources = pyqtSignal(object)
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

    def delete_last_message(self, session_id, role):
        self.deleted_last = getattr(self, "deleted_last", []) + [(session_id, role)]
        return True

    def append(self, session_id, role, content, citations=None, meta=None):
        if self.fail:
            raise RuntimeError("history unavailable")
        self.appended.append((session_id, role, content, list(citations or [])))
        self.metas = getattr(self, "metas", []) + [meta]


class _PanelFixture(unittest.TestCase):
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
        # Delete them now. Left alive, every later app-wide restyle runs the
        # chat's Python layout code, and a garbage collection that fires
        # there can free another test's leftover widget while Qt is still
        # iterating over it.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp_dir.cleanup()

    def _make_executor(self, scope_doc_ids):
        self.scope_calls.append(scope_doc_ids)
        return {"scope": scope_doc_ids}


class ChatPanelTests(_PanelFixture):
    def test_construct_with_fakes_does_not_start_server(self):
        self.assertEqual("Trợ lý pháp luật", self.panel.windowTitle())
        self.assertEqual(0, self.server.started)

    def test_the_legal_note_is_always_one_hover_away_and_said_once_on_a_new_chat(self):
        # Not printed under every answer: on the info button of the header,
        # always there, and in small print on a new conversation.
        self.assertIn("không thay thế tư vấn pháp lý", self.panel.info_button.toolTip())
        self.assertIn("không thay thế tư vấn pháp lý", self.panel.info_text())
        self.assertIn("không thay thế tư vấn pháp lý", self.panel.empty_state.note.text())
        self.assertFalse(hasattr(self.panel, "disclaimer_label"))

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

    def test_a_question_about_the_open_document_searches_only_it(self):
        self.panel.set_scope_doc_ids([7])
        self.panel.scope_combo.setCurrentText("Toàn kho")

        for question in ("tóm tắt tài liệu đang mở", "Văn bản tôi đang xem do ai ký?"):
            self.panel._replace_executor(question)
            self.assertEqual([7], self.scope_calls[-1], question)
        for question in ("Văn bản hiện hành về thuế", "Tóm tắt Kế hoạch 336/KH-KTNS"):
            self.panel._replace_executor(question)
            self.assertIsNone(self.scope_calls[-1], question)

        self.panel.set_scope_doc_ids(None)
        self.panel._replace_executor("tóm tắt tài liệu đang mở")
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
        self.assertIn('href="cite:1:1"', self.panel._transcript_html())

    def test_stream_hides_reasoning_even_when_tag_arrives_in_fragments(self):
        self.panel._on_token("<thi")
        self.panel._on_token("nk>phân tích nội bộ")
        self.panel._flush_stream()
        self.assertNotIn("phân tích nội bộ", self.panel._live_text())
        self.panel._on_token("</think>Kết luận hiển thị")
        self.panel._flush_stream()
        self.assertIn("Kết luận hiển thị", self.panel._live_text())
        self.assertNotIn("phân tích nội bộ", self.panel._live_text())

    def test_context_size_follows_running_model_and_backend(self):
        self.assertIsNone(self.panel._chat_context_size())
        self.server.active_model_key = "qwen3-4b"
        self.assertEqual(8192, self.panel._chat_context_size())
        self.server.backend = "cuda"
        self.assertEqual(16384, self.panel._chat_context_size())

    def test_context_size_is_what_the_server_was_started_with(self):
        self.server.active_model_key = "qwen3-8b"
        self.server.backend = "cuda"
        self.server.active_context_size = 24576
        self.assertEqual(24576, self.panel._chat_context_size())

    def test_cuda_uses_three_rounds_and_stop_cancels_worker(self):
        self.server.backend = "cuda"
        self.panel.question_input.setPlainText("Tìm căn cứ")
        self.panel.send_question()
        worker = self.panel._agent_worker

        self.assertEqual(8, worker.agent.max_rounds)
        self.panel.stop_generation()

        self.assertTrue(worker.cancelled)
        self.assertFalse(self.panel.send_button.isEnabled())
        # Stop is acknowledged at once but cannot be pressed twice; the box
        # stays editable so the next question can be typed meanwhile.
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertTrue(self.panel.question_input.isEnabled())
        self.assertIn("Đang dừng", self.panel.steps_label.text())
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

        html = panel._transcript_html()
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

    def test_history_context_drops_old_citation_numbers_and_footers(self):
        from rag_agent import DISCLAIMER_VI

        self.server.active_model_key = "qwen3-4b"
        self.panel._messages = [
            ("user", "Hộ chiếu có thời hạn bao lâu?", []),
            ("assistant", "Thời hạn 5 năm [1].\n\nNguồn: [1]\n\n" + DISCLAIMER_VI,
             [Citation(1, 7, 2, "01/QĐ", 3)]),
        ]

        context = self.panel._agent_history()

        self.assertEqual(
            [{"role": "user", "content": "Hộ chiếu có thời hạn bao lâu?"},
             {"role": "assistant", "content": "Thời hạn 5 năm."}],
            context,
        )

    def test_history_context_is_capped_by_tokens_for_the_cpu_context(self):
        self.server.active_model_key = "qwen3-4b"  # cpu: 8192 tokens
        self.panel._messages = [
            ("user" if index % 2 == 0 else "assistant", "nội dung dài " * 300, [])
            for index in range(12)
        ]

        context = self.panel._agent_history()

        # One 1500-token turn alone is over the 800-token CPU cap: the
        # beginning of its question and answer is kept rather than losing
        # the previous turn entirely, and the answer keeps its question.
        self.assertEqual(["user", "assistant"], [message["role"] for message in context])
        for message in context:
            self.assertTrue(message["content"].startswith("nội dung dài nội dung dài"))
        self.assertLessEqual(estimate_prompt_tokens(context), 800)

    def test_history_is_bounded_by_the_context_the_server_started_with(self):
        # The first question after opening the app starts the server: its
        # context (8192 on the CPU) is known only once it is running.
        self.server.is_running = False
        starter = _FakeServerWorker(self.server)
        self.panel._server_worker_factory = lambda _manager: starter
        self.panel._messages = [
            ("user", "Câu hỏi trước", []),
            ("assistant", "nội dung trả lời " * 200, []),
        ]
        self.panel.question_input.setPlainText("Câu hỏi tiếp theo")
        self.panel.send_question()

        self.server.active_model_key = "qwen3-4b"  # as start_chat sets them
        self.server.active_context_size = 8192
        self.server.is_running = True
        starter.ready.emit()

        history = self.panel._agent_worker.history
        self.assertEqual(["user", "assistant"], [message["role"] for message in history])
        self.assertEqual("Câu hỏi trước", history[0]["content"])
        self.assertLessEqual(estimate_prompt_tokens(history), 800)
        self.assertEqual("Câu hỏi tiếp theo", self.panel._agent_worker.question)

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


class ChatExperienceTests(_PanelFixture):
    """The redesigned panel: streaming, working state, stop, keyboard, sources."""

    def _send(self, question="Văn bản này còn hiệu lực không?"):
        self.panel.question_input.setPlainText(question)
        self.panel.send_question()
        return self.panel._agent_worker

    def _finish(self, worker, answer="Còn hiệu lực [1]", citations=None):
        citations = citations if citations is not None else [Citation(1, 7, 2, "01/QĐ", 3)]
        worker.finished_result.emit(AgentResult(answer, citations, []))
        worker.finished.emit()

    def test_enter_sends_and_shift_enter_adds_a_line(self):
        box = self.panel.question_input
        QTest.keyClicks(box, "Dong mot")
        QTest.keyClick(box, Qt.Key_Return, Qt.ShiftModifier)
        QTest.keyClicks(box, "dong hai")
        self.assertIsNone(self.panel._agent_worker)

        QTest.keyClick(box, Qt.Key_Return)

        self.assertEqual("Dong mot\ndong hai", self.panel._agent_worker.question)
        self.assertEqual("", box.toPlainText())

    def test_enter_while_answering_keeps_the_draft_and_starts_nothing(self):
        first = self._send()
        QTest.keyClicks(self.panel.question_input, "cau tiep")
        QTest.keyClick(self.panel.question_input, Qt.Key_Return)

        self.assertIs(first, self.panel._agent_worker)
        self.assertEqual("cau tiep", self.panel.question_input.toPlainText())

    def test_escape_in_the_box_stops_the_answer(self):
        worker = self._send()
        QTest.keyClick(self.panel.question_input, Qt.Key_Escape)

        self.assertTrue(worker.cancelled)

    def test_send_button_turns_into_stop_while_busy(self):
        self.window.show()
        self.assertTrue(self.panel.send_button.isVisibleTo(self.window))
        self.assertFalse(self.panel.stop_button.isVisibleTo(self.window))

        worker = self._send()
        self.assertFalse(self.panel.send_button.isVisibleTo(self.window))
        self.assertTrue(self.panel.stop_button.isVisibleTo(self.window))
        self.assertTrue(self.panel.stop_button.isEnabled())

        self._finish(worker)
        self.assertTrue(self.panel.send_button.isVisibleTo(self.window))
        self.assertFalse(self.panel.stop_button.isVisibleTo(self.window))

    def test_working_indicator_runs_exactly_while_busy(self):
        self.window.show()
        row = self.panel.status_row
        self.assertFalse(row.isVisibleTo(self.window))
        self.assertFalse(self.panel.working_indicator.is_running())

        worker = self._send()
        self.assertTrue(row.isVisibleTo(self.window))
        self.assertTrue(self.panel.working_indicator.is_running())
        self.assertIn("tra cứu", self.panel.steps_label.text())

        self._finish(worker)
        self.assertFalse(row.isVisibleTo(self.window))
        self.assertFalse(self.panel.working_indicator.is_running())

    def test_indicator_also_stops_after_a_stop(self):
        worker = self._send()
        self.panel.stop_generation()
        worker.finished_result.emit(AgentResult("", [], [], "cancelled"))
        worker.finished.emit()

        self.assertFalse(self.panel.working_indicator.is_running())

    def test_step_labels_follow_the_real_phase(self):
        worker = self._send()
        worker.sources.emit([Citation(n, n, 1, "SỐ {}".format(n), None) for n in (1, 2, 3)])
        worker.step.emit("prefetch_documents")
        self.assertIn("Đang đọc 3 nguồn", self.panel.steps_label.text())

        worker.token.emit("Văn bản")
        self.panel._flush_stream()
        self.assertIn("Đang soạn câu trả lời", self.panel.steps_label.text())

    def test_tokens_append_without_rebuilding_the_transcript(self):
        history = _FakeHistory(12, [
            {"role": "user", "content": "Hỏi trước", "citations": []},
            {"role": "assistant", "content": "Đáp trước", "citations": []},
        ])
        panel = ChatPanel(
            self.window, self.db_path, object(), object(),
            server_manager=self.server, executor_factory=self._make_executor,
            client_factory=lambda _url: object(), agent_factory=_FakeAgent,
            worker_factory=_FakeWorker, history_store=history,
        )
        panel.question_input.setPlainText("Hỏi mới")
        panel.send_question()
        worker = panel._agent_worker
        earlier = [view for _index, view in panel._message_views]

        with mock.patch.object(panel, "_render_messages", side_effect=AssertionError):
            worker.token.emit("Một")
            worker.token.emit(" hai")
            panel._flush_stream()
            worker.token.emit(" ba")
            panel._flush_stream()

        self.assertEqual("Một hai ba", panel._live_text())
        self.assertEqual(earlier, [view for _index, view in panel._message_views][:len(earlier)])
        panel.shutdown()
        panel.deleteLater()

    def test_a_burst_of_tokens_is_drawn_once_per_frame(self):
        worker = self._send()
        before = self.panel._stream_renders
        for _ in range(50):
            worker.token.emit("x")

        # The first visible token is drawn at once, the rest wait for the frame.
        self.assertEqual(before + 1, self.panel._stream_renders)
        QTest.qWait(80)
        self.assertEqual(before + 2, self.panel._stream_renders)
        self.assertEqual("x" * 50, self.panel._live_text())

    def test_text_streamed_before_a_tool_call_is_withdrawn(self):
        worker = self._send()
        worker.token.emit("Tôi sẽ tra thêm")
        self.panel._flush_stream()
        worker.step.emit("search_documents")

        self.assertEqual("", self.panel._live_text())
        self.assertIn("Đang tìm", self.panel.steps_label.text())

    def test_stop_keeps_the_partial_answer_and_offers_retry(self):
        worker = self._send()
        worker.token.emit("Phần đã viết")
        self.panel._flush_stream()
        self.panel.stop_generation()
        worker.finished_result.emit(AgentResult("", [], [], "cancelled"))
        worker.finished.emit()

        live = self.panel._last_turn_view()
        self.assertIn("Phần đã viết", self.panel._live_text(live))
        self.assertIn("Đã dừng", live.notice_text())
        self.assertTrue(live.retry_available())
        self.assertIn("Đã dừng", self.panel.steps_label.text())
        self.assertEqual(["user"], [role for role, _c, _s in self.panel._messages])

    def test_tokens_after_stop_are_not_drawn(self):
        worker = self._send()
        worker.token.emit("Trước")
        self.panel._flush_stream()
        self.panel.stop_generation()
        worker.token.emit(" sau")
        self.panel._flush_stream()

        self.assertEqual("Trước", self.panel._live_text())

    def test_retry_after_stop_asks_the_same_question_once(self):
        worker = self._send("Câu cần hỏi lại")
        self.panel.stop_generation()
        worker.finished_result.emit(AgentResult("", [], [], "cancelled"))
        worker.finished.emit()

        self.panel._last_turn_view().retry_button.click()

        retried = self.panel._agent_worker
        self.assertIsNot(worker, retried)
        self.assertEqual("Câu cần hỏi lại", retried.question)
        self.assertEqual([], retried.history)
        self.assertEqual(1, sum(1 for role, _c, _s in self.panel._messages if role == "user"))

    def test_error_card_offers_retry(self):
        worker = self._send("Câu lỗi")
        worker.error.emit("server unavailable")
        worker.finished.emit()

        view = self.panel._last_turn_view()
        self.assertTrue(view.error_visible())
        self.assertTrue(view.retry_available())
        view.retry_button.click()
        self.assertEqual("Câu lỗi", self.panel._agent_worker.question)

    def test_scope_combo_draws_its_own_dropdown_chevron(self):
        from chat_widgets import CompactCombo

        self.assertIsInstance(self.panel.scope_combo, CompactCombo)

    def test_server_start_failure_is_an_error_card_with_retry(self):
        self.server.is_running = False
        starter = _FakeServerWorker(self.server)
        self.panel._server_worker_factory = lambda _manager: starter
        self._send("Tìm văn bản")
        starter.error.emit("Chat model GGUF not found")
        starter.finished.emit()

        view = self.panel._last_turn_view()
        self.assertTrue(view.error_visible())
        self.assertIn("Chưa có model trợ lý", view.error_text())

    def test_source_chip_opens_the_document_at_its_page(self):
        worker = self._send()
        opened = []
        self.panel.open_document_requested.connect(lambda d, p: opened.append((d, p)))
        self._finish(worker, "Theo [1] và [2]", [
            Citation(1, 7, 2, "01/QĐ", 3), Citation(2, 9, None, "02/QĐ", 4),
        ])

        chips = self.panel._last_turn_view().source_chips()
        self.assertEqual(2, len(chips))
        chips[1].click()
        chips[0].click()

        self.assertEqual([(9, 1), (7, 2)], opened)

    def test_sources_being_read_are_clickable_before_the_answer(self):
        worker = self._send()
        opened = []
        self.panel.open_document_requested.connect(lambda d, p: opened.append((d, p)))
        worker.sources.emit([Citation(1, 5, 8, "05/KH", None)])

        chips = self.panel._live.source_chips()
        self.assertEqual(1, len(chips))
        chips[0].click()
        self.assertEqual([(5, 8)], opened)

    def test_sources_read_are_replaced_by_the_ones_the_answer_cites(self):
        worker = self._send()
        worker.sources.emit([Citation(n, n, 1, "SỐ {}".format(n), None) for n in (1, 2, 3)])
        self._finish(worker, "Theo [2]", [Citation(2, 2, 1, "SỐ 2", None)])

        chips = self.panel._last_turn_view().source_chips()
        self.assertEqual([2], [chip.property("citationNumber") for chip in chips])
        self.assertEqual(["SỐ 2, tr. 1"], [chip.text() for chip in chips])

    def test_inline_citation_in_the_stream_links_to_the_live_source(self):
        worker = self._send()
        opened = []
        self.panel.open_document_requested.connect(lambda d, p: opened.append((d, p)))
        worker.sources.emit([Citation(1, 5, 8, "05/KH", None)])
        worker.token.emit("Theo [1]")
        self.panel._flush_stream()

        self.assertIn('href="cite:live:1"', self.panel._live.body_html())
        self.panel._on_anchor_clicked("cite:live:1")
        self.assertEqual([(5, 8)], opened)

    def test_copy_puts_the_answer_and_its_sources_on_the_clipboard(self):
        worker = self._send()
        self._finish(worker, "Còn hiệu lực [1]", [Citation(1, 7, 2, "01/QĐ", 3)])

        self.panel._last_turn_view().copy_button.click()

        text = QApplication.clipboard().text()
        self.assertIn("Còn hiệu lực [1]", text)
        self.assertIn("[1] 01/QĐ", text)
        self.assertIn("trang 2", text)

    def test_regenerate_replaces_the_last_answer(self):
        worker = self._send("Câu gốc")
        self._finish(worker, "Đáp cũ")

        self.panel._last_turn_view().regenerate_button.click()

        again = self.panel._agent_worker
        self.assertIsNot(worker, again)
        self.assertEqual("Câu gốc", again.question)
        self.assertEqual([], again.history)
        self.assertEqual(["user"], [role for role, _c, _s in self.panel._messages])
        # The stored answer goes only once its replacement exists.
        self.assertEqual([], getattr(self.history, "deleted_last", []))
        self._finish(again, "Đáp mới")
        self.assertEqual([(77, "assistant")], self.history.deleted_last)
        self.assertEqual(["user", "assistant"], [r for r, _c, _s in self.panel._messages])
        self.assertNotIn("Đáp cũ", self.panel._transcript_html())

    def test_stopping_a_regeneration_keeps_the_previous_answer(self):
        worker = self._send("Câu gốc")
        self._finish(worker, "Đáp cũ")
        self.panel._last_turn_view().regenerate_button.click()
        again = self.panel._agent_worker
        again.token.emit("Đáp mới dở")
        self.panel._flush_stream()

        self.panel.stop_generation()
        again.finished_result.emit(AgentResult("", [], [], "cancelled"))
        again.finished.emit()

        self.assertEqual(["user", "assistant"], [r for r, _c, _s in self.panel._messages])
        self.assertEqual("Đáp cũ", self.panel._messages[-1][1])
        self.assertEqual([], getattr(self.history, "deleted_last", []))
        self.assertIn("Đáp cũ", self.panel._transcript_html())

    def test_failed_regeneration_keeps_the_previous_answer(self):
        worker = self._send("Câu gốc")
        self._finish(worker, "Đáp cũ")
        self.panel._last_turn_view().regenerate_button.click()
        again = self.panel._agent_worker

        again.error.emit("máy chủ bận")
        again.finished.emit()

        self.assertEqual("Đáp cũ", self.panel._messages[-1][1])
        self.assertEqual([], getattr(self.history, "deleted_last", []))
        self.assertIn("Đáp cũ", self.panel._transcript_html())

    def test_an_unanswered_question_is_left_out_of_the_next_history(self):
        first = self._send("Câu bị dừng")
        self.panel.stop_generation()
        first.finished_result.emit(AgentResult("", [], [], "cancelled"))
        first.finished.emit()

        second = self._send("Câu tiếp theo")

        self.assertEqual([], second.history)
        roles = [m["role"] for m in self.panel._agent_history()]
        self.assertFalse(any(a == b == "user" for a, b in zip(roles, roles[1:])))

    def test_only_the_latest_answer_can_be_regenerated(self):
        first = self._send("Một")
        self._finish(first, "Đáp một")
        second = self._send("Hai")
        self._finish(second, "Đáp hai")

        views = [view for _i, view in self.panel._message_views if hasattr(view, "regenerate_button")]
        self.assertFalse(views[0].regenerate_button.isVisibleTo(views[0]))
        self.assertTrue(views[-1].regenerate_button.isVisibleTo(views[-1]))

    def test_finished_answer_shows_elapsed_time_and_speed(self):
        clock = iter(100.0 + 0.25 * step for step in range(1000))
        with mock.patch("chat_panel.time.perf_counter", lambda: next(clock)):
            worker = self._send()
            for token in ("Còn", " hiệu", " lực", " theo", " quy", " định"):
                worker.token.emit(token)
            self._finish(worker)

        view = self.panel._last_turn_view()
        # The time is a quiet word on screen; the speed is one hover away.
        self.assertIn("giây", view.meta_text())
        self.assertNotIn("token/giây", view.meta_text())
        self.assertNotIn("·", view.meta_text())
        self.assertIn("token/giây", view.meta_detail())

    def test_speed_is_not_claimed_from_an_instant_burst(self):
        worker = self._send()
        for token in ("Còn", " hiệu", " lực"):
            worker.token.emit(token)
        self._finish(worker)

        view = self.panel._last_turn_view()
        self.assertNotIn("token/giây", view.meta_text())
        self.assertNotIn("token/giây", view.meta_detail())

    def test_empty_state_suggestion_asks_its_question(self):
        self.window.show()
        self.assertTrue(self.panel.empty_state.isVisibleTo(self.window))

        button = self.panel.empty_state.suggestion_buttons()[0]
        question = button.property("question")
        button.click()

        self.assertEqual(question, self.panel._agent_worker.question)
        self.assertFalse(self.panel.empty_state.isVisibleTo(self.window))

    def test_document_suggestion_needs_an_open_document_and_asks_about_it(self):
        doc_button = self.panel.empty_state.document_button()
        self.assertFalse(doc_button.isEnabled())

        self.panel.set_scope_doc_ids([42])
        self.assertTrue(doc_button.isEnabled())
        doc_button.click()

        # That question is about the open document; the scope box is left
        # as it was (ConversationContextTests).
        self.assertEqual("Toàn kho", self.panel.scope_combo.currentText())
        self.assertEqual([42], self.scope_calls[-1])

    def test_theme_refresh_during_an_answer_keeps_the_live_turn(self):
        worker = self._send("Câu đang trả lời")
        worker.token.emit("Đang viết")
        self.panel._flush_stream()
        live = self.panel._live

        self.panel._render_messages()

        self.assertIs(live, self.panel._live)
        self.assertIn("Đang viết", self.panel._live_text())
        self.assertIn(live, self.panel.transcript.views())
        # The working line stays under the answer being written.
        self.assertEqual(
            ["U:Câu đang trả lời", "A:Đang viết", "STATUS"], self._order(with_status=True)
        )

    _SAVED = [
        {"role": "user", "content": "Hỏi trước", "citations": []},
        {"role": "assistant", "content": "Đáp trước", "citations": []},
    ]

    def _order(self, with_status=False):
        """The transcript top to bottom, as the reader sees it."""
        layout = self.panel.transcript._layout
        order = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None:
                continue
            if widget is self.panel.status_row:
                if with_status:
                    order.append("STATUS")
            elif widget is self.panel.empty_state:
                order.append("EMPTY")
            elif isinstance(widget, UserMessageView):
                order.append("U:" + widget.text)
            elif isinstance(widget, AssistantTurnView):
                order.append("A:" + widget.plain_text())
            else:
                order.append(type(widget).__name__)
        return order

    def _reopen_with(self, messages, session_id=12):
        """A fresh panel over a saved conversation, as when the app starts."""
        self.panel.shutdown()
        self.panel.deleteLater()
        self.history = _FakeHistory(session_id, messages)
        self.panel = ChatPanel(
            self.window, self.db_path, object(), object(),
            server_manager=self.server, executor_factory=self._make_executor,
            client_factory=lambda _url: object(), agent_factory=_FakeAgent,
            worker_factory=_FakeWorker, history_store=self.history,
        )
        return self.panel

    def _stop(self, worker, partial=""):
        if partial:
            worker.token.emit(partial)
            self.panel._flush_stream()
        self.panel.stop_generation()
        worker.finished_result.emit(AgentResult("", [], [], "cancelled"))
        worker.finished.emit()

    def test_a_saved_conversation_opens_without_the_suggestions(self):
        panel = self._reopen_with(self._SAVED)
        self._show_window()

        self.assertEqual(["U:Hỏi trước", "A:Đáp trước"], self._order())
        self.assertFalse(panel.empty_state.isVisibleTo(self.window))

        panel._render_messages()  # what a theme change does
        QApplication.processEvents()

        self.assertEqual(["U:Hỏi trước", "A:Đáp trước"], self._order())
        self.assertFalse(panel.empty_state.isVisibleTo(self.window))

    def test_suggestions_show_only_while_the_conversation_is_new(self):
        panel = self._reopen_with(self._SAVED)
        self._show_window()

        panel.new_session()
        QApplication.processEvents()
        self.assertEqual(["EMPTY"], self._order())
        self.assertTrue(panel.empty_state.isVisibleTo(self.window))

        panel.session_combo.setCurrentIndex(panel.session_combo.findData(12))
        QApplication.processEvents()
        self.assertEqual(["U:Hỏi trước", "A:Đáp trước"], self._order())
        self.assertFalse(panel.empty_state.isVisibleTo(self.window))

    def test_a_new_conversation_after_a_question_shows_the_suggestions_again(self):
        self._show_window()
        self._finish(self._send("Câu đầu"), "Đáp đầu")
        self.assertFalse(self.panel.empty_state.isVisibleTo(self.window))

        self.panel.new_session()
        QApplication.processEvents()

        self.assertEqual(["EMPTY"], self._order())
        self.assertTrue(self.panel.empty_state.isVisibleTo(self.window))

    def test_theme_refresh_keeps_a_stopped_turn_where_it_was(self):
        self._stop(self._send("Q1"), "Một phần")
        self._finish(self._send("Q2"), "Trả lời hai")
        expected = ["U:Q1", "A:Một phần", "U:Q2", "A:Trả lời hai", "STATUS"]
        self.assertEqual(expected, self._order(with_status=True))

        self.panel._render_messages()

        self.assertEqual(expected, self._order(with_status=True))
        # The next turn still goes last, not above the stopped one.
        self._finish(self._send("Q3"), "Trả lời ba")
        self.assertEqual(
            expected[:-1] + ["U:Q3", "A:Trả lời ba", "STATUS"], self._order(with_status=True)
        )

    def test_a_stopped_turn_is_hidden_together_with_its_question(self):
        self._stop(self._send("Câu bị dừng"))
        for n in range(10):
            self._finish(self._send("Câu {}".format(n)), "Đáp {}".format(n))

        # 21 messages: only the last 20 are shown, the stopped question is not.
        self.panel._render_messages()

        expected = ["NoteView"]
        for n in range(10):
            expected += ["U:Câu {}".format(n), "A:Đáp {}".format(n)]
        self.assertEqual(expected, self._order())

    def _show_window(self, width=520, height=480):
        self.window.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.window.resize(width, height)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)
        QApplication.processEvents()

    def test_opening_the_panel_puts_the_cursor_in_the_question_box(self):
        self.window.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.panel.hide()
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)
        self.window.activateWindow()

        self.panel.show()
        QTest.qWait(20)

        self.assertIs(self.panel.question_input, QApplication.focusWidget())

    def _editing_elsewhere(self):
        """The dock is open while the reader types in the main window."""
        editor = QLineEdit()
        self.window.setCentralWidget(editor)
        self._show_window()
        self.window.activateWindow()
        QTest.qWait(20)
        editor.setFocus()
        QTest.qWait(20)
        self.assertIs(editor, QApplication.focusWidget())
        return editor

    def test_restoring_a_minimised_window_leaves_the_cursor_where_it_was(self):
        editor = self._editing_elsewhere()

        self.window.showMinimized()
        QTest.qWait(20)
        self.window.showNormal()
        self.window.activateWindow()
        QTest.qWait(20)

        self.assertTrue(self.panel.isVisible())
        # The window's focus child is where typing goes once it is active
        # (offscreen windows are not always activated again).
        self.assertIs(editor, self.window.focusWidget())

    def test_showing_the_window_again_leaves_the_cursor_where_it_was(self):
        # The home screen hides the main window and shows it again later.
        editor = self._editing_elsewhere()

        self.window.hide()
        QTest.qWait(20)
        self.window.show()
        self.window.activateWindow()
        QTest.qWait(20)

        self.assertIs(editor, self.window.focusWidget())

    def test_reopening_a_closed_dock_puts_the_cursor_in_the_question_box(self):
        editor = self._editing_elsewhere()
        self.panel.close()
        QTest.qWait(20)
        editor.setFocus()
        QTest.qWait(20)

        self.panel.show()
        QTest.qWait(20)

        self.assertIs(self.panel.question_input, QApplication.focusWidget())

    def test_clicking_a_citation_in_an_answer_opens_its_document_at_the_page(self):
        self._show_window()
        worker = self._send()
        self._finish(worker, "Theo [2] và [1].", [
            Citation(1, 7, 2, "01/QĐ", 3),
            Citation(2, 9, 5, "02/KH", 4),
        ])
        QApplication.processEvents()
        opened = []
        self.panel.open_document_requested.connect(lambda d, p: opened.append((d, p)))
        body = self.panel._last_turn_view().body

        # Click the pill the reader sees, through the view's own link handling.
        block = body.document().begin()
        target = None
        while block.isValid() and target is None:
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                if fragment.charFormat().anchorHref().endswith(":2"):
                    target = fragment.position() + fragment.length() // 2
                    break
                fragments += 1
            block = block.next()
        self.assertIsNotNone(target)
        cursor = body.textCursor()
        cursor.setPosition(target)
        point = body.cursorRect(cursor).center()
        QTest.mouseClick(body.viewport(), Qt.LeftButton, Qt.NoModifier, point)

        self.assertEqual([(9, 5)], opened)

    def test_streaming_follows_the_bottom_until_the_reader_scrolls_up(self):
        self._show_window(height=420)
        worker = self._send()
        bar = self.panel.transcript.verticalScrollBar()
        line = "Dòng nội dung trả lời khá dài để chiếm chỗ trong khung chat.\n\n"
        for _ in range(12):
            worker.token.emit(line)
            self.panel._flush_stream()
            QApplication.processEvents()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.maximum(), bar.value())

        bar.setValue(0)
        for _ in range(6):
            worker.token.emit(line)
            self.panel._flush_stream()
            QApplication.processEvents()

        # The reader is left where they are, with a way back down.
        self.assertEqual(0, bar.value())
        self.assertTrue(self.panel.transcript.jump_button.isVisible())
        self.panel.transcript.jump_button.click()
        QApplication.processEvents()
        self.assertEqual(bar.maximum(), bar.value())


class MinimalPanelTests(_PanelFixture):
    """Hints, notes and figures are one hover away; the screen keeps the
    conversation, and the sources are said once, above the answer."""

    def _send(self, question="Văn bản này còn hiệu lực không?"):
        self.panel.question_input.setPlainText(question)
        self.panel.send_question()
        return self.panel._agent_worker

    def _finish(self, worker, answer, citations):
        worker.finished_result.emit(AgentResult(answer, citations, []))
        worker.finished.emit()

    def _show(self):
        self.window.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.window.resize(520, 640)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)
        QApplication.processEvents()

    def _shown_text(self):
        """Every piece of text on screen in the dock."""
        widgets = self.panel.findChildren(QLabel) + self.panel.findChildren(QAbstractButton)
        return [widget.text() for widget in widgets
                if widget.isVisibleTo(self.window) and widget.text()]

    def test_keyboard_hints_are_tooltips_not_text_on_screen(self):
        self._show()

        shown = " ".join(self._shown_text())
        for hint in ("Enter", "Shift", "Esc"):
            self.assertNotIn(hint, shown)
        self.assertIn("Shift+Enter", self.panel.question_input.toolTip())
        self.assertIn("Esc", self.panel.question_input.toolTip())
        self.assertIn("Enter", self.panel.send_button.toolTip())
        self.assertIn("Esc", self.panel.stop_button.toolTip())

    def test_an_answer_shows_no_source_line_and_no_legal_note_under_it(self):
        self._show()
        self._finish(self._send(), "Còn hiệu lực [1].\n\n" + DISCLAIMER_VI,
                     [Citation(1, 7, 2, "01/QĐ", 3)])

        view = self.panel._last_turn_view()
        self.assertEqual("", view.notice_text())
        self.assertNotIn("tư vấn pháp lý", view.plain_text())
        self.assertNotIn("Nguồn:", view.plain_text())
        self.assertIn('href="cite:1:1"', view.body_html())
        self.assertEqual([1], [chip.property("citationNumber") for chip in view.source_chips()])
        self.assertFalse(any(" · " in text for text in self._shown_text()))

    def test_a_saved_answer_with_a_source_line_shows_its_sources_only_as_chips(self):
        history = _FakeHistory(12, [
            {"role": "user", "content": "Hộ chiếu có thời hạn bao lâu?", "citations": []},
            {"role": "assistant",
             "content": "Thời hạn 5 năm [1].\n\nNguồn: [1] [2]\n\n" + DISCLAIMER_VI,
             "citations": [Citation(1, 7, 2, "01/QĐ", 3), Citation(2, 8, 1, "02/QĐ", 4)]},
        ])
        panel = ChatPanel(
            self.window, self.db_path, object(), object(),
            server_manager=self.server, executor_factory=self._make_executor,
            client_factory=lambda _url: object(), agent_factory=_FakeAgent,
            worker_factory=_FakeWorker, history_store=history,
        )
        view = panel._last_turn_view()

        self.assertIn("5 năm", view.plain_text())
        self.assertNotIn("Nguồn", view.plain_text())
        self.assertIn('href="cite:1:1"', view.body_html())
        self.assertEqual([1, 2], [chip.property("citationNumber") for chip in view.source_chips()])
        # Copying still lists the sources, once, for pasting elsewhere.
        view.copy_button.click()
        copied = QApplication.clipboard().text()
        self.assertNotIn("Nguồn: [1] [2]", copied)
        self.assertEqual(1, copied.count("Nguồn:"))
        self.assertIn("[2] 02/QĐ, trang 1", copied)
        panel.shutdown()
        panel.deleteLater()

    def test_a_cut_off_answer_still_says_so_in_its_text(self):
        from rag_agent import TRUNCATED_NOTICE_VI

        self._finish(self._send(), "Phần đầu.\n\n" + TRUNCATED_NOTICE_VI + "\n\n" + DISCLAIMER_VI, [])

        text = self.panel._last_turn_view().plain_text()
        self.assertIn("Câu trả lời bị cắt", text)
        self.assertNotIn("⚠", text)

    def test_store_contents_are_a_tooltip_when_ready_and_on_screen_when_not(self):
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("CREATE TABLE documents (id INTEGER)")
            conn.execute("CREATE TABLE document_chunks (id INTEGER)")
            conn.executemany("INSERT INTO documents VALUES (?)", [(n,) for n in range(3)])
        self.panel.refresh_knowledge_status()

        label = self.panel.knowledge_status_label
        self.assertFalse(label.isHidden())  # the reader has to index the store
        self.assertIn("chưa được lập chỉ mục", label.text())

        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executemany("INSERT INTO document_chunks VALUES (?)", [(n,) for n in range(9)])
        self.panel.refresh_knowledge_status()

        self.assertTrue(label.isHidden())
        for tip in (self.panel.info_button.toolTip(), self.panel.scope_combo.toolTip()):
            self.assertIn("3 văn bản, 9 đoạn", tip)
            self.assertNotIn("·", tip)

    def test_the_dock_paints_its_own_background_in_both_themes(self):
        # A subclassed QWidget body showed the window behind it: a light
        # strip around a dark transcript, the session title unreadable.
        from chat_widgets import chat_colors

        self._show()
        body = self.panel.widget()
        for theme in ("dark", "light"):
            with mock.patch("chat_panel.current_theme", return_value=theme):
                self.panel._apply_theme()
            QApplication.processEvents()
            # Drawn by the window, as on screen: the body's own grab fills
            # in its palette and would hide the fault.
            point = body.mapTo(self.window, body.rect().topLeft()) + QPoint(2, 2)
            corner = self.window.grab().toImage().pixelColor(point).name()
            self.assertEqual(chat_colors(theme)["page"].lower(), corner, theme)

    def test_the_info_button_shows_its_text_when_pressed(self):
        self._show()
        # The module's name is replaced, not the Qt class: patching an
        # attribute of a PyQt type changes it for every later test.
        with mock.patch("chat_panel.QToolTip") as tooltip:
            self.panel.info_button.click()

        self.assertIn("không thay thế tư vấn pháp lý", tooltip.showText.call_args[0][1])


class ConversationContextTests(_PanelFixture):
    """Each turn keeps the document open when it was asked and the documents
    it was about, and the agent is told about them (bench/rag/multiturn)."""

    def _send(self, question):
        self.panel.question_input.setPlainText(question)
        self.panel.send_question()
        return self.panel._agent_worker

    def _finish(self, worker, answer, citations=(), **fields):
        worker.finished_result.emit(AgentResult(answer, list(citations), [], **fields))
        worker.finished.emit()

    def test_each_turn_saves_the_scope_open_document_and_resolved_documents(self):
        self.panel.set_scope_doc_ids([3])
        self.panel.scope_combo.setCurrentText("Tài liệu đang mở")
        worker = self._send("tóm tắt về văn bản tôi đang mở")
        self._finish(worker, "Kế hoạch về tham nhũng [1].", [Citation(1, 3, 1, "166-KH/ĐĐHĐND", 30)],
                     refs=[3], how={3: "scope"}, labels={3: "166-KH/ĐĐHĐND"})

        self.assertEqual([{"scope": "open", "open_doc": 3},
                          {"intent": "documents", "status": "answered", "refs": [3],
                           "how": {"3": "scope"}, "labels": {"3": "166-KH/ĐĐHĐND"}}],
                         self.history.metas)
        self.assertEqual({"scope": "open", "open_doc": 3}, self.panel._messages[0].meta)
        # Elsewhere the messages are still (role, content, citations).
        role, content, citations = self.panel._messages[1]
        self.assertEqual("assistant", role)

    def test_a_table_turn_and_what_answer_checking_did_are_saved(self):
        worker = self._send("thống kê các lãnh đạo")
        self._finish(worker, "Ba người ký [1].", [Citation(1, 3, None, "166-KH/ĐĐHĐND", None)],
                     shown=[3, 8], table="people",
                     check={"checked": {"numbers": 0}, "violations": ["list"], "items": ["Nguyễn Văn A"],
                            "regenerated": True, "after": [], "hedged": False})

        meta = self.history.metas[-1]
        self.assertEqual("people", meta["table"])
        # The kinds only: no content of the answer goes into the saved context.
        self.assertEqual({"violations": ["list"], "regenerated": True, "after": [], "hedged": False},
                         meta["check"])
        # The next turn reads the table back: "kế hoạch đầu tiên trong danh sách".
        from rag_agent import turn_records
        self.assertEqual("people", turn_records(self.panel._messages)[-1].table)

    def test_a_regenerated_answer_discards_the_streamed_draft(self):
        worker = self._send("Hạn nộp là ngày nào?")
        worker.token.emit("Hạn là 25/6/2017.")
        self.panel._flush_stream()
        worker.step.emit("verify_answer")
        self.assertEqual("", self.panel._stream_text)
        self.assertEqual("Đang kiểm tra lại câu trả lời với nguồn…", self.panel.steps_label.text())

    def test_the_agent_gets_the_earlier_turns_and_the_document_open_now(self):
        self.panel.set_scope_doc_ids([3])
        self.panel.scope_combo.setCurrentText("Tài liệu đang mở")
        self._finish(self._send("tóm tắt về văn bản tôi đang mở"), "Tham nhũng.", [],
                     refs=[3], how={3: "scope"}, labels={3: "166-KH/ĐĐHĐND"})
        self.panel.set_scope_doc_ids([8])
        self.panel.scope_combo.setCurrentText("Toàn kho")

        worker = self._send("ở đầu đoạn chat tôi đã hỏi bạn về văn bản nào")
        state = worker.agent.conversation

        self.assertEqual(8, state.open_doc)
        self.assertEqual("all", state.scope)
        self.assertEqual(1, len(state.turns))
        self.assertEqual(([3], 3, "open"), (state.turns[0].docs, state.turns[0].open_doc,
                                            state.turns[0].scope))
        self.assertEqual("tóm tắt về văn bản tôi đang mở", worker.history[0]["content"])

    def test_a_reopened_session_carries_its_saved_context(self):
        history = _FakeHistory(12, [
            {"role": "user", "content": "Văn bản này do ai ký?", "citations": [],
             "meta": {"scope": "open", "open_doc": 7}},
            {"role": "assistant", "content": "Trần Quốc Toàn.", "citations": [],
             "meta": {"intent": "documents", "refs": [7], "how": {"7": "scope"},
                      "labels": {"7": "336/KH-KTNS"}}},
        ])
        panel = ChatPanel(
            self.window, self.db_path, object(), object(), server_manager=self.server,
            executor_factory=self._make_executor, client_factory=lambda _url: object(),
            agent_factory=_FakeAgent, worker_factory=_FakeWorker, history_store=history,
        )
        try:
            panel.question_input.setPlainText("Văn bản đó ban hành ngày nào?")
            panel.send_question()
            state = panel._agent_worker.agent.conversation
            self.assertEqual([7], state.turns[0].docs)
            self.assertEqual(7, state.turns[0].open_doc)
        finally:
            panel.shutdown()
            panel.deleteLater()

    def test_a_scope_notice_is_said_under_the_answer_not_in_it(self):
        from rag_agent import SCOPE_NOTICE_VI

        notice = SCOPE_NOTICE_VI.format("336/KH-KTNS")
        self._finish(self._send("Kế hoạch 336/KH-KTNS do ai ký?"),
                     notice + "\n\nTrần Quốc Toàn ký [1].\n\n" + DISCLAIMER_VI,
                     [Citation(1, 7, 1, "336/KH-KTNS", 70)])

        view = self.panel._last_turn_view()
        self.assertEqual(notice, view.notice_text())
        self.assertNotIn("Ngoài phạm vi", view.plain_text())
        self.assertIn("Trần Quốc Toàn", view.plain_text())

    def test_the_document_suggestion_scopes_one_question_and_leaves_the_box(self):
        self.panel.set_scope_doc_ids([42])
        self.panel.empty_state.document_button().click()

        self.assertEqual("Toàn kho", self.panel.scope_combo.currentText())
        self.assertEqual([42], self.scope_calls[-1])
        self._finish(self.panel._agent_worker, "Tóm tắt.")
        self._send("Có văn bản nào về tham nhũng không?")
        self.assertIsNone(self.scope_calls[-1])


if __name__ == "__main__":
    unittest.main()
