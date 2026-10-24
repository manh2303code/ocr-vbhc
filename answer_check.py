"""Rule-based checking of a chat answer against what the model was shown.

The local 8B model sometimes writes a figure, a date or a document number
that is in none of the sources ("cách nhau 114 ngày", "1.479.300.000 đồng"),
calls a công văn a tờ trình, says "Không tìm thấy thông tin" with the asked
document's passages in front of it, leaves one side out of a comparison, or
copies source label lines ("[1] CHÁNH VĂN PHÒNG Nguyễn Văn A") instead of
answering. check() finds these with rules only, against

  * the sources shown this turn (prefetch and tool results),
  * the TRẠNG THÁI HỘI THOẠI note and the question itself,
  * the store's metadata (every document's number, type and date);

note() turns what it found into a short correction for one more request,
and hedge() takes out what is still unsupported after it. No model call is
made unless something is found.
"""

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from doc_resolver import NUMBER_RE, DocumentCatalog, kind_matches, number_key
from search_store import fold_vietnamese


# ── What an answer states ─────────────────────────────────────────────

_DATE_RES = (
    re.compile(r"(?<![\d/.])(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})(?![\d/])"),
    re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s*(?:năm|/)\s*(\d{4})", re.IGNORECASE),
)
# "28/3", "ngày 25 tháng 6": a day without its year.
_DAY_MONTH_RES = (
    re.compile(r"(?<![\d/.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])"),
    re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})(?!\s*(?:năm|/)\s*\d)", re.IGNORECASE),
)
# A figure: "180.500", "34.450.000đ", "70%", "114", "180,5".
_FIGURE_RE = re.compile(r"(?<![\w/.,-])(\d{1,3}(?:[.,]\d{3})+(?:,\d+)?|\d+(?:,\d{1,2})?)(?![\d/])")
# A document number as an answer writes it: "195/KH-HĐND", "M2/TTr-UBND",
# with nothing between the parts. doc_resolver.NUMBER_RE allows the spaces
# OCR leaves in the sources ("46 /CV- ĐĐBQH"), and in an answer it read
# "Công văn: 15\n- Kế hoạch: 8" as a number "15 - Kế".
ANSWER_NUMBER_RE = re.compile(r"(?<![\w/.-])[A-Za-z]?\d{1,5}[a-zA-Z]?[/-][\wĐđ]+(?:[/-][\wĐđ]+)*")
# "1. …", "2) …" at the start of a line: list markers, not figures.
_ENUMERATOR_RE = re.compile(r"(?m)^\s*(?:[-*•]\s*)?\d{1,2}[.)]\s")
# Below this a bare number is an ordinal, a month, "lớp 2", "kỳ họp thứ 9".
MIN_FIGURE = 13
_TYPE_WORDS = ("kế hoạch", "quyết định", "tờ trình", "thông báo", "công văn", "báo cáo", "chỉ thị",
               "nghị quyết", "kết luận", "chương trình", "hướng dẫn", "thông tư", "nghị định", "quy chế",
               "biên bản")
_TYPE_ALT = "|".join(sorted((re.escape(word) for word in _TYPE_WORDS), key=len, reverse=True))
# The type word right before a document number: "Tờ trình 09/CV-ĐĐBQH",
# "công văn số 46/CV-ĐĐBQH", "**Tờ trình** số M2".
_TYPE_BEFORE_RE = re.compile(r"(?P<type>{types})[\s*_]*(?:số(?:\s+(?:ký\s+)?hiệu)?[\s:]*)?$".format(
    types=_TYPE_ALT), re.IGNORECASE)
# "15 công văn", "Công văn: 15", "kế hoạch (7)": a count next to a type.
_COUNT_AFTER_RE = re.compile(r"(?<![\d/.,])(\d+)\s+(?:văn bản\s+)?(?P<type>{types})".format(types=_TYPE_ALT),
                             re.IGNORECASE)
_COUNT_BEFORE_RE = re.compile(
    r"(?P<type>{types})[*_]*\s*(?::|\(|–|-)\s*(\d+)(?![\d/.,-])".format(types=_TYPE_ALT), re.IGNORECASE)
# "24 văn bản", "3 kế hoạch": a number of documents the answer states.
_STATED_COUNT_RE = re.compile(r"(?<![\d/.,-])(\d+)\s+(?:văn bản|{types})\b".format(types=_TYPE_ALT),
                              re.IGNORECASE)
_COUNTING_RE = re.compile(r"\b(?:bao nhieu|so luong|co may|tong so|tong cong|dem)\b")
_LISTING_RE = re.compile(
    r"\b(?:liet ke|danh sach|thong ke|tat ca|toan bo|ke ra|ke ten|nhung van ban nao|cac van ban nao"
    r"|nhung ai|ky nhung|ky cac|gom nhung)\b")
_UPPER = "A-ZĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ"
# A source label line copied into the answer: "[3] KT. CHÁNH VĂN PHÒNG …",
# the 【…】 label, or the "Cơ quan: … · Ký: …" line.
RAW_LABEL_RE = re.compile(
    r"\[\d+\]\s*(?:(?:KT|TL|TM|T/M)\.?\s*)?[{u}]{{2,}}\.?(?:\s+[{u}]{{2,}}\.?)+|【|·\s*Ký:".format(u=_UPPER))
_COMPARE_RE = re.compile(
    r"\b(?:so sanh|khac nhau|giong nhau|doi chieu|ca hai|ca ba|lan luot|moi van ban|tung van ban"
    r"|hai van ban|ba van ban|diem chung|khac biet)\b")


def _dates(text: str) -> Dict[Tuple[int, int, int], str]:
    """(day, month, year) -> the date as first written."""
    found = {}  # type: Dict[Tuple[int, int, int], str]
    for pattern in _DATE_RES:
        for match in pattern.finditer(text or ""):
            day, month, year = (int(group) for group in match.groups())
            if 1 <= day <= 31 and 1 <= month <= 12:
                found.setdefault((day, month, year), match.group(0).strip())
    return found


def _day_months(text: str) -> Dict[Tuple[int, int], str]:
    """(day, month) -> the day as first written, for days without a year."""
    found = {}  # type: Dict[Tuple[int, int], str]
    for pattern in _DAY_MONTH_RES:
        for match in pattern.finditer(text or ""):
            day, month = int(match.group(1)), int(match.group(2))
            if 1 <= day <= 31 and 1 <= month <= 12:
                found.setdefault((day, month), match.group(0).strip())
    return found


def _figure_value(text: str) -> str:
    """A figure in one form: '180.500' and '180500' -> '180500';
    '180,5' -> '180.5'."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:,\d+)?", text):
        whole, _, decimals = text.partition(",") if re.search(r",\d{1,2}$", text) else (text, "", "")
        whole = re.sub(r"[.,]", "", whole)
        return "{}.{}".format(whole, decimals) if decimals else whole.lstrip("0") or "0"
    if "," in text:
        whole, decimals = text.split(",", 1)
        return "{}.{}".format(whole.lstrip("0") or "0", decimals)
    return text.lstrip("0") or "0"


def _figures(text: str) -> Set[str]:
    return {_figure_value(match.group(1)) for match in _FIGURE_RE.finditer(text or "")}


def _masked(text: str, numbers=NUMBER_RE) -> str:
    """The text without document numbers, dates and list markers, so that
    what is left of its digits are figures."""
    text = numbers.sub(" ", text or "")
    for pattern in _DATE_RES + _DAY_MONTH_RES:
        text = pattern.sub(" ", text)
    text = re.sub(r"\[\d+\]", " ", text)
    return _ENUMERATOR_RE.sub(" ", text)


# ── The check ─────────────────────────────────────────────────────────

@dataclass
class Violation:
    kind: str  # "number", "date", "figure", "count", "type", "not_found", "compare", "raw"
    item: str  # as the answer writes it
    note: str  # one line of the correction
    # "type": (the type word as written, the number, the document's type)
    fix: Tuple[str, ...] = ()


@dataclass
class Evidence:
    """What the answer may rest on."""
    text: str = ""  # every source block shown this turn, the conversation note, the question
    numbers: Sequence[str] = ()  # so_ki_hieu of the sources shown
    catalog: Optional[DocumentCatalog] = None
    refs: Sequence[int] = ()  # the documents the question named or pointed at
    shown: Sequence[int] = ()  # the documents of the sources shown
    ref_numbers: Dict[int, List[int]] = field(default_factory=dict)  # doc -> its [n]
    cited: Sequence[int] = ()  # the documents the answer is credited with
    compare: bool = False  # the question compares the documents it names
    aggregate_counts: Optional[Dict[str, int]] = None  # folded kind -> count of a metadata table
    aggregate_empty: bool = False  # the table found nothing: "không có" is right
    # A question asking how many: the counts the table gives as its result.
    totals: Sequence[int] = ()
    # A question asking for a list: what each row must be named by
    # ((label, [ways to write it])).
    required: Sequence[Tuple[str, Sequence[str]]] = ()
    # The table's result line ("KẾT QUẢ: 24 văn bản … theo loại: …"), kept
    # in a list written again: the retry listed every row and dropped it.
    result_line: str = ""


@dataclass
class Report:
    checked: Dict[str, int] = field(default_factory=dict)
    violations: List[Violation] = field(default_factory=list)

    def kinds(self) -> List[str]:
        return [violation.kind for violation in self.violations]


def _known_numbers(evidence: Evidence) -> Set[str]:
    keys = {number_key(match.group()) for match in NUMBER_RE.finditer(evidence.text)}
    keys.update(number_key(number) for number in evidence.numbers if number)
    if evidence.catalog is not None:
        keys.update(number_key(entry.number) for entry in evidence.catalog.entries.values() if entry.number)
    return {key for key in keys if re.search(r"[a-z]", key)}


def _number_supported(key: str, known: Set[str]) -> bool:
    if key in known:
        return True
    lead = re.match(r"\d+", key)
    for have in known:
        have_lead = re.match(r"\d+", have)
        if lead and have_lead and lead.group() == have_lead.group() and \
                difflib.SequenceMatcher(None, have, key).ratio() >= 0.8:
            return True
    return False


def _document_of(key: str, catalog: Optional[DocumentCatalog]):
    if catalog is None:
        return None
    for entry in catalog.entries.values():
        if entry.number and number_key(entry.number) == key:
            return entry
    return None


def _labels(evidence: Evidence, doc_id: int) -> str:
    if evidence.catalog is not None and evidence.catalog.get(doc_id) is not None:
        return evidence.catalog.label(doc_id)
    return "văn bản #{}".format(doc_id)


def _sources_of(evidence: Evidence, doc_id: int) -> str:
    numbers = evidence.ref_numbers.get(doc_id) or []
    return " (nguồn {})".format(", ".join("[{}]".format(n) for n in numbers)) if numbers else ""


def _nearby_figures(value: str, known: Set[str]) -> List[str]:
    """For a large figure (an amount), the source's amounts closest to it on
    a log scale ("1.479.300.000" -> "180.500" triệu)."""
    if len(value.split(".")[0]) < 4:
        return []
    amounts = [have for have in known if len(have.split(".")[0]) >= 4 and float(have) > 0]
    return sorted(amounts, key=lambda have: abs(math.log10(float(have)) - math.log10(float(value))))[:3]


def _plain(value: str) -> str:
    """'180500' -> '180.500' for the note."""
    whole, _, decimals = value.partition(".")
    grouped = "{:,}".format(int(whole)).replace(",", ".") if whole.isdigit() else whole
    return grouped + ("," + decimals if decimals else "")


def check(answer: str, evidence: Evidence, not_found: bool = False) -> Report:
    """Everything the answer states that the evidence does not hold."""
    report = Report()
    text = answer or ""
    support = "{}\n{}".format(evidence.text, "\n".join(evidence.numbers))
    if evidence.catalog is not None:
        support += "\n" + "\n".join(entry.issued for entry in evidence.catalog.entries.values() if entry.issued)

    # Document numbers, and the type written before each.
    known = _known_numbers(evidence)
    numbers = [match for match in ANSWER_NUMBER_RE.finditer(text) if re.search(r"[a-z]", number_key(match.group()))]
    report.checked["numbers"] = len(numbers)
    report.checked["pairs"] = 0
    seen = set()
    for match in numbers:
        written = match.group().strip()
        key = number_key(written)
        if key in seen:
            continue
        seen.add(key)
        if not _number_supported(key, known):
            report.violations.append(Violation(
                "number", written, "Số ký hiệu \"{}\" không có trong nguồn hay trong kho.".format(written)))
            continue
        before = _TYPE_BEFORE_RE.search(text[max(0, match.start() - 40):match.start()])
        entry = _document_of(key, evidence.catalog)
        if before is None or entry is None or not entry.kind:
            continue
        report.checked["pairs"] += 1
        type_word = fold_vietnamese(before.group("type"))
        if not kind_matches(entry, type_word):
            report.violations.append(Violation(
                "type", "{} {}".format(before.group("type"), written),
                "\"{}\" là {}, không phải {}.".format(written, entry.kind, before.group("type").lower()),
                fix=(before.group("type"), written, entry.kind)))

    # Dates.
    have_dates = set(_dates(support))
    have_day_months = set(_day_months(support)) | {(day, month) for day, month, _year in have_dates}
    dates = _dates(text)
    report.checked["dates"] = len(dates)
    for (day, month, year), written in sorted(dates.items()):
        if (day, month, year) not in have_dates:
            report.violations.append(Violation(
                "date", written, "Ngày {}/{}/{} không có trong nguồn.".format(day, month, year)))
    for (day, month), written in sorted(_day_months(_masked_dates_only(text)).items()):
        if (day, month) not in have_day_months:
            report.violations.append(Violation(
                "date", written, "Ngày {}/{} không có trong nguồn.".format(day, month)))

    # Figures. A document's leading number ("văn bản 166") is one.
    have_figures = _figures(_masked(support)) | _figures(support)
    # Any digits of the sources, also inside a range or a code ("2021-2025").
    have_figures.update(run.lstrip("0") or "0" for run in re.findall(r"\d+", support))
    leads = [match.group() for match in NUMBER_RE.finditer(support)]
    if evidence.catalog is not None:
        leads += [entry.number for entry in evidence.catalog.entries.values() if entry.number]
    for number in leads:
        lead = re.match(r"\s*[A-Za-z]?0*(\d+)", number)
        if lead:
            have_figures.add(lead.group(1))
    figures = []
    for match in _FIGURE_RE.finditer(_masked(text, ANSWER_NUMBER_RE)):
        value = _figure_value(match.group(1))
        grouped = bool(re.search(r"[.,]\d{3}", match.group(1)))
        if not grouped and float(value) < MIN_FIGURE:
            continue
        figures.append((match.group(1), value))
    report.checked["figures"] = len(figures)
    flagged = set()
    for written, value in figures:
        if value in have_figures or value in flagged:
            continue
        flagged.add(value)
        close = _nearby_figures(value, have_figures)
        report.violations.append(Violation(
            "figure", written, "Số liệu \"{}\" không có trong nguồn{}.".format(
                written, " (trong nguồn có: {})".format(", ".join(_plain(item) for item in close))
                if close else "")))

    # Counts of a metadata table: "12 Kế hoạch" when the table says 7.
    if evidence.aggregate_counts:
        pairs, used = [], set()
        for match in _COUNT_AFTER_RE.finditer(text):
            pairs.append((int(match.group(1)), match.group("type"), match.group(0)))
            used.add(match.start(1))
        for match in _COUNT_BEFORE_RE.finditer(text):
            if match.start(2) not in used:
                pairs.append((int(match.group(2)), match.group("type"), match.group(0)))
        report.checked["counts"] = len(pairs)
        for count, type_word, written in pairs:
            folded = fold_vietnamese(type_word.lower())
            expected = evidence.aggregate_counts.get(folded)
            if expected is not None and count != expected:
                report.violations.append(Violation(
                    "count", written,
                    "Bảng thống kê ghi {} {}, không phải {}.".format(expected, type_word.lower(), count)))

    # The table's result itself: "26 văn bản liên quan đến Hưng Yên" when
    # the table counted 24.
    if evidence.totals:
        stated = [int(match.group(1)) for match in _STATED_COUNT_RE.finditer(text)]
        report.checked["totals"] = len(stated)
        if stated and not set(stated) & set(evidence.totals):
            report.violations.append(Violation(
                "count", "{} văn bản".format(stated[0]),
                "Bảng thống kê ghi KẾT QUẢ là {} văn bản; câu trả lời lại nêu {}.".format(
                    evidence.totals[0], ", ".join(str(count) for count in stated[:3]))))

    # A list that leaves rows out.
    if evidence.required:
        folded_answer = fold_vietnamese(text.lower())
        keys = {number_key(match.group()) for match in numbers}
        missing = [label for label, ways in evidence.required
                   if not any(fold_vietnamese(way.lower()) in folded_answer or number_key(way) in keys
                              for way in ways if way)]
        report.checked["listed"] = len(evidence.required)
        if missing:
            report.violations.append(Violation(
                "list", ", ".join(missing[:8]),
                "Câu hỏi yêu cầu liệt kê; bảng có {} mục nhưng câu trả lời chưa nêu: {}{}. Hãy {}liệt kê đủ "
                "từng mục của bảng.".format(
                    len(evidence.required), ", ".join(missing[:8]), " …" if len(missing) > 8 else "",
                    "mở đầu bằng tổng số và số theo loại như dòng \"{}\", rồi ".format(evidence.result_line)
                    if evidence.result_line else "")))

    # "Không tìm thấy" with the asked document, or a table, in front of it.
    if not_found:
        shown = [doc_id for doc_id in evidence.refs if doc_id in set(evidence.shown)]
        if shown:
            report.violations.append(Violation(
                "not_found", "không tìm thấy",
                "Câu trả lời nói không tìm thấy, nhưng nguồn đã có văn bản được hỏi: {}. Hãy đọc kỹ các "
                "nguồn đó; nếu chúng thật sự không có điều được hỏi thì nói rõ văn bản đó không đề cập, "
                "kèm số ký hiệu.".format("; ".join(_labels(evidence, doc_id) + _sources_of(evidence, doc_id)
                                                     for doc_id in shown))))
        elif evidence.aggregate_counts is not None and not evidence.aggregate_empty:
            report.violations.append(Violation(
                "not_found", "không tìm thấy",
                "Câu trả lời nói không tìm thấy, nhưng NGUỒN TRONG KHO có bảng thống kê do ứng dụng "
                "tính từ thông tin văn bản; hãy trả lời theo bảng đó."))

    # A comparison that leaves one of its documents out.
    if evidence.compare and len(evidence.refs) >= 2:
        named = {number_key(match.group()) for match in numbers}
        leads = set(re.findall(r"(?<![\d/.,])(\d{1,5})(?![\d.,]\d)", text))
        missing = []
        for doc_id in evidence.refs:
            entry = evidence.catalog.get(doc_id) if evidence.catalog is not None else None
            if doc_id in set(evidence.cited):
                continue
            if entry is not None and entry.number and (
                    number_key(entry.number) in named
                    or (re.match(r"\s*[A-Za-z]?(\d+)", entry.number) or [None, None])[1] in leads):
                continue
            missing.append(doc_id)
        report.checked["compare"] = len(evidence.refs)
        for doc_id in missing:
            report.violations.append(Violation(
                "compare", _labels(evidence, doc_id),
                "Câu hỏi nói tới nhiều văn bản nhưng câu trả lời chưa nói gì về {}{}.".format(
                    _labels(evidence, doc_id), _sources_of(evidence, doc_id))))

    # Source label lines copied as they are.
    if RAW_LABEL_RE.search(text):
        report.violations.append(Violation(
            "raw", "nhãn nguồn",
            "Câu trả lời chép nguyên các dòng nhãn của nguồn (chữ in hoa, số [n]); hãy trả lời bằng câu "
            "văn hoàn chỉnh, nêu rõ ai, chức vụ gì, văn bản nào."))
    return report


def _masked_dates_only(text: str) -> str:
    """The text without full dates and document numbers: what is left of
    "d/m" are days without a year."""
    text = ANSWER_NUMBER_RE.sub(" ", text or "")
    for pattern in _DATE_RES:
        text = pattern.sub(" ", text)
    return text


# ── What to do about it ───────────────────────────────────────────────

NOTE_HEADER = "KIỂM TRA TỰ ĐỘNG câu trả lời trên (đối chiếu với nguồn và thông tin văn bản trong kho):"
NOTE_FOOTER = (
    "Hãy viết lại toàn bộ câu trả lời cho câu hỏi của người dùng, sửa các điểm trên và giữ nguyên những "
    "phần đúng; chỉ dùng số ký hiệu, ngày tháng, số liệu có trong NGUỒN TRONG KHO và trích dẫn [n]. Không "
    "nhắc tới việc kiểm tra này."
)


def note(report: Report) -> str:
    lines = [NOTE_HEADER] + ["- " + violation.note for violation in report.violations] + [NOTE_FOOTER]
    return "\n".join(lines)


_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")


def hedge(answer: str, report: Report) -> Tuple[str, bool]:
    """The answer without what is still unsupported: a wrong type word is
    replaced by the document's type; a sentence with an unsupported number,
    date or figure goes when something else remains, else the item is
    replaced by "(chưa xác minh)". (answer, whether it changed)."""
    text = answer or ""
    changed = False
    for violation in report.violations:
        if violation.kind == "type" and len(violation.fix) == 3:
            type_word, written, kind = violation.fix
            kind = kind if type_word[:1].isupper() else kind.lower()
            replaced = re.sub(r"{}(?=[\s*_]*(?:số(?:\s+(?:ký\s+)?hiệu)?[\s:]*)?{})".format(
                re.escape(type_word), re.escape(written)), kind, text)
            changed = changed or replaced != text
            text = replaced
    # An item as a whole token: "114" is not the start of "1149" or "114/QĐ".
    items = [re.compile(r"(?<![\w/.,]){}(?![\w/]|[.,]\d)".format(re.escape(violation.item)))
             for violation in report.violations if violation.kind in ("number", "date", "figure", "count")]
    if not items:
        return text, changed
    lines = text.split("\n")
    kept_lines, removed = [], 0
    for line in lines:
        sentences = _SENTENCE_RE.split(line)
        kept = [sentence for sentence in sentences if not any(item.search(sentence) for item in items)]
        removed += len(sentences) - len(kept)
        if kept or not line.strip():
            kept_lines.append(" ".join(kept))
        # a line whose every sentence goes is dropped
    remaining = "\n".join(kept_lines).strip()
    if removed and re.search(r"\w{3,}", re.sub(r"\[\d+\]", "", remaining)):
        return re.sub(r"\n{3,}", "\n\n", remaining), True
    hedged = text
    for item in items:
        hedged = item.sub("(chưa xác minh)", hedged)
    return hedged, changed or hedged != text


def table_counts(catalog: Optional[DocumentCatalog], doc_ids: Iterable[int]) -> Dict[str, int]:
    """folded kind -> number of the table's documents of that kind."""
    counts = Counter()  # type: Counter
    if catalog is None:
        return {}
    for doc_id in doc_ids:
        entry = catalog.get(doc_id)
        if entry is not None and entry.kind:
            counts[fold_vietnamese(entry.kind.lower())] += 1
    return dict(counts)


def _folded(question: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", fold_vietnamese(question or "")).split())


def is_comparison(question: str) -> bool:
    return _COMPARE_RE.search(_folded(question)) is not None


def is_counting(question: str) -> bool:
    """'bao nhiêu văn bản', 'số lượng', 'có mấy'."""
    return _COUNTING_RE.search(_folded(question)) is not None


def is_listing(question: str) -> bool:
    """'liệt kê', 'thống kê toàn bộ', 'ký những văn bản nào'."""
    return _LISTING_RE.search(_folded(question)) is not None
