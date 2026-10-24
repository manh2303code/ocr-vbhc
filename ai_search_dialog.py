"""
AI Search Dialog - Giao diện tìm kiếm ngữ nghĩa bằng AI
Cho phép người dùng nhập mô tả tự nhiên để tìm văn bản liên quan.

2026-10 minimal redesign (docs/design/minimal-ui-2026-10.md): the search
field is the header; one filled button; the index count and the rebuild
button sit quietly under it; hints live in tooltips. Each result is a row of
the store's own list style: the number, type and date of the document, its
main content on one line (whole in the tooltip), and the passage that
matched, quoted in the document typeface with the words of the question in
bold. The passage used to be hidden in a tooltip and cut to 300 of its
~2000 characters; now a readable window of it is on screen, the expand
button shows the whole passage, and Enter / double click / the open button
opens the document itself.

Colours come from the theme stylesheet (area "analytics", objectNames
aiSearch*) and theme_tokens; nothing here writes a colour of its own.
"""
import html
import logging
import re
import sqlite3
from contextlib import closing

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from doc_list_widget import build_tooltip_html
from editor_widgets import DOCUMENT_FONT, ElidedLabel
from retrieval import hybrid_search
from reindex_dialog import ReindexDialog
from search_store import best_passage, fold_vietnamese
from theme_manager_v2 import ThemeManager
from theme_tokens import colors_for
from ui_icons import icon
from vector_index import VectorIndex

logger = logging.getLogger("AISearch")

# Characters of the matched passage shown before the row is expanded: about
# three lines of the dialog at its default width.
PASSAGE_CHARS = 320
_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Filler words of a question, never set in bold. With their accents: folded,
# "bao" (bao nhiêu) would also hide "bảo", and "tai" (tại) "tài".
_FILLER = frozenset(
    "là của và có gì nào các những được cho về trong theo với này ai bao nhiêu "
    "không một thì mà để tại từ đến hay như sao bị ở ra vào khi nay hỏi".split()
)
_FILLER_FOLDED = frozenset(fold_vietnamese(word) for word in _FILLER)


def _hybrid_or_legacy_search(
    embedding_model, vector_store, vector_index, query_text, reranker=None
):
    """Return the dialog's legacy tuple shape, falling back on any hybrid error."""
    try:
        with closing(sqlite3.connect(vector_store.db_path)) as conn, conn:
            chunks = hybrid_search(
                conn, vector_index, embedding_model, reranker, query_text,
                doc_ids=None,
            )
        best = {}
        for chunk in chunks:
            if chunk.doc_id not in best or chunk.score > best[chunk.doc_id].score:
                best[chunk.doc_id] = chunk
        return [
            (chunk.doc_id, chunk.score, chunk.chunk_text)
            for chunk in sorted(best.values(), key=lambda item: item.score, reverse=True)
        ]
    except Exception as exc:
        logger.warning("Hybrid search failed; using legacy vector search: %s", exc)
        embedding = embedding_model.encode(query_text)
        if embedding is None:
            raise ValueError("Không thể mã hóa truy vấn tìm kiếm")
        return vector_store.search_chunks(
            embedding, reranker=reranker, query_text=query_text
        )


def _word_key(word, accented):
    """How a word is compared: as written (ignoring case) when the question
    was typed with accents, else without accents ("quy" finds "quỹ")."""
    return word.lower() if accented else fold_vietnamese(word)


def highlight_terms(text, query):
    """HTML of `text` with the phrases of `query` in bold.

    Vietnamese words are single syllables, so one word alone says little
    ("bộ" of "đường bộ" is also in "cán bộ"): when the question has two
    content words or more, only neighbouring pairs of them are marked.
    Filler words of the question ("và", "của") never are. A question typed
    with accents is matched with accents, one typed without, without.
    """
    text = text or ""
    words = _WORD_RE.findall(query or "")
    accented = any(fold_vietnamese(w) != w.lower() for w in words)
    filler = _FILLER if accented else _FILLER_FOLDED
    content = [w for w in words if _word_key(w, accented) not in filler and len(w) > 1]
    keys = [_word_key(w, accented) for w in words]
    pairs = {(a, b) for (a, b), (wa, wb) in zip(zip(keys, keys[1:]), zip(words, words[1:]))
             if wa in content and wb in content}
    singles = {_word_key(w, accented) for w in content} if len(content) < 2 or not pairs else set()

    matches = list(_WORD_RE.finditer(text))
    found = [_word_key(m.group(), accented) for m in matches]
    marked = [False] * len(matches)
    for index, key in enumerate(found):
        if key in singles:
            marked[index] = True
        if index + 1 < len(found) and (key, found[index + 1]) in pairs:
            marked[index] = marked[index + 1] = True

    parts, last, index = [], 0, 0
    while index < len(matches):
        if not marked[index]:
            index += 1
            continue
        end = index
        while end + 1 < len(matches) and marked[end + 1] and \
                not text[matches[end].end():matches[end + 1].start()].strip(" "):
            end += 1  # one bold run across a phrase ("quỹ phát triển đất")
        start, stop = matches[index].start(), matches[end].end()
        parts.append(html.escape(text[last:start], quote=False))
        parts.append("<b>{}</b>".format(html.escape(text[start:stop], quote=False)))
        last, index = stop, end + 1
    parts.append(html.escape(text[last:], quote=False))
    return "".join(parts)


def passage_preview(chunk_text, query, size=PASSAGE_CHARS):
    """The part of a matched chunk the question is about, on one paragraph,
    with "…" where it was cut from a longer text."""
    flat = " ".join((chunk_text or "").split())
    window = best_passage(flat, query, size)
    if not window:
        return ""
    start = flat.find(window)
    lead = "…" if start > 0 else ""
    tail = "…" if start >= 0 and start + len(window) < len(flat) else ""
    return lead + window + tail


class EmbeddingWorker(QThread):
    """Worker thread để encode query + search, tránh đóng băng UI."""

    finished = pyqtSignal(list)  # List[(doc_id, score, best_chunk_text)]
    error = pyqtSignal(str)

    def __init__(
        self, embedding_model, vector_store, vector_index, query_text, reranker=None
    ):
        super().__init__()
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.vector_index = vector_index
        self.query_text = query_text
        self.reranker = reranker

    def run(self):
        import time as _t
        import logging as _log
        _logger = _log.getLogger("AISearchTiming")
        try:
            t0 = _t.time()
            results = _hybrid_or_legacy_search(
                self.embedding_model, self.vector_store, self.vector_index,
                self.query_text, reranker=self.reranker,
            )
            total = _t.time() - t0
            _logger.info(
                f"[SEARCH] query={self.query_text[:40]!r} | "
                f"total={total*1000:.0f}ms | results={len(results)}"
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class ResultRow(QFrame):
    """One result: number, type and date; main content; matched passage."""

    open_requested = pyqtSignal(int)
    resized = pyqtSignal()

    def __init__(self, doc_id, number, is_file, kind, issued, summary,
                 chunk_text, query, similarity, parent=None):
        super().__init__(parent)
        self.setObjectName("aiResultRow")
        self.doc_id = doc_id
        self._query = query
        self._chunk = chunk_text or ""
        self._preview = passage_preview(self._chunk, query)
        self._expanded = False
        self._theme = "light"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 9, 10, 10)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.number_label = QLabel(number)
        self.number_label.setObjectName("aiResultNumber")
        self.number_label.setProperty("kind", "file" if is_file else "number")
        head.addWidget(self.number_label)
        for text, name in ((kind, "aiResultKind"), (issued, "aiResultDate")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setVisible(bool(text))
            head.addWidget(label)
        head.addStretch(1)
        head.addWidget(similarity)
        self.expand_button = QToolButton()
        self.expand_button.setObjectName("aiResultExpand")
        self.expand_button.setAutoRaise(True)
        self.expand_button.setIconSize(QSize(16, 16))
        self.expand_button.setCursor(Qt.PointingHandCursor)
        self.expand_button.clicked.connect(lambda: self.set_expanded(not self._expanded))
        # Nothing more to show when the preview is the whole passage.
        self.expand_button.setVisible(len(" ".join(self._chunk.split())) > len(self._preview))
        head.addWidget(self.expand_button)
        self.open_button = QToolButton()
        self.open_button.setObjectName("aiResultOpen")
        self.open_button.setAutoRaise(True)
        self.open_button.setIconSize(QSize(16, 16))
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setToolTip("Mở văn bản\nEnter hoặc nhấp đúp vào kết quả")
        self.open_button.setAccessibleName("Mở văn bản")
        self.open_button.clicked.connect(lambda: self.open_requested.emit(self.doc_id))
        head.addWidget(self.open_button)
        layout.addLayout(head)

        self.summary_label = ElidedLabel(summary)
        self.summary_label.setObjectName("aiResultSummary")
        self.summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.summary_label.setVisible(bool(summary))
        layout.addWidget(self.summary_label)

        self.passage_label = QLabel()
        self.passage_label.setObjectName("aiResultPassage")
        self.passage_label.setTextFormat(Qt.RichText)
        self.passage_label.setWordWrap(True)
        self.passage_label.setFont(QFont(DOCUMENT_FONT, 11))
        layout.addWidget(self.passage_label)
        self._show_passage()

    def is_expanded(self):
        return self._expanded

    def passage_text(self):
        """The passage as shown, without markup."""
        text = self.passage_label.text().replace("<br>", "\n")
        return html.unescape(re.sub(r"<[^>]+>", "", text))

    def set_expanded(self, expanded):
        self._expanded = bool(expanded) and self.expand_button.isVisible()
        self._show_passage()
        self.resized.emit()

    def apply_theme(self, theme):
        self._theme = theme
        self._sync_icons()

    def _show_passage(self):
        if self._expanded:
            paragraphs = [" ".join(p.split()) for p in self._chunk.splitlines()]
            body = "<br>".join(highlight_terms(p, self._query) for p in paragraphs if p)
        else:
            body = highlight_terms(self._preview, self._query)
        self.passage_label.setText(body)
        self.passage_label.setVisible(bool(body))
        self._sync_icons()

    def _sync_icons(self):
        colour = colors_for(self._theme)["text_secondary"]
        self.expand_button.setIcon(icon(
            "chevron-up" if self._expanded else "chevron-down", size=16, color=colour))
        tip = "Thu gọn đoạn" if self._expanded else "Xem cả đoạn phù hợp"
        self.expand_button.setToolTip(tip)
        self.expand_button.setAccessibleName(tip)
        self.open_button.setIcon(icon("open", size=16, color=colour))


class ResultList(QListWidget):
    """The result rows; their height follows the width (passages wrap)."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def relayout(self):
        width = self.viewport().width()
        for index in range(self.count()):
            item = self.item(index)
            row = self.itemWidget(item)
            if row is not None and width > 0:
                item.setSizeHint(QSize(width, row.heightForWidth(width)))


class AISearchDialog(QDialog):
    """Dialog tìm kiếm ngữ nghĩa bằng AI embedding."""

    document_selected = pyqtSignal(int, list)  # doc_id, keywords (rỗng cho AI search)

    def __init__(self, db, embedding_model, vector_store, reranker=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.vector_index = VectorIndex(vector_store.db_path)
        if not self.vector_index.is_native():
            with closing(sqlite3.connect(vector_store.db_path)) as conn, conn:
                self.vector_index.sync_from_chunks(conn)
        self.reranker = reranker
        self.search_worker = None
        self._theme = ThemeManager.instance().current
        self.setObjectName("aiSearchDialog")
        self.setWindowTitle("Tìm kiếm AI ngữ nghĩa")
        self.resize(900, 640)
        self.setup_ui()
        ThemeManager.instance().theme_changed.connect(self.apply_theme)
        self.apply_theme(self._theme)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QWidget()
        top.setObjectName("aiSearchHeader")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(16, 14, 16, 8)
        top_layout.setSpacing(6)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("aiSearchInput")
        self.search_input.setPlaceholderText("Mô tả nội dung văn bản cần tìm")
        self.search_input.setToolTip(
            "Viết bằng lời thường, ví dụ: Công văn tỉnh Hưng Yên về sử dụng hộ chiếu ngoại giao\n"
            "Enter để tìm")
        self.search_input.setClearButtonEnabled(True)
        self._search_icon = self.search_input.addAction(QIcon(), QLineEdit.LeadingPosition)
        self.search_input.returnPressed.connect(self.do_search)
        input_layout.addWidget(self.search_input, 1)

        self.search_btn = QPushButton("Tìm")
        self.search_btn.setObjectName("aiSearchButton")
        self.search_btn.setProperty("variant", "primary")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.setToolTip("Tìm theo ý nghĩa\nEnter")
        self.search_btn.clicked.connect(self.do_search)
        input_layout.addWidget(self.search_btn)
        top_layout.addLayout(input_layout)

        # What the last search found (or why not), then the index.
        info = QHBoxLayout()
        info.setSpacing(6)
        self.response_label = QLabel("")
        self.response_label.setObjectName("aiSearchSummary")
        self.response_label.setVisible(False)
        info.addWidget(self.response_label)
        self.status_label = QLabel("")
        self.status_label.setObjectName("aiSearchStatus")
        self.status_label.setWordWrap(True)
        info.addWidget(self.status_label, 1)
        embed_count = self.vector_store.get_embedding_count()
        self.count_label = QLabel(self._count_text(embed_count))
        self.count_label.setObjectName("aiSearchIndexCount")
        self.count_label.setToolTip("Số văn bản đã có trong chỉ mục tìm kiếm ngữ nghĩa")
        info.addWidget(self.count_label)
        self.reindex_btn = QToolButton()
        self.reindex_btn.setObjectName("aiSearchReindex")
        self.reindex_btn.setAutoRaise(True)
        self.reindex_btn.setIconSize(QSize(16, 16))
        self.reindex_btn.setCursor(Qt.PointingHandCursor)
        self.reindex_btn.setToolTip(
            "Cập nhật chỉ mục\n"
            "Tạo lại chỉ mục tìm kiếm cho toàn bộ tài liệu.\n"
            "Dùng khi kết quả tìm kiếm chưa chính xác."
        )
        self.reindex_btn.setAccessibleName("Cập nhật chỉ mục")
        self.reindex_btn.clicked.connect(self.do_reindex)
        info.addWidget(self.reindex_btn)
        top_layout.addLayout(info)
        layout.addWidget(top)

        self.result_list = ResultList()
        self.result_list.setObjectName("aiSearchResults")
        self.result_list.setSelectionMode(QListWidget.SingleSelection)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.result_list.setFrameShape(QFrame.NoFrame)
        self.result_list.itemActivated.connect(
            lambda item: self.preview_document(item.data(Qt.UserRole)))
        layout.addWidget(self.result_list, 1)

        self.search_input.setFocus()

    @staticmethod
    def _count_text(count):
        return f"Đã lập chỉ mục {count} văn bản"

    def apply_theme(self, name):
        """Recolour the icons and rebuild the tooltips (they carry token
        colours); everything else follows the application stylesheet."""
        self._theme = "dark" if name == "dark" else "light"
        tokens = colors_for(self._theme)
        self._search_icon.setIcon(icon("sparkles", size=16, color=tokens["text_muted"]))
        self.reindex_btn.setIcon(icon("sync", size=16, color=tokens["text_secondary"]))
        for index in range(self.result_list.count()):
            item = self.result_list.item(index)
            row = self.result_list.itemWidget(item)
            if row is not None:
                row.apply_theme(self._theme)
                info = item.data(Qt.UserRole + 1)
                if info:
                    item.setToolTip(self._build_tooltip(*info))

    def _set_status(self, text, state=""):
        """A transient line: progress (state "busy") or a problem ("error")."""
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def do_search(self):
        """Thực hiện tìm kiếm ngữ nghĩa."""
        query = self.search_input.text().strip()
        if not query:
            self._set_status("Nhập nội dung cần tìm.", "error")
            return

        if not self.embedding_model.is_available():
            self._set_status(
                "Mô hình embedding chưa được cài đặt. "
                "Vui lòng tải AITeamVN/Vietnamese_Embedding vào thư mục model_embedding/",
                "error",
            )
            return

        self.search_btn.setEnabled(False)
        self.search_btn.setText("Đang tìm…")
        self.response_label.setVisible(False)
        self._set_status("Đang phân tích truy vấn và tìm kiếm…", "busy")
        QApplication.processEvents()

        self.search_worker = EmbeddingWorker(
            self.embedding_model,
            self.vector_store,
            self.vector_index,
            query,
            reranker=self.reranker,
        )
        self.search_worker.finished.connect(self.on_search_complete)
        self.search_worker.error.connect(self.on_search_error)
        self.search_worker.start()

    def _build_ai_response(self, query: str, results: list) -> str:
        """Tạo phản hồi tự nhiên từ AI dựa trên kết quả tìm kiếm."""
        total_docs = self.vector_store.get_embedding_count()

        if not results:
            return (
                f'Tôi đã tìm kiếm trong toàn bộ {total_docs} tài liệu '
                f'nhưng không tìm thấy kết quả nào phù hợp với "{query}". '
                f'Bạn thử mô tả lại bằng cách khác hoặc dùng từ khóa cụ thể hơn nhé.'
            )

        top_score = int(results[0][1] * 100)
        count = len(results)

        if count == 1:
            return (
                f'Tôi tìm thấy 1 tài liệu có liên quan tới "{query}" '
                f'với độ phù hợp {top_score}%. '
                f'Nếu chưa đúng ý, bạn thử mô tả chi tiết hơn nhé.'
            )

        if count <= 3:
            return (
                f'Có {count} tài liệu có vẻ liên quan tới "{query}". '
                f'Kết quả phù hợp nhất đạt {top_score}%. '
                f'Nếu chưa tìm được văn bản cần thiết, thử diễn đạt lại truy vấn.'
            )

        if top_score >= 70:
            confidence = "Kết quả đầu tiên rất có khả năng đúng với nội dung bạn cần."
        elif top_score >= 50:
            confidence = "Tài liệu đầu tiên có khả năng chứa nội dung bạn cần tìm."
        else:
            confidence = "Các kết quả mang tính tham khảo, bạn nên xem qua từng tài liệu."

        return (
            f'Đây là {count} kết quả có liên quan nhất tới "{query}", '
            f'được sắp xếp theo mức độ phù hợp (cao nhất {top_score}%). '
            f'{confidence}'
        )

    def on_search_complete(self, results):
        """Show the results, best first, each with its matched passage."""
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Tìm")
        self._set_status("")
        self.result_list.clear()

        query = self.search_input.text().strip()
        self.response_label.setToolTip(self._build_ai_response(query, results))
        if not results:
            # An empty list is a moment for direction, on screen.
            self.response_label.setText(
                "Không tìm thấy văn bản phù hợp. Thử mô tả theo cách khác, "
                "hoặc dùng từ khóa cụ thể hơn.")
            self.response_label.setVisible(True)
            return
        self.response_label.setText(f"{len(results)} văn bản phù hợp nhất")
        self.response_label.setVisible(True)

        for doc_id, score, best_chunk_text in results:
            doc_info = self.db.get_document_info(doc_id)
            version = self.db.get_latest_version(doc_id)
            file_name = doc_info[2] if doc_info else f"Tài liệu {doc_id}"
            so_ky_hieu = ((version[5] if version else "") or "").strip()
            row = ResultRow(
                doc_id,
                so_ky_hieu or file_name,
                not so_ky_hieu,
                ((version[9] if version else "") or "").strip(),
                ((version[6] if version else "") or "").strip(),
                " ".join(((version[10] if version else "") or "").split()),
                best_chunk_text,
                query,
                self._create_similarity_widget(int(score * 100)),
            )
            row.apply_theme(self._theme)
            row.open_requested.connect(self.preview_document)
            row.resized.connect(self.result_list.relayout)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, doc_id)
            item.setData(Qt.UserRole + 1, (doc_info, version, best_chunk_text))
            item.setToolTip(self._build_tooltip(doc_info, version, best_chunk_text))
            self.result_list.addItem(item)
            self.result_list.setItemWidget(item, row)
        self.result_list.relayout()
        self.result_list.setCurrentRow(0)

    def result_rows(self):
        """The rows on screen, best result first."""
        return [self.result_list.itemWidget(self.result_list.item(i))
                for i in range(self.result_list.count())]

    def set_result_expanded(self, index, expanded=True):
        """Show the whole matched passage of one result, or its preview."""
        rows = self.result_rows()
        if 0 <= index < len(rows):
            rows[index].set_expanded(expanded)

    def _build_tooltip(self, doc_info, version, best_chunk_text: str = "") -> str:
        """The document behind a result, in the library's tooltip style:
        number, type and issue date, the whole main content, the file.
        The matched passage is on screen, so it is not repeated here."""
        if not doc_info and not version:
            return "Không có thông tin chi tiết"
        file_name = doc_info[2] if doc_info else ""
        if not version:
            return build_tooltip_html("", "", "", filename=file_name, theme=self._theme)
        # [9]=loai_vb, [10]=nd_chinh, [5]=so_ki_hieu, [6]=ngay_bh
        return build_tooltip_html(
            version[5] or "", version[9] or "", version[10] or "",
            filename=file_name, issued=version[6] or "", theme=self._theme,
        )

    def _create_similarity_widget(self, pct: int) -> QWidget:
        """Relevance: a short thin bar and the percentage, both quiet."""
        widget = QWidget()
        widget.setObjectName("aiResultRelevance")
        widget.setToolTip(f"Độ liên quan {pct}%")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(6)

        bar = QProgressBar()
        bar.setObjectName("aiResultRelevanceBar")
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, pct)))
        bar.setTextVisible(False)
        bar.setFixedSize(40, 4)

        label = QLabel(f"{pct}%")
        label.setObjectName("aiResultRelevanceValue")
        label.setMinimumWidth(30)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(bar)
        layout.addWidget(label)
        return widget

    def on_search_error(self, error_msg):
        """Xử lý lỗi tìm kiếm."""
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Tìm")
        self._set_status(f"Lỗi: {error_msg}", "error")
        logger.error(f"AI Search error: {error_msg}")

    def do_reindex(self):
        """Tạo lại chỉ mục embedding cho toàn bộ tài liệu."""
        reply = QMessageBox.question(
            self,
            "Cập nhật chỉ mục",
            "Tạo lại chỉ mục tìm kiếm cho toàn bộ tài liệu?\n"
            "Quá trình này có thể mất vài phút tùy số lượng văn bản.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.reindex_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self._set_status("Đang cập nhật chỉ mục…", "busy")

        dialog = ReindexDialog(
            self.db.db_path, self.embedding_model, parent=self
        )
        dialog.completed.connect(self.on_reindex_complete)
        dialog.failed.connect(self.on_reindex_error)
        dialog.exec_()

    def on_reindex_complete(self, success_count):
        """Hoàn tất reindex."""
        self.reindex_btn.setEnabled(True)
        self.search_btn.setEnabled(True)

        if not self.vector_index.is_native():
            with closing(sqlite3.connect(self.vector_store.db_path)) as conn, conn:
                self.vector_index.sync_from_chunks(conn)

        embed_count = self.vector_store.get_embedding_count()
        self.count_label.setText(self._count_text(embed_count))
        self._set_status(f"Đã cập nhật chỉ mục cho {success_count} tài liệu.")

    def on_reindex_error(self, error_msg):
        """Xử lý lỗi reindex."""
        self.reindex_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self._set_status(f"Lỗi cập nhật chỉ mục: {error_msg}", "error")

    def preview_document(self, doc_id):
        """Chọn tài liệu và đóng dialog."""
        self.document_selected.emit(doc_id, [])
        self.accept()
