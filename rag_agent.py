"""Small, framework-free agent loop for the local legal RAG tools."""

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import answer_check
from agent_tools import TOOLS_SCHEMA, ToolResult
from doc_resolver import (
    HOW_NOTES,
    ConversationState,
    TurnRecord,
    conversation_block,
    is_meta_question,
)
from llm_client import ContextOverflowError, estimate_prompt_tokens
from search_store import fold_vietnamese


logger = logging.getLogger("RagAgent")

DISCLAIMER_VI = "⚠️ Thông tin tra cứu tự động, không thay thế tư vấn pháp lý."
TRUNCATED_NOTICE_VI = (
    "⚠️ Câu trả lời bị cắt vì chạm giới hạn độ dài của mô hình; "
    "hãy hỏi hẹp hơn để nhận câu trả lời đầy đủ."
)
CONTEXT_EXCEEDED_VI = (
    "Câu hỏi cùng lịch sử trò chuyện và nguồn trích dẫn dài hơn ngữ cảnh của mô hình. "
    "Hãy mở cuộc trò chuyện mới hoặc hỏi ngắn gọn hơn."
)

SYSTEM_PROMPT_VI = """Bạn là trợ lý nghiên cứu văn bản hành chính theo phong cách NotebookLM.
Ưu tiên trả lời trực tiếp, có cấu trúc ngắn gọn, tổng hợp đúng trọng tâm từ NGUỒN TRONG KHO.
Mọi nhận định về số hiệu, nội dung, cơ quan, ngày tháng hoặc hiệu lực văn bản phải dựa trên
nguồn và trích dẫn đúng [n]. Không được tự thêm bộ lọc cơ quan/ngày/loại nếu người dùng không
nêu rõ. Có thể dùng kiến thức đã học sẵn để giải thích bối cảnh chung, nhưng phải đặt riêng dưới
nhãn "Bối cảnh chung", không biến kiến thức chung thành nội dung của văn bản và không tạo citation.
Nếu nguồn không đủ thì nói rõ phần nào chưa xác minh được; nếu kho không có thông tin cho câu hỏi
thì mở đầu bằng "Không tìm thấy thông tin" và không trích dẫn [n]. Không hiển thị reasoning, thinking,
tên công cụ hay quy trình nội bộ. Luôn cảnh báo khi văn bản liên quan đã hết hiệu lực.
Hai dòng đầu của mỗi nguồn cho biết số ký hiệu, loại, ngày, trang, cơ quan ban hành và người ký
của văn bản chứa đoạn đó; dùng khi được hỏi về chính văn bản ấy, không chép lại hai dòng này
mà viết câu trả lời bằng lời của bạn và đặt [n] ngay sau ý lấy từ nguồn n.
Câu hỏi liệt kê, đếm hoặc lọc văn bản theo loại, năm, cơ quan hay người ký thì gọi list_documents.
Mọi câu trả lời phải kết thúc bằng: {}""".format(DISCLAIMER_VI)
# An answer that opens by saying the store has nothing on the question.
_NOT_FOUND_RE = re.compile(
    r"không tìm thấy|không tìm được|chưa tìm thấy|không có (?:thông tin|văn bản|dữ liệu|căn cứ)"
    r"|chưa có (?:thông tin|văn bản|dữ liệu)|không có trong kho|không đủ căn cứ|chưa xác minh được"
    r"|không (?:được )?(?:nêu|đề cập)",
    re.IGNORECASE,
)

# ── Turns that are not about the documents ──────────────────────────────
#
# Greetings, thanks and questions about the assistant itself used to go
# through retrieval like any question, and the answer then carried a source
# footer pointing at whatever chunks scored least badly. They are answered
# as conversation now: no search, no tools, no sources, no legal disclaimer.

CONVERSATION_PROMPT_VI = """Bạn là trợ lý của ứng dụng OCR VBHC: số hóa, tra cứu và hỏi đáp văn bản
hành chính trong kho của người dùng, chạy hoàn toàn trên máy. Trả lời ngắn gọn, thân thiện bằng
tiếng Việt. Khi được hỏi bạn làm được gì: tìm văn bản theo số ký hiệu hoặc nội dung, cho biết người
ký, ngày ban hành, nơi nhận, liệt kê văn bản theo loại, năm hay cơ quan, và luôn trích dẫn nguồn khi
trả lời từ văn bản. Không bịa nội dung văn bản."""

# Questions about the conversation itself ("ở đầu đoạn chat tôi đã hỏi về
# văn bản nào", "nhắc lại câu trả lời vừa rồi") went through retrieval and
# the sources then filled the "conversation" with documents never discussed.
# They are answered from the turns and the conversation state alone.
META_PROMPT_VI = """Bạn là trợ lý của ứng dụng OCR VBHC. Câu hỏi này là về chính cuộc trò chuyện:
người dùng đã hỏi gì, bạn đã trả lời gì, các lượt trước nói về văn bản nào. Chỉ dựa vào các lượt
trước và TRẠNG THÁI HỘI THOẠI; không suy đoán, không thêm văn bản chưa được nhắc tới, không trích
dẫn [n]. Khi nói tới một văn bản, nêu số ký hiệu của nó. Trả lời ngắn gọn bằng tiếng Việt."""
META_STATE_TOKENS = 600

# Switches of the conversation-memory behaviour, for A/B runs of the bench
# (bench/rag/multiturn); all on in the app.
RESOLVE_DOCUMENTS = True    # pin documents a question names or points at
CONVERSATION_FOCUS = True   # a follow-up with weak sources reads the last turn's documents
STATE_BLOCK = True          # send the TRẠNG THÁI HỘI THOẠI note with each question
META_ROUTING = True         # answer questions about the conversation without retrieval
FOCUS_ANAPHORA = True       # "ông ấy", "X đó": the last turn's documents when the search finds them
# Which documents a question was taken to mean, said next to the question
# instead of in the note: measured equal (125/138 against 126/138, the one
# difference the OCR rewrite), so off; the note says it.
UNDERSTOOD_LINE = False
# Write a number matched through an OCR slip as the store has it, and say
# next to the question what was asked: 3/3 against 0/3 (probe_ocr_note.py).
REWRITE_OCR_NUMBER = True
# Questions about the documents' metadata (who signed, positions, counts by
# type / issuer / year) answered from a table computed by the app
# (metadata_query) instead of passages.
AGGREGATES = True
# Check each answer against what the model was shown (answer_check) and,
# when something does not hold, ask once more with the correction.
ANSWER_CHECK = True
AGGREGATE_NOTE_VI = (
    "Các nguồn trên là bảng do ứng dụng tính từ thông tin của các văn bản trong kho: số lượng, người ký, "
    "chức vụ đã chính xác và đầy đủ. Trả lời bằng câu văn dựa trên bảng, dùng đúng con số ở dòng KẾT QUẢ "
    "và đúng chức vụ trong bảng (người ký thay không giữ chức vụ của cấp trưởng), trích dẫn [n]. Câu hỏi "
    "về số lượng thì nêu tổng số và số theo từng loại đúng như dòng KẾT QUẢ. Câu hỏi liệt kê hoặc thống kê "
    "thì nêu đủ từng mục của bảng, mỗi mục một dòng, không bỏ mục nào."
)
# A list answer is checked for every row of a table up to this many rows.
MAX_LISTED = 30

# One clause of a message that is pure conversation, after fold_vietnamese
# and with punctuation removed: one or more of these phrases ("ok cảm ơn",
# "tốt quá cảm ơn bạn").
_CONVERSATION_PHRASE = (
    r"(?:(?:xin )?chao(?: (?:ban|anh|chi|em|tro ly|moi nguoi|buoi sang|buoi chieu|buoi toi))*"
    r"|hello|hi|hey|alo"
    r"|(?:ban )?(?:co )?khoe khong|ban the nao"
    r"|cam on(?: (?:ban|nhieu|rat nhieu|tro ly))*|thanks?(?: you)?"
    r"|tam biet|bye|hen gap lai"
    r"|ok(?:e|ay)?|vang|da|duoc roi|tot(?: qua)?|hay qua|dung roi|hieu roi|ro roi"
    r"|ban la ai|ban ten (?:la )?gi"
    r"|(?:ban )?(?:co the |lam duoc |giup duoc )?(?:giup|lam)(?: duoc)? (?:gi|nhung gi)(?: cho toi)?"
    r"|huong dan su dung)"
    r"(?: (?:nhe|nha|nhe ban|a|nhi|vay))*"
)
_CONVERSATION_CLAUSE_RE = re.compile(
    "{phrase}(?: {phrase})*".format(phrase=_CONVERSATION_PHRASE)
)
_CLAUSE_SPLIT_RE = re.compile(r"[,.!?;:\n]+| va ")


def is_conversation(question: str) -> bool:
    """True when every clause of the message is a greeting, thanks or small talk."""
    folded = fold_vietnamese(question).strip()
    if not folded or re.search(r"\d", folded):
        return False
    clauses = [re.sub(r"[^\w\s]", " ", part).split() for part in _CLAUSE_SPLIT_RE.split(folded)]
    clauses = [" ".join(words) for words in clauses if words]
    return bool(clauses) and all(_CONVERSATION_CLAUSE_RE.fullmatch(clause) for clause in clauses)


# ── Follow-up questions ─────────────────────────────────────────────────
#
# "Ông ấy bị giám sát từ ngày nào?" names nobody; searched alone it finds
# nothing. A question that points back ("ông ấy", "văn bản đó", "sai rồi") is
# searched together with the previous question. Any other question is
# searched alone first, and again with the previous question only when alone
# it finds nothing relevant ("Hạn đăng ký là ngày nào?"). A question that
# names a document number always stands alone.

_FOLLOW_UP_MARKERS = (
    "ong ay", "ba ay", "anh ay", "chi ay", "nguoi do", "nguoi nay", "van ban do",
    "van ban nay", "van ban tren", "van ban ay", "tai lieu do", "cai do", "dieu do",
    "sai roi", "khong phai", "the con", "con ve", "vay thi", "y toi la",
)
_NAMES_A_NUMBER_RE = re.compile(r"\d+\s*[/-]\s*\w")


def previous_question(history: Optional[List[dict]]) -> str:
    return next(
        (m.get("content") or "" for m in reversed(history or []) if m.get("role") == "user"),
        "",
    ).strip()


def refers_back(question: str) -> bool:
    if _NAMES_A_NUMBER_RE.search(question):
        return False
    folded = " " + fold_vietnamese(question) + " "
    return any(" " + marker + " " in folded for marker in _FOLLOW_UP_MARKERS)


# ── Does a source support the answer? ───────────────────────────────────
#
# An answer that cites nothing inline used to be credited with every source
# registered during the turn, so a general-knowledge answer still pointed at
# unrelated documents. A source is credited now only when some sentence of
# the answer shares enough of its wording (adjacent word pairs), or a number
# such as a date or a document code. The credited sources are returned as
# AgentResult.citations; the chat shows them above the answer, so the answer
# text no longer carries a "Nguồn: [1] [2]" line.

# Below this reranker score (the cross-encoder's probability that the best
# chunk answers the question) the chunks are not about the question and are
# not offered as sources. Documents the question names or points at, and a
# follow-up's documents from the conversation, are shown whatever their
# score (_ask). Calibrated on the prefetch of the 53 single questions
# (bench/rag/multiturn/chat-memory/probe_prefetch.py): accented questions
# answered from a chunk score 0.41-1.0; questions the store cannot answer
# ("Ai là Giám đốc Sở Tài chính?", "UBND là viết tắt của từ gì?")
# 0.001-0.011. 0.08, the 0.52 used while the reranker applied a second
# sigmoid (0.52 = 1/(1+e^-0.08)), is near the middle of that gap on a log
# scale (0.067) and stays.
MIN_SOURCE_RELEVANCE = 0.08
_GENERAL_KNOWLEDGE_RE = re.compile(r"theo kien thuc chung|boi canh chung")
_CODE_RE = re.compile(r"\d+(?:\s*[/.-]\s*[\w]+)+")


def _pairs(text):
    words = re.findall(r"\w+", fold_vietnamese(text))
    return set(zip(words, words[1:]))


def _codes(text):
    return {re.sub(r"\s+", "", code) for code in _CODE_RE.findall(fold_vietnamese(text))}


def supports(answer: str, evidence: str) -> bool:
    evidence_pairs, evidence_codes = _pairs(evidence), _codes(evidence)
    if _codes(answer) & evidence_codes:
        return True
    for sentence in re.split(r"(?<=[.!?;:])\s+|\n+", answer):
        pairs = _pairs(sentence)
        shared = pairs & evidence_pairs
        if len(shared) >= 2 and len(shared) >= 0.35 * len(pairs):
            return True
    return False


# Token budget per request (research/token_budget_audit.md §3.9). llama-server
# does not hold back room for max_tokens: a prompt that fits but leaves no room
# for the answer is cut off with finish_reason "length", and one that does not
# fit at all is refused with HTTP 400. So every request keeps
#   prompt <= n_ctx - answer_reserve - CONTEXT_MARGIN
# and asks for max_tokens = min(2048, n_ctx - prompt - 64): at least the
# reserve, more when the prompt leaves room. Measured answers to single
# questions are at most ~520 tokens; tool-call turns 13-70.
ANSWER_TOKENS = 2048
CONTEXT_MARGIN = 256
_COMPLETION_SLACK = 64
# The 41-question harness never used more than one tool round, but multi-
# lookup conversations used every round they had, so fewer rounds were not
# shown to be safe; older rounds are shrunk to fit the prompt budget.
MAX_ROUNDS_GPU = 8
MAX_ROUNDS_CPU = 4
# At most this many earlier messages go with a question (0: as many as fit
# the token budget). The cap dropped the first turn of every 8-10-turn
# conversation of bench/rag/multiturn; with the TRẠNG THÁI HỘI THOẠI note,
# which names every turn, and the resolver, removing it measured equal
# (40/44 on the long conversations either way), so it stays.
HISTORY_MESSAGES = 12
# What an older source block keeps when the prompt must shrink: its [n]
# label line and the start of its text.
SHRUNK_BLOCK_CHARS = 300


def answer_reserve(n_ctx: int) -> int:
    """Tokens the prompt budget keeps free for the answer: 2048, or 1024 in
    the 8192-token CPU context."""
    return min(ANSWER_TOKENS, n_ctx // 8)


def prompt_budget(n_ctx: int) -> int:
    """Most prompt tokens one request may use in a context of n_ctx."""
    return n_ctx - answer_reserve(n_ctx) - CONTEXT_MARGIN


def max_rounds_for(backend: Optional[str]) -> int:
    return MAX_ROUNDS_GPU if backend == "cuda" else MAX_ROUNDS_CPU


def history_token_budget(n_ctx: Optional[int]) -> int:
    """Tokens of earlier turns sent with a question: 800 in the 8192-token
    CPU context, else 1500."""
    if n_ctx and n_ctx <= 8192:
        return 800
    return 1500


def state_token_budget(n_ctx: Optional[int]) -> int:
    """Estimated tokens of the TRẠNG THÁI HỘI THOẠI note: 450 (about 350 of
    the model's), 300 in the 8192-token CPU context, where prompt
    processing runs at tens of tokens per second."""
    if n_ctx and n_ctx <= 8192:
        return 300
    return 450


# Answers saved before the sources moved out of the text end with this line.
_SOURCE_FOOTER_RE = re.compile(r"^[ \t]*Nguồn:[ \t]*(?:\[\d+\][ \t]*)+$", re.MULTILINE)
# An answer without inline [n] is credited with the sources of at most this
# many documents, at most MAX_CREDITED_PER_DOCUMENT chunks of each: counting
# chunks let one document's four chunks hide the second document of a
# comparison.
MAX_CREDITED_SOURCES = 4
MAX_CREDITED_PER_DOCUMENT = 2

# The first line of an answer whose question named a document outside the
# "Tài liệu đang mở" scope: that document was searched as well.
SCOPE_NOTICE_VI = "Ngoài phạm vi tài liệu đang mở: đã tra cả {} vì câu hỏi nêu văn bản này."
_SCOPE_NOTICE_RE = re.compile(r"^Ngoài phạm vi tài liệu đang mở: [^\n]*(?:\n+|$)")


def split_scope_notice(text: str) -> Tuple[str, str]:
    """(notice, rest) of an answer that starts with the scope notice."""
    match = _SCOPE_NOTICE_RE.match(text or "")
    if match is None:
        return "", text or ""
    return match.group(0).strip(), (text or "")[match.end():]


def answer_status(answer: str) -> str:
    """'not_found' for an answer that opens by saying the store has nothing
    on the question, else 'answered'."""
    _notice, text = split_scope_notice(answer)
    first_sentence = re.split(r"(?<=[.!?])\s|\n", text.strip(), 1)[0]
    return "not_found" if _NOT_FOUND_RE.search(first_sentence) else "answered"


def strip_source_footer(text: str) -> str:
    """An answer without the "Nguồn: [1] [2]" line that answers saved by
    earlier versions carry; its sources are the message's citations."""
    text = _SOURCE_FOOTER_RE.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_answer(text: str) -> str:
    """An earlier answer as history: without the disclaimer, the "Nguồn:"
    footer and the [n] markers, whose numbers belong to that turn's sources
    and would be read as this turn's."""
    text = text.replace(DISCLAIMER_VI, "").replace(TRUNCATED_NOTICE_VI, "")
    text = split_scope_notice(text)[1]
    text = _SOURCE_FOOTER_RE.sub("", text)
    text = re.sub(r"[ \t]*\[\d+\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _shorten(message: dict, max_tokens: int) -> dict:
    """The beginning of a message that fits in max_tokens (estimated)."""
    content = message["content"]
    chars = len(content)
    while chars and estimate_prompt_tokens([{"content": content}]) > max_tokens:
        # Cut in proportion to the overshoot; capitals and figures cost a
        # token per character, so one cut may not be enough.
        chars = min(chars - 1, chars * max_tokens // estimate_prompt_tokens([{"content": content}]))
        content = message["content"][:chars].rsplit(" ", 1)[0] + "…"
    return {"role": message["role"], "content": content}


def _turns(messages: List[dict]) -> List[List[dict]]:
    """Messages grouped into turns, in order: a question with the answer that
    follows it. An answer without a question, or a question that got no
    answer, is a turn of its own."""
    turns = []  # type: List[List[dict]]
    for message in messages:
        if (message["role"] == "assistant" and turns
                and [item["role"] for item in turns[-1]] == ["user"]):
            turns[-1].append(message)
        else:
            turns.append([message])
    return turns


def _shorten_turn(turn: List[dict], max_tokens: int) -> List[dict]:
    """A turn cut down to max_tokens: its question keeps at most a quarter
    of them, its answer the rest."""
    if len(turn) == 1:
        return [_shorten(turn[0], max_tokens)]
    question = _shorten(turn[0], max_tokens // 4)
    return [question, _shorten(turn[1], max_tokens - estimate_prompt_tokens([question]))]


def bounded_history(messages: List[dict], n_ctx: Optional[int] = None) -> List[dict]:
    """The recent turns that go with a new question: as many as fit
    history_token_budget(n_ctx) tokens (and HISTORY_MESSAGES, when set),
    newest first, in whole turns, so an answer never goes without its
    question. Turns that do not fit are still named, one line each, in the
    conversation state (doc_resolver.conversation_block). Earlier answers
    lose their disclaimer, source footer and [n] markers. A newest turn that
    alone is over the budget is shortened rather than dropped."""
    cleaned = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(message.get("content") or "")
        if role == "assistant":
            content = _clean_answer(content)
        if content.strip():
            cleaned.append({"role": role, "content": content})
    cap = history_token_budget(n_ctx)
    kept, used = [], 0  # type: List[dict], int
    for turn in reversed(_turns(cleaned)):
        if HISTORY_MESSAGES and len(kept) + len(turn) > HISTORY_MESSAGES:
            break
        tokens = sum(estimate_prompt_tokens([message]) for message in turn)
        if used + tokens > cap:
            if not kept:
                kept = _shorten_turn(turn, cap)
            break
        kept = turn + kept
        used += tokens
    return kept


# What an earlier answer that found nothing becomes in the history: "Không
# tìm thấy thông tin về văn bản số 166" read as a fact made the next turn
# deny that 166 exists.
NOT_ANSWERED_VI = "(Lượt này trợ lý chưa trả lời được; không có nghĩa là kho không có văn bản được hỏi.)"
# Measured equal (41/52 with and without, the same turns, on the 12
# conversations with a "not found" answer before a later question), so off.
MARK_NOT_FOUND = False
# Earlier questions that pointed at a document ("văn bản đang mở", "văn bản
# đó") could carry the document they meant: "[= 166-KH/ĐĐHĐND]". Measured
# equal with the conversation note, which says the same (117/121 without,
# 116/121 with, on the 27 conversations it touched), so it is off.
ANNOTATE_HISTORY = False
_NAMED_HOW = ("named", "number", "ocr")


def _message_meta(message) -> dict:
    """The context saved with a (role, content, citations) message
    (chat_panel keeps it as .meta), {} when there is none."""
    meta = getattr(message, "meta", None)
    if meta is None and isinstance(message, dict):
        meta = message.get("meta")
    return meta if isinstance(meta, dict) else {}


def _int_keyed(mapping) -> Dict[int, str]:
    result = {}
    for key, value in (mapping or {}).items():
        try:
            result[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return result


def _answered_pairs(messages):
    """(question, answer) message pairs; a question left without an answer
    (stopped, failed) is not a turn."""
    items = [message for message in messages if message[0] in ("user", "assistant")]
    for position, message in enumerate(items):
        if (message[0] == "user" and position + 1 < len(items)
                and items[position + 1][0] == "assistant"):
            yield message, items[position + 1]


def turn_records(messages) -> List["TurnRecord"]:
    """The conversation's turns from stored (role, content, citations)
    messages and their saved context. Turns saved before the context was
    recorded are read from their citations and text: the documents they
    cited, "not found" when the answer opens that way; their scope and open
    document stay unknown rather than guessed."""
    records = []
    for question, answer in _answered_pairs(messages):
        asked, answered = _message_meta(question), _message_meta(answer)
        intent = answered.get("intent")
        if intent is None and not answered:
            intent = ("conversation" if is_conversation(question[1])
                      else "meta" if is_meta_question(question[1]) else "documents")
        labels = _int_keyed(answered.get("labels"))
        cited = []  # type: List[int]
        for citation in answer[2] or []:
            doc_id = int(citation.doc_id)
            labels.setdefault(doc_id, citation.so_ki_hieu or "")
            if doc_id not in cited:
                cited.append(doc_id)
        if intent in ("conversation", "meta"):
            status, docs, how = intent, [], ""
        else:
            status = answered.get("status") or answer_status(answer[1])
            refs = [int(doc_id) for doc_id in answered.get("refs") or []]
            hows = _int_keyed(answered.get("how"))
            docs = refs or cited[:4]
            how = (hows.get(docs[0], "") if refs else "cited") if docs else ""
        open_doc = asked.get("open_doc")
        records.append(TurnRecord(
            question=str(question[1] or ""), answer=str(answer[1] or ""), docs=docs,
            open_doc=int(open_doc) if open_doc is not None else None,
            scope=asked.get("scope"), status=status, how=how,
            labels={doc_id: label for doc_id, label in labels.items() if label},
            cited=cited if intent not in ("conversation", "meta") else [],
            shown=[int(doc_id) for doc_id in answered.get("shown") or []],
            table=str(answered.get("table") or ""),
        ))
    return records


def conversation_history(messages, n_ctx: Optional[int] = None) -> List[dict]:
    """The earlier turns sent with a new question (bounded_history), made
    safe to read back: an answer that found nothing is replaced by
    NOT_ANSWERED_VI (MARK_NOT_FOUND), and with ANNOTATE_HISTORY a question
    that pointed at a document names it."""
    plain = []
    for question, answer in _answered_pairs(messages):
        text = str(question[1] or "")
        answered = _message_meta(answer)
        if ANNOTATE_HISTORY and answered.get("intent", "documents") == "documents":
            hows = _int_keyed(answered.get("how"))
            labels = _int_keyed(answered.get("labels"))
            pointed = [labels[doc_id] for doc_id in (int(d) for d in answered.get("refs") or [])
                       if hows.get(doc_id) not in _NAMED_HOW and labels.get(doc_id)]
            if pointed:
                text = "{} [= {}]".format(text, ", ".join(pointed))
        reply = str(answer[1] or "")
        if MARK_NOT_FOUND and answered.get("intent", "documents") == "documents" and (
                answered.get("status") or answer_status(reply)) == "not_found":
            reply = NOT_ANSWERED_VI
        plain.append({"role": "user", "content": text})
        plain.append({"role": "assistant", "content": reply})
    return bounded_history(plain, n_ctx)


def _clip(text: str, keep_chars: int) -> str:
    """The first keep_chars characters of text, cut at a space."""
    if len(text) <= keep_chars:
        return text
    return text[:keep_chars].rsplit(" ", 1)[0] + "…"


def _shrink_sources(text: str, keep_chars: int) -> str:
    """Each source block reduced to its label line ("[n] 【…】") and the
    first keep_chars characters of the rest, so [n] still means the same."""
    blocks = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        cut = next((index for index, line in enumerate(lines) if re.match(r"\[\d+\]", line)), 0)
        head = "\n".join(lines[:cut + 1])
        body = " ".join(" ".join(lines[cut + 1:]).split())
        if len(body) > keep_chars:
            body = (body[:keep_chars].rsplit(" ", 1)[0] + "…") if keep_chars else ""
        blocks.append(head + ("\n" + body if body else ""))
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class Citation:
    n: int
    doc_id: int
    page: Optional[int]
    so_ki_hieu: str
    chunk_id: Optional[int]


@dataclass
class AgentResult:
    answer: str
    citations: List[Citation]
    steps: List[str]
    error: Optional[str] = None
    # The answer stopped at the length limit (finish_reason "length").
    truncated: bool = False
    # The documents the question was taken to be about (named, pointed at,
    # carried over from the conversation or the one-document scope), and
    # how each was found; empty when it was a search of the whole store.
    refs: List[int] = field(default_factory=list)
    how: Dict[int, str] = field(default_factory=dict)
    # Their numbers ("166-KH/ĐĐHĐND"), saved with the turn.
    labels: Dict[int, str] = field(default_factory=dict)
    # The documents of the prefetched sources the model was shown.
    shown: List[int] = field(default_factory=list)
    # "documents", "conversation" (small talk) or "meta" (about the
    # conversation itself).
    intent: str = "documents"
    # What answer checking found and did ({"checked": {...}, "violations":
    # [kinds], "regenerated": bool, "after": [kinds], "hedged": bool});
    # None when nothing was checked. Not part of equality.
    check: Optional[dict] = field(default=None, compare=False)
    # The metadata table the answer was given (metadata_query kind), "" if none.
    table: str = field(default="", compare=False)


class RagAgent:
    def __init__(
        self,
        client,
        executor,
        max_rounds: int = MAX_ROUNDS_GPU,
        step_cb=None,
        token_cb=None,
        cancel_check=None,
        n_ctx: Optional[int] = None,
        sources_cb=None,
    ):
        if max_rounds < 1:
            raise ValueError("max_rounds phải lớn hơn 0")
        self.client = client
        self.executor = executor
        self.max_rounds = max_rounds
        self.step_cb = step_cb
        self.token_cb = token_cb
        self.cancel_check = cancel_check or (lambda: False)
        # Called with the numbered citations whenever a tool registers new
        # ones, so a UI can show what is being read before the answer exists.
        self.sources_cb = sources_cb
        # Context tokens of the chat server. Unknown: asked once from the
        # client (llama-server /props); a client that cannot say gets no
        # budget, as before, but an overflow it reports is still handled.
        self.n_ctx = n_ctx
        self._n_ctx_known = n_ctx is not None
        # One entry per request of the last ask(): token counts, no content.
        self.usage = []  # type: List[dict]
        # The earlier turns of the conversation and the document open now,
        # set by the chat panel before ask(); None for a single question.
        self.conversation = None  # type: Optional[ConversationState]

    def _context_size(self) -> Optional[int]:
        if not self._n_ctx_known:
            self._n_ctx_known = True
            probe = getattr(self.client, "context_size", None)
            if callable(probe):
                try:
                    self.n_ctx = probe()
                except Exception as exc:
                    logger.warning("Chat context size unavailable: %s", exc)
        return self.n_ctx

    def _count(self, messages, tools) -> int:
        counter = getattr(self.client, "count_prompt_tokens", None)
        if callable(counter):
            try:
                return int(counter(messages, tools))
            except Exception as exc:
                logger.warning("Prompt token count unavailable: %s", exc)
        return estimate_prompt_tokens(messages, tools)

    @staticmethod
    def _trim_steps(messages, history, prefetch):
        """Shrink the prompt one step at a time, least needed first: tool
        results of earlier rounds, then earlier turns of the conversation,
        each question with its answer, oldest first (the newest answer is cut
        to its beginning before that turn goes), then the newest tool
        results, then the prefetched sources. Sources keep their [n] label
        lines. The system prompt, the question and the model's own tool calls
        are never touched."""
        def shrink(message, keep):
            if message is None:
                return False
            smaller = _shrink_sources(message["content"], keep)
            if smaller == message["content"]:
                return False
            message["content"] = smaller
            return True

        last_call = max(
            (index for index, message in enumerate(messages)
             if message.get("role") == "assistant" and message.get("tool_calls")),
            default=-1,
        )
        older = [message for index, message in enumerate(messages)
                 if message.get("role") == "tool" and index < last_call]
        newest = [message for index, message in enumerate(messages)
                  if message.get("role") == "tool" and index > last_call]
        for message in older:
            if shrink(message, SHRUNK_BLOCK_CHARS):
                yield
        turns = _turns(history)
        for index, turn in enumerate(turns):
            if not any(item is message for item in messages for message in turn):
                continue
            answer = turn[-1] if turn[-1]["role"] == "assistant" else None
            if index == len(turns) - 1 and answer is not None:
                shorter = _clip(answer["content"], SHRUNK_BLOCK_CHARS)
                if shorter != answer["content"]:
                    answer["content"] = shorter
                    yield
            messages[:] = [item for item in messages
                           if not any(item is message for message in turn)]
            yield
        for message in newest + [prefetch]:
            if shrink(message, SHRUNK_BLOCK_CHARS):
                yield
        for message in older + newest + [prefetch]:
            if shrink(message, 0):
                yield

    def _fit(self, messages, tools, budget, history, prefetch, known=None) -> int:
        """Trim messages in place until the prompt fits budget; return its
        token count. The cheap estimate decides when it is clearly inside;
        otherwise the server counts."""
        tokens, exact = (known, True) if known is not None else (
            estimate_prompt_tokens(messages, tools), False)
        steps = self._trim_steps(messages, history, prefetch)
        while tokens > budget:
            if not exact:
                tokens, exact = self._count(messages, tools), True
                if tokens <= budget:
                    break
            if next(steps, StopIteration) is StopIteration:
                break
            tokens, exact = estimate_prompt_tokens(messages, tools), False
        return tokens

    @staticmethod
    def _max_tokens(n_ctx: int, prompt: int) -> int:
        return max(16, min(ANSWER_TOKENS, n_ctx - prompt - _COMPLETION_SLACK))

    def _send(self, messages, tools, history, prefetch):
        """One chat request that fits the context, retried once, trimmed to
        the server's own numbers, if the server still finds it too long."""
        record = {"tools": tools is not None}
        kwargs = {}
        n_ctx = self._context_size()
        if n_ctx:
            prompt = self._fit(messages, tools, prompt_budget(n_ctx), history, prefetch)
            kwargs["max_tokens"] = self._max_tokens(n_ctx, prompt)
            record.update(n_ctx=n_ctx, prompt_counted=prompt, max_tokens=kwargs["max_tokens"])
        try:
            response = self.client.chat(messages, tools=tools, stream_cb=self.token_cb, **kwargs)
        except ContextOverflowError as exc:
            if exc.n_ctx:
                self.n_ctx = int(exc.n_ctx)
            n_ctx = self.n_ctx
            if not n_ctx:
                raise
            known = exc.n_prompt_tokens if exc.n_prompt_tokens else self._count(messages, tools)
            budget = prompt_budget(n_ctx)
            if known <= budget:
                budget = int(known * 0.75)
            logger.warning("Chat prompt of %s tokens over context %s; trimming to %s",
                           known, n_ctx, budget)
            prompt = self._fit(messages, tools, budget, history, prefetch, known=known)
            kwargs["max_tokens"] = self._max_tokens(n_ctx, prompt)
            record.update(n_ctx=n_ctx, prompt_counted=prompt, max_tokens=kwargs["max_tokens"],
                          retried=True)
            response = self.client.chat(messages, tools=tools, stream_cb=self.token_cb, **kwargs)
        record.update(
            prompt_tokens=getattr(response, "prompt_tokens", None),
            completion_tokens=getattr(response, "completion_tokens", None),
            finish_reason=response.finish_reason,
        )
        self.usage.append(record)
        logger.info("Chat request: %s", record)
        return response

    @staticmethod
    def _assistant_message(response):
        message = {"role": "assistant", "content": response.content or ""}
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
        return message

    @staticmethod
    def _source_key(source: dict) -> Tuple:
        chunk_id = source.get("chunk_id")
        if chunk_id is not None:
            return ("chunk", int(source["doc_id"]), int(chunk_id))
        return ("document", int(source["doc_id"]), source.get("page"))

    @staticmethod
    def _citation(source: dict, n: int) -> Citation:
        return Citation(
            n=n,
            doc_id=int(source["doc_id"]),
            page=int(source["page"]) if source.get("page") is not None else None,
            so_ki_hieu=str(source.get("so_ki_hieu") or ""),
            chunk_id=(
                int(source["chunk_id"])
                if source.get("chunk_id") is not None
                else None
            ),
        )

    @staticmethod
    def _format_tool_result(result: ToolResult, numbers: List[int]) -> str:
        blocks = result.text.split("\n\n")
        if numbers and len(blocks) == len(numbers):
            return "\n\n".join(
                "[{}] {}".format(number, block) for number, block in zip(numbers, blocks)
            )
        prefix = " ".join("[{}]".format(number) for number in numbers)
        return "{} {}".format(prefix, result.text).strip()

    @staticmethod
    def _evidence(result: ToolResult, numbers: List[int]) -> Dict[int, str]:
        """The text the model was shown for each citation number."""
        blocks = result.text.split("\n\n")
        if numbers and len(blocks) == len(numbers):
            pairs = zip(numbers, blocks)
        else:
            pairs = ((number, result.text) for number in numbers)
        shown = {}  # type: Dict[int, str]
        for number, block in pairs:
            shown[number] = (shown.get(number, "") + "\n" + block).strip()
        return shown

    @staticmethod
    def _strip_reasoning(content: str) -> str:
        visible = re.sub(
            r"<think\b[^>]*>.*?</think\s*>",
            "",
            content or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        return re.sub(
            r"<(?:think|analysis|reasoning)\b[^>]*>.*$",
            "",
            visible,
            flags=re.IGNORECASE | re.DOTALL,
        )

    @staticmethod
    def _final_answer(
        content: str,
        citations: List[Citation],
        evidence: Optional[Dict[int, str]] = None,
        truncated: bool = False,
    ) -> str:
        return RagAgent._finish(content, citations, evidence, truncated)[0]

    @staticmethod
    def _finish(
        content: str,
        citations: List[Citation],
        evidence: Optional[Dict[int, str]] = None,
        truncated: bool = False,
    ) -> Tuple[str, List[Citation]]:
        """The answer as shown and stored, and the sources it rests on.

        The sources are the ones it cites inline [n]. An answer that cites
        none inline is credited with the sources whose evidence supports it
        (at most MAX_CREDITED_SOURCES): none when it says nothing was found
        or answers from general knowledge. Without evidence (an old replay)
        every registered source counts as supporting it.
        """
        visible = RagAgent._strip_reasoning(content)
        visible = re.sub(
            r"\[(?:n|nguồn)\s*(\d+)\]", r"[\1]", visible,
            flags=re.IGNORECASE,
        )
        # Sources are labelled 【…】; a model sometimes cites as 【2】.
        visible = re.sub(r"【\s*(\d+)\s*】", r"[\1]", visible)
        visible = re.sub(r"\[n\]", "", visible, flags=re.IGNORECASE)
        valid_numbers = {citation.n for citation in citations}
        visible = re.sub(
            r"\[(\d+)\]",
            lambda match: match.group(0) if int(match.group(1)) in valid_numbers else "",
            visible,
        )
        answer = visible.replace(DISCLAIMER_VI, "").strip()
        if not answer:
            answer = "Không tìm thấy đủ căn cứ trong kho tài liệu."
        inline = {int(number) for number in re.findall(r"\[(\d+)\]", answer)}
        if inline:
            numbers = inline
        else:
            numbers = set(RagAgent._supported_numbers(answer, citations, evidence))
        used = [citation for citation in citations if citation.n in numbers]
        if truncated:
            answer += "\n\n" + TRUNCATED_NOTICE_VI
        return "{}\n\n{}".format(answer, DISCLAIMER_VI), used

    @staticmethod
    def _supported_numbers(
        answer: str,
        citations: List[Citation],
        evidence: Optional[Dict[int, str]] = None,
    ) -> List[int]:
        """Citation numbers credited to an answer that cites none inline."""
        if not citations:
            return []
        # "Nothing found" rests on nothing: crediting sources would point the
        # reader at documents the answer itself says do not answer it.
        first_sentence = re.split(r"(?<=[.!?])\s|\n", answer, 1)[0]
        if _NOT_FOUND_RE.search(first_sentence) is not None:
            return []
        numbers = list(dict.fromkeys(citation.n for citation in citations))
        if evidence is not None:
            # Credit only what the answer actually rests on; an answer from
            # general knowledge rests on none of the sources.
            if _GENERAL_KNOWLEDGE_RE.search(fold_vietnamese(answer)) is not None:
                return []
            numbers = [
                number for number in numbers if supports(answer, evidence.get(number, ""))
            ]
        documents = {citation.n: citation.doc_id for citation in citations}
        kept, per_document = [], {}  # type: List[int], Dict[int, int]
        for number in numbers:
            doc_id = documents.get(number)
            if doc_id not in per_document and len(per_document) >= MAX_CREDITED_SOURCES:
                continue
            if per_document.get(doc_id, 0) >= MAX_CREDITED_PER_DOCUMENT:
                continue
            per_document[doc_id] = per_document.get(doc_id, 0) + 1
            kept.append(number)
        return kept

    def _focus(self, resolution) -> List[int]:
        """The conversation's documents a question that names none leans
        on: those of the last turn that had any, when the question reads as
        a follow-up ("ông ấy", "còn … thì sao") or is a few words long
        ("Họp ở đâu?"), and asks about them rather than about other
        documents; only with the whole store in scope."""
        if (not CONVERSATION_FOCUS or resolution is None or not resolution.focus
                or resolution.other_documents
                or getattr(self.executor, "scope_doc_ids", None) is not None):
            return []
        if resolution.follow_up or resolution.short:
            return list(resolution.focus)
        return []

    def _understood(self, refs, how, resolution) -> str:
        """'(Ứng dụng hiểu câu hỏi này là về: 4243-QĐ/TU (câu hỏi ghi
        1213-QĐ/TU; …).)', said next to the question, for a document matched
        through an OCR slip (REWRITE_OCR_NUMBER) and, with UNDERSTOOD_LINE,
        for documents the question pointed at or carried over from the last
        turn; '' when the question named them as stored or the scope chose
        them."""
        catalog = self._catalog()
        parts, groups = [], {}  # type: List[str], Dict[str, List[str]]
        for doc_id in refs:
            kind = how.get(doc_id)
            if kind in (None, "named", "number", "open", "scope"):
                continue
            if (kind == "ocr" and not REWRITE_OCR_NUMBER) or (kind != "ocr" and not UNDERSTOOD_LINE):
                continue
            label = catalog.label(doc_id) if catalog is not None else "văn bản #{}".format(doc_id)
            if kind == "ocr":
                asked = resolution.asked.get(doc_id, "") if resolution is not None else ""
                parts.append("{} (câu hỏi ghi {}; số ký hiệu trong kho được nhận dạng từ ảnh quét "
                             "nên có thể sai)".format(label, asked or "số khác"))
            else:
                if kind not in groups:
                    groups[kind] = []
                    parts.append(kind)  # placeholder, filled below in order
                groups[kind].append(label)
        parts = ["{} ({})".format(", ".join(groups[part]), HOW_NOTES.get(part, "theo cuộc trò chuyện"))
                 if part in groups else part for part in parts]
        if not parts:
            return ""
        return "(Ứng dụng hiểu câu hỏi này là về: {}.)".format("; ".join(parts))

    def _groups_note(self, result: ToolResult, numbers: List[int]) -> str:
        """For sources from several documents a question named: which [n]
        belong to which document, and to answer for each of them."""
        groups = {}  # type: Dict[int, List[int]]
        labels = {}  # type: Dict[int, str]
        for source, number in zip(result.sources, numbers):
            doc_id = int(source["doc_id"])
            groups.setdefault(doc_id, [])
            if number not in groups[doc_id]:
                groups[doc_id].append(number)
            labels.setdefault(doc_id, str(source.get("so_ki_hieu") or ""))
        if len(groups) < 2 or len(getattr(result, "documents", None) or []) < 2:
            return ""
        catalog = self._catalog()
        parts = []
        for doc_id, group in groups.items():
            label = catalog.label(doc_id) if catalog is not None else (
                labels[doc_id] or "văn bản #{}".format(doc_id))
            parts.append("{}: {}".format(label, ", ".join("[{}]".format(n) for n in group)))
        return ("\n\nNguồn theo văn bản: {}. Câu hỏi nói tới {} văn bản: trả lời cho từng văn bản, "
                "mỗi ý trích nguồn của đúng văn bản đó; văn bản nào không có thông tin thì nói rõ."
                ).format("; ".join(parts), len(groups))

    def _converse(self, question: str, history: Optional[List[dict]]) -> AgentResult:
        """Answer small talk directly: no retrieval, no tools, no sources."""
        steps = ["conversation"]
        if self.step_cb:
            self.step_cb("conversation")
        if self.cancel_check():
            return AgentResult("", [], steps, "cancelled")
        messages = [{"role": "system", "content": CONVERSATION_PROMPT_VI}]
        history_messages = copy.deepcopy(history or [])
        messages.extend(history_messages)
        messages.append({"role": "user", "content": question})
        response = self._send(messages, None, history_messages, None)
        if self.cancel_check():
            return AgentResult("", [], steps, "cancelled")
        answer = self._strip_reasoning(response.content).replace(DISCLAIMER_VI, "").strip()
        answer = answer or "Xin chào! Bạn muốn tra cứu văn bản nào?"
        truncated = response.finish_reason == "length"
        if truncated:
            answer += "\n\n" + TRUNCATED_NOTICE_VI
        return AgentResult(answer, [], steps, truncated=truncated)

    def _about_conversation(self, question: str, history: Optional[List[dict]]) -> AgentResult:
        """Answer a question about the conversation itself from the turns
        and the conversation state: no retrieval, no tools, no sources."""
        steps = ["conversation"]
        if self.step_cb:
            self.step_cb("conversation")
        if self.cancel_check():
            return AgentResult("", [], steps, "cancelled", intent="meta")
        messages = [{"role": "system", "content": META_PROMPT_VI}]
        history_messages = copy.deepcopy(history or [])
        messages.extend(history_messages)
        block = conversation_block(self.conversation, self._catalog(), answers=True,
                                   max_tokens=META_STATE_TOKENS)
        if block:
            messages.append({"role": "system", "content": block})
        messages.append({"role": "user", "content": question})
        response = self._send(messages, None, history_messages, None)
        if self.cancel_check():
            return AgentResult("", [], steps, "cancelled", intent="meta")
        answer = self._strip_reasoning(response.content).replace(DISCLAIMER_VI, "").strip()
        answer = re.sub(r"[ \t]*\[\d+\]", "", answer)
        answer = answer or "Chưa có lượt trao đổi nào trước câu hỏi này."
        truncated = response.finish_reason == "length"
        if truncated:
            answer += "\n\n" + TRUNCATED_NOTICE_VI
        return AgentResult(answer, [], steps, truncated=truncated, intent="meta")

    def _catalog(self):
        """The executor's document catalog, if it has one."""
        try:
            return getattr(self.executor, "catalog", None)
        except Exception as exc:  # a broken store must not stop the answer
            logger.warning("Document catalog unavailable: %s", exc)
            return None

    def _resolve(self, question: str):
        """doc_resolver.Resolution of the question, or None when the executor
        cannot resolve (older executors, test fakes) or it is switched off."""
        resolver = getattr(self.executor, "resolve", None)
        if not RESOLVE_DOCUMENTS or not callable(resolver):
            return None
        try:
            return resolver(question, self.conversation)
        except Exception as exc:
            logger.warning("Document references not resolved: %s", exc)
            return None

    def _aggregate(self, question: str, within=None):
        """The executor's metadata table for the question (ToolExecutor.
        aggregate), or None: not such a question, switched off, an executor
        without it, or a broken store."""
        aggregate = getattr(self.executor, "aggregate", None)
        if not AGGREGATES or not callable(aggregate):
            return None
        try:
            return aggregate(question, self.conversation, within or None)
        except Exception as exc:  # a broken store must not stop the answer
            logger.warning("Metadata table unavailable: %s", exc)
            return None

    def _report(self, question, answer, used, citations, evidence, refs, shown, table, state):
        """answer_check.Report of an answer as _finish made it, or None when
        checking is off or there is no document catalog (no store)."""
        catalog = self._catalog()
        if not ANSWER_CHECK or catalog is None:
            return None
        aggregate = getattr(table, "aggregate", None) if table is not None else None
        ref_numbers = {}  # type: Dict[int, List[int]]
        for citation in citations:
            if citation.doc_id in refs:
                ref_numbers.setdefault(citation.doc_id, []).append(citation.n)
        body = self._body(answer)
        facts = answer_check.Evidence(
            text="\n".join(list(evidence.values()) + [state or "", question]),
            numbers=[citation.so_ki_hieu for citation in citations],
            catalog=catalog, refs=list(refs), shown=list(shown), ref_numbers=ref_numbers,
            cited=[citation.doc_id for citation in used],
            compare=len(refs) >= 2 and answer_check.is_comparison(question),
            aggregate_counts=(answer_check.table_counts(catalog, aggregate.docs)
                              if aggregate is not None else None),
            aggregate_empty=bool(getattr(aggregate, "empty", False)),
            totals=(list(getattr(aggregate, "totals", None) or [])
                    if aggregate is not None and answer_check.is_counting(question) else []),
            required=(list(getattr(aggregate, "items", None) or [])
                      if aggregate is not None and not aggregate.empty and answer_check.is_listing(question)
                      and len(getattr(aggregate, "items", None) or []) <= MAX_LISTED else []),
            result_line=self._result_line(aggregate))
        return answer_check.check(body, facts, not_found=answer_status(body) == "not_found")

    @staticmethod
    def _result_line(aggregate) -> str:
        """'KẾT QUẢ: 24 văn bản … theo loại: …' of a documents table, '' else."""
        blocks = getattr(aggregate, "blocks", None) or []
        match = re.search(r"KẾT QUẢ:[^\n]*", blocks[0]) if blocks else None
        return match.group(0).strip() if match else ""

    @staticmethod
    def _body(answer: str) -> str:
        """An answer without the disclaimer, the length notice and the scope notice."""
        text = (answer or "").replace(DISCLAIMER_VI, "").replace(TRUNCATED_NOTICE_VI, "")
        return split_scope_notice(text)[1].strip()

    def _widen_scope(self, documents: List[int]) -> str:
        """A document the question names wins over the one-document scope:
        it joins the scope for this question. The notice for the answer,
        when the reader chose that scope."""
        scope = getattr(self.executor, "scope_doc_ids", None)
        if scope is None:
            return ""
        outside = [doc_id for doc_id in documents if doc_id not in scope]
        if not outside:
            return ""
        self.executor.scope_doc_ids = list(scope) + outside
        if self.conversation is not None and self.conversation.scope != "open":
            return ""  # the scope came from "văn bản đang mở" in the question
        catalog = self._catalog()
        labels = [catalog.label(doc_id) if catalog is not None else "văn bản #{}".format(doc_id)
                  for doc_id in outside]
        return SCOPE_NOTICE_VI.format(", ".join(labels))

    def ask(self, question: str, history: Optional[List[dict]] = None) -> AgentResult:
        self.usage = []
        citations = []  # type: List[Citation]
        steps = []  # type: List[str]
        try:
            if is_conversation(question):
                result = self._converse(question, history)
                result.intent = "conversation"
                return result
            if META_ROUTING and is_meta_question(question):
                return self._about_conversation(question, history)
            return self._ask(question, history, citations, steps)
        except ContextOverflowError as exc:
            logger.warning("Chat request still over the context after trimming: %s", exc)
            return AgentResult("", list(citations), list(steps), CONTEXT_EXCEEDED_VI)

    def _ask(self, question, history, citations, steps) -> AgentResult:
        messages = [{"role": "system", "content": SYSTEM_PROMPT_VI}]
        history_messages = copy.deepcopy(history or [])
        messages.extend(history_messages)
        prefetch_message = None
        registry = {}  # type: Dict[Tuple, int]
        evidence = {}  # type: Dict[int, str]
        malformed_feedbacks = 0
        tool_rounds = 0
        refs, how, notice, understood = [], {}, "", ""  # type: List[int], Dict[int, str], str, str
        shown, resolution = [], None  # type: List[int], object
        table, state = None, ""  # type: Optional[ToolResult], str

        def register(result):
            numbers = []
            known = len(citations)
            for source in result.sources:
                key = self._source_key(source)
                if key not in registry:
                    registry[key] = len(citations) + 1
                    citations.append(self._citation(source, registry[key]))
                numbers.append(registry[key])
            for number, text in self._evidence(result, numbers).items():
                evidence[number] = (evidence.get(number, "") + "\n" + text).strip()
            if self.sources_cb and len(citations) > known:
                self.sources_cb(list(citations))
            return numbers

        prefetch = getattr(self.executor, "prefetch", None)
        if callable(prefetch) and not self.cancel_check():
            steps.append("prefetch_documents")
            if self.step_cb:
                self.step_cb("prefetch_documents")
            previous = previous_question(history)
            together = "{} {}".format(previous, question) if previous else question
            follows = bool(previous) and refers_back(question)
            query = together if follows else question

            def is_weak(found):
                return found.relevance is not None and found.relevance < MIN_SOURCE_RELEVANCE

            resolution = self._resolve(question)
            if not (resolution is not None and resolution.docs):
                # "thống kê các lãnh đạo", "bao nhiêu công văn", "ông ấy còn
                # ký văn bản nào khác": a table computed from the metadata.
                table = self._aggregate(question, getattr(resolution, "within", None))
            if table is not None:
                result = table
            elif resolution is not None and resolution.docs:
                # Named or pointed at ("văn bản 166", "văn bản trước", "cả
                # hai"): shown whatever the reranker thinks of the question.
                refs, how = list(resolution.docs), dict(resolution.how)
                notice = self._widen_scope(refs)
                result = prefetch(question, documents=refs)
            elif resolution is not None and getattr(resolution, "within", None):
                # "trong các công văn đó, công văn nào …": the search keeps to
                # the documents the last turn listed.
                result = prefetch(question, within=resolution.within)
            else:
                result = prefetch(query)
                focus = self._focus(resolution)
                found = {int(source["doc_id"]) for source in result.sources}
                if focus and not self.cancel_check() and (
                        is_weak(result)
                        or (resolution.anaphora and FOCUS_ANAPHORA and found & set(focus))):
                    # "Họp ở đâu?", "Ông ấy phải nộp báo cáo trước ngày nào?":
                    # nothing in the store is about the words alone, or
                    # "đoàn công tác đó" finds it among others; the
                    # conversation says which document they are about. The
                    # previous question gives the search its subject; the
                    # whole store was searched already.
                    result = prefetch(together, documents=focus, others=False)
                    refs, how = list(focus), {doc_id: "follow-up" for doc_id in focus}
                elif (is_weak(result) and previous and not refers_back(question)
                        and not _NAMES_A_NUMBER_RE.search(question) and not self.cancel_check()):
                    retry = prefetch(together)
                    if not is_weak(retry):
                        result = retry
            scope = getattr(self.executor, "scope_doc_ids", None)
            if not refs and scope is not None and len(scope) == 1:
                refs, how = list(scope), {scope[0]: "scope"}
            understood = self._understood(refs, how, resolution)
            state = conversation_block(self.conversation, self._catalog(),
                                       () if understood and UNDERSTOOD_LINE else refs, how,
                                       max_tokens=state_token_budget(self._context_size())) \
                if STATE_BLOCK else ""
            if state:
                messages.append({"role": "system", "content": state})
            if is_weak(result):
                # The best chunk is not about the question: offering it as a
                # source only invites a citation it does not deserve.
                messages.append({"role": "system", "content": (
                    "NGUỒN TRONG KHO: không có đoạn nào liên quan rõ tới câu hỏi này. "
                    "Nếu đây là kiến thức chung, hãy trả lời bằng kiến thức đã học, mở đầu "
                    "bằng \"Theo kiến thức chung\" và không trích dẫn [n]. Nếu câu hỏi cần "
                    "nội dung văn bản trong kho, có thể gọi search_documents với cụm từ khác; "
                    "nếu vẫn không có thì nói rõ là không tìm thấy thông tin."
                    + (" Câu hỏi về chính cuộc trò chuyện thì trả lời theo TRẠNG THÁI HỘI THOẠI."
                       if state else "")
                )})
            else:
                numbers = register(result)
                shown = list(dict.fromkeys(int(source["doc_id"]) for source in result.sources))
                if table is not None:
                    # Every document the table covers, in its order: "các
                    # văn bản đó", "kế hoạch đầu tiên trong danh sách" next.
                    shown = list(dict.fromkeys(
                        list(getattr(table.aggregate, "docs", None) or []) + shown))
                prefetch_message = {
                    "role": "system",
                    "content": "NGUỒN TRONG KHO (truy xuất tự động, phải ưu tiên):\n{}{}".format(
                        self._format_tool_result(result, numbers),
                        "\n\n" + AGGREGATE_NOTE_VI if table is not None
                        else self._groups_note(result, numbers),
                    ),
                }
                messages.append(prefetch_message)
        # The question as asked, and next to it which documents it was
        # taken to be about when it did not name them itself. A number
        # matched through an OCR slip is written as the store has it: next
        # to a source numbered 4243-QĐ/TU, the 8B model answered a question
        # about 1213-QĐ/TU with "không tìm thấy" even when told they match.
        asked = question
        if REWRITE_OCR_NUMBER and resolution is not None:
            catalog = self._catalog()
            for doc_id, written in resolution.asked.items():
                if doc_id in refs and written and catalog is not None:
                    asked = asked.replace(written, catalog.label(doc_id))
        messages.append({"role": "user",
                         "content": "{}\n\n{}".format(asked, understood) if understood else asked})

        def send(tools):
            return self._send(messages, tools, history_messages, prefetch_message)

        def cancelled():
            return AgentResult("", list(citations), list(steps), "cancelled")

        def report_of(answer, used):
            return self._report(question, answer, used, citations, evidence, refs, shown, table, state)

        def completed(response):
            truncated = response.finish_reason == "length"
            answer, used = self._finish(response.content, citations, evidence, truncated)
            report = report_of(answer, used)
            record = None
            if report is not None:
                record = {"checked": dict(report.checked), "violations": report.kinds(),
                          "items": [violation.item for violation in report.violations],
                          "regenerated": False, "after": [], "hedged": False}
            if report is not None and report.violations and not self.cancel_check():
                # One more request with the correction: the draft, then
                # what does not hold and the evidence for it.
                steps.append("verify_answer")
                if self.step_cb:
                    self.step_cb("verify_answer")
                messages.append({"role": "assistant",
                                 "content": self._strip_reasoning(response.content).strip()})
                messages.append({"role": "user", "content": answer_check.note(report)})
                try:
                    second = send(None)
                except Exception as exc:  # the draft stands, hedged below
                    logger.warning("Answer check: second request failed: %s", exc)
                    second = None
                if self.cancel_check():
                    return cancelled()
                if second is not None:
                    truncated_again = second.finish_reason == "length"
                    again, again_used = self._finish(second.content, citations, evidence, truncated_again)
                    after = report_of(again, again_used)
                    record.update(regenerated=True, after=after.kinds(),
                                  items_after=[violation.item for violation in after.violations])
                    answer, used, truncated, report = again, again_used, truncated_again, after
                if report.violations:
                    body, changed = answer_check.hedge(self._body(answer), report)
                    if changed:
                        answer, used = self._finish(body, citations, evidence, truncated)
                        record["hedged"] = True
            if record is not None:
                logger.info("Answer check: %s", {key: value for key, value in record.items()
                                                 if key not in ("items", "items_after")})
            if notice:
                answer = "{}\n\n{}".format(notice, answer)
            catalog = self._catalog() if refs else None
            labels = {doc_id: catalog.label(doc_id) for doc_id in refs} if catalog is not None else {}
            return AgentResult(answer, used, list(steps), truncated=truncated,
                               refs=list(refs), how=dict(how), labels=labels, shown=list(shown),
                               check=record,
                               table=getattr(getattr(table, "aggregate", None), "kind", "") or "")

        def finish_now():
            messages.append(
                {"role": "user", "content": "Hãy trả lời ngay bằng dữ liệu đã có."}
            )
            if self.cancel_check():
                return cancelled()
            response = send(None)
            if self.cancel_check():
                return cancelled()
            return completed(response)

        while True:
            if self.cancel_check():
                return cancelled()
            # A metadata table is the answer's whole evidence: the tools
            # would only fetch passages that count differently.
            response = send(None if table is not None else TOOLS_SCHEMA)
            if self.cancel_check():
                return cancelled()
            if not response.tool_calls:
                return completed(response)

            parsed = []
            malformed = []
            for call in response.tool_calls:
                try:
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError
                    parsed.append((call, arguments))
                except (json.JSONDecodeError, TypeError, ValueError):
                    malformed.append(call)

            if malformed and malformed_feedbacks + len(malformed) > 2:
                return finish_now()

            messages.append(self._assistant_message(response))
            for call, arguments in parsed:
                if self.cancel_check():
                    return cancelled()
                steps.append(call.name)
                if self.step_cb:
                    self.step_cb(call.name)
                result = self.executor.execute(call.name, arguments)
                numbers = register(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": self._format_tool_result(result, numbers),
                    }
                )
            for call in malformed:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": "Lỗi: arguments không phải JSON hợp lệ, thử lại",
                    }
                )
                malformed_feedbacks += 1

            tool_rounds += 1
            if tool_rounds >= self.max_rounds:
                return finish_now()
