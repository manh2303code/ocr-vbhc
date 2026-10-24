"""Which documents a chat question is about, decided in code before the model.

A question names documents by number ("Kế hoạch 195/KH-HĐND", "văn bản
166", "hai tờ trình 183 và 410", an OCR slip such as 1213-QĐ/TU for the
stored 4243-QĐ/TU) or points back into the conversation ("văn bản trước",
"văn bản tôi mở đầu tiên", "cả hai", "kế hoạch đó", "văn bản đang mở").
The local 8B chat model almost never calls a tool to look a document up and
never sees document ids, so these are resolved here and the documents are
put in front of it (agent_tools.ToolExecutor.prefetch).

The conversation is a list of TurnRecord: for each earlier question, the
documents it was about, the document open when it was asked and how it was
answered. chat_panel keeps them with the messages (chat_history meta_json);
conversation_block() turns them into the short "TRẠNG THÁI HỘI THOẠI" note
the model reads with every question.
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from llm_client import estimate_text_tokens
from search_store import fold_vietnamese


# "410/TTr-HĐND", "1213-QĐ/TU", "17-CT/TW", "03/2023/TT-BTC"
NUMBER_RE = re.compile(r"\b\d{1,5}[a-zA-Z]?\s*[/-]\s*[\wĐđ]+(?:\s*[/-]\s*[\wĐđ]+)*", re.UNICODE)

# Digits OCR mixes up in these scans: the store holds 4243-QĐ/TU for
# 1213-QĐ/TU and 117/QĐ-HĐND for 147/QĐ-HĐND.
_OCR_DIGIT_PAIRS = frozenset(
    frozenset(pair) for pair in ("14", "17", "47", "08", "38", "56", "68", "09", "89", "35")
)

# Words that, right before a number, make it a document number: "văn bản
# 166", "kế hoạch số 195", "KH 101", "tờ trình 183 và 410". Without one, a
# number is an article, a year, a count ("Điều 166", "năm 2024", "15 người").
_TYPE_WORDS = (
    "van ban", "vb", "ke hoach", "kh", "quyet dinh", "qd", "to trinh", "ttr", "thong bao", "tb",
    "cong van", "cv", "bao cao", "bc", "chi thi", "ct", "nghi quyet", "nq", "ket luan", "kl",
    "chuong trinh", "ctr", "huong dan", "thong tu", "nghi dinh", "quy che", "bien ban",
)
# What a type word says about the document it names: its kind (folded
# loai_vb) or the letters of its number.
_TYPE_KINDS = {
    "ke hoach": ("ke hoach", "kh"), "kh": ("ke hoach", "kh"),
    "quyet dinh": ("quyet dinh", "qd"), "qd": ("quyet dinh", "qd"),
    "to trinh": ("to trinh", "ttr"), "ttr": ("to trinh", "ttr"),
    "thong bao": ("thong bao", "tb"), "tb": ("thong bao", "tb"),
    "cong van": ("cong van", "cv"), "cv": ("cong van", "cv"),
    "bao cao": ("bao cao", "bc"), "bc": ("bao cao", "bc"),
    "chi thi": ("chi thi", "ct"), "ct": ("chi thi", "ct"),
    "nghi quyet": ("nghi quyet", "nq"), "nq": ("nghi quyet", "nq"),
    "ket luan": ("ket luan", "kl"), "kl": ("ket luan", "kl"),
    "chuong trinh": ("chuong trinh", "ctr"), "ctr": ("chuong trinh", "ctr"),
    "huong dan": ("huong dan", "hd"), "thong tu": ("thong tu", "tt"),
    "nghi dinh": ("nghi dinh", "nd"), "quy che": ("quy che", "qc"),
    "bien ban": ("bien ban", "bb"),
}
_TYPE_ALT = "|".join(sorted((re.escape(word) for word in _TYPE_WORDS), key=len, reverse=True))
# A type word, an optional "số", then the number (not the start of a full
# number such as 195/KH-HĐND, which NUMBER_RE reads).
_BARE_RE = re.compile(
    r"\b(?P<type>{types})(?: so(?: hieu)?)? (?P<num>\d{{1,5}})\b(?!\s*[/-]\s*\w)".format(types=_TYPE_ALT)
)
# "…183 và 410", "…339, 850 và 166", "…336 và văn bản 105".
_MORE_RE = re.compile(
    r"\s*(?:,|\bva\b|\bvoi\b|\bhoac\b|&)\s*(?:(?:{types})(?: so)? )?(?P<num>\d{{1,5}})\b(?!\s*[/-]\s*\w)".format(
        types=_TYPE_ALT)
)

# Folded: "tài liệu đang mở", "văn bản đang xem", "file hiện tại".
OPEN_DOCUMENT_RE = re.compile(
    r"\b(?:tai lieu|van ban|file|tep)\b(?: \w+){0,2} (?:dang mo|dang xem|hien tai|dang hien thi)\b"
)
_DOC_NOUNS = ("văn bản", "tài liệu", "kế hoạch", "quyết định", "tờ trình", "thông báo", "công văn",
              "báo cáo", "chỉ thị", "nghị quyết", "lịch khảo sát", "lịch", "tệp", "file")
_DOC_NOUN_ALT = "|".join(sorted((re.escape(noun) for noun in _DOC_NOUNS), key=len, reverse=True))
_DOC_NOUN_FOLDED_ALT = "|".join(sorted({re.escape(fold_vietnamese(noun)) for noun in _DOC_NOUNS},
                                       key=len, reverse=True))
# Lowercase text with its marks: "đó", "ấy", "này" are "do", "ay", "nay" once
# folded, and "do" is also "do ai ký" (by whom).
# Not "kế hoạch … do bà ấy ký": "ấy" after a person is that person.
_LAST_RE = re.compile(
    r"\b(?:{nouns})\b(?:\s+[^\s,.?!;:]+){{0,6}}?"
    r"(?<!\bông)(?<!\bbà)(?<!\banh)(?<!\bchị)(?<!\bngười)(?<!\bcô)(?<!\bchú)"
    r"\s+(?:đó|ấy|trên|nói trên|vừa nói|vừa rồi|vừa hỏi|"
    r"vừa nhắc|lúc nãy|lúc trước|ban nãy)(?=$|[\s,.?!;:])".format(nouns=_DOC_NOUN_ALT)
)
_THIS_RE = re.compile(r"\b(?:{nouns})\s+(?:này|hiện tại)(?=$|[\s,.?!;:])".format(nouns=_DOC_NOUN_ALT))
# Not "ban nay" (bản nãy): folded it is also "văn bản này".
_JUST_SAID_RE = re.compile(r"\b(?:vua (?:noi|roi|hoi|nhac|tra loi)|luc nay)\b")
# "văn bản trước (đó)"; not "gửi báo cáo trước ngày 25/8".
_PREVIOUS_RE = re.compile(
    r"\b(?:van ban|tai lieu|ke hoach|quyet dinh|to trinh|thong bao|cong van|chi thi|nghi quyet)"
    r" (?:truoc do|truoc|o tren)\b(?! (?:ngay|khi|thang|nam|han|thoi|gio|luc|\d))"
)
# "văn bản đầu tiên tôi hỏi", "văn bản tôi hỏi ở câu đầu tiên", "tờ trình tôi
# hỏi ở đầu cuộc trò chuyện"; not "điểm dừng đầu tiên của đoàn".
_FIRST_RE = re.compile(
    r"\b(?:{nouns})\b(?: \w+){{0,4}} (?:dau tien|thu nhat|luc dau|ban dau|truoc tien)\b"
    r"|\bdau (?:doan|cuoc) (?:chat|tro chuyen|hoi thoai)\b|\bcau (?:hoi )?(?:dau tien|dau|thu nhat)\b"
    r"|\b(?:toi|minh|em) hoi (?:dau tien|luc dau|ban dau|truoc tien)\b".format(nouns=_DOC_NOUN_FOLDED_ALT)
)
_FIRST_OPEN_RE = re.compile(
    r"\b(?:toi|minh|em) (?:mo|xem)\b(?: \w+){0,2} (?:dau tien|thu nhat|truoc tien|luc dau)\b"
    r"|\b(?:dau tien|thu nhat) (?:toi|minh|em) (?:mo|xem)\b|\bmo dau tien\b"
)
_PLURAL_RE = re.compile(
    r"\bca (?P<all>hai|ba|2|3)\b"
    r"|\b(?P<n>hai|ba|2|3) (?:{nouns}|dot|doan|cuoc|ben|lich)\b".format(nouns=_DOC_NOUN_FOLDED_ALT)
)
_COUNT_WORDS = {"hai": 2, "2": 2, "ba": 3, "3": 3}
# "văn bản nào …", "cái nào", "từng đoàn": one of, or each of, the documents
# the last turn was about, when it was about several.
_WHICH_RE = re.compile(
    r"\b(?:{nouns}|cai|dot|doan|ben|lich) nao\b|\b(?:tung|moi) (?:{nouns}|dot|doan|ben|lich)\b".format(
        nouns=_DOC_NOUN_FOLDED_ALT)
)

# A question that leans on the previous turn without naming its subject.
_FOLLOW_UP_FOLDED = (
    "ong ay", "ba ay", "anh ay", "chi ay", "nguoi do", "nguoi nay", "cai do", "dieu do",
    "sai roi", "khong phai", "the con", "con ve", "vay thi", "y toi la", "thi sao", "cai nao",
    "dot nao", "ben nao", "tung doan", "tung van ban", "moi van ban", "trong do",
)
_FOLLOW_UP_START_RE = re.compile(r"^(?:con|vay|a|the|the con|a con|con lai)\b")
# Pronouns among them: the question is about whatever the last turn was.
_ANAPHORA_FOLDED = ("ong ay", "ba ay", "anh ay", "chi ay", "nguoi do", "nguoi nay", "cai do", "dieu do")
# "nó", or any noun followed by "đó"/"ấy" ("đoàn công tác đó").
_ANAPHOR_RE = re.compile(r"(?:^|\s)(?:nó)(?=$|[\s,.?!;:])|\S\s+(?:đó|ấy)(?=$|[\s,.?!;:])")
# Looking for documents other than the one under discussion: searching
# only that one would hide the answer.
_OTHER_DOCUMENTS_RE = re.compile(
    r"\b(?:{nouns}) (?:nao )?khac\b|\b(?:nhung|cac) van ban\b|\btrong kho\b|\bkho (?:con|co)\b".format(
        nouns=_DOC_NOUN_FOLDED_ALT)
)

# ── Questions about the conversation itself ───────────────────────────
#
# "ở đầu đoạn chat tôi đã hỏi bạn về văn bản nào", "câu trước tôi hỏi gì?",
# "nhắc lại câu trả lời vừa rồi", "tóm tắt lại những gì chúng ta đã trao
# đổi". Not "văn bản tôi mở đầu tiên khảo sát vào những ngày nào?", which
# asks about a document's content and goes to the documents.

# Points at the conversation.
_META_REF_RE = re.compile(
    r"\b(?:dau (?:doan|cuoc) (?:chat|tro chuyen|hoi thoai)|luc dau|ban dau|luc nay|vua roi"
    r"|tu dau|(?:toi|minh|em) (?:da |vua )?(?:hoi|mo|xem)|chung ta"
    r"|cau (?:hoi )?(?:truoc|dau|thu \w+|vua roi|cuoi))\b"
)
# Asks about a question of the conversation.
_META_QUESTION_RE = re.compile(r"\bcau (?:hoi )?(?:truoc|dau tien|dau|thu \w+|vua roi|cuoi)\b")
# Asks for an answer again, or for the exchange as a whole.
_META_ANSWER_RE = re.compile(
    r"\bnhac lai\b(?: \w+){0,3} (?:cau|tra loi|nhung gi|y vua|noi dung vua)"
    r"|\btom tat lai\b(?: \w+){0,2} (?:nhung gi|cuoc (?:tro chuyen|hoi thoai)|(?:cac|nhung) (?:luot|cau)|tu dau)"
    r"|\bban (?:vua |da )?(?:noi|tra loi) (?:gi|nhung gi|the nao|ve gi)\b"
    r"|\b(?:chung ta|minh) da (?:trao doi|noi chuyen|ban)\b"
)
# Asks which document, or what was asked.
_META_WHICH_RE = re.compile(
    r"\b(?:van ban|tai lieu)\b(?: \w+){0,3} (?:nao|so may|gi)\b|\bso may\b|\bhoi (?:ban )?(?:gi|ve gi|nhung gi)\b"
)
# Asks about a document's content: a recall question, answered from the documents.
_CONTENT_ASK_RE = re.compile(
    r"\b(?:ai ky|do ai|ngay nao|bao gio|khi nao|bao nhieu|may gio|ban hanh|thoi gian|han chot"
    r"|nhung ngay|chuc vu|gom nhung|truong doan)\b"
    # "ở đâu", not "ở đầu đoạn chat": both fold to "o dau".
    r"|\bo dau\b(?! (?:doan|cuoc|chat|tien))"
)

# ── A document named by its type ──────────────────────────────────────
#
# "vậy trong số các văn bản đó thì tóm tắt văn bản tờ trình đi" after a list
# of 24 documents was read as "văn bản đó" = the last document cited, a
# công văn, and "tờ trình đó" next as that công văn again. A type ("tờ
# trình", "các công văn đó", "kế hoạch đầu tiên trong danh sách") picks
# among the documents of the last turn (the ones listed or shown), else
# among the whole store when only one document has that type; a document of
# another type is never taken for it.

# Switch for A/B runs of the bench (bench/rag/multiturn); on in the app.
TYPE_REFERENCES = True
_TYPE_NOUNS = ("kế hoạch", "quyết định", "tờ trình", "thông báo", "công văn", "báo cáo", "chỉ thị",
               "nghị quyết", "kết luận", "chương trình", "hướng dẫn", "thông tư", "nghị định", "quy chế",
               "biên bản")
_TYPE_NOUN_MARKED_ALT = "|".join(sorted((re.escape(noun) for noun in _TYPE_NOUNS), key=len, reverse=True))
_TYPE_NOUN_ALT = "|".join(sorted((re.escape(fold_vietnamese(noun)) for noun in _TYPE_NOUNS),
                                 key=len, reverse=True))
_DEMONSTRATIVE = r"(?:đó|ấy|này|kia|trên|nói trên|vừa nói|vừa rồi|vừa liệt kê)(?=$|[\s,.?!;:])"
# Lowercase text with marks ("đó", not "do ai ký"): "tờ trình đó", "công văn này".
_TYPE_THAT_RE = re.compile(r"\b(?P<type>{types})\s+{that}".format(types=_TYPE_NOUN_MARKED_ALT,
                                                                   that=_DEMONSTRATIVE))
# "các công văn đó", "những kế hoạch trên".
_TYPE_PLURAL_RE = re.compile(r"\b(?:các|những|mấy)\s+(?P<type>{types})\s+{that}".format(
    types=_TYPE_NOUN_MARKED_ALT, that=_DEMONSTRATIVE))
# "các văn bản đó", "trong số đó", "trong các văn bản trên".
_AMONG_RE = re.compile(r"\b(?:(?:các|những)\s+(?:văn bản|tài liệu)\s+{that}|trong\s+(?:số\s+)?(?:đó|này|trên)"
                       r"(?=$|[\s,.?!;:]))".format(that=_DEMONSTRATIVE))
# Folded: "văn bản tờ trình", "tờ trình trong kho", "thông báo duy nhất".
_DOC_TYPE_RE = re.compile(r"\b(?:van ban|tai lieu) (?:loai )?(?P<type>{types})\b(?! (?:so )?\d)".format(
    types=_TYPE_NOUN_ALT))
_TYPE_IN_STORE_RE = re.compile(r"\b(?P<type>{types}) (?:(?:co |hien co )?trong kho|duy nhat)\b".format(
    types=_TYPE_NOUN_ALT))
# "kế hoạch đầu tiên trong danh sách", "công văn thứ hai", "văn bản cuối cùng".
_ORDINAL_RE = re.compile(
    r"\b(?P<type>{types}|van ban|tai lieu) (?P<ord>dau tien|thu nhat|thu hai|thu ba|thu tu|thu nam"
    r"|cuoi cung)\b".format(types=_TYPE_NOUN_ALT))
_ORDINALS = {"dau tien": 0, "thu nhat": 0, "thu hai": 1, "thu ba": 2, "thu tu": 3, "thu nam": 4,
             "cuoi cung": -1}
_LIST_CUE_RE = re.compile(
    r"\b(?:trong danh sach|danh sach (?:tren|vua roi|do|nay)|o tren|vua liet ke|trong so (?:do|nay|tren))\b")
# "văn bản đầu tiên tôi hỏi" is the conversation's first, not the list's.
_OWN_TURN_RE = re.compile(
    r"\b(?:toi|minh|em) (?:da |vua )?(?:hoi|mo|xem)\b|\b(?:luot|cau hoi|cuoc tro chuyen|doan chat)\b")


def kind_matches(entry: Optional["CatalogEntry"], type_word: str) -> bool:
    """Whether a document is of the type a (folded) type word names: its
    kind, or the letters of its number ("TTr" for tờ trình)."""
    if entry is None:
        return False
    kind = _TYPE_KINDS.get(type_word, (type_word, ""))
    if kind[0] and kind[0] in fold_vietnamese(entry.kind):
        return True
    return bool(kind[1]) and not entry.kind and kind[1] in re.findall(r"[a-z]+", fold_vietnamese(entry.number))


MAX_DOCUMENTS = 4
# Estimated tokens (llm_client.estimate_text_tokens errs high: 450 estimated
# are about 330 of Qwen3's tokens for this Vietnamese text).
STATE_TOKENS = 450


def number_key(value: str) -> str:
    """A document number without marks, case and punctuation: both sides of
    a comparison."""
    return re.sub(r"[^0-9a-z]", "", fold_vietnamese(value or ""))


def _lead(value: str) -> Optional[int]:
    """The number a document number starts with ("195/KH-HĐND" -> 195;
    "a266/VP" -> 266)."""
    match = re.match(r"\s*[A-Za-z]?(\d+)", value or "")
    return int(match.group(1)) if match else None


def _split_key(key: str) -> Tuple[str, str]:
    match = re.match(r"([a-z]?)(\d+)(.*)", key)
    if not match:
        return "", key
    return match.group(2), match.group(1) + match.group(3)


def _ocr_twins(have: str, want: str) -> bool:
    """Two leading numbers of the same length that differ only in digits OCR
    confuses (1/4, 1/7, 0/8...)."""
    if len(have) != len(want) or have == want:
        return False
    return all(a == b or frozenset((a, b)) in _OCR_DIGIT_PAIRS for a, b in zip(have, want))


def _one_line(value, limit=0) -> str:
    text = " ".join(str(value or "").split())
    if limit and len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0] + "…"
    return text


@dataclass(frozen=True)
class CatalogEntry:
    doc_id: int
    number: str = ""
    kind: str = ""
    issued: str = ""
    summary: str = ""


class DocumentCatalog:
    """The newest version of each document, by id and by number."""

    def __init__(self, rows: Iterable[Sequence]):
        """rows: (doc_id, so_ki_hieu, loai_vb, ngay_bh, nd_chinh)."""
        self.entries = {}  # type: Dict[int, CatalogEntry]
        for row in rows:
            entry = CatalogEntry(int(row[0]), str(row[1] or "").strip(), _one_line(row[2]),
                                 _one_line(row[3]), _one_line(row[4]))
            self.entries[entry.doc_id] = entry

    def get(self, doc_id) -> Optional[CatalogEntry]:
        return self.entries.get(int(doc_id)) if doc_id is not None else None

    def label(self, doc_id) -> str:
        """How the document is named to the model and the reader."""
        entry = self.get(doc_id)
        if entry is None:
            return "văn bản #{}".format(doc_id)
        if entry.number:
            return entry.number
        parts = [entry.kind or "Văn bản", "(không số)"]
        if entry.issued:
            parts.append("ngày {}".format(entry.issued))
        return " ".join(parts)

    def describe(self, doc_id, limit=55) -> str:
        """'166-KH/ĐĐHĐND: Kế hoạch, 08/04/2022, Quán triệt…'"""
        entry = self.get(doc_id)
        if entry is None:
            return self.label(doc_id)
        details = [part for part in (entry.kind if entry.number else "", entry.issued,
                                     _one_line(entry.summary, limit)) if part]
        return "{}: {}".format(self.label(doc_id), ", ".join(details)) if details else self.label(doc_id)

    # ── Numbers ───────────────────────────────────────────────────────

    def by_number(self, text: str) -> List[Tuple[int, str, str]]:
        """(doc id, how, number as written) of every full document number
        in text, in order.

        Exact matches ignore marks and punctuation. A recognition slip in
        the letters still matches when the leading number agrees and the
        rest is close (difflib ratio >= 0.8); one in the leading digits
        when the letters agree exactly and the digits differ only where
        OCR confuses them, and no document has the number as written.
        """
        found = []  # type: List[Tuple[int, str, str]]
        written = [match.group().strip() for match in NUMBER_RE.finditer(text or "")]
        stored = [(entry.doc_id, number_key(entry.number)) for entry in self.entries.values()
                  if entry.number]
        for asked in written:
            key = number_key(asked)
            if not re.search(r"[a-z]", key):  # a date such as 10/01/2018 is not a number
                continue
            exact = [doc_id for doc_id, have in stored if have == key]
            if exact:
                found.extend((doc_id, "named", asked) for doc_id in exact)
                continue
            key_lead, key_rest = _split_key(key)
            best, best_ratio = None, 0.0
            for doc_id, have in stored:
                have_lead, have_rest = _split_key(have)
                if have_lead != key_lead:
                    continue
                ratio = difflib.SequenceMatcher(None, have, key).ratio()
                if ratio >= 0.8 and ratio > best_ratio:
                    best, best_ratio = doc_id, ratio
            if best is not None:
                found.append((best, "named", asked))
                continue
            twins = [doc_id for doc_id, have in stored
                     if _split_key(have)[1] == key_rest and key_rest
                     and _ocr_twins(_split_key(have)[0], key_lead)]
            if len(twins) == 1:
                found.append((twins[0], "ocr", asked))
        return found

    def by_lead(self, number: int, type_word: str = "") -> List[int]:
        """Documents whose number starts with `number`, narrowed to the kind
        a type word names ("kế hoạch 23" is 23/KH-VP, not 23/TTTr-CD)."""
        matches = [entry for entry in self.entries.values() if _lead(entry.number) == number]
        kind = _TYPE_KINDS.get(type_word)
        if kind and len(matches) > 1:
            narrowed = [entry for entry in matches
                        if kind[0] in fold_vietnamese(entry.kind)
                        or kind[1] in re.findall(r"[a-z]+", fold_vietnamese(entry.number))]
            matches = narrowed or matches
        return [entry.doc_id for entry in matches]


def bare_numbers(folded: str) -> List[Tuple[int, str]]:
    """(number, type word) of the numbers a question gives without letters."""
    found = []
    for match in _BARE_RE.finditer(folded):
        found.append((int(match.group("num")), match.group("type")))
        position = match.end()
        while True:
            more = _MORE_RE.match(folded, position)
            if more is None:
                break
            found.append((int(more.group("num")), match.group("type")))
            position = more.end()
    return found


# ── The conversation so far ───────────────────────────────────────────

@dataclass
class TurnRecord:
    """One earlier question and how it went."""
    question: str
    answer: str = ""
    # The documents the turn was about: the ones its question named or
    # pointed at, else the ones its answer cited.
    docs: List[int] = field(default_factory=list)
    # The document open in the viewer when it was asked; None also for
    # turns saved before this was recorded.
    open_doc: Optional[int] = None
    # "all" / "open" (the scope box), None when unknown.
    scope: Optional[str] = None
    # "answered", "not_found", "conversation", "meta"; "" unknown.
    status: str = ""
    # How docs were found ("named", "open", "first", "cited", ...).
    how: str = ""
    # Labels saved with the turn, for documents later deleted from the store.
    labels: Dict[int, str] = field(default_factory=dict)
    # The documents its answer cited (docs may be only the named ones).
    cited: List[int] = field(default_factory=list)
    # The documents of the sources the model was shown, in their order.
    shown: List[int] = field(default_factory=list)
    # The metadata table the turn was answered from (metadata_query kind),
    # "" when none: a list for "kế hoạch thứ hai trong danh sách".
    table: str = ""


@dataclass
class ConversationState:
    turns: List[TurnRecord] = field(default_factory=list)
    open_doc: Optional[int] = None
    scope: Optional[str] = None

    def _document_turns(self) -> List[TurnRecord]:
        return [turn for turn in self.turns
                if turn.docs and turn.status not in ("conversation", "meta")]

    def last_documents(self) -> List[int]:
        turns = self._document_turns()
        return list(turns[-1].docs) if turns else []

    def last_cited(self) -> List[int]:
        """What the last turn with documents cited, then what it was shown,
        besides its documents."""
        turns = self._document_turns()
        if not turns:
            return []
        extra = []  # type: List[int]
        for doc_id in turns[-1].cited + turns[-1].shown:
            if doc_id not in turns[-1].docs and doc_id not in extra:
                extra.append(doc_id)
        return extra

    def first_documents(self) -> List[int]:
        turns = self._document_turns()
        return list(turns[0].docs) if turns else []

    def first_open_document(self) -> Optional[int]:
        return next((turn.open_doc for turn in self.turns if turn.open_doc is not None), None)

    def recent_documents(self) -> List[int]:
        """Every document of the conversation once, newest turn first."""
        seen = []  # type: List[int]
        for turn in reversed(self._document_turns()):
            for doc_id in turn.docs:
                if doc_id not in seen:
                    seen.append(doc_id)
        return seen

    @staticmethod
    def pool(turn: "TurnRecord") -> List[int]:
        """The documents a turn put before the reader: for a list (no
        document named) the ones shown in their order, else the named ones
        first, then what was shown and cited."""
        order = (turn.shown + turn.docs + turn.cited) if turn.how == "cited" else (
            turn.docs + turn.shown + turn.cited)
        return list(dict.fromkeys(int(doc_id) for doc_id in order))

    def last_pool(self, at_least: int = 1) -> List[int]:
        """pool() of the newest turn that had at least this many documents."""
        for turn in reversed(self.turns):
            if turn.status in ("conversation", "meta"):
                continue
            documents = self.pool(turn)
            if len(documents) >= at_least:
                return documents
        return []

    def last_list(self) -> List[int]:
        """pool() of the newest turn that listed documents: answered from a
        metadata table, or with three or more; else two or more. A turn
        about one of them since ("kế hoạch đầu tiên do ai ký?" shows that
        plan and a sibling) is not the list."""
        for turn in reversed(self.turns):
            if turn.status in ("conversation", "meta"):
                continue
            documents = self.pool(turn)
            if turn.table or (len(documents) >= 3 and turn.how in ("cited", "plural", "")):
                return documents
        return self.last_pool(at_least=2)


@dataclass
class Resolution:
    """What a question is about."""
    # Named or pointed at: pinned for this question, whatever the scope.
    docs: List[int] = field(default_factory=list)
    how: Dict[int, str] = field(default_factory=dict)
    # The number as the question wrote it, for a document found through an
    # OCR slip ("1213-QĐ/TU" for the stored 4243-QĐ/TU).
    asked: Dict[int, str] = field(default_factory=dict)
    # Names nothing but leans on the previous turn ("ông ấy", "còn … thì
    # sao", "Họp ở đâu?").
    follow_up: bool = False
    # ... through a pronoun or "X đó/ấy" ("ông ấy", "nó", "đoàn công tác đó").
    anaphora: bool = False
    short: bool = False
    # The documents of the last turn that had any.
    focus: List[int] = field(default_factory=list)
    # Asks for documents other than the one under discussion.
    other_documents: bool = False
    # "trong các công văn đó, công văn nào …" over more documents than are
    # pinned: search only among these.
    within: List[int] = field(default_factory=list)
    # The type the question named its document by ("to trinh"), "" if none.
    kind: str = ""


def _add(docs: List[int], how: Dict[int, str], found: Iterable[int], kind: str) -> None:
    for doc_id in found:
        if doc_id is None:
            continue
        doc_id = int(doc_id)
        if doc_id not in docs:
            docs.append(doc_id)
            how[doc_id] = kind


def _folded_words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", fold_vietnamese(text or "")).split())


def is_meta_question(question: str) -> bool:
    """A question about the conversation itself: what was asked first, what
    the assistant just said, a summary of the exchange, which document an
    earlier turn was about. Not "the first document I asked about: when was
    it issued?", which asks about a document's content."""
    folded = _folded_words(question)
    if not folded:
        return False
    if _META_ANSWER_RE.search(folded):
        return True
    if _META_REF_RE.search(folded) is None or _CONTENT_ASK_RE.search(folded):
        return False
    return bool(_META_QUESTION_RE.search(folded) or _META_WHICH_RE.search(folded))


def _of_type(doc_ids: Iterable[int], catalog: DocumentCatalog, type_word: str) -> List[int]:
    return [doc_id for doc_id in doc_ids if kind_matches(catalog.get(doc_id), type_word)]


def _type_reference(folded: str, lower: str, state: ConversationState, catalog: DocumentCatalog,
                    open_doc: Optional[int]) -> Optional[Tuple[List[int], str, List[int], str]]:
    """(documents, how, documents to search within, type) for a question
    that names its documents by type or by "các văn bản đó"; None when it
    does not. A type that matches nothing gives no document rather than the
    last one."""
    pool = state.last_pool()
    ordinal = _ORDINAL_RE.search(folded)
    if ordinal is not None and _OWN_TURN_RE.search(folded) is None:
        listed = state.last_list()
        if _LIST_CUE_RE.search(folded) or len(pool) >= 3 or len(listed) >= 3:
            type_word = ordinal.group("type")
            candidates = listed if type_word in ("van ban", "tai lieu") else _of_type(listed, catalog, type_word)
            position = _ORDINALS[ordinal.group("ord")]
            kind = "" if type_word in ("van ban", "tai lieu") else type_word
            if candidates and position < len(candidates):
                return [candidates[position]], "ordinal", [], kind
            return [], "ordinal", [], kind
    plural = _TYPE_PLURAL_RE.search(lower)
    among = _AMONG_RE.search(lower)
    singular = _TYPE_THAT_RE.search(lower)
    named = _DOC_TYPE_RE.search(folded) or _TYPE_IN_STORE_RE.search(folded)
    if plural is not None or (among is not None and singular is None and named is None):
        type_word = fold_vietnamese(plural.group("type")) if plural is not None else ""
        if not type_word:
            # "Kế hoạch nào trong số đó": the type said elsewhere in the question.
            types = sorted((match.start(), word) for word in _TYPE_KINDS if " " in word
                           for match in [re.search(r"\b{}\b".format(word), folded)] if match)
            type_word = types[0][1] if types else ""
        candidates = _of_type(pool, catalog, type_word) if type_word else list(pool)
        if not candidates:
            return ([], "plural", [], type_word) if type_word else None
        if len(candidates) <= MAX_DOCUMENTS:
            return candidates, "plural", [], type_word
        return [], "plural", candidates, type_word
    if singular is None and named is None:
        return None
    type_word = fold_vietnamese((singular or named).group("type"))
    if singular is not None and singular.group(0).split()[-1] == "này" and open_doc is not None \
            and kind_matches(catalog.get(open_doc), type_word):
        return [open_doc], "open", [], type_word
    candidates = _of_type(pool, catalog, type_word)
    if candidates:
        # The last document when it is of that type, as before.
        last = state.last_documents()
        return [candidates[0]], "last" if last and candidates[0] == last[0] else "type", [], type_word
    in_store = [doc_id for doc_id, entry in catalog.entries.items() if kind_matches(entry, type_word)]
    if len(in_store) == 1:
        return in_store, "type", [], type_word
    return [], "type", [], type_word


def resolve(question: str, catalog: DocumentCatalog, state: Optional[ConversationState] = None,
            open_doc: Optional[int] = None) -> Resolution:
    """The documents a question names or points at, and whether it leans on
    the previous turn. open_doc: the document open now (state.open_doc when
    a state is given)."""
    result = Resolution()
    text = question or ""
    lower = text.lower()
    # Marks, case and most punctuation go; "/" and "-" stay, they end a
    # bare number ("195/KH-HĐND" is a full one) and "," joins a list.
    folded = " ".join(re.sub(r"[^\w\s/,-]", " ", fold_vietnamese(text)).split())
    words = re.findall(r"\w+", folded)
    if state is not None and state.open_doc is not None:
        open_doc = state.open_doc
    docs, how = result.docs, result.how

    for doc_id, kind, asked in catalog.by_number(text):
        _add(docs, how, [doc_id], kind)
        if kind == "ocr":
            result.asked[doc_id] = asked
    conversation_docs = state.recent_documents() if state is not None else []
    for number, word in bare_numbers(folded):
        candidates = catalog.by_lead(number, word)
        if len(candidates) > 1:
            # Two documents numbered 23: the one already discussed, else both.
            known = [doc_id for doc_id in candidates if doc_id in conversation_docs or doc_id == open_doc]
            candidates = known or candidates[:3]
        _add(docs, how, candidates, "number")

    if open_doc is not None and OPEN_DOCUMENT_RE.search(folded):
        _add(docs, how, [open_doc], "open")

    result.other_documents = _OTHER_DOCUMENTS_RE.search(folded) is not None
    typed = None
    if (TYPE_REFERENCES and state is not None and not docs
            and _PLURAL_RE.search(folded) is None):
        typed = _type_reference(folded, lower, state, catalog, open_doc)
        if typed is not None:
            _add(docs, how, typed[0], typed[1])
            result.within = list(typed[2])
            result.kind = typed[3]
    if typed is not None:
        pass
    elif state is not None:
        last = state.last_documents()
        plural = _PLURAL_RE.search(folded)
        if plural is not None and len(docs) < 2:
            count = _COUNT_WORDS[plural.group("all") or plural.group("n")]
            # "Hai văn bản đó" after a question that named one document and
            # whose answer cited (or was shown) a second: those two.
            pool = last + state.last_cited()
            if len(pool) < count:
                pool = state.recent_documents()
            _add(docs, how, pool[:count], "plural")
        elif (not docs and len(last) >= 2 and not result.other_documents
              and _WHICH_RE.search(folded)):
            _add(docs, how, last, "plural")
        elif _FIRST_OPEN_RE.search(folded) and state.first_open_document() is not None:
            _add(docs, how, [state.first_open_document()], "first_open")
        elif not docs and _FIRST_RE.search(folded):
            _add(docs, how, state.first_documents()[:MAX_DOCUMENTS], "first")
        if _PREVIOUS_RE.search(folded):
            recent = state.recent_documents()
            just_said = _JUST_SAID_RE.search(folded) is not None
            if just_said or open_doc is None or open_doc not in recent:
                current = (last or [None])[0]
            else:
                current = open_doc
            if just_said and current is not None:
                _add(docs, how, [current], "last")
            _add(docs, how, [next((doc_id for doc_id in recent if doc_id != current), None)],
                 "previous")
        elif not docs and _LAST_RE.search(lower):
            _add(docs, how, last[:1], "last")
        if not docs and _THIS_RE.search(lower):
            this = open_doc if open_doc is not None else (last or [None])[0]
            _add(docs, how, [this], "open" if this == open_doc else "last")
    elif not docs and open_doc is not None and _THIS_RE.search(lower):
        _add(docs, how, [open_doc], "open")

    for doc_id in docs[MAX_DOCUMENTS:]:
        how.pop(doc_id, None)
    del docs[MAX_DOCUMENTS:]
    if not docs:
        padded = " {} ".format(folded)
        result.follow_up = (
            any(" {} ".format(marker) in padded for marker in _FOLLOW_UP_FOLDED)
            or _FOLLOW_UP_START_RE.search(folded) is not None
            or _ANAPHOR_RE.search(lower) is not None
        )
        result.anaphora = (
            any(" {} ".format(marker) in padded for marker in _ANAPHORA_FOLDED)
            or _ANAPHOR_RE.search(lower) is not None
        )
        result.short = len(words) <= 6
        if state is not None and typed is None:
            result.focus = state.last_documents()[:MAX_DOCUMENTS]
        elif state is not None and result.kind and not result.within:
            # "tờ trình đó" after a công văn: never that công văn.
            result.focus = _of_type(state.last_documents(), catalog, result.kind)[:MAX_DOCUMENTS]
    return result


# ── What the model is told about the conversation ─────────────────────

_STATE_HEADER = (
    "TRẠNG THÁI HỘI THOẠI (ứng dụng ghi lại, chính xác): cho biết \"văn bản này/đó/trước/đầu tiên/"
    "cả hai\" là văn bản nào; nội dung văn bản vẫn lấy từ NGUỒN TRONG KHO."
)
_META_HEADER = "TRẠNG THÁI HỘI THOẠI (ứng dụng ghi lại, chính xác):"
HOW_NOTES = {
    "open": "văn bản đang mở", "first_open": "văn bản mở đầu tiên", "first": "văn bản của lượt đầu",
    "previous": "văn bản trước đó", "last": "văn bản vừa nói", "plural": "các văn bản vừa nói",
    "follow-up": "tiếp tục lượt trước", "scope": "văn bản đang mở",
    "type": "văn bản thuộc loại câu hỏi nêu", "ordinal": "theo thứ tự trong danh sách lượt trước",
}


def _clip(text: str, limit: int) -> str:
    return _one_line(text, limit)


def conversation_block(state: Optional[ConversationState], catalog: Optional[DocumentCatalog],
                       current: Sequence[int] = (), current_how: Optional[Dict[int, str]] = None,
                       answers: bool = False, max_tokens: int = STATE_TOKENS) -> str:
    """The short note on the conversation sent with a question: the
    document open now, one line per earlier turn (its question, the
    documents it was about, the document open then, its outcome) and what
    this question was taken to be about. A document is described (kind,
    date, start of its summary) where it is first named, then by its number
    only. Over max_tokens the middle turns' lines get shorter, then the
    descriptions, then the oldest turns after the first go; the first and
    the newest turn stay. answers: add the start of each answer (for questions
    about the conversation itself). Empty before the first answer."""
    if state is None or not state.turns:
        return ""
    saved = {}  # type: Dict[int, str]
    for turn in state.turns:
        saved.update(turn.labels)

    def label(doc_id):
        if catalog is not None and catalog.get(doc_id) is not None:
            return catalog.label(doc_id)
        return saved.get(doc_id) or "văn bản #{}".format(doc_id)

    def render(kept, short, summary):
        described = set()

        def name(doc_id):
            if doc_id in described or catalog is None or catalog.get(doc_id) is None:
                return label(doc_id)
            described.add(doc_id)
            entry = catalog.get(doc_id)
            details = [part for part in (entry.kind if entry.number else "", entry.issued,
                                         _clip(entry.summary, summary) if summary else "") if part]
            return "{} ({})".format(label(doc_id), ", ".join(details)) if details else label(doc_id)

        lines = [_META_HEADER if answers else _STATE_HEADER]
        if state.open_doc is not None:
            lines.append("Văn bản đang mở bây giờ: {}".format(name(state.open_doc)))
        for index, turn in kept:
            brief = index in short
            line = "Lượt {}: \"{}\"".format(index, _clip(turn.question, 40 if brief else 90 if answers else 70))
            if turn.status == "conversation":
                lines.append(line + " (trò chuyện)")
                continue
            if turn.docs:
                line += " → " + ", ".join(name(doc_id) for doc_id in turn.docs)
            if turn.open_doc is not None and (turn.how in ("open", "scope") or turn.scope == "open"):
                if turn.open_doc in turn.docs:
                    line += " (văn bản đang mở lúc đó)"
                else:
                    line += " (lúc đó đang mở {})".format(name(turn.open_doc))
            elif turn.open_doc is not None and turn.open_doc not in turn.docs:
                line += " (lúc đó đang mở {})".format(name(turn.open_doc))
            if turn.status == "not_found":
                line += "; trợ lý chưa trả lời được"
            elif answers and turn.answer:
                first = re.split(r"(?<=[.!?])\s|\n", turn.answer.strip(), 1)[0]
                line += "; đã trả lời: \"{}\"".format(_clip(first, 70 if brief else 140))
            lines.append(line)
        if current:
            notes = current_how or {}
            lines.append("Câu hỏi hiện tại được hiểu là về: " + "; ".join(
                "{}{}".format(name(doc_id), " ({})".format(HOW_NOTES[notes[doc_id]])
                              if notes.get(doc_id) in HOW_NOTES else "") for doc_id in current))
        return "\n".join(lines)

    kept = [(index, turn) for index, turn in enumerate(state.turns, 1)
            if answers or turn.status not in ("conversation", "meta")]
    middle = frozenset(index for index, _turn in kept[1:-2])
    text = render(kept, frozenset(), 40)
    for short, summary in ((middle, 40), (middle, 0)):
        if estimate_text_tokens(text) <= max_tokens:
            break
        text = render(kept, short, summary)
    while len(kept) > 2 and estimate_text_tokens(text) > max_tokens:
        del kept[1]  # the oldest after the first
        text = render(kept, middle, 0)
    if estimate_text_tokens(text) > max_tokens:
        text = render(kept, frozenset(index for index, _turn in kept), 0)
    return text
