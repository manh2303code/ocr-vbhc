"""OpenAI tool schemas and local executors for legal-document RAG."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional

from legal_graph import GraphStore, get_validity
from legal_relations import normalize_so_ki_hieu
from retrieval import hybrid_search


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
]


@dataclass(frozen=True)
class ToolResult:
    text: str
    sources: List[dict]


class ToolExecutor:
    """Dispatch the five local tools without allowing calls outside UI scope."""

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
    ):
        self.conn = conn
        self.vindex = vindex
        self.embedding_model = embedding_model
        self.reranker = reranker
        self.scope_doc_ids = (
            list(dict.fromkeys(int(item) for item in scope_doc_ids))
            if scope_doc_ids is not None
            else None
        )
        self._handlers: Dict[str, Callable[[dict], ToolResult]] = {
            "search_documents": self._search_documents,
            "get_document_info": self._get_document_info,
            "get_related_documents": self._get_related_documents,
            "check_validity": self._check_validity,
            "read_document_section": self._read_document_section,
        }

    def prefetch(self, question: str) -> ToolResult:
        """Ground every answer before the model can invent optional filters."""
        query = self._required_text({"question": question}, "question")
        normalized = query.casefold()
        overview_markers = (
            "bao nhiêu văn bản", "thống kê", "toàn bộ kho", "kho kiến thức",
            "gồm những loại", "nhóm theo loại", "tổng quan kho",
        )
        if any(marker in normalized for marker in overview_markers):
            return self._corpus_overview()
        doc_ids = self._eligible_doc_ids({})
        chunks = hybrid_search(
            self.conn, self.vindex, self.embedding_model, self.reranker,
            query, top_k=12, doc_ids=doc_ids, rerank_top=20,
        )
        if not chunks:
            return ToolResult("Không tìm thấy nội dung phù hợp.", [])
        best = chunks[0].score
        tolerance = max(0.05, abs(best) * 0.20)
        focused = [chunk for chunk in chunks if best - chunk.score <= tolerance]
        counts, selected = {}, []
        for chunk in focused:
            counts[chunk.doc_id] = counts.get(chunk.doc_id, 0) + 1
            if counts[chunk.doc_id] <= 3:
                selected.append(chunk)
            if len(selected) >= 6:
                break
        return self._format_chunks(selected or chunks[:3])

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

    def _latest_versions(self):
        return self.conn.execute(
            "SELECT d.id, d.file_name, v.so_ki_hieu, v.loai_vb, v.ngay_bh, "
            "v.cqbh_tren, v.cqbh_duoi, v.nd_chinh "
            "FROM documents d LEFT JOIN document_versions v ON v.document_id = d.id "
            "AND v.version_number = (SELECT MAX(v2.version_number) "
            "FROM document_versions v2 WHERE v2.document_id = d.id) "
            "ORDER BY d.id"
        ).fetchall()

    def _metadata(self, doc_id):
        return next((row for row in self._latest_versions() if int(row[0]) == doc_id), None)

    @staticmethod
    def _source(doc_id, so_ki_hieu="", chunk_id=None, page=None):
        return {
            "chunk_id": int(chunk_id) if chunk_id is not None else None,
            "doc_id": int(doc_id),
            "page": int(page) if page is not None else None,
            "so_ki_hieu": so_ki_hieu or "",
        }

    def _eligible_doc_ids(self, arguments):
        loai = str(arguments.get("loai_vb") or "").strip().casefold()
        agency = str(arguments.get("co_quan") or "").strip().casefold()
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
            if loai and loai not in (row[3] or "").casefold():
                continue
            agencies = "{} {}".format(row[5] or "", row[6] or "").casefold()
            if agency and agency not in agencies:
                continue
            issued = self._parse_date(row[4], "ngay_bh") if row[4] else None
            if (start and (issued is None or issued < start)) or (
                end and (issued is None or issued > end)
            ):
                continue
            eligible.append(doc_id)
        return eligible

    @staticmethod
    def _format_chunks(chunks):
        blocks, sources = [], []
        for chunk in chunks:
            body = " ".join((chunk.chunk_text or "").split())
            body = body if len(body) <= 600 else body[:597].rstrip() + "..."
            page = "trang {}".format(chunk.page_number) if chunk.page_number else "không rõ trang"
            blocks.append(
                "【{} · {} · {}】\n{}".format(
                    chunk.so_ki_hieu or "Không số",
                    chunk.loai_vb or "Không rõ loại",
                    page,
                    body,
                )
            )
            sources.append(
                ToolExecutor._source(
                    chunk.doc_id, chunk.so_ki_hieu, chunk.chunk_id, chunk.page_number
                )
            )
        return ToolResult("\n\n".join(blocks) if blocks else "Không tìm thấy nội dung phù hợp.", sources)

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
        return self._format_chunks(chunks)

    def _corpus_overview(self):
        rows = self._latest_versions()
        if self.scope_doc_ids is not None:
            allowed = set(self.scope_doc_ids)
            rows = [row for row in rows if int(row[0]) in allowed]
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
        return self._format_chunks(chunks)
