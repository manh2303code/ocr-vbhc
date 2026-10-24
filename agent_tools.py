"""OpenAI tool schemas and local executors for legal-document RAG."""

import difflib
import math
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence

import metadata_query
from doc_resolver import NUMBER_RE, DocumentCatalog, number_key, resolve
from legal_graph import GraphStore, get_validity
from legal_relations import normalize_so_ki_hieu
from retrieval import RetrievedChunk, hybrid_search
from search_store import best_passage, fold_vietnamese

# What the chat model is shown of each retrieved chunk: the window the question
# is about (search_store.best_passage), not the first N characters. A tool
# result stays inside a budget so a 4B model with an 8K context still has room.
PASSAGE_CHARS = 1400
# A chunk is at most about 2300 characters: a document read from its
# beginning shows each chunk whole.
WHOLE_CHUNK_CHARS = 2400
RESULT_BUDGET_CHARS = 9000
# Prefetch keeps 12 candidates and shows at most 6 of them. Reranking the top
# 12 instead of 20 showed the model the answer as often (36/39 questions; the
# first source differed on 1 of 41) at 0.58x the CPU prefetch time.
PREFETCH_RERANK_TOP = 12


def result_budget_for_context(n_ctx: Optional[int]) -> int:
    """Characters one tool result may use with a chat model of n_ctx tokens.

    Measured with Qwen3 on Vietnamese documents: a request is about 1350
    tokens of instructions and tool schemas plus 0.36 token per character of
    sources. GPU contexts (16384 and up) take prefetch plus tool rounds of
    about 3200 tokens each, chat history and the answer; RagAgent trims older
    rounds when they would not fit. 8192 is the CPU context, where prompt
    processing runs at tens of tokens per second: its sources stay at the
    3600 characters prefetch used before passages grew.
    """
    if not n_ctx or n_ctx >= 16384:
        return RESULT_BUDGET_CHARS
    if n_ctx > 8192:
        return 6000
    return 3600


# Sources say who signed in words ("Nguyễn Văn A, Phó chánh văn phòng (ký
# thay Chánh văn phòng)") rather than the signature block in capitals; see
# _signer_text. Measured equal (52 conversations 186/199 on, 185 off; the
# 26-document set 65 and 66/67 on, 64 off), so off: the block is written as
# before. Label lines copied into an answer are caught by answer_check.
READABLE_SIGNER = False

# Questions about several named documents: each gets this share of the
# result budget, at most this many of them.
MAX_PREFETCH_DOCUMENTS = 4
# A chunk shown for one of several documents gets at least this much text.
MIN_GROUP_PASSAGE_CHARS = 600


def _tuned_scale(score: float) -> float:
    """A reranker score on the scale the prefetch tolerance was tuned on.

    The reranker used to apply a second sigmoid to CrossEncoder output that
    was already a probability, so every score lay in [0.5, 0.731]; the 20 %
    tolerance below (and the 36/39 measurement behind PREFETCH_RERANK_TOP)
    were made on that scale. Comparing on it keeps the same chunks selected
    now that the reranker returns the probability itself."""
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(score)))))


# "410/TTr-HĐND", "1213-QĐ/TU", "17-CT/TW", "03/2023/TT-BTC"
_NUMBER_RE = NUMBER_RE
# Folded (see fold_vietnamese) phrases. "các"/"những" alone are not enough:
# "các cấp", "những tỉnh nào" ask about a document's content, not for a list.
_LIST_MARKERS = ("liet ke", "danh sach", "nhung van ban", "cac van ban", "co bao nhieu")
_OVERVIEW_MARKERS = ("bao nhieu van ban", "thong ke", "toan bo kho", "kho kien thuc",
                     "gom nhung loai", "nhom theo loai", "tong quan kho")
# A question about one document as a whole rather than a detail in it. Its
# words ("tóm tắt", "nói về gì") match nothing in the text, so a search finds
# arbitrary passages; the document is read from its beginning instead.
_WHOLE_DOCUMENT_MARKERS = ("tom tat", "tom luoc", "noi ve gi", "noi ve viec gi", "noi ve van de",
                           "noi dung chinh", "noi dung gi", "muc dich", "tong quan", "khai quat",
                           "y chinh", "gioi thieu ve", "ve viec gi")
_DOC_TYPES = ("quyet dinh", "cong van", "thong bao", "to trinh", "ke hoach", "bao cao", "chi thi",
              "nghi quyet", "thong tu", "nghi dinh", "huong dan", "bien ban", "chuong trinh", "quy che")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_ISSUER_RE = re.compile(r"\bdo (.+?) ban hanh\b")
_SIGNER_RE = re.compile(r"\bdo (.+?) ky\b")
# Agency headers spell these out or abbreviate them; compare the short form.
_AGENCY_ABBREVIATIONS = (
    ("hoi dong nhan dan", "hdnd"),
    ("uy ban nhan dan", "ubnd"),
    ("dai bieu quoc hoi", "dbqh"),
)
_LIST_LIMIT = 40


def _function(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_DOC_ID = {"type": "integer", "description": "ID nội bộ của văn bản."}
_QUERY = {"type": "string", "description": "Cụm từ hoặc câu cần tra cứu."}
_RELATION_TYPES = {
    "type": "array",
    "items": {"type": "string", "enum": ["can_cu", "thay_the", "sua_doi", "bai_bo"]},
    "description": "Các loại quan hệ pháp lý cần lọc.",
}

TOOLS_SCHEMA: List[dict] = [
    _function(
        "search_documents",
        "Tìm các đoạn liên quan trong kho văn bản hành chính.",
        {
            "query": _QUERY,
            "loai_vb": {"type": "string", "description": "Loại văn bản."},
            "co_quan": {"type": "string", "description": "Cơ quan ban hành."},
            "from_date": {"type": "string", "description": "Ngày bắt đầu (YYYY-MM-DD)."},
            "to_date": {"type": "string", "description": "Ngày kết thúc (YYYY-MM-DD)."},
        },
        ["query"],
    ),
    _function(
        "get_document_info",
        "Lấy thông tin nhận dạng và metadata của một văn bản theo số ký hiệu hoặc ID.",
        {
            "so_ki_hieu": {"type": "string", "description": "Số, ký hiệu văn bản."},
            "doc_id": _DOC_ID,
        },
        [],
    ),
    _function(
        "get_related_documents",
        "Tra các văn bản căn cứ, thay thế, sửa đổi hoặc bãi bỏ có liên quan.",
        {"doc_id": _DOC_ID, "relation_types": _RELATION_TYPES},
        ["doc_id"],
    ),
    _function(
        "check_validity",
        "Kiểm tra trạng thái hiệu lực được suy diễn từ quan hệ giữa các văn bản trong kho.",
        {"doc_id": _DOC_ID},
        ["doc_id"],
    ),
    _function(
        "read_document_section",
        "Đọc các đoạn phù hợp trong đúng một văn bản.",
        {"doc_id": _DOC_ID, "query": _QUERY},
        ["doc_id", "query"],
    ),
    _function(
        "list_documents",
        "Liệt kê văn bản trong kho theo loại, năm ban hành, cơ quan ban hành hoặc người ký. "
        "Dùng cho câu hỏi liệt kê, đếm, lọc; không cần cụm từ tìm kiếm.",
        {
            "loai_vb": {"type": "string", "description": "Loại văn bản, ví dụ: Tờ trình."},
            "nam": {"type": "integer", "description": "Năm ban hành."},
            "co_quan": {"type": "string", "description": "Cơ quan ban hành."},
            "nguoi_ky": {"type": "string", "description": "Họ tên người ký."},
        },
        [],
    ),
]


@dataclass(frozen=True)
class ToolResult:
    text: str
    sources: List[dict]
    # Reranker score (0..1) of the best chunk behind a prefetch; None when it
    # is not a similarity search or no reranker ran. Lets the agent tell
    # "the store has this" from "these are merely the least unrelated chunks".
    relevance: Optional[float] = None
    # The documents a prefetch of several named documents was split between.
    documents: Optional[List[int]] = None
    # A table computed from the documents' metadata (metadata_query.Aggregate)
    # rather than passages: its counts and positions are exact.
    aggregate: Optional[object] = None


class ToolExecutor:
    """Dispatch the six local tools without allowing calls outside UI scope."""

    _STATUS_LABELS = {
        "con_hieu_luc": "còn hiệu lực",
        "het_hieu_luc": "hết hiệu lực",
        "sua_doi_mot_phan": "đã bị sửa đổi một phần",
        "khong_xac_dinh": "không xác định",
    }
    _RELATION_LABELS = {
        "can_cu": "căn cứ",
        "thay_the": "thay thế",
        "sua_doi": "sửa đổi",
        "bai_bo": "bãi bỏ",
    }

    def __init__(
        self,
        conn,
        vindex,
        embedding_model,
        reranker,
        scope_doc_ids: Optional[List[int]] = None,
        result_budget_chars: int = RESULT_BUDGET_CHARS,
    ):
        self.conn = conn
        # Settable after construction: the chat panel learns the model's
        # context size only once the chat server is up.
        self.result_budget_chars = int(result_budget_chars)
        self.vindex = vindex
        self.embedding_model = embedding_model
        self.reranker = reranker
        self.scope_doc_ids = (
            list(dict.fromkeys(int(item) for item in scope_doc_ids))
            if scope_doc_ids is not None
            else None
        )
        self._version_columns = None  # type: Optional[str]
        self._catalog = None  # type: Optional[DocumentCatalog]
        self._handlers: Dict[str, Callable[[dict], ToolResult]] = {
            "search_documents": self._search_documents,
            "get_document_info": self._get_document_info,
            "get_related_documents": self._get_related_documents,
            "check_validity": self._check_validity,
            "read_document_section": self._read_document_section,
            "list_documents": self._list_documents,
        }

    @property
    def catalog(self) -> DocumentCatalog:
        """Every document's number, kind, date and summary (newest version),
        read once per executor: the chat panel makes one per question."""
        if self._catalog is None:
            self._catalog = DocumentCatalog(
                (row[0], row[2], row[3], row[4], row[7]) for row in self._latest_versions()
            )
        return self._catalog

    def resolve(self, question: str, state=None):
        """The documents the question names or points at
        (doc_resolver.resolve), looked up in the whole store: a named
        document wins over the scope. Without a conversation state, a scope
        of one document is the open one ("văn bản này")."""
        open_doc = None
        if state is None and self.scope_doc_ids is not None and len(self.scope_doc_ids) == 1:
            open_doc = self.scope_doc_ids[0]
        return resolve(question, self.catalog, state, open_doc=open_doc)

    def aggregate(self, question: str, conversation=None,
                  within: Optional[Sequence[int]] = None) -> Optional[ToolResult]:
        """A question about the documents' metadata ("thống kê các lãnh
        đạo", "bao nhiêu công văn", "ông ấy còn ký văn bản nào khác")
        answered by a table computed here (metadata_query), one block per
        source; None for any other question, and with a scope set (the
        open document is not a store to count in). within: count among
        these documents only (the ones the last turn listed)."""
        if self.scope_doc_ids is not None:
            return None
        found = metadata_query.answer(question, self._latest_versions(), conversation,
                                      list(within) if within else None)
        if found is None:
            return None
        if not found.sources:
            return ToolResult("\n\n".join(found.blocks), [], aggregate=found)
        entries = [(block, self._source(doc_id, number))
                   for block, (doc_id, number) in zip(found.blocks, found.sources)]
        blocks, sources = self._within_budget(entries)
        if len(blocks) < len(entries):
            blocks[-1] += "\n(Còn {} mục nữa không hiện vì giới hạn độ dài.)".format(len(entries) - len(blocks))
        return ToolResult("\n\n".join(blocks), sources, aggregate=found)

    def prefetch(self, question: str, documents: Optional[Sequence[int]] = None,
                 others: bool = True, within: Optional[Sequence[int]] = None) -> ToolResult:
        """Ground every answer before the model can invent optional filters.

        documents: the ones the question names or points at, in its order
        (RagAgent gets them from resolve). They are shown first whatever
        their reranker score; several share the result budget. Without them
        a document number in the question, or a scope of one document,
        decides as before. others=False: one such document alone, without
        the search of the whole store for chunks that answer better (a
        follow-up whose search of the whole store was already made).
        within: search only these documents ("trong các công văn đó, công
        văn nào …" over the ones the last turn listed), whatever the score.
        """
        query = self._required_text({"question": question}, "question")
        folded = fold_vietnamese(query)
        if within and not documents:
            allowed = [int(doc_id) for doc_id in within]
            if self.scope_doc_ids is not None:
                allowed = [doc_id for doc_id in allowed if doc_id in self.scope_doc_ids]
            chunks = hybrid_search(
                self.conn, self.vindex, self.embedding_model, self.reranker,
                query, top_k=12, doc_ids=allowed, rerank_top=PREFETCH_RERANK_TOP,
            ) if allowed else []
            if not chunks:
                return ToolResult("Không tìm thấy nội dung phù hợp.", [])
            counts, selected = {}, []  # at most two chunks of each document, six in all
            for chunk in chunks:
                counts[chunk.doc_id] = counts.get(chunk.doc_id, 0) + 1
                if counts[chunk.doc_id] <= 2:
                    selected.append(chunk)
                if len(selected) >= 6:
                    break
            return self._format_chunks(
                selected, query, passage_chars=self._prefetch_passage_chars(len(selected)))
        rows = [row for row in (self._metadata(int(doc_id)) for doc_id in (documents or []))
                if row is not None][:MAX_PREFETCH_DOCUMENTS]
        if len(rows) > 1:
            return self._prefetch_documents(rows, query, folded)
        # A question that names a document is about that document, and so is
        # every question asked with the scope set to one open document.
        numbered = (rows[0] if rows else None) or self._numbered_document(query) \
            or self._only_scoped_document()
        if numbered is not None and any(marker in folded for marker in _WHOLE_DOCUMENT_MARKERS):
            return self._read_document(numbered)
        # A named document is about that document, even when the question
        # also says "các" or a year.
        filters = self._listing_filters(folded) if numbered is None else {}
        overview = any(marker in folded for marker in _OVERVIEW_MARKERS)
        if overview and not filters:
            return self._corpus_overview()
        if filters and (overview or self._asks_for_list(folded)):
            listed = self._list_documents(filters)
            if listed.sources:
                return listed
        if rows and not others:
            chunks = []  # type: List[RetrievedChunk]
        else:
            doc_ids = self._eligible_doc_ids({})
            chunks = hybrid_search(
                self.conn, self.vindex, self.embedding_model, self.reranker,
                query, top_k=12, doc_ids=doc_ids, rerank_top=PREFETCH_RERANK_TOP,
            )
        if numbered is None and not chunks:
            return ToolResult("Không tìm thấy nội dung phù hợp.", [])
        # Chunks scoring close to the best one, at most three per document
        # and six in all, but never fewer than three: the chunk that holds a
        # date or a figure often scores a little below the one that repeats
        # the question's words.
        best = chunks[0].score if chunks else 0.0
        top = _tuned_scale(best) if chunks else 0.0
        tolerance = max(0.05, abs(top) * 0.20)
        counts, selected = {}, []
        for chunk in chunks:
            if top - _tuned_scale(chunk.score) > tolerance and len(selected) >= 3:
                break
            counts[chunk.doc_id] = counts.get(chunk.doc_id, 0) + 1
            if counts[chunk.doc_id] <= 3:
                selected.append(chunk)
            if len(selected) >= 6:
                break
        if numbered is None:
            shown = self._format_chunks(
                selected, query, passage_chars=self._prefetch_passage_chars(len(selected)),
            )
            # The reranker cannot read a question typed without marks (it
            # scores the right chunk near zero), so its score says nothing then.
            # A scope the user chose is never second-guessed by the score.
            reranked = self.reranker is not None and self.reranker.is_available()
            marked = fold_vietnamese(query) != query.lower()
            gated = reranked and chunks and marked and self.scope_doc_ids is None
            return replace(shown, relevance=best if gated else None)
        # The named document goes first: its card (number, issuer, signer,
        # summary) and its best chunks, then what the open search found.
        # RRF alone let other documents that share the number's words win.
        named = int(numbered[0])
        pinned = hybrid_search(
            self.conn, self.vindex, self.embedding_model, self.reranker,
            query, top_k=3, doc_ids=[named], rerank_top=8,
        )
        pinned_ids = {chunk.chunk_id for chunk in pinned}
        # Another document is shown only when it answers better than the
        # named one's own best passage: a sibling plan of the same committee
        # otherwise gets cited for a question about this one.
        floor = pinned[0].score if pinned else float("-inf")
        others = [chunk for chunk in selected if chunk.chunk_id not in pinned_ids
                  and (chunk.doc_id == named or chunk.score >= floor)]
        chosen = pinned + others[:max(0, 6 - len(pinned))]
        lead = self._document_card(numbered)
        return self._format_chunks(
            chosen, query, lead=lead,
            passage_chars=self._prefetch_passage_chars(len(chosen), len(lead[0])),
        )

    def _prefetch_documents(self, rows, query, folded):
        """Several named documents, each in an equal share of the result
        budget, in the order the question names them: its card (number,
        issuer, signer, summary), then its best chunks for the question, or
        its opening chunks when the question is about the documents as a
        whole ("tóm tắt", "nói về gì"). One document's chunks can no longer
        crowd out the next one's."""
        share = self.result_budget_chars // len(rows)
        whole = any(marker in folded for marker in _WHOLE_DOCUMENT_MARKERS)
        wanted = {2: 3, 3: 2}.get(len(rows), 1)
        entries = []
        for row in rows:
            doc_id = int(row[0])
            lead = self._document_card(row)
            room = share - len(lead[0])
            count = max(1, min(wanted, room // (MIN_GROUP_PASSAGE_CHARS + 300)))
            if whole:
                chunks = self._document_chunks(row)
                passage_chars = min(WHOLE_CHUNK_CHARS, max(MIN_GROUP_PASSAGE_CHARS, room - 300))
            else:
                chunks = hybrid_search(
                    self.conn, self.vindex, self.embedding_model, self.reranker,
                    query, top_k=count, doc_ids=[doc_id], rerank_top=8,
                )
                passage_chars = min(WHOLE_CHUNK_CHARS,
                                    max(MIN_GROUP_PASSAGE_CHARS, room // max(1, len(chunks)) - 300))
            group = [lead] + self._chunk_entries(chunks, "" if whole else query, passage_chars)
            used, kept = 0, []
            for block, source in group:
                if kept and used + len(block) > share:
                    break
                kept.append((block, source))
                used += len(block) + 2
            entries.extend(kept)
        return ToolResult("\n\n".join(block for block, _source in entries),
                          [source for _block, source in entries],
                          documents=[int(row[0]) for row in rows])

    def _prefetch_passage_chars(self, count, reserved=0):
        """Split the result budget between the chunks prefetch selected: a
        few are shown whole (a chunk is at most about 2300 characters), six
        get PASSAGE_CHARS each. Label lines take about 300 per block."""
        return max(PASSAGE_CHARS, (self.result_budget_chars - reserved) // max(1, count) - 300)

    def _only_scoped_document(self):
        """The metadata row of the one document the scope is set to, if so."""
        if self.scope_doc_ids is None or len(self.scope_doc_ids) != 1:
            return None
        return self._metadata(self.scope_doc_ids[0])

    def _document_chunks(self, row):
        """A document's chunks in reading order."""
        doc_id = int(row[0])
        stored = self.conn.execute(
            "SELECT id, chunk_text, page_number FROM document_chunks "
            "WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        return [
            RetrievedChunk(
                chunk_id=int(chunk_id), doc_id=doc_id, chunk_text=text or "",
                so_ki_hieu=row[2] or "", loai_vb=row[3] or "", ngay_bh=row[4] or "",
                page_number=page,
            )
            for chunk_id, text, page in stored
        ]

    def _read_document(self, row):
        """A document read from its beginning: its card, then its chunks in
        reading order, whole, while the result budget lasts."""
        chunks = self._document_chunks(row)
        lead = self._document_card(row)
        # An empty query keeps each passage at the start of its chunk.
        return self._format_chunks(chunks, "", lead=lead, passage_chars=WHOLE_CHUNK_CHARS)

    def execute(self, name: str, arguments: dict) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult("Công cụ không tồn tại: {}".format(name), [])
        if not isinstance(arguments, dict):
            return ToolResult("Lỗi: tham số công cụ phải là một JSON object.", [])
        try:
            return handler(arguments)
        except (TypeError, ValueError) as exc:
            return ToolResult("Lỗi tham số: {}".format(exc), [])
        except Exception as exc:
            return ToolResult("Lỗi khi thực thi công cụ: {}".format(exc), [])

    @staticmethod
    def _required_text(arguments, name):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("'{}' phải là chuỗi không rỗng".format(name))
        return value.strip()

    @staticmethod
    def _required_int(arguments, name):
        value = arguments.get(name)
        if isinstance(value, bool):
            raise ValueError("'{}' phải là số nguyên".format(name))
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("'{}' phải là số nguyên".format(name))
        if value <= 0:
            raise ValueError("'{}' phải lớn hơn 0".format(name))
        return value

    @staticmethod
    def _parse_date(value, field_name):
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("'{}' phải là ngày hợp lệ".format(field_name))
        match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
        if match:
            parts = match.groups()
        else:
            match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
            parts = (match.group(3), match.group(2), match.group(1)) if match else None
        try:
            return date(*(int(item) for item in parts))
        except (TypeError, ValueError):
            raise ValueError("'{}' phải là ngày hợp lệ".format(field_name))

    def _signer_columns(self):
        # Databases from before the signer fields existed (and small test
        # fixtures) lack them; read them as empty instead of failing.
        if self._version_columns is None:
            names = {row[1] for row in self.conn.execute("PRAGMA table_info(document_versions)")}
            self._version_columns = ", ".join(
                "v.{}".format(name) if name in names else "''"
                for name in ("chu_ky", "chuc_vu")
            )
        return self._version_columns

    def _latest_versions(self, doc_ids=None):
        """Rows: id, file_name, so_ki_hieu, loai_vb, ngay_bh, cqbh_tren,
        cqbh_duoi, nd_chinh, chu_ky, chuc_vu."""
        sql = (
            "SELECT d.id, d.file_name, v.so_ki_hieu, v.loai_vb, v.ngay_bh, "
            "v.cqbh_tren, v.cqbh_duoi, v.nd_chinh, {} "
            "FROM documents d LEFT JOIN document_versions v ON v.document_id = d.id "
            "AND v.version_number = (SELECT MAX(v2.version_number) "
            "FROM document_versions v2 WHERE v2.document_id = d.id) "
        ).format(self._signer_columns())
        params = []  # type: List[int]
        if doc_ids is not None:
            params = sorted({int(item) for item in doc_ids})
            if not params:
                return []
            sql += "WHERE d.id IN ({}) ".format(",".join("?" for _ in params))
        return self.conn.execute(sql + "ORDER BY d.id", params).fetchall()

    def _scoped_versions(self):
        rows = self._latest_versions()
        if self.scope_doc_ids is None:
            return rows
        allowed = set(self.scope_doc_ids)
        return [row for row in rows if int(row[0]) in allowed]

    def _metadata(self, doc_id):
        rows = self._latest_versions([doc_id])
        return rows[0] if rows else None

    @staticmethod
    def _one_line(value, limit=0, drop_notes=False):
        """An OCR field on one line: blank, repeated and mark-only lines go,
        and with drop_notes so do "(Handwritten signature)"-style notes."""
        lines = []  # type: List[str]
        for line in str(value or "").splitlines():
            if drop_notes:
                line = re.sub(r"\([^)]*\)", " ", line)
            line = " ".join(line.split())
            if re.search(r"\w", line) and line not in lines:
                lines.append(line)
        text = " ".join(lines)
        if limit and len(text) > limit:
            text = text[:limit - 1].rsplit(" ", 1)[0] + "…"
        return text

    def _agency_text(self, row):
        return "; ".join(part for part in (self._one_line(row[5]), self._one_line(row[6])) if part)

    def _signer_text(self, row):
        """'Tên, Chức vụ (ký thay …)', or whichever is known. With
        READABLE_SIGNER the signature block is read (metadata_query.
        parse_position) instead of copied in capitals: the model copied
        "TL. THƯỜNG TRỰC HĐND CHÁNH VĂN PHÒNG" into answers, or read it as
        "Trưởng ban Thường trực HĐND"."""
        signer = self._one_line(row[8], 80, drop_notes=True)
        title = self._one_line(row[9], 120, drop_notes=True)
        if READABLE_SIGNER and (row[8] or row[9]):
            position, name = metadata_query.parse_position(row[9] or "", row[8] or "")
            name, described = name or signer, position.describe()
            if name and described:
                return "{}, {}".format(name, described)
            return name or described or title
        if signer and title:
            return "{} ({})".format(signer, title)
        return signer or title

    def _issuer_line(self, row):
        """'Cơ quan: … · Ký: Tên (Chức vụ)'; empty parts are left out."""
        parts = []
        agency = self._agency_text(row)
        if agency:
            parts.append("Cơ quan: {}".format(agency))
        signer = self._signer_text(row)
        if signer:
            parts.append("Ký: {}".format(signer))
        return " · ".join(parts)

    @staticmethod
    def _label(*parts):
        return "【{}】".format(" · ".join(part for part in parts if part))

    def _document_card(self, row):
        """The named document's identity block, cited as the document itself."""
        summary = self._one_line(row[7], 400)
        lines = (
            self._label(row[2] or "Không số", row[3] or "Không rõ loại",
                        self._one_line(row[4]), "thông tin văn bản"),
            self._issuer_line(row),
            "Trích yếu: {}".format(summary) if summary else "",
        )
        return "\n".join(line for line in lines if line), self._source(row[0], row[2])

    @staticmethod
    def _number_key(value):
        return number_key(value)

    def _numbered_document(self, question):
        """The document whose số ký hiệu the question names, if any.

        Both sides are compared without marks and punctuation. Recognition
        slips such as 410/TTtr-HĐND for 410/TTr-HĐND still match when the
        leading number agrees and the rest is close (difflib ratio >= 0.8).
        """
        wanted = [
            key for key in (self._number_key(match.group()) for match in _NUMBER_RE.finditer(question))
            if re.search(r"[a-z]", key)  # a date such as 10/01/2018 is not a number
        ]
        if not wanted:
            return None
        best, best_ratio = None, 0.0
        for row in self._scoped_versions():
            have = self._number_key(row[2])
            if not have:
                continue
            have_lead = re.search(r"\d+", have)
            for key in wanted:
                if have == key:
                    return row
                key_lead = re.search(r"\d+", key)
                if not (have_lead and key_lead and have_lead.group() == key_lead.group()):
                    continue
                ratio = difflib.SequenceMatcher(None, have, key).ratio()
                if ratio >= 0.8 and ratio > best_ratio:
                    best, best_ratio = row, ratio
        return best

    @staticmethod
    def _asks_for_list(folded):
        if any(marker in folded for marker in _LIST_MARKERS):
            return True
        return any(
            "{} {}".format(word, kind) in folded
            for word in ("cac", "nhung") for kind in _DOC_TYPES
        )

    @staticmethod
    def _listing_filters(folded):
        """list_documents arguments a listing question states outright."""
        filters = {}  # type: Dict[str, object]
        kinds = [(folded.find(kind), kind) for kind in _DOC_TYPES if kind in folded]
        if kinds:
            filters["loai_vb"] = min(kinds)[1]
        year = _YEAR_RE.search(folded)
        if year:
            filters["nam"] = int(year.group(1))
        issuer = _ISSUER_RE.search(folded)
        if issuer and issuer.group(1).strip():
            filters["co_quan"] = issuer.group(1).strip()
        return filters

    @staticmethod
    def _agency_words(value):
        folded = fold_vietnamese(str(value or ""))
        for long_form, short_form in _AGENCY_ABBREVIATIONS:
            folded = folded.replace(long_form, short_form)
        return set(re.findall(r"[0-9a-z]+", folded))

    @staticmethod
    def _issued_year(value):
        match = _YEAR_RE.search(value or "")
        return int(match.group(1)) if match else None

    @staticmethod
    def _source(doc_id, so_ki_hieu="", chunk_id=None, page=None):
        return {
            "chunk_id": int(chunk_id) if chunk_id is not None else None,
            "doc_id": int(doc_id),
            "page": int(page) if page is not None else None,
            "so_ki_hieu": so_ki_hieu or "",
        }

    def _eligible_doc_ids(self, arguments):
        loai = fold_vietnamese(str(arguments.get("loai_vb") or "")).strip()
        agency = fold_vietnamese(str(arguments.get("co_quan") or "")).strip()
        start = self._parse_date(arguments.get("from_date"), "from_date")
        end = self._parse_date(arguments.get("to_date"), "to_date")
        if start and end and start > end:
            raise ValueError("'from_date' không được sau 'to_date'")
        has_filter = bool(loai or agency or start or end)
        if not has_filter:
            return self.scope_doc_ids

        scope = set(self.scope_doc_ids) if self.scope_doc_ids is not None else None
        eligible = []
        for row in self._latest_versions():
            doc_id = int(row[0])
            if scope is not None and doc_id not in scope:
                continue
            if loai and loai not in fold_vietnamese(row[3] or ""):
                continue
            agencies = fold_vietnamese("{} {}".format(row[5] or "", row[6] or ""))
            if agency and agency not in agencies:
                continue
            issued = self._parse_date(row[4], "ngay_bh") if row[4] else None
            if (start and (issued is None or issued < start)) or (
                end and (issued is None or issued > end)
            ):
                continue
            eligible.append(doc_id)
        return eligible

    def _within_budget(self, entries):
        """Keep (block, source) pairs while the text stays inside the budget;
        the first one is always kept."""
        blocks, sources, used = [], [], 0
        for block, source in entries:
            if blocks and used + len(block) > self.result_budget_chars:
                break
            blocks.append(block)
            sources.append(source)
            used += len(block) + 2
        return blocks, sources

    @staticmethod
    def _passage(text, query, size=PASSAGE_CHARS):
        """The part of a chunk the question is about, on one line, with "…"
        where the chunk goes on."""
        text = (text or "").strip()
        window = best_passage(text, query, size)
        body = " ".join(window.split())
        if not body:
            return ""
        start = text.find(window)
        if start > 0:
            body = "…" + body
        if start >= 0 and start + len(window) < len(text):
            body += "…"
        return body

    def _format_chunks(self, chunks, query, lead=None, passage_chars=PASSAGE_CHARS):
        """One block per chunk: label, issuer and signer, then the passage.

        Blocks never contain a blank line: RagAgent numbers them [n] by
        splitting on one.
        """
        entries = ([lead] if lead else []) + self._chunk_entries(chunks, query, passage_chars)
        blocks, sources = self._within_budget(entries)
        return ToolResult("\n\n".join(blocks) if blocks else "Không tìm thấy nội dung phù hợp.", sources)

    def _chunk_entries(self, chunks, query, passage_chars=PASSAGE_CHARS):
        """(block, source) for each chunk; see _format_chunks."""
        entries = []
        metadata = {
            int(row[0]): row
            for row in self._latest_versions([chunk.doc_id for chunk in chunks])
        } if chunks else {}
        for chunk in chunks:
            page = "trang {}".format(chunk.page_number) if chunk.page_number else "không rõ trang"
            row = metadata.get(int(chunk.doc_id))
            lines = (
                self._label(chunk.so_ki_hieu or "Không số", chunk.loai_vb or "Không rõ loại",
                            self._one_line(chunk.ngay_bh), page),
                self._issuer_line(row) if row else "",
                self._passage(chunk.chunk_text, query, passage_chars),
            )
            entries.append((
                "\n".join(line for line in lines if line),
                self._source(chunk.doc_id, chunk.so_ki_hieu, chunk.chunk_id, chunk.page_number),
            ))
        return entries

    def _list_documents(self, arguments):
        """Documents whose metadata match every given filter, marks ignored."""
        kind = fold_vietnamese(str(arguments.get("loai_vb") or "")).strip()
        agency = self._agency_words(arguments.get("co_quan"))
        signer = set(re.findall(r"[0-9a-z]+", fold_vietnamese(str(arguments.get("nguoi_ky") or ""))))
        year = None
        if arguments.get("nam") not in (None, ""):
            year = self._required_int(arguments, "nam")
        matched = []
        for row in self._scoped_versions():
            if kind and kind not in fold_vietnamese(row[3] or ""):
                continue
            if year is not None and self._issued_year(row[4]) != year:
                continue
            if agency and not agency <= self._agency_words("{} {}".format(row[5] or "", row[6] or "")):
                continue
            if signer and not signer <= set(re.findall(r"[0-9a-z]+", fold_vietnamese(row[8] or ""))):
                continue
            matched.append(row)
        if not matched:
            return ToolResult("Không có văn bản nào trong kho khớp điều kiện liệt kê.", [])
        entries = []
        for row in matched[:_LIST_LIMIT]:
            parts = (
                row[2] or "Không số", row[3] or "Không rõ loại", self._one_line(row[4]),
                self._agency_text(row), self._one_line(row[7], 160),
            )
            entries.append((" · ".join(part for part in parts if part), self._source(row[0], row[2])))
        blocks, sources = self._within_budget(entries)
        header = "Có {} văn bản phù hợp".format(len(matched))
        if len(blocks) < len(matched):
            header += ", liệt kê {} văn bản đầu".format(len(blocks))
        blocks[0] = "{}:\n{}".format(header, blocks[0])
        return ToolResult("\n\n".join(blocks), sources)

    def _search_documents(self, arguments):
        query = self._required_text(arguments, "query")
        doc_ids = self._eligible_doc_ids(arguments)
        if doc_ids == []:
            return ToolResult("Không tìm thấy nội dung phù hợp.", [])
        chunks = hybrid_search(
            self.conn,
            self.vindex,
            self.embedding_model,
            self.reranker,
            query,
            top_k=8,
            doc_ids=doc_ids,
        )
        return self._format_chunks(chunks, query)

    def _corpus_overview(self):
        rows = self._scoped_versions()
        if not rows:
            return ToolResult("Kho tài liệu chưa có văn bản nào.", [])

        grouped = {}
        for row in rows:
            grouped.setdefault((row[3] or "Không rõ loại").strip(), []).append(row)
        lines = ["Tổng số văn bản: {}".format(len(rows))]
        sources = []
        for kind in sorted(grouped, key=str.casefold):
            items = grouped[kind]
            samples = []
            for row in items[:3]:
                summary = " ".join((row[7] or "").split()) or "chưa có tóm tắt"
                samples.append("{} — {}".format(row[2] or row[1] or "Không số", summary))
                sources.append(self._source(row[0], row[2]))
            lines.append("{}: {}. Mẫu: {}".format(kind, len(items), "; ".join(samples)))
        return ToolResult("\n".join(lines), sources)

    def _get_document_info(self, arguments):
        doc_id = arguments.get("doc_id")
        number = arguments.get("so_ki_hieu")
        if doc_id is None and not (isinstance(number, str) and number.strip()):
            raise ValueError("cần cung cấp 'doc_id' hoặc 'so_ki_hieu'")
        if doc_id is not None:
            row = self._metadata(self._required_int(arguments, "doc_id"))
        else:
            normalized = normalize_so_ki_hieu(number)
            row = next(
                (item for item in self._latest_versions()
                 if normalize_so_ki_hieu(item[2] or "") == normalized),
                None,
            )
        if row is None:
            return ToolResult("Không tìm thấy văn bản.", [])
        text = (
            "Văn bản: {number}\nLoại: {kind}\nCơ quan: {agency}\n"
            "Ngày ban hành: {issued}\nTệp: {file_name}\nNội dung chính: {summary}"
        ).format(
            number=row[2] or "Không số",
            kind=row[3] or "Không rõ",
            agency="; ".join(value for value in (row[5], row[6]) if value) or "Không rõ",
            issued=row[4] or "Không rõ",
            file_name=row[1] or "Không rõ",
            summary=row[7] or "Chưa có",
        )
        signer = self._signer_text(row)
        if signer:
            text += "\nNgười ký: {}".format(signer)
        return ToolResult(text, [self._source(row[0], row[2])])

    def _get_related_documents(self, arguments):
        doc_id = self._required_int(arguments, "doc_id")
        relation_types = arguments.get("relation_types")
        if relation_types is not None and not isinstance(relation_types, list):
            raise ValueError("'relation_types' phải là danh sách")
        allowed = set(relation_types or self._RELATION_LABELS)
        unknown = allowed.difference(self._RELATION_LABELS)
        if unknown:
            raise ValueError("loại quan hệ không hợp lệ: {}".format(", ".join(sorted(unknown))))

        graph = GraphStore(self.conn)
        relations = [("out", row) for row in graph.get_relations(doc_id, "out")]
        relations += [("in", row) for row in graph.get_relations(doc_id, "in")]
        lines, sources = [], []
        current = self._metadata(doc_id)
        current_number = current[2] if current else "Văn bản #{}".format(doc_id)
        for direction, relation in relations:
            relation_type = relation["relation_type"]
            if relation_type not in allowed:
                continue
            if direction == "out":
                other_id = relation["target_doc_id"]
                other = self._metadata(int(other_id)) if other_id is not None else None
                other_number = (other[2] if other else relation["target_so_ki_hieu"]) or "Không rõ"
                line = "{} {} {}".format(
                    current_number, self._RELATION_LABELS[relation_type], other_number
                )
            else:
                other_id = relation["source_doc_id"]
                other = self._metadata(int(other_id))
                other_number = other[2] if other else "Văn bản #{}".format(other_id)
                line = "{} {} {}".format(
                    other_number, self._RELATION_LABELS[relation_type], current_number
                )
            lines.append(line)
            if other_id is not None:
                sources.append(self._source(other_id, other_number))
        return ToolResult(
            "\n".join(lines) if lines else "Không tìm thấy quan hệ pháp lý phù hợp.",
            sources,
        )

    def _check_validity(self, arguments):
        doc_id = self._required_int(arguments, "doc_id")
        current = self._metadata(doc_id)
        if current is None:
            return ToolResult("Không tìm thấy văn bản.", [])
        status, reason_id = get_validity(self.conn, doc_id)
        text = "{}: {}.".format(
            current[2] or "Văn bản #{}".format(doc_id),
            self._STATUS_LABELS.get(status, status),
        )
        sources = [self._source(doc_id, current[2])]
        if reason_id is not None:
            reason = self._metadata(int(reason_id))
            reason_number = reason[2] if reason else "Văn bản #{}".format(reason_id)
            text += " Căn cứ suy diễn: {}.".format(reason_number)
            sources.append(self._source(reason_id, reason_number))
        return ToolResult(text, sources)

    def _read_document_section(self, arguments):
        doc_id = self._required_int(arguments, "doc_id")
        query = self._required_text(arguments, "query")
        if self.scope_doc_ids is not None and doc_id not in self.scope_doc_ids:
            return ToolResult("Văn bản nằm ngoài phạm vi tra cứu hiện tại.", [])
        if self._metadata(doc_id) is None:
            return ToolResult("Không tìm thấy văn bản.", [])
        chunks = hybrid_search(
            self.conn,
            self.vindex,
            self.embedding_model,
            self.reranker,
            query,
            top_k=5,
            doc_ids=[doc_id],
        )
        return self._format_chunks(chunks, query)
