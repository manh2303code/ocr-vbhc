"""Dockable UI for the local, citation-grounded legal assistant."""

import html
import logging
import re
import sqlite3
from contextlib import closing
from typing import List, Optional

from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QMessageBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from agent_tools import ToolExecutor
from background_workers import AgentWorker
from chat_history import ChatHistory
from chat_server_manager import ChatServerManager
from llm_client import LlamaChatClient
from rag_agent import RagAgent
from theme_tokens import COLORS, colors_for
from ui_icons import icon
from vector_index import VectorIndex


DISCLAIMER = "Lưu ý: Công cụ hỗ trợ tra cứu — không thay thế tư vấn pháp lý."
_CITATION_RE = re.compile(r"\[(\d+)\]")
_STEP_LABELS = {
    "prefetch_documents": "Đang đọc nguồn phù hợp trong kho…",
    "search_documents": "Đang tìm trong kho văn bản…",
    "get_document_info": "Đang đọc thông tin văn bản…",
    "get_related_documents": "Đang tra văn bản liên quan…",
    "check_validity": "Đang kiểm tra hiệu lực văn bản…",
    "read_document_section": "Đang đọc nội dung liên quan…",
}
logger = logging.getLogger("OCRApp.ChatPanel")


def _visible_stream_text(text):
    """Hide complete, open and fragmented reasoning tags while streaming."""
    visible = text or ""
    for tag in ("think", "analysis", "reasoning"):
        visible = re.sub(
            r"<{}\b[^>]*>.*?</{}\s*>".format(tag, tag),
            "", visible, flags=re.IGNORECASE | re.DOTALL,
        )
        visible = re.sub(
            r"<{}\b[^>]*>.*$".format(tag),
            "", visible, flags=re.IGNORECASE | re.DOTALL,
        )
    lower = visible.casefold()
    tags = ("<think>", "</think>", "<analysis>", "</analysis>",
            "<reasoning>", "</reasoning>")
    for index in range(max(0, len(lower) - 12), len(lower)):
        suffix = lower[index:]
        if suffix and any(tag.startswith(suffix) for tag in tags):
            return visible[:index]
    return visible


class _ServerStartWorker(QThread):
    """Keep the 30–60 second first model load off the GUI thread."""

    ready = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            self.manager.start_chat()
            if not self.isInterruptionRequested():
                self.ready.emit()
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error.emit(str(exc))


class ChatPanel(QDockWidget):
    """Right-side chat dock with streaming, tool steps and citation links."""

    open_document_requested = pyqtSignal(int, int)

    def __init__(
        self,
        main_window,
        db_path,
        embedding_model,
        reranker,
        server_manager=None,
        executor_factory=None,
        client_factory=None,
        agent_factory=None,
        worker_factory=None,
        server_worker_factory=None,
        history_store=None,
        worker_tracker=None,
    ):
        super().__init__("Trợ lý pháp luật", main_window)
        self.setObjectName("chatPanel")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.setMinimumWidth(350)

        self.db_path = str(db_path)
        self.embedding_model = embedding_model
        self.reranker = reranker
        self.server_manager = server_manager or ChatServerManager()
        self._executor_factory = executor_factory or self._default_executor_factory
        self._client_factory = client_factory or LlamaChatClient
        self._agent_factory = agent_factory or RagAgent
        self._worker_factory = worker_factory or AgentWorker
        self._server_worker_factory = server_worker_factory or _ServerStartWorker
        self._history = history_store or ChatHistory(self.db_path)
        self._worker_tracker = worker_tracker

        self._db_conn = None
        self._vector_index = None
        self._executor = None
        self._client = None
        self._agent_worker = None
        self._server_worker = None
        self._pending_question = None
        self._scope_doc_ids = None  # type: Optional[List[int]]
        self._citations = {}
        self._citation_targets = {}
        self._messages = []
        self._session_id = None
        self._pending_history = []
        self._stream_text = ""
        self._stream_raw = ""
        self._shutting_down = False

        self._build_ui()
        self._load_history()
        self.refresh_knowledge_status()

    def _build_ui(self):
        body = QWidget(self)
        body.setObjectName("chatPanelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)

        session_row = QHBoxLayout()
        self.session_combo = QComboBox()
        self.session_combo.setObjectName("chatSessionCombo")
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        self.new_session_button = QPushButton("Mới")
        self.new_session_button.clicked.connect(self.new_session)
        self.delete_session_button = QPushButton("Xóa phiên")
        self.delete_session_button.clicked.connect(self.delete_current_session)
        session_row.addWidget(self.session_combo, 1)
        session_row.addWidget(self.new_session_button)
        session_row.addWidget(self.delete_session_button)
        layout.addLayout(session_row)

        scope_row = QHBoxLayout()
        scope_label = QLabel("Phạm vi")
        scope_label.setObjectName("chatScopeLabel")
        self.scope_combo = QComboBox()
        self.scope_combo.setObjectName("chatScopeCombo")
        self.scope_combo.addItems(["Toàn kho", "Tài liệu đang mở"])
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(scope_label)
        scope_row.addWidget(self.scope_combo, 1)
        layout.addLayout(scope_row)

        self.knowledge_status_label = QLabel("")
        self.knowledge_status_label.setObjectName("chatKnowledgeStatus")
        self.knowledge_status_label.setWordWrap(True)
        layout.addWidget(self.knowledge_status_label)

        self.chat_browser = QTextBrowser()
        self.chat_browser.setObjectName("chatBrowser")
        self.chat_browser.setOpenLinks(False)
        self.chat_browser.setOpenExternalLinks(False)
        self.chat_browser.setPlaceholderText(
            "Hỏi về nội dung, căn cứ, quan hệ hoặc hiệu lực của văn bản trong kho."
        )
        self.chat_browser.anchorClicked.connect(self._on_anchor_clicked)
        layout.addWidget(self.chat_browser, 1)

        self.steps_label = QLabel("Sẵn sàng tra cứu")
        self.steps_label.setObjectName("chatSteps")
        self.steps_label.setWordWrap(True)
        layout.addWidget(self.steps_label)

        self.history_status_label = QLabel("")
        self.history_status_label.setObjectName("chatHistoryStatus")
        self.history_status_label.setWordWrap(True)
        self.history_status_label.hide()
        layout.addWidget(self.history_status_label)

        self.question_input = QPlainTextEdit()
        self.question_input.setObjectName("chatQuestionInput")
        self.question_input.setPlaceholderText("Nhập câu hỏi pháp lý…")
        self.question_input.setMaximumHeight(100)
        self.question_input.setMinimumHeight(68)
        layout.addWidget(self.question_input)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setObjectName("chatStopButton")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.setIcon(icon("x", size=17, color=COLORS["cta"]))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_generation)
        actions.addWidget(self.stop_button)

        self.send_button = QPushButton("Gửi")
        self.send_button.setObjectName("chatSendButton")
        self.send_button.setProperty("variant", "primary")
        self.send_button.setIcon(
            icon("sparkles", size=17, color=COLORS["text_on_primary"])
        )
        self.send_button.clicked.connect(self.send_question)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)

        self.disclaimer_label = QLabel(DISCLAIMER)
        self.disclaimer_label.setObjectName("chatDisclaimer")
        self.disclaimer_label.setAlignment(Qt.AlignCenter)
        self.disclaimer_label.setWordWrap(True)
        layout.addWidget(self.disclaimer_label)

        self.setWidget(body)

    def _default_executor_factory(self, scope_doc_ids):
        if self._db_conn is None:
            self._db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._vector_index = VectorIndex(self._db_conn)
        return ToolExecutor(
            self._db_conn,
            self._vector_index,
            self.embedding_model,
            self.reranker,
            scope_doc_ids=scope_doc_ids,
        )

    def _effective_scope(self):
        if self.scope_combo.currentText() == "Tài liệu đang mở":
            return list(self._scope_doc_ids or [])
        return None

    def _replace_executor(self):
        self._executor = self._executor_factory(self._effective_scope())

    def set_scope_doc_ids(self, doc_ids: Optional[List[int]]):
        """Update the IDs represented by the ``current document`` scope."""
        self._scope_doc_ids = (
            list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
            if doc_ids is not None
            else None
        )
        self._replace_executor()

    def _on_scope_changed(self, _index):
        self._replace_executor()

    @staticmethod
    def _basic_markdown(text):
        safe = html.escape(text or "")
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
        return safe.replace("\n", "<br>")

    def _render_citations(self, answer, citations, turn_key=None):
        """Render trusted local citation numbers while escaping answer HTML."""
        if turn_key is None:
            self._citations = {int(citation.n): citation for citation in citations}
            href = lambda n: "cite:{}".format(n)
        else:
            for citation in citations:
                self._citation_targets[(int(turn_key), int(citation.n))] = citation
            href = lambda n: "cite:{}:{}".format(turn_key, n)
        rendered = self._basic_markdown(answer)
        return _CITATION_RE.sub(
            lambda match: '<a href="{0}">[{1}]</a>'.format(
                href(match.group(1)), match.group(1)
            ),
            rendered,
        )

    def _render_messages(self):
        blocks = []
        self._citation_targets = {}
        visible_messages = self._messages[-20:]
        turn_offset = len(self._messages) - len(visible_messages)
        if turn_offset:
            blocks.append('<div class="message assistant">{} lượt cũ được ẩn.</div>'.format(turn_offset))
        for offset, (role, content, citations) in enumerate(visible_messages):
            turn = turn_offset + offset
            label = "Bạn" if role == "user" else "Trợ lý"
            kind = "user" if role == "user" else "assistant"
            rendered = (
                self._basic_markdown(content)
                if role == "user"
                else self._render_citations(content, citations, turn_key=turn)
            )
            blocks.append(
                '<div class="message {0}"><b>{1}</b><br>{2}</div>'.format(
                    kind, label, rendered
                )
            )
        if self._stream_text:
            blocks.append(
                '<div class="message assistant"><b>Trợ lý</b><br>{}</div>'.format(
                    self._basic_markdown(self._stream_text)
                )
            )
        try:
            from theme_manager_v2 import ThemeManager
            theme = "dark" if ThemeManager.instance().is_dark else "light"
        except Exception:
            theme = "light"
        # The transcript is HTML inside a QTextBrowser, so it gets no stylesheet
        # and has to name its own colours. They come from the token table rather
        # than from literals, which is how the dark values here drifted out of
        # step with theme_dark.qss in the first place.
        palette = colors_for(theme)
        document_style = (
            "<style>"
            "body{{font-family:'Be Vietnam Pro','Segoe UI';font-size:10pt;color:{text};}}"
            ".message{{margin:8px 2px;padding:10px 12px;border-radius:8px;}}"
            ".user{{background:{user};}}"
            ".assistant{{background:{assistant};}}"
            "a{{color:{link};text-decoration:none;font-weight:600;}}"
            "</style>"
        ).format(
            user=palette["primary_soft"],
            assistant=palette["surface_alt"],
            link=palette["accent_gold"] if theme == "dark" else palette["info"],
            text=palette["text"],
        )
        self.chat_browser.setHtml(document_style + "".join(blocks))
        scrollbar = self.chat_browser.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_anchor_clicked(self, url):
        value = url.toString() if isinstance(url, QUrl) else str(url)
        turn_match = re.fullmatch(r"cite:(\d+):(\d+)", value)
        direct_match = re.fullmatch(r"cite:(\d+)", value)
        citation = (
            self._citation_targets.get(
                (int(turn_match.group(1)), int(turn_match.group(2)))
            )
            if turn_match
            else self._citations.get(int(direct_match.group(1)))
            if direct_match
            else None
        )
        if citation:
            self.open_document_requested.emit(
                int(citation.doc_id), int(citation.page or 1)
            )

    def _set_busy(self, busy):
        self.send_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.question_input.setEnabled(not busy)
        self.scope_combo.setEnabled(not busy)
        self.session_combo.setEnabled(not busy)
        self.new_session_button.setEnabled(not busy)
        self.delete_session_button.setEnabled(
            not busy and self._session_id is not None
        )

    def refresh_knowledge_status(self):
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                chunks = conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
            if documents == 0:
                text = "Kho tri thức đang trống. Hãy thêm tệp hoặc nhập thư mục văn bản."
            elif chunks == 0:
                text = (
                    f"Kho có {documents} văn bản nhưng chưa được lập chỉ mục. "
                    "Mở Công cụ khác và chọn Xây lại chỉ mục AI."
                )
            else:
                text = f"Kho tri thức: {documents} văn bản · {chunks} đoạn đã lập chỉ mục"
            self.knowledge_status_label.setText(text)
        except Exception:
            self.knowledge_status_label.setText("Kho tri thức chưa sẵn sàng")

    def send_question(self):
        self.refresh_knowledge_status()
        question = self.question_input.toPlainText().strip()
        if not question or not self.send_button.isEnabled():
            return
        self.question_input.clear()
        self._pending_history = self._agent_history()
        self._messages.append(("user", question, []))
        self._persist_message("user", question, [])
        self._render_messages()
        self._set_busy(True)
        self._pending_question = question

        if self.server_manager.is_running:
            self._start_agent(question)
            return

        self.steps_label.setText(
            "Đang khởi động trợ lý (lần đầu ~30-60s)…"
        )
        self._server_worker = self._server_worker_factory(self.server_manager)
        self._server_worker.ready.connect(self._on_server_ready)
        self._server_worker.error.connect(self._on_server_error)
        self._server_worker.finished.connect(self._on_server_start_finished)
        self._track(self._server_worker)
        self._server_worker.start()

    def _on_server_ready(self):
        question = self._pending_question
        if not self._shutting_down and question:
            self._start_agent(question)

    def _on_server_start_finished(self):
        self._server_worker = None
        if self._pending_question is None and self._agent_worker is None:
            self._set_busy(False)

    def _on_server_error(self, message):
        self._pending_question = None
        friendly = (
            "Chưa có model trợ lý. Vui lòng bổ sung model GGUF vào thư mục "
            "gguf/chat rồi nhấn Gửi để thử lại."
            if "model" in message.lower() or "gguf" in message.lower()
            else "Không thể khởi động trợ lý: {}. Hãy nhấn Gửi để thử lại.".format(
                message
            )
        )
        self.steps_label.setText(friendly)
        self._set_busy(False)

    def _start_agent(self, question):
        try:
            self._replace_executor()
            self._client = self._client_factory(self.server_manager.url)
            max_rounds = 8 if self.server_manager.backend == "cuda" else 4
            agent = self._agent_factory(
                self._client, self._executor, max_rounds=max_rounds
            )
            self._stream_text = ""
            self._stream_raw = ""
            self.steps_label.setText("Đang phân tích câu hỏi…")
            self._agent_worker = self._worker_factory(
                agent, question, history=list(self._pending_history)
            )
            self._agent_worker.step.connect(self._on_step)
            self._agent_worker.token.connect(self._on_token)
            self._agent_worker.finished_result.connect(self._on_result)
            self._agent_worker.error.connect(self._on_agent_error)
            self._agent_worker.finished.connect(self._on_agent_worker_finished)
            self._track(self._agent_worker)
            self._agent_worker.start()
        except Exception as exc:
            self._on_agent_error(str(exc))

    def _on_step(self, step):
        self.steps_label.setText(_STEP_LABELS.get(step, "Đang xử lý: {}…".format(step)))

    def _on_token(self, token):
        self._stream_raw += token
        self._stream_text = _visible_stream_text(self._stream_raw)
        self._render_messages()

    def _on_result(self, result):
        self._stream_text = ""
        self._stream_raw = ""
        if result.error == "cancelled":
            self.steps_label.setText("Đã dừng theo yêu cầu.")
        elif result.error:
            self.steps_label.setText("Không thể hoàn tất: {}".format(result.error))
        else:
            self._messages.append(("assistant", result.answer, result.citations))
            self._persist_message("assistant", result.answer, result.citations)
            self._render_messages()
            self.steps_label.setText("Đã hoàn tất — bấm [n] để mở nguồn.")
        self._pending_question = None

    def _on_agent_error(self, message):
        self._stream_text = ""
        self._stream_raw = ""
        self._pending_question = None
        self.steps_label.setText(
            "Trợ lý gặp lỗi: {}. Hãy nhấn Gửi để thử lại.".format(message)
        )

    def _on_agent_worker_finished(self):
        self._agent_worker = None
        if not self._shutting_down:
            self._set_busy(False)
            self.question_input.setFocus()

    def _track(self, worker):
        if self._worker_tracker is not None:
            try:
                self._worker_tracker(worker)
            except Exception as exc:
                logger.warning("Không thể theo dõi chat worker: %s", exc)

    def _agent_history(self):
        """Return a bounded recent context, never the whole persisted session."""
        messages = [
            {"role": role, "content": content}
            for role, content, _citations in self._messages
            if role in ("user", "assistant")
        ]
        bounded, total = [], 0
        for message in reversed(messages[-12:]):
            size = len(message["content"])
            if bounded and total + size > 12000:
                break
            bounded.append(message)
            total += size
        return list(reversed(bounded))

    def _refresh_sessions(self, select_id=None):
        sessions = self._history.list_sessions(limit=20)
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        self.session_combo.addItem("Cuộc trò chuyện mới", None)
        for session in sessions:
            self.session_combo.addItem(session["title"], int(session["id"]))
        index = self.session_combo.findData(select_id)
        self.session_combo.setCurrentIndex(max(0, index))
        self.session_combo.blockSignals(False)
        self.delete_session_button.setEnabled(select_id is not None)

    def _on_session_changed(self, _index):
        session_id = self.session_combo.currentData()
        if session_id is None or session_id == self._session_id or self._agent_worker:
            return
        loaded_id, messages = self._history.load_session(int(session_id))
        self._session_id = loaded_id
        self._messages = [
            (item["role"], str(item.get("content") or ""), list(item.get("citations") or []))
            for item in messages if item.get("role") in ("user", "assistant")
        ]
        self._render_messages()

    def new_session(self):
        if self._agent_worker is not None:
            return
        self._session_id = None
        self._messages = []
        self._stream_text = self._stream_raw = ""
        self._refresh_sessions(None)
        self._render_messages()
        self.steps_label.setText("Cuộc trò chuyện mới")

    def delete_current_session(self, _checked=False, confirm=True):
        if self._session_id is None or self._agent_worker is not None:
            return False
        if confirm and QMessageBox.question(
            self, "Xóa phiên trò chuyện", "Xóa toàn bộ nội dung của phiên hiện tại?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return False
        deleted = self._history.delete_session(self._session_id)
        if deleted:
            self._session_id = None
            self._messages = []
            self._refresh_sessions(None)
            self._render_messages()
            self.steps_label.setText("Đã xóa phiên trò chuyện")
        return deleted

    def _set_history_warning(self, message):
        logger.warning("Chat history unavailable: %s", message)
        self.history_status_label.setText(
            "Lịch sử trò chuyện tạm thời không lưu được; bạn vẫn có thể tiếp tục hỏi."
        )
        self.history_status_label.show()

    def _load_history(self):
        try:
            self._session_id, messages = self._history.load_last_session()
            self._messages = [
                (
                    item["role"],
                    str(item.get("content") or ""),
                    list(item.get("citations") or []),
                )
                for item in messages
                if item.get("role") in ("user", "assistant")
            ]
            if self._messages:
                self._render_messages()
            self._refresh_sessions(self._session_id)
        except Exception as exc:
            self._set_history_warning(exc)

    def _persist_message(self, role, content, citations):
        try:
            if self._session_id is None:
                self._session_id = self._history.new_session(str(content)[:80])
                self._refresh_sessions(self._session_id)
            self._history.append(self._session_id, role, content, citations)
        except Exception as exc:
            self._set_history_warning(exc)

    def stop_generation(self):
        if self._agent_worker is not None:
            self._agent_worker.cancel()
        if self._server_worker is not None:
            self._server_worker.requestInterruption()
            self.server_manager.stop()
        self._pending_question = None
        self.steps_label.setText("Đang dừng…")
        if self._agent_worker is None and self._server_worker is None:
            self._set_busy(False)

    def shutdown(self):
        """Cancel background work and release local model/database resources."""
        self._shutting_down = True
        agent_worker = self._agent_worker
        if agent_worker is not None:
            agent_worker.cancel()
        if self._server_worker is not None:
            self._server_worker.requestInterruption()
        # Stopping the local server also unblocks an in-flight HTTP stream.
        self.server_manager.stop()
        if agent_worker is not None and agent_worker.wait(10000):
            self._agent_worker = None
        if self._server_worker is not None:
            if self._server_worker.wait(10000):
                self._server_worker = None
        if self._agent_worker is not None:
            # Keep dependencies alive rather than closing SQLite/httpx under a
            # worker that did not acknowledge cancellation.
            return False
        if self._server_worker is not None:
            self.server_manager.stop()
            return False
        if self._client is not None:
            close = getattr(getattr(self._client, "client", None), "close", None)
            if close:
                close()
            self._client = None
        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None
        return True
