"""Dockable UI for the local, citation-grounded legal assistant."""

import html
import logging
import re
import sqlite3
import time
from contextlib import closing
from typing import List, Optional

from PyQt5.QtCore import QEvent, QSize, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from agent_tools import ToolExecutor, result_budget_for_context
from app_config import CHAT_MODELS, chat_context_size
from background_workers import AgentWorker
from chat_history import ChatHistory
from chat_server_manager import ChatServerManager
from chat_widgets import (
    CARET_MARK,
    STREAM_FRAME_MS,
    AssistantTurnView,
    ChatInput,
    CompactCombo,
    EmptyStateView,
    NoteView,
    StatusRow,
    Transcript,
    UserMessageView,
    answer_document,
    chat_colors,
    chat_stylesheet,
    citation_anchor,
    current_theme,
    format_seconds,
    markdown_to_html,
)
from doc_resolver import OPEN_DOCUMENT_RE, ConversationState
from llm_client import LlamaChatClient
from rag_agent import (
    DISCLAIMER_VI,
    TRUNCATED_NOTICE_VI,
    RagAgent,
    answer_status,
    conversation_history,
    max_rounds_for,
    split_scope_notice,
    strip_source_footer,
    turn_records,
)
from search_store import fold_vietnamese
from ui_icons import icon
from vector_index import VectorIndex


# Said once, quietly: in the info button's tooltip and in the empty state,
# not under every answer.
DISCLAIMER = "Lưu ý: đây là công cụ hỗ trợ tra cứu, không thay thế tư vấn pháp lý."
ABOUT = (
    "Trợ lý tìm trong kho văn bản trên máy này và trả lời kèm nguồn trích dẫn.\n"
    "Bấm một nguồn để mở văn bản tại đúng trang."
)
# Keyboard help, one hover away on the box and the buttons it concerns.
INPUT_TIP = (
    "Enter để gửi, Shift+Enter để xuống dòng.\n"
    "Esc dừng câu trả lời đang viết.\n"
    "Mũi tên lên trong ô trống gọi lại câu hỏi trước."
)
SEND_TIP = "Gửi câu hỏi\nEnter"
STOP_TIP = "Dừng trả lời\nEsc"
_STEP_LABELS = {
    "prefetch_documents": "Đang đọc nguồn phù hợp trong kho…",
    "search_documents": "Đang tìm trong kho văn bản…",
    "get_document_info": "Đang đọc thông tin văn bản…",
    "get_related_documents": "Đang tra văn bản liên quan…",
    "check_validity": "Đang kiểm tra hiệu lực văn bản…",
    "read_document_section": "Đang đọc nội dung liên quan…",
    "list_documents": "Đang liệt kê văn bản trong kho…",
    "conversation": "Đang trả lời…",
    # The answer did not hold against its sources (answer_check): the
    # draft streamed so far is discarded and written again.
    "verify_answer": "Đang kiểm tra lại câu trả lời với nguồn…",
}
_STATUS_STARTING = "Đang khởi động trợ lý (lần đầu ~30-60s)…"
_STATUS_RETRIEVING = "Đang tra cứu kho văn bản…"
_STATUS_READING = "Đang đọc {} nguồn liên quan…"
_STATUS_WRITING = "Đang soạn câu trả lời…"
_STATUS_STOPPING = "Đang dừng…"
_STATUS_STOPPED = "Đã dừng theo yêu cầu."
_STATUS_DONE = "Đã hoàn tất — bấm [n] để mở nguồn."
# Rebuild the transcript once this many views pile up in one sitting.
_MAX_VIEWS = 60
# Folded: "tài liệu đang mở", "văn bản đang xem", "file hiện tại".
_OPEN_DOCUMENT_RE = OPEN_DOCUMENT_RE
logger = logging.getLogger("OCRApp.ChatPanel")


class _Message(tuple):
    """A message as (role, content, citations), as everywhere in the panel,
    plus .meta: the context of its turn, saved with it (chat_history
    meta_json). A question keeps the scope box and the open document; an
    answer the documents the question was about and how it went."""

    def __new__(cls, role, content, citations, meta=None):
        message = super().__new__(cls, (role, content, citations))
        message.meta = dict(meta or {})
        return message


class _PanelBody(QWidget):
    """The dock's content. It asks for a reading column of PREFERRED_WIDTH
    (about 55 characters a line); nothing in it is wide by itself any more,
    so without this the dock would open at its minimum width."""

    PREFERRED_WIDTH = 440

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt paints a stylesheet background by itself only for a plain
        # QWidget; without this the dock showed what lay behind it.
        self.setAttribute(Qt.WA_StyledBackground, True)

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(max(hint.width(), self.PREFERRED_WIDTH), hint.height())


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


class DockTitleBar(QWidget):
    """The dock's own title bar: the assistant's icon and name, and the
    float and close buttons drawn with the app's icons.

    Qt's built-in title bar buttons were hidden by the stylesheet
    (titlebar-close-icon: none) and showed only as an empty pale square on
    hover. A custom title bar still lets the user drag the dock by it.
    """

    def __init__(self, dock):
        super().__init__(dock)
        self.setObjectName("chatDockTitle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._dock = dock
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 4, 4)
        row.setSpacing(6)
        self.mark = QLabel()
        self.mark.setObjectName("chatDockTitleIcon")
        self.mark.setFixedSize(16, 16)
        self.title = QLabel(dock.windowTitle())
        self.title.setObjectName("chatDockTitleText")
        row.addWidget(self.mark)
        row.addWidget(self.title)
        row.addStretch(1)
        self.float_button = QToolButton()
        self.float_button.setObjectName("chatDockFloat")
        self.float_button.setAutoRaise(True)
        self.float_button.setToolTip("Tách trợ lý thành cửa sổ riêng\nNhấn lại để gắn vào cạnh cửa sổ")
        self.float_button.clicked.connect(lambda: dock.setFloating(not dock.isFloating()))
        self.close_button = QToolButton()
        self.close_button.setObjectName("chatDockClose")
        self.close_button.setAutoRaise(True)
        self.close_button.setToolTip("Đóng trợ lý\nCtrl+Shift+K")
        self.close_button.clicked.connect(dock.close)
        for button in (self.float_button, self.close_button):
            button.setFixedSize(26, 26)
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
        dock.topLevelChanged.connect(lambda _floating: self.apply_theme())

    def apply_theme(self, theme=None):
        colors = chat_colors(theme or current_theme())
        self.mark.setPixmap(icon("chat", size=16, color=colors["secondary"]).pixmap(16, 16))
        floating = self._dock.isFloating()
        self.float_button.setIcon(icon("collapse" if floating else "open", size=16,
                                       color=colors["secondary"]))
        self.close_button.setIcon(icon("x", size=16, color=colors["secondary"]))
        self.setStyleSheet(
            "QWidget#chatDockTitle {{ background: {page}; border-bottom: 1px solid {border}; }}"
            "QLabel#chatDockTitleText {{ color: {secondary}; font-weight: 600; }}"
            "QToolButton {{ border: none; border-radius: 4px; background: transparent; }}"
            "QToolButton:hover {{ background: {hover}; }}"
            "QToolButton:pressed {{ background: {pressed}; }}".format(**colors)
        )


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
        self.title_bar = DockTitleBar(self)
        self.setTitleBarWidget(self.title_bar)
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
        # (message, view) of the answer a regeneration would replace: kept
        # until the new answer exists, put back if it is stopped or fails.
        self._replacing = None
        self._stream_text = ""
        self._stream_raw = ""
        self._shutting_down = False
        # The answer being written, its sources so far, and how it is going.
        self._live = None
        self._live_anchor = None
        self._live_sources = {}
        self._message_views = []  # (index in _messages, view)
        self._transient = []  # (anchor message index, view): stopped / failed turns
        self._stopping = False
        self._turn_started = None
        self._stream_tokens = 0
        self._tokens_at_first_visible = 0
        self._first_visible_at = None
        self._last_token_at = None
        self._rate_shown_at = 0.0
        self._stream_dirty = False
        self._stream_renders = 0
        # Closed until the reader opens the dock; see showEvent.
        self._closed = True
        self._theme = current_theme()
        # Tokens arrive far faster than a screen refreshes; draw at ~30 fps.
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(STREAM_FRAME_MS)
        self._flush_timer.timeout.connect(self._on_flush_timer)

        self._build_ui()
        self._load_history()
        self.refresh_knowledge_status()

    def _build_ui(self):
        body = _PanelBody(self)
        body.setObjectName("chatPanelBody")
        self._body = body
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(6)

        # Header: which conversation, start one, delete it, about.
        session_row = QHBoxLayout()
        session_row.setSpacing(2)
        self.session_combo = CompactCombo()
        self.session_combo.setObjectName("chatSessionCombo")
        self.session_combo.setToolTip("Các cuộc trò chuyện đã lưu")
        self.session_combo.setAccessibleName("Cuộc trò chuyện")
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        self.new_session_button = self._header_button(
            "Cuộc trò chuyện mới", self.new_session
        )
        self.delete_session_button = self._header_button(
            "Xóa cuộc trò chuyện này", self.delete_current_session
        )
        self.info_button = self._header_button("Giới thiệu trợ lý", self._show_info)
        session_row.addWidget(self.session_combo)
        session_row.addStretch(1)
        session_row.addSpacing(4)
        session_row.addWidget(self.new_session_button)
        session_row.addWidget(self.delete_session_button)
        session_row.addWidget(self.info_button)
        layout.addLayout(session_row)

        # Shown only when the store needs the reader to act; otherwise what
        # it holds is in the tooltips of the info button and the scope box.
        self._knowledge_text = ""
        self.knowledge_status_label = QLabel("")
        self.knowledge_status_label.setObjectName("chatKnowledgeStatus")
        self.knowledge_status_label.setWordWrap(True)
        self.knowledge_status_label.hide()
        layout.addWidget(self.knowledge_status_label)

        self.history_status_label = QLabel("")
        self.history_status_label.setObjectName("chatHistoryStatus")
        self.history_status_label.setWordWrap(True)
        self.history_status_label.hide()
        layout.addWidget(self.history_status_label)

        self.transcript = Transcript()
        self.status_row = StatusRow()
        self.transcript.set_footer(self.status_row)
        self.steps_label = self.status_row.label
        self.working_indicator = self.status_row.indicator
        self.empty_state = EmptyStateView(self._theme)
        self.empty_state.chosen.connect(self._on_suggestion)
        self.transcript.add_view(self.empty_state)
        layout.addWidget(self.transcript, 1)

        # Composer: one hairline field holding the question, the scope and
        # the one filled button of the dock. Keyboard help is in tooltips.
        self.composer = QFrame()
        self.composer.setObjectName("chatComposer")
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(6, 6, 5, 5)
        composer_layout.setSpacing(2)
        self.question_input = ChatInput()
        self.question_input.setObjectName("chatQuestionInput")
        self.question_input.setPlaceholderText("Hỏi về nội dung, căn cứ, hiệu lực văn bản…")
        self.question_input.setAccessibleName("Câu hỏi cho trợ lý")
        self.question_input.setToolTip(INPUT_TIP)
        self.question_input.setAccessibleDescription(INPUT_TIP)
        self.question_input.submitted.connect(self.send_question)
        self.question_input.escape_pressed.connect(self.stop_generation)
        self.question_input.installEventFilter(self)
        composer_layout.addWidget(self.question_input)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.scope_combo = CompactCombo()
        self.scope_combo.setObjectName("chatScopeCombo")
        self.scope_combo.setToolTip("Phạm vi tra cứu")
        self.scope_combo.setAccessibleName("Phạm vi tra cứu")
        self.scope_combo.addItems(["Toàn kho", "Tài liệu đang mở"])
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        actions.addWidget(self.scope_combo)
        actions.addStretch(1)

        # Send and Stop share one place: the button turns into Stop while busy.
        self.stop_button = QToolButton()
        self.stop_button.setObjectName("chatStopButton")
        self.stop_button.setText("Dừng")
        self.stop_button.setToolTip(STOP_TIP)
        self.stop_button.setAccessibleName("Dừng trả lời")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.setEnabled(False)
        self.stop_button.hide()
        self.stop_button.clicked.connect(self.stop_generation)
        actions.addWidget(self.stop_button)

        self.send_button = QToolButton()
        self.send_button.setObjectName("chatSendButton")
        self.send_button.setText("Gửi")
        self.send_button.setToolTip(SEND_TIP)
        self.send_button.setAccessibleName("Gửi câu hỏi")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.clicked.connect(self.send_question)
        actions.addWidget(self.send_button)
        composer_layout.addLayout(actions)
        layout.addWidget(self.composer)

        self.setWidget(body)
        self._apply_theme()
        self._update_info()

    def _header_button(self, tooltip, callback):
        button = QToolButton()
        button.setProperty("chatRole", "header")
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def info_text(self):
        """What the info button says: what the assistant does, what the
        store holds, and the legal note."""
        parts = [ABOUT]
        if self._knowledge_text:
            parts.append(self._knowledge_text)
        return "\n".join(parts) + "\n\n" + DISCLAIMER

    def _update_info(self):
        text = self.info_text()
        self.info_button.setToolTip(text)
        self.info_button.setAccessibleDescription(text)
        scope_tip = "Phạm vi tra cứu: toàn kho hoặc chỉ văn bản đang mở"
        if self._knowledge_text:
            scope_tip += "\n" + self._knowledge_text
        self.scope_combo.setToolTip(scope_tip)

    def _show_info(self, _checked=False):
        """A click (or Space) shows what hovering shows."""
        button = self.info_button
        QToolTip.showText(
            button.mapToGlobal(button.rect().bottomLeft()), self.info_text(), button
        )

    def _apply_theme(self):
        """Restyle from the token table; the app stylesheet has no chat rules."""
        theme = current_theme()
        self._theme = theme
        colors = chat_colors(theme)
        self._body.setStyleSheet(chat_stylesheet(theme))
        self.title_bar.apply_theme(theme)
        for button, name in ((self.new_session_button, "plus"),
                             (self.delete_session_button, "trash-2"),
                             (self.info_button, "info")):
            button.setIcon(icon(name, size=18, color=colors["secondary"]))
        self.send_button.setIcon(icon("send", size=16, color=colors["on_fill"]))
        self.send_button.setIconSize(QSize(16, 16))
        self.stop_button.setIcon(icon("stop", size=16, color=colors["on_fill"]))
        self.stop_button.setIconSize(QSize(16, 16))
        self.transcript.jump_button.setIcon(icon("arrow-down", size=16, color=colors["text"]))
        self.status_row.apply_theme(theme)
        self.empty_state.apply_theme(theme)
        for combo in (self.session_combo, self.scope_combo):
            combo.set_chevron_color(colors["secondary"])
        palette = self.question_input.palette()
        palette.setColor(QPalette.PlaceholderText, QColor(colors["muted"]))
        palette.setColor(QPalette.Text, QColor(colors["text"]))
        self.question_input.setPalette(palette)

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

    def _scope_mode(self):
        """'open' when the scope box says "Tài liệu đang mở", else 'all'."""
        return "open" if self.scope_combo.currentText() == "Tài liệu đang mở" else "all"

    def _open_document(self):
        """The one document open in the viewer, or None."""
        ids = self._scope_doc_ids or []
        return int(ids[0]) if len(ids) == 1 else None

    def _conversation_state(self, messages):
        """What the agent knows of the conversation before this question:
        its earlier turns, the scope box and the document open now."""
        return ConversationState(turn_records(messages), self._open_document(), self._scope_mode())

    def _effective_scope(self, question=""):
        if self._scope_mode() == "open":
            return list(self._scope_doc_ids or [])
        # "Tóm tắt tài liệu đang mở" means the open document whatever the
        # scope box says; searching the whole store for it finds others.
        if self._scope_doc_ids and _OPEN_DOCUMENT_RE.search(fold_vietnamese(question or "")):
            return list(self._scope_doc_ids)
        return None

    def _replace_executor(self, question=""):
        self._executor = self._executor_factory(self._effective_scope(question))

    def set_scope_doc_ids(self, doc_ids: Optional[List[int]]):
        """Update the IDs represented by the ``current document`` scope."""
        self._scope_doc_ids = (
            list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
            if doc_ids is not None
            else None
        )
        self.empty_state.set_document_available(bool(self._scope_doc_ids))
        self._replace_executor()

    def _on_scope_changed(self, _index):
        self._replace_executor()

    def _render_citations(self, answer, citations, turn_key=None):
        """Render trusted local citation numbers while escaping answer HTML."""
        if turn_key is None:
            self._citations = {int(citation.n): citation for citation in citations}
            href = lambda n: "cite:{}".format(n)
        else:
            for citation in citations:
                self._citation_targets[(int(turn_key), int(citation.n))] = citation
            href = lambda n: "cite:{}:{}".format(turn_key, n)
        known = {int(citation.n) for citation in citations}
        theme = self._theme
        return markdown_to_html(
            answer,
            cite=lambda n: citation_anchor(href(n), n, theme) if n in known else "[{}]".format(n),
        )

    def _render_messages(self):
        """Rebuild the transcript from _messages: theme change or session load.

        Streaming never comes here; it updates the one view being written.
        """
        self._apply_theme()
        visible_from = max(0, len(self._messages) - 20)
        # A stopped or failed turn is hidden together with its question.
        self._transient = [
            (anchor, view) for anchor, view in self._transient if anchor >= visible_from
        ]
        kept = [view for _anchor, view in self._transient]
        if self._live is not None:
            kept.append(self._live)
        # The suggestions belong to a new conversation only; they are taken
        # out here (not deleted) and put back below when nothing was said.
        self._show_empty_state(False)
        self.transcript.clear_views(keep=kept)
        self._message_views = []
        self._citation_targets = {}
        if visible_from:
            self.transcript.add_view(NoteView("{} lượt cũ được ẩn.".format(visible_from)))
        if not self._messages and self._live is None:
            self._show_empty_state(True)
        # Kept views are added again in reading order: add_view moves them.
        for index in range(visible_from, len(self._messages)):
            view = self._make_view(index)
            self.transcript.add_view(view)
            self._message_views.append((index, view))
            for anchor, transient in self._transient:
                if anchor == index:
                    transient.apply_theme(self._theme)
                    self.transcript.add_view(transient)
        if self._live is not None:
            self._live.apply_theme(self._theme)
            self.transcript.add_view(self._live)
            if self._stream_text:
                self._live.set_body_html(self._stream_document(self._stream_text))
        self._update_turn_actions()
        self.transcript.scroll_to_bottom()

    def _make_view(self, index):
        role, content, citations = self._messages[index]
        if role == "user":
            return UserMessageView(content)
        view = self._new_turn_view()
        self._present_answer(view, index, content, citations)
        return view

    def _new_turn_view(self):
        view = AssistantTurnView(self._theme)
        view.link_activated.connect(self._on_anchor_clicked)
        view.open_source.connect(self.open_document_requested)
        view.copy_requested.connect(lambda v=view: self._copy_answer(v))
        view.regenerate_requested.connect(self._regenerate)
        view.retry_requested.connect(lambda v=view: self._retry(v))
        view.answer_content = ""
        view.answer_citations = []
        return view

    @staticmethod
    def _answer_text(content):
        """An answer as the transcript shows it.

        The agent ends every answer with its disclaimer, which the info
        button and the empty state carry instead. Answers saved before the
        sources moved out of the text end with a "Nguồn: [1] [2]" line,
        which the chips above the answer replace. A length cut-off stays,
        as a quiet italic line rather than a warning sign.
        """
        text = strip_source_footer((content or "").replace(DISCLAIMER_VI, ""))
        notice = TRUNCATED_NOTICE_VI.replace("⚠️", "").strip()
        return text.replace(TRUNCATED_NOTICE_VI, "*{}*".format(notice))

    def _present_answer(self, view, index, content, citations, meta=("", "")):
        # "Ngoài phạm vi tài liệu đang mở: …" is said quietly under the
        # answer, in the notice line, not as part of it.
        notice, text = split_scope_notice(content)
        body = self._render_citations(self._answer_text(text), citations, turn_key=index)
        view.set_body_html(answer_document(body, self._theme))
        view.set_notice(notice)
        view.set_sources(citations, "Nguồn trích dẫn")
        view.show_actions(copy=True, meta=meta[0], meta_detail=meta[1])
        view.answer_content = content
        view.answer_citations = list(citations or [])

    def _stream_document(self, text):
        theme = self._theme
        sources = self._live_sources

        def cite(n):
            if n in sources:
                return citation_anchor("cite:live:{}".format(n), n, theme)
            return "[{}]".format(n)

        return answer_document(
            markdown_to_html(text + CARET_MARK, cite=cite), theme, streaming=True
        )

    def _on_anchor_clicked(self, url):
        value = url.toString() if isinstance(url, QUrl) else str(url)
        turn_match = re.fullmatch(r"cite:(\d+):(\d+)", value)
        direct_match = re.fullmatch(r"cite:(\d+)", value)
        live_match = re.fullmatch(r"cite:live:(\d+)", value)
        citation = (
            self._citation_targets.get(
                (int(turn_match.group(1)), int(turn_match.group(2)))
            )
            if turn_match
            else self._citations.get(int(direct_match.group(1)))
            if direct_match
            else self._live_sources.get(int(live_match.group(1)))
            if live_match
            else None
        )
        if citation:
            self.open_document_requested.emit(
                int(citation.doc_id), int(citation.page or 1)
            )

    def _set_busy(self, busy):
        # The question box stays editable: the next question can be drafted
        # while this one is answered; Enter just does not send it yet.
        self.send_button.setEnabled(not busy)
        self.send_button.setVisible(not busy)
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.question_input.capture_escape = busy
        self.scope_combo.setEnabled(not busy)
        self.session_combo.setEnabled(not busy)
        self.new_session_button.setEnabled(not busy)
        self.delete_session_button.setEnabled(
            not busy and self._session_id is not None
        )
        self.empty_state.setEnabled(not busy)
        if not busy:
            self.status_row.end()
        self._update_turn_actions()

    def _is_busy(self):
        return self._agent_worker is not None or self._server_worker is not None

    def refresh_knowledge_status(self):
        """What the store holds. A ready store is described in tooltips; an
        empty, unindexed or unreadable one is said on screen, because the
        reader has to act before the assistant can answer."""
        needs_action = True
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
                text = f"Kho tri thức: {documents} văn bản, {chunks} đoạn đã lập chỉ mục."
                needs_action = False
        except Exception:
            text = "Kho tri thức chưa sẵn sàng."
        self._knowledge_text = text
        self.knowledge_status_label.setText(text)
        self.knowledge_status_label.setVisible(needs_action)
        self._update_info()

    def send_question(self):
        self.refresh_knowledge_status()
        question = self.question_input.toPlainText().strip()
        if not question or not self.send_button.isEnabled():
            return
        self.question_input.clear()
        self.question_input.recall_text = question
        # Bounded in _start_agent: the token budget follows the context of
        # the server, which may only be starting now.
        self._pending_history = list(self._messages)
        # Which document "văn bản này" meant when it was asked, for later
        # turns and for the session reopened another day.
        meta = {"scope": self._scope_mode(), "open_doc": self._open_document()}
        self._messages.append(_Message("user", question, [], meta))
        self._persist_message("user", question, [], meta)
        if len(self.transcript.views()) > _MAX_VIEWS:
            self._render_messages()
        else:
            self._show_message(len(self._messages) - 1)
        self._begin_turn(question)

    def _show_message(self, index):
        """Append one message view; nothing already shown is redrawn."""
        self._show_empty_state(False)
        view = self._make_view(index)
        self.transcript.add_view(view)
        self._message_views.append((index, view))
        self.transcript.scroll_to_bottom()

    def _show_empty_state(self, visible):
        present = self.empty_state in self.transcript.views()
        if visible and not present:
            self.transcript.add_view(self.empty_state)
        elif not visible and present:
            self.transcript.remove_view(self.empty_state, delete=False)

    def _begin_turn(self, question):
        """Open the answer view and start (or first boot) the assistant."""
        self._set_busy(True)
        self._pending_question = question
        self._stopping = False
        self._live_sources = {}
        self._stream_raw = self._stream_text = ""
        self._stream_tokens = self._tokens_at_first_visible = 0
        self._first_visible_at = self._last_token_at = None
        self._turn_started = time.perf_counter()
        self._show_empty_state(False)
        self._live = self._new_turn_view()
        self._live_anchor = len(self._messages) - 1
        self.transcript.add_view(self._live)
        self.transcript.scroll_to_bottom()
        running = self.server_manager.is_running
        self.status_row.begin(_STATUS_RETRIEVING if running else _STATUS_STARTING)
        self.question_input.setFocus()

        if running:
            self._start_agent(question)
            return

        self.steps_label.setText(_STATUS_STARTING)
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
            if self._live is not None:
                # Stopped while the model was still loading.
                self._finish_stopped()
            self._stopping = False
            self._set_busy(False)

    def _on_server_error(self, message):
        self._pending_question = None
        missing_model = "model" in message.lower() or "gguf" in message.lower()
        friendly = (
            "Chưa có model trợ lý. Vui lòng bổ sung model GGUF vào thư mục "
            "gguf/chat rồi nhấn Thử lại."
            if missing_model
            else "Không thể khởi động trợ lý: {}. Hãy nhấn Thử lại.".format(message)
        )
        self.steps_label.setText(friendly)
        self._finish_error(
            "Chưa có model trợ lý" if missing_model else "Không thể khởi động trợ lý",
            "Hãy chép model GGUF vào thư mục gguf/chat rồi bấm Thử lại."
            if missing_model else message,
        )
        self._set_busy(False)

    def _chat_context_size(self):
        """Context tokens of the running chat model: what its server was
        started with, else what its profile gives on this backend."""
        started = getattr(self.server_manager, "active_context_size", None)
        if started:
            return started
        model_key = getattr(self.server_manager, "active_model_key", None) or ""
        if model_key not in CHAT_MODELS:
            return None
        return chat_context_size(model_key, self.server_manager.backend)

    def _start_agent(self, question):
        try:
            self._replace_executor(question)
            if isinstance(self._executor, ToolExecutor):
                # Sources stay small in the 8192-token CPU context.
                self._executor.result_budget_chars = result_budget_for_context(
                    self._chat_context_size()
                )
            self._client = self._client_factory(self.server_manager.url)
            max_rounds = max_rounds_for(self.server_manager.backend)
            agent = self._agent_factory(
                self._client, self._executor, max_rounds=max_rounds
            )
            try:
                agent.conversation = self._conversation_state(self._pending_history)
            except Exception as exc:  # an agent without the attribute, or bad saved data
                logger.warning("Conversation state unavailable: %s", exc)
            self._stream_text = ""
            self._stream_raw = ""
            self.steps_label.setText(_STATUS_RETRIEVING)
            self._agent_worker = self._worker_factory(
                agent, question, history=self._agent_history(self._pending_history)
            )
            self._agent_worker.step.connect(self._on_step)
            self._agent_worker.token.connect(self._on_token)
            sources = getattr(self._agent_worker, "sources", None)
            if sources is not None:
                sources.connect(self._on_sources)
            self._agent_worker.finished_result.connect(self._on_result)
            self._agent_worker.error.connect(self._on_agent_error)
            self._agent_worker.finished.connect(self._on_agent_worker_finished)
            self._track(self._agent_worker)
            self._agent_worker.start()
        except Exception as exc:
            self._on_agent_error(str(exc))

    def _on_step(self, step):
        if self._stopping:
            return
        if step != "prefetch_documents" and self._stream_raw:
            # Text written before a tool call belongs to that round, not to
            # the answer; the old panel kept it and appended the answer to it.
            self._discard_stream()
        if step == "prefetch_documents" and self._live_sources:
            label = _STATUS_READING.format(len(self._live_sources))
        else:
            label = _STEP_LABELS.get(step, "Đang xử lý: {}…".format(step))
        self.steps_label.setText(label)

    def _on_sources(self, citations):
        if self._stopping:
            return
        self._live_sources = {int(citation.n): citation for citation in citations}
        live = self._ensure_live()
        live.set_sources(citations, "Đang đọc {} nguồn".format(len(citations)))
        if not self._stream_text:
            self.steps_label.setText(_STATUS_READING.format(len(citations)))

    def _on_token(self, token):
        if self._stopping:
            return
        self._stream_raw += token
        self._stream_tokens += 1
        self._last_token_at = time.perf_counter()
        if self._flush_timer.isActive():
            self._stream_dirty = True
            return
        # Leading edge: a token arriving after a quiet frame is drawn at once,
        # so the first words never wait for the frame timer.
        self._flush_stream()
        self._flush_timer.start()

    def _on_flush_timer(self):
        if self._stream_dirty:
            self._flush_stream()
            self._flush_timer.start()

    def _flush_stream(self):
        """Draw the visible part of the stream into the one live view."""
        self._stream_dirty = False
        visible = _visible_stream_text(self._stream_raw).lstrip()
        if visible == self._stream_text:
            return
        if visible and not self._stream_text:
            self._first_visible_at = time.perf_counter()
            self._tokens_at_first_visible = self._stream_tokens
            self.steps_label.setText(_STATUS_WRITING)
        self._stream_text = visible
        live = self._ensure_live()
        live.set_body_html(self._stream_document(visible) if visible else "")
        self._stream_renders += 1
        self._update_rate()

    def _discard_stream(self):
        self._flush_timer.stop()
        self._stream_dirty = False
        self._stream_raw = self._stream_text = ""
        self._first_visible_at = None
        self._stream_tokens = self._tokens_at_first_visible = 0
        if self._live is not None:
            self._live.set_body_html("")

    def _rate(self):
        """Tokens per second since the first visible word, or None.

        A few tokens arriving in one burst say nothing about speed, so no
        figure is claimed until there is a measurable span behind it.
        """
        if self._first_visible_at is None or self._last_token_at is None:
            return None
        written = self._stream_tokens - self._tokens_at_first_visible
        span = self._last_token_at - self._first_visible_at
        if written < 3 or span < 0.3:
            return None
        return written / span

    def _update_rate(self):
        now = time.perf_counter()
        if now - self._rate_shown_at < 0.5:
            return
        rate = self._rate()
        if rate is not None:
            self._rate_shown_at = now
            self.status_row.set_rate("{} token/giây".format(int(round(rate))))

    def _ensure_live(self):
        if self._live is None:
            self._show_empty_state(False)
            self._live = self._new_turn_view()
            self._live_anchor = len(self._messages) - 1
            self.transcript.add_view(self._live)
        return self._live

    def _turn_meta(self):
        """(on screen, on hover): the time the answer took, and its details."""
        if self._turn_started is None:
            return "", ""
        seconds = format_seconds(time.perf_counter() - self._turn_started)
        detail = ["Trả lời trong {}".format(seconds)]
        rate = self._rate()
        if rate is not None:
            detail.append("Tốc độ: {} token/giây".format(int(round(rate))))
        return seconds, "\n".join(detail)

    def _on_result(self, result):
        self._flush_timer.stop()
        if not self._stopping and self._stream_dirty:
            self._flush_stream()
        self._pending_question = None
        if result.error == "cancelled":
            self._finish_stopped()
        elif result.error:
            self.steps_label.setText("Không thể hoàn tất: {}".format(result.error))
            self._finish_error("Không thể hoàn tất câu trả lời", str(result.error))
        else:
            self._drop_replaced()
            meta = self._answer_meta(result)
            self._messages.append(_Message("assistant", result.answer, result.citations, meta))
            self._persist_message("assistant", result.answer, result.citations, meta)
            index = len(self._messages) - 1
            view = self._ensure_live()
            self._live = None
            # The view that streamed becomes the answer in place: no jump.
            self._present_answer(
                view, index, result.answer, result.citations, self._turn_meta()
            )
            self._message_views.append((index, view))
            self.steps_label.setText(_STATUS_DONE)
        self._stream_text = self._stream_raw = ""

    @staticmethod
    def _answer_meta(result):
        """What is kept of an answer besides its text and citations: the
        documents its question was about, how each was found, their numbers,
        the kind of question and whether the answer found anything."""
        intent = getattr(result, "intent", "documents") or "documents"
        meta = {"intent": intent}
        if intent == "documents":
            meta["status"] = answer_status(result.answer)
        shown = [int(doc_id) for doc_id in getattr(result, "shown", None) or []]
        if shown:
            meta["shown"] = shown
        refs = [int(doc_id) for doc_id in getattr(result, "refs", None) or []]
        if refs:
            meta["refs"] = refs
            how = getattr(result, "how", None) or {}
            labels = getattr(result, "labels", None) or {}
            meta["how"] = {str(doc_id): how[doc_id] for doc_id in refs if doc_id in how}
            meta["labels"] = {str(doc_id): labels[doc_id] for doc_id in refs if labels.get(doc_id)}
        table = getattr(result, "table", "") or ""
        if table:
            meta["table"] = table
        check = getattr(result, "check", None) or {}
        if check.get("violations"):
            # What answer checking found and did, without the items.
            meta["check"] = {key: check[key] for key in ("violations", "regenerated", "after", "hedged")
                             if key in check}
        return meta

    def _finish_stopped(self):
        view = self._ensure_live()
        self._live = None
        if self._restore_replaced(view):
            self.steps_label.setText("Đã dừng — giữ câu trả lời trước.")
            return
        partial = self._stream_text
        if partial:
            sources = self._live_sources
            theme = self._theme
            body = markdown_to_html(
                partial,
                cite=lambda n: citation_anchor("cite:live:{}".format(n), n, theme)
                if n in sources else "[{}]".format(n),
            )
            view.set_body_html(answer_document(body, theme))
        view.set_notice(
            "Đã dừng. Câu trả lời chưa hoàn chỉnh."
            if partial else "Đã dừng trước khi có câu trả lời."
        )
        if self._live_sources:
            view.set_sources(list(self._live_sources.values()), "Nguồn đã đọc")
        view.answer_content = partial
        view.answer_citations = list(self._live_sources.values())
        seconds, detail = self._turn_meta()
        view.show_actions(copy=bool(partial), retry=True, meta=seconds, meta_detail=detail)
        self._transient.append((self._live_anchor, view))
        self.steps_label.setText(_STATUS_STOPPED)
        self._update_turn_actions()

    def _finish_error(self, title, detail):
        view = self._ensure_live()
        self._live = None
        if self._restore_replaced(view):
            self.steps_label.setText("Không tạo lại được ({}) — giữ câu trả lời trước.".format(detail))
            return
        if self._stream_text:
            view.set_body_html(answer_document(markdown_to_html(self._stream_text), self._theme))
        else:
            view.set_body_html("")
        view.show_error(title, detail)
        view.show_actions(retry=True)
        self._transient.append((self._live_anchor, view))
        self._update_turn_actions()

    def _on_agent_error(self, message):
        self._flush_timer.stop()
        self._pending_question = None
        self.steps_label.setText(
            "Trợ lý gặp lỗi: {}. Hãy nhấn Thử lại.".format(message)
        )
        self._finish_error("Trợ lý gặp lỗi", message)
        self._stream_text = self._stream_raw = ""

    def _on_agent_worker_finished(self):
        self._agent_worker = None
        if self._live is not None and self._server_worker is None:
            # The worker ended without a result: stopped, or it died.
            if self._stopping:
                self._finish_stopped()
            else:
                self._finish_error(
                    "Trợ lý dừng bất thường", "Không nhận được câu trả lời. Hãy thử lại."
                )
        self._stopping = False
        if not self._shutting_down:
            self._set_busy(False)
            self._restore_focus()

    def showEvent(self, event):
        super().showEvent(event)
        if self._closed:
            self._closed = False
            if not self._shutting_down:
                # Opening the assistant means asking it something. After the
                # show completes; focusing a widget mid-show is lost.
                QTimer.singleShot(0, self._focus_question)

    def hideEvent(self, event):
        super().hideEvent(event)
        # Only the reader closing the dock hides it explicitly. Minimising the
        # window, or hiding it for the home screen, hides the dock as well and
        # shows it again later; the cursor must stay where the reader left it.
        self._closed = self.isHidden()

    def _focus_question(self):
        if self.isVisible() and self.question_input.isEnabled():
            self.question_input.setFocus(Qt.OtherFocusReason)

    def _restore_focus(self):
        """Back to the question box, unless the reader is working elsewhere."""
        focus = QApplication.focusWidget()
        if focus is None or focus is self or self.isAncestorOf(focus):
            self.question_input.setFocus()

    def _last_turn_view(self):
        for view in reversed(self.transcript.views()):
            if isinstance(view, AssistantTurnView):
                return view
        return None

    def _live_text(self, view=None):
        view = view or self._live
        return view.plain_text() if view is not None else ""

    def _transcript_html(self):
        parts = []
        for view in self.transcript.views():
            if isinstance(view, AssistantTurnView):
                parts.append(view.body.html())
            elif isinstance(view, UserMessageView):
                parts.append(html.escape(view.text))
        return "\n".join(parts)

    def _update_turn_actions(self):
        """Regenerate on the latest answer only; Retry on the latest failure."""
        busy = self._is_busy()
        last_index = len(self._messages) - 1
        latest_view = self.transcript.views()[-1] if self.transcript.views() else None
        for index, view in self._message_views:
            if isinstance(view, AssistantTurnView):
                view.set_regenerate_visible(
                    not busy and index == last_index and view is latest_view
                )
        for anchor, view in self._transient:
            view.set_retry_visible(not busy and anchor == last_index and view is latest_view)

    def _history_before_last_question(self):
        """Context for re-asking the last question: what came before it,
        bounded in _start_agent like any other question's."""
        return list(self._messages[:-1])

    def _ask_again(self):
        if self._is_busy() or not self._messages or self._messages[-1][0] != "user":
            return
        self._pending_history = self._history_before_last_question()
        self._begin_turn(self._messages[-1][1])

    def _retry(self, view):
        if self._is_busy():
            return
        self._transient = [(a, v) for a, v in self._transient if v is not view]
        self.transcript.remove_view(view)
        self._ask_again()

    def _regenerate(self):
        if self._is_busy() or not self._messages or self._messages[-1][0] != "assistant":
            return
        old = self._messages.pop()
        view = None
        if self._message_views and self._message_views[-1][0] == len(self._messages):
            _index, view = self._message_views.pop()
            view.hide()
        self._replacing = (old, view)
        self._ask_again()

    def _drop_replaced(self):
        """The new answer exists: forget the one it replaces, here and on disk."""
        if self._replacing is None:
            return
        _old, view = self._replacing
        self._replacing = None
        if view is not None:
            self.transcript.remove_view(view)
        try:
            delete_last = getattr(self._history, "delete_last_message", None)
            if self._session_id is not None and delete_last is not None:
                delete_last(self._session_id, "assistant")
        except Exception as exc:
            self._set_history_warning(exc)

    def _restore_replaced(self, live_view):
        """A regeneration was stopped or failed: show the previous answer again."""
        if self._replacing is None:
            return False
        old, view = self._replacing
        self._replacing = None
        self.transcript.remove_view(live_view)
        self._messages.append(old)
        if view is not None:
            view.show()
            self._message_views.append((len(self._messages) - 1, view))
        self._update_turn_actions()
        return True

    def _copy_answer(self, view):
        """The answer for pasting elsewhere: its text, the list of its
        sources (which the screen shows as chips) and the legal note."""
        raw = view.answer_content or ""
        content = strip_source_footer(raw.replace(DISCLAIMER_VI, ""))
        if not content:
            return
        lines = [content]
        if view.answer_citations:
            lines += ["", "Nguồn:"]
            for citation in view.answer_citations:
                name = citation.so_ki_hieu or "Văn bản #{}".format(citation.doc_id)
                where = ", trang {}".format(citation.page) if citation.page else ""
                lines.append("[{}] {}{}".format(citation.n, name, where))
        if DISCLAIMER_VI in raw:
            lines += ["", DISCLAIMER_VI]
        QApplication.clipboard().setText("\n".join(lines))
        view.show_copied()

    def _on_suggestion(self, question, needs_document):
        """Ask a suggested question. The document suggestion says "văn bản
        đang mở", which already scopes that one question to the open
        document; the scope box is left as the reader set it, so the next
        questions are not quietly locked to that document."""
        if self._is_busy():
            return
        if needs_document and not self._scope_doc_ids:
            return
        self.question_input.setPlainText(question)
        self.send_question()

    def eventFilter(self, watched, event):
        if watched is self.question_input and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.composer.setProperty("focused", event.type() == QEvent.FocusIn)
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)
        return super().eventFilter(watched, event)

    def event(self, event):
        if (event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Escape
                and self._is_busy()):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._is_busy():
            event.accept()
            self.stop_generation()
            return
        super().keyPressEvent(event)

    def _track(self, worker):
        if self._worker_tracker is not None:
            try:
                self._worker_tracker(worker)
            except Exception as exc:
                logger.warning("Không thể theo dõi chat worker: %s", exc)

    def _agent_history(self, messages=None):
        """Return a bounded recent context, never the whole persisted session:
        a token budget that follows the model's context, without old [n]
        markers, source footers or disclaimers, "not found" answers marked
        as such and pointing questions resolved (rag_agent.
        conversation_history). A question that was stopped or failed has no
        answer and is left out. messages: (role, content, citations)
        entries, else the session's."""
        return conversation_history(
            self._messages if messages is None else messages, self._chat_context_size()
        )

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
        self.session_combo.updateGeometry()  # it is as wide as the title shown
        self._update_session_tip()
        self.delete_session_button.setEnabled(select_id is not None)

    def _update_session_tip(self):
        """A long title is cut short on screen; the tooltip has all of it."""
        tip = "Các cuộc trò chuyện đã lưu"
        if self.session_combo.currentData() is not None:
            tip = "{}\n{}".format(self.session_combo.currentText(), tip)
        self.session_combo.setToolTip(tip)

    def _on_session_changed(self, _index):
        self._update_session_tip()
        session_id = self.session_combo.currentData()
        if session_id is None or session_id == self._session_id or self._agent_worker:
            return
        loaded_id, messages = self._history.load_session(int(session_id))
        self._session_id = loaded_id
        self._messages = self._stored_messages(messages)
        self._transient = []
        self._live = None
        self._replacing = None
        self._render_messages()

    def new_session(self):
        if self._agent_worker is not None:
            return
        self._session_id = None
        self._messages = []
        self._stream_text = self._stream_raw = ""
        self._transient = []
        self._live = None
        self._replacing = None
        self._refresh_sessions(None)
        self._render_messages()
        self.steps_label.setText("Cuộc trò chuyện mới")
        self.question_input.setFocus()

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
            self._transient = []
            self._live = None
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
            self._messages = self._stored_messages(messages)
            if self._messages:
                self._render_messages()
            self._refresh_sessions(self._session_id)
        except Exception as exc:
            self._set_history_warning(exc)

    @staticmethod
    def _stored_messages(items):
        """Loaded rows as messages; rows saved before the turn context was
        recorded have none (rag_agent.turn_records reads their citations)."""
        return [
            _Message(item["role"], str(item.get("content") or ""),
                     list(item.get("citations") or []), item.get("meta") or {})
            for item in items
            if item.get("role") in ("user", "assistant")
        ]

    def _persist_message(self, role, content, citations, meta=None):
        try:
            if self._session_id is None:
                self._session_id = self._history.new_session(str(content)[:80])
                self._refresh_sessions(self._session_id)
            if meta:
                self._history.append(self._session_id, role, content, citations, meta=meta)
            else:
                self._history.append(self._session_id, role, content, citations)
        except Exception as exc:
            self._set_history_warning(exc)

    def stop_generation(self):
        busy = self._is_busy()
        if busy and not self._stopping:
            # Words already received are kept; nothing after the click is drawn.
            self._flush_timer.stop()
            if self._stream_dirty:
                self._flush_stream()
            self._stopping = True
        if self._agent_worker is not None:
            self._agent_worker.cancel()
        if self._server_worker is not None:
            self._server_worker.requestInterruption()
            self.server_manager.stop()
        self._pending_question = None
        if busy:
            self.steps_label.setText(_STATUS_STOPPING)
            self.stop_button.setEnabled(False)
        else:
            self._set_busy(False)

    def shutdown(self):
        """Cancel background work and release local model/database resources."""
        self._shutting_down = True
        self._flush_timer.stop()
        self.status_row.end()
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
