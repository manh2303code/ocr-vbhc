"""Questions answered from the documents' metadata, counted in code.

"thống kê toàn bộ các lãnh đạo phòng ban", "ai là chánh văn phòng",
"liệt kê các văn bản liên quan tới Hưng Yên, bao nhiêu văn bản", "ông ấy còn
ký văn bản nào khác": the local 8B model, given passages, answered the first
"Không tìm thấy thông tin", the second with three source label lines copied
as they were, the third with the right total and a made-up breakdown
("12 Kế hoạch, 10 Công văn" for 7 and 15). Every document's type, number,
date, issuer, signer and the signer's position are in document_versions, so
these questions are answered here: the documents are filtered and counted in
code and the result is a compact table, sent as the sources, that the model
only phrases.

A position is read the way the signature block of a Vietnamese
administrative document is written (Nghị định 30/2020/NĐ-CP): "KT." (ký
thay), "TL." (thừa lệnh), "TUQ." (thừa ủy quyền) and "TM."/"T/M" (thay mặt)
say in whose name the document is signed; the line without a prefix is the
signer's own position. "KT. CHÁNH VĂN PHÒNG / PHÓ CHÁNH VĂN PHÒNG / Nguyễn Văn
A" is the deputy chief of office signing for the chief, not the chief.

A question is taken as one of these only when every word of it is
accounted for (list and count words, document types, a year, an issuer, a
signer, a position, filler); one that also asks about content ("các văn bản
về Luật Thanh niên", "quyết định nào cử cán bộ đi học") goes to the search
as before.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from search_store import fold_vietnamese


# ── Text helpers ──────────────────────────────────────────────────────

# Spelled out or abbreviated in headers and questions; compared in one form.
_ABBREVIATIONS = (
    ("hoi dong nhan dan", "hdnd"),
    ("uy ban nhan dan", "ubnd"),
    ("dai bieu quoc hoi", "dbqh"),
    ("ktns", "kinh te ngan sach"),
    ("vhxh", "van hoa xa hoi"),
    ("ddbqh", "doan dbqh"),
)
_ACRONYMS = {
    "HĐND", "UBND", "ĐBQH", "ĐĐBQH", "ĐBBQH", "MTTQ", "UBMTTQ", "VN", "TU", "CQ", "CQVP", "UBKT",
    "KTNS", "VHXH", "VP", "BCH", "TW", "XIV", "XIII", "XV", "XVI", "XVII", "TP", "TX", "QH",
}
_PROPER = {"hưng yên": "Hưng Yên", "việt nam": "Việt Nam", "quốc hội": "Quốc hội", "nga": "Nga"}
_TYPES = ("cong van", "ke hoach", "to trinh", "thong bao", "quyet dinh", "bao cao", "chi thi",
          "nghi quyet", "ket luan", "chuong trinh", "huong dan", "thong tu", "nghi dinh", "quy che",
          "bien ban")
_CAPACITY_WORDS = {"kt": "ký thay", "tl": "thừa lệnh", "t/l": "thừa lệnh", "tm": "thay mặt",
                   "t/m": "thay mặt", "tuq": "thừa ủy quyền"}
_CAPACITY_RE = re.compile(r"^\s*(KT|TL|T/L|TM|T/M|TUQ)(?:\s*[.:]\s*|\s+)(.*)$", re.IGNORECASE)
_POSITION_WORDS = ("PHÓ", "CHỦ TỊCH", "TRƯỞNG", "CHÁNH", "BÍ THƯ", "GIÁM ĐỐC", "VĂN PHÒNG", "ỦY VIÊN")
# Titles a question may ask for ("ai là chánh văn phòng"), folded.
_TITLE_RE = re.compile(
    r"\b(?:pho )?(?:chanh van phong|chanh vp|chu tich(?: thuong truc)?|bi thu|truong ban|truong doan"
    r"|giam doc|truong phong|chu nhiem|tong thu ky|uy vien thuong truc)\b"
)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_PERSON_PRONOUN_RE = re.compile(r"\b(?:ong|ba|anh|chi|nguoi) (?:ay|do|nay)\b")

# Words of a question about metadata alone: collecting, counting, filler.
_FILLER = frozenset("""
cho toi minh em chung ta giup hay di nhe nha a ah vay thi the con oi xin vui long ban
trong kho he thong hien nay co la gi ai nao nhung cac moi tung tat ca toan bo het bao nhieu may
so luong tong cong dem thong ke liet ke danh sach ke ra xem biet van tai lieu loai theo phan nhom
gom duoc da dang se do ky hanh nguoi lanh dao phong can bo chuc vu danh giu dam nhiem ho
nhieu nhat it khac nua khong chua roi ve lien quan toi den tu cua thuoc tai o va voi hoac nam
ong ba anh chi ay nay kia tren hien day du chi tiet cu the ky phai hieu tinh huyen xa
""".split())
_LIST_WORDS_RE = re.compile(
    r"\b(?:liet ke|danh sach|thong ke|bao nhieu|so luong|tong so|co may|ke ra|tat ca|toan bo"
    r"|nhung (?:ai|nguoi|van ban)|cac (?:van ban|lanh dao|nguoi)|van ban nao|nhieu nhat|moi loai"
    r"|theo loai|nguoi nao|nhung lanh dao|ai ky)\b"
)
_PEOPLE_RE = re.compile(r"\b(?:lanh dao|nguoi ky|can bo|chuc vu|chuc danh|ai ky|nguoi nao ky)\b")


def fold(text: str) -> str:
    """Marks, case and punctuation gone, abbreviations in one form."""
    folded = " ".join(re.sub(r"[^\w\s]", " ", fold_vietnamese(text or "")).split())
    padded = " {} ".format(folded)
    for long_form, short_form in _ABBREVIATIONS:
        padded = padded.replace(" {} ".format(long_form), " {} ".format(short_form))
    return " ".join(padded.split())


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and " {} ".format(phrase) in " {} ".format(text)


def _lines(value: str) -> List[str]:
    """An OCR field's lines: notes in brackets, blank and repeated lines gone;
    lower-case scraps in a capitals line ("TM. THƯỜNG TRỰC HĐND ph") too."""
    lines = []  # type: List[str]
    for line in str(value or "").splitlines():
        line = " ".join(re.sub(r"\([^)]*\)", " ", line).split())
        words = line.split()
        if sum(1 for word in words if word.isupper()) >= 2:
            words = [word for word in words if not word.islower()]
        line = " ".join(words)
        if re.search(r"\w", line) and line not in lines:
            lines.append(line)
    return lines


def sentence_case(text: str) -> str:
    """'PHÓ CHÁNH VĂN PHÒNG' -> 'Phó chánh văn phòng'; acronyms and a few
    proper names keep their capitals."""
    words = []
    for index, word in enumerate(str(text or "").split()):
        core = word.strip(".,;:()-")
        if core.upper() in _ACRONYMS and core == core.upper():
            words.append(word)
        else:
            words.append(word.lower())
    result = " ".join(words)
    for lower, proper in _PROPER.items():
        result = re.sub(r"(?<!\w){}(?!\w)".format(re.escape(lower)), proper, result)
    return result[:1].upper() + result[1:] if result else result


def _name_case(name: str) -> str:
    if name and name == name.upper():
        return " ".join(word.capitalize() for word in name.split())
    return name


# ── One document's metadata ───────────────────────────────────────────

@dataclass(frozen=True)
class Position:
    own: str = ""  # the signer's own position, as written ("PHÓ CHÁNH VĂN PHÒNG")
    capacities: Tuple[Tuple[str, str], ...] = ()  # ("ký thay", "CHÁNH VĂN PHÒNG")

    @property
    def own_key(self) -> str:
        return fold(self.own).replace("chanh vp", "chanh van phong")

    def describe(self) -> str:
        """'Phó chánh văn phòng (ký thay Chánh văn phòng)'."""
        order = {"ký thay": 0, "thừa lệnh": 1, "thừa ủy quyền": 2, "thay mặt": 3}
        notes = ["{} {}".format(word, sentence_case(target))
                 for word, target in sorted(self.capacities, key=lambda item: order.get(item[0], 9))
                 if target]
        if self.own:
            return "{}{}".format(sentence_case(self.own), " ({})".format("; ".join(notes)) if notes else "")
        return "; ".join(notes)


def parse_position(chuc_vu: str, chu_ky: str = "") -> Tuple[Position, str]:
    """(position, signer's name) from the signature block fields. The
    signer's own position is the line above the name without a prefix; a
    body named above the prefixes ("ĐOÀN ĐẠI BIỂU QUỐC HỘI TỈNH / KT.
    TRƯỞNG ĐOÀN / PHÓ TRƯỞNG ĐOÀN") is signed for ("thay mặt"). A capitals
    line of chu_ky that reads as a position ("PHÓ VĂN PHÒNG") is one."""
    lines, names = [], []  # type: List[str], List[str]
    for line in _lines(chu_ky):
        if line == line.upper() and any(word in line for word in _POSITION_WORDS):
            lines.append(line)
        else:
            names.append(_name_case(line))
    lines = _lines(chuc_vu) + lines
    last_capacity = max((index for index, line in enumerate(lines) if _CAPACITY_RE.match(line)), default=-1)
    own, capacities = [], []  # type: List[str], List[Tuple[str, str]]
    for index, line in enumerate(lines):
        match = _CAPACITY_RE.match(line)
        if match:
            capacities.append((_CAPACITY_WORDS[match.group(1).lower()], match.group(2).strip()))
        elif index < last_capacity:
            capacities.append(("thay mặt", line))
        else:
            own.append(line)
    name = names[0] if names else ""
    return Position(" ".join(own).strip(), tuple(capacities)), name


@dataclass
class DocMeta:
    doc_id: int
    number: str
    kind: str
    issued: str
    agency: str  # for the reader: "Văn phòng, HĐND tỉnh Hưng Yên"
    cores: Tuple[str, ...]  # issuing bodies, folded, without the place
    places: Tuple[str, ...]  # "hung yen"
    summary: str
    signer: str
    signer_key: str
    position: Position

    @property
    def year(self) -> Optional[int]:
        match = _YEAR_RE.search(self.issued or "")
        return int(match.group(1)) if match else None

    @property
    def kind_key(self) -> str:
        return fold(self.kind)

    def label(self) -> str:
        return self.number or "{} (không số{})".format(
            self.kind or "Văn bản", ", ngày {}".format(self.issued) if self.issued else "")


def _units(top: str, bottom: str) -> List[str]:
    """The issuing bodies of a header, top first: 'HỘI ĐỒNG NHÂN DÂN' over
    'TỈNH HƯNG YÊN' is one body; 'HĐND TỈNH HƯNG YÊN' over 'VĂN PHÒNG' two."""
    upper, lower = _lines(top), _lines(bottom)
    units = list(upper)
    for line in lower:
        if units and re.match(r"(?i)^(tỉnh|thành phố|huyện|tp\.?)\b", line):
            units[-1] = "{} {}".format(units[-1], line)
        else:
            units.append(line)
    return units


def _core_and_place(unit: str) -> Tuple[str, str]:
    """('hdnd', 'hung yen') for 'HĐND TỈNH HƯNG YÊN'; ('tinh uy', 'hung yen')
    for 'TỈNH ỦY HƯNG YÊN'; ('dang doan hdnd', '') for 'ĐẢNG ĐOÀN HĐND TỈNH'."""
    folded = fold(unit)
    match = re.match(r"^tinh uy(?: (.+))?$", folded)
    if match:
        return "tinh uy", match.group(1) or ""
    match = re.match(r"^(.+?) (?:tinh|thanh pho|tp|huyen)(?: (.+))?$", folded)
    if match:
        return match.group(1), match.group(2) or ""
    return folded, ""


def _agency_display(units: List[str]) -> str:
    if not units:
        return ""
    if len(units) == 1:
        return sentence_case(units[0])
    return "{}, {}".format(sentence_case(" ".join(units[1:])), sentence_case(units[0]))


def build_index(rows: Iterable[Sequence]) -> List[DocMeta]:
    """rows: ToolExecutor._latest_versions() rows (id, file_name, so_ki_hieu,
    loai_vb, ngay_bh, cqbh_tren, cqbh_duoi, nd_chinh, chu_ky, chuc_vu)."""
    docs = []
    for row in rows:
        units = _units(row[5], row[6])
        cores, places = [], []
        for unit in units:
            core, place = _core_and_place(unit)
            if core and core not in cores:
                cores.append(core)
            if place and place not in places:
                places.append(place)
        position, name = parse_position(row[9] if len(row) > 9 else "", row[8] if len(row) > 8 else "")
        docs.append(DocMeta(
            doc_id=int(row[0]), number=str(row[2] or "").strip(), kind=" ".join(str(row[3] or "").split()),
            issued=" ".join(str(row[4] or "").split()), agency=_agency_display(units),
            cores=tuple(cores), places=tuple(places), summary=" ".join(str(row[7] or "").split()),
            signer=name, signer_key=fold(name), position=position,
        ))
    return docs


# ── People ────────────────────────────────────────────────────────────

@dataclass
class Person:
    key: str
    name: str
    docs: List[DocMeta] = field(default_factory=list)

    def roles(self) -> List[Tuple[str, str, List[DocMeta]]]:
        """(own position, agency of its first document, documents), one per
        position in the order first signed."""
        groups = {}  # type: Dict[str, List[DocMeta]]
        for doc in self.docs:
            groups.setdefault(doc.position.own_key, []).append(doc)
        return [(sentence_case(docs[0].position.own) if docs[0].position.own else "", docs[0].agency, docs)
                for docs in groups.values()]


def people_of(docs: Iterable[DocMeta]) -> List[Person]:
    """Signers, most documents first; the spelling with marks names them."""
    people = {}  # type: Dict[str, Person]
    spellings = {}  # type: Dict[str, Counter]
    for doc in docs:
        if not doc.signer_key:
            continue
        person = people.setdefault(doc.signer_key, Person(doc.signer_key, doc.signer))
        person.docs.append(doc)
        spellings.setdefault(doc.signer_key, Counter())[doc.signer] += 1
    for key, person in people.items():
        marked = [name for name, _count in spellings[key].most_common()
                  if fold_vietnamese(name) != name.lower()]
        person.name = marked[0] if marked else spellings[key].most_common(1)[0][0]
    return sorted(people.values(), key=lambda person: (-len(person.docs), person.docs[0].doc_id))


# ── The question ──────────────────────────────────────────────────────

@dataclass
class Aggregate:
    kind: str  # "people", "position", "person", "signer_documents", "documents"
    blocks: List[str]  # one per source
    sources: List[Tuple[int, str]]  # (doc_id, number) of each block
    docs: List[int]  # every document the table covers, in its order
    empty: bool = False  # nothing matched: "không có" is the right answer
    # The counts an answer may give as "the" number of documents (the
    # rows; for "còn … khác" also the person's whole count); [] for people.
    totals: List[int] = field(default_factory=list)
    # What a list must name, one entry per row: (label, ways to write it).
    items: List[Tuple[str, List[str]]] = field(default_factory=list)


@dataclass
class _Query:
    folded: str
    types: List[str]
    year: Optional[int]
    cores: List[str]
    places: List[str]
    signer: Optional[Person]
    title: str
    words_left: List[str]


def _spans(text: str, phrase: str) -> List[Tuple[int, int]]:
    return [(match.start(), match.end())
            for match in re.finditer(r"(?<!\S){}(?!\S)".format(re.escape(phrase)), text)] if phrase else []


def _outside(spans: List[Tuple[int, int]], taken: List[Tuple[int, int]]) -> bool:
    """Some occurrence lies outside every taken span."""
    return any(not any(start >= low and end <= high for low, high in taken) for start, end in spans)


def _matched_cores(folded: str, docs: Sequence[DocMeta], taken: List[Tuple[int, int]]) -> List[str]:
    """Issuing bodies the question names, the longest when one holds
    another ("đảng đoàn hđnd", not also "hđnd"), none inside a title or a
    name ("chánh văn phòng" names no "Văn phòng")."""
    found = sorted({core for doc in docs for core in doc.cores if _outside(_spans(folded, core), taken)},
                   key=lambda core: -len(core))
    kept = []  # type: List[str]
    for core in found:
        if not any(_has_phrase(longer, core) for longer in kept):
            kept.append(core)
    return kept


_TITLE_NAMES = {
    "chanh van phong": "chánh văn phòng", "chu tich": "chủ tịch", "thuong truc": "thường trực",
    "bi thu": "bí thư", "truong ban": "trưởng ban", "truong doan": "trưởng đoàn", "giam doc": "giám đốc",
    "truong phong": "trưởng phòng", "chu nhiem": "chủ nhiệm", "tong thu ky": "tổng thư ký",
    "uy vien": "ủy viên", "pho": "phó",
}
_TYPE_NAMES = dict(zip(_TYPES, ("Công văn", "Kế hoạch", "Tờ trình", "Thông báo", "Quyết định", "Báo cáo",
                                "Chỉ thị", "Nghị quyết", "Kết luận", "Chương trình", "Hướng dẫn", "Thông tư",
                                "Nghị định", "Quy chế", "Biên bản")))


def _title_name(title: str) -> str:
    """'pho chanh van phong' -> 'Phó chánh văn phòng'."""
    text = " {} ".format(title)
    for folded, marked in sorted(_TITLE_NAMES.items(), key=lambda item: -len(item[0])):
        text = text.replace(" {} ".format(folded), " {} ".format(marked))
    text = " ".join(text.split())
    return text[:1].upper() + text[1:]


def _parse(question: str, docs: Sequence[DocMeta], people: Sequence[Person]) -> _Query:
    folded = fold(question)
    types = [kind for kind in _TYPES if _has_phrase(folded, kind)]
    year = _YEAR_RE.search(folded)
    signer = next((person for person in sorted(people, key=lambda p: -len(p.key))
                   if _has_phrase(folded, person.key)), None)
    title, taken = "", []  # type: str, List[Tuple[int, int]]
    for match in _TITLE_RE.finditer(folded):
        if len(match.group()) > len(title):
            title, taken = match.group().replace("chanh vp", "chanh van phong"), [match.span()]
    if signer is not None:
        taken += _spans(folded, signer.key)
    cores = _matched_cores(folded, docs, taken)
    places = sorted({place for doc in docs for place in doc.places
                     if _outside(_spans(folded, place), taken)})
    accounted = set()
    for phrase in types + cores + places + ([signer.key] if signer else []) + ([title] if title else []):
        accounted.update(phrase.split())
    if year:
        accounted.add(year.group(1))
    left = [word for word in folded.split() if word not in accounted and word not in _FILLER]
    return _Query(folded, types, int(year.group(1)) if year else None, cores, places, signer, title, left)


def _type_matches(doc: DocMeta, kind: str) -> bool:
    return kind in doc.kind_key


def _filter(docs: Sequence[DocMeta], query: _Query, within: Optional[Sequence[int]] = None,
            use_signer: bool = True) -> List[DocMeta]:
    allowed = set(within) if within is not None else None
    result = []
    for doc in docs:
        if allowed is not None and doc.doc_id not in allowed:
            continue
        if query.types and not any(_type_matches(doc, kind) for kind in query.types):
            continue
        if query.year is not None and doc.year != query.year:
            continue
        if query.cores and not all(core in doc.cores for core in query.cores):
            continue
        if query.places and not all(place in doc.places for place in query.places):
            continue
        if use_signer and query.signer is not None and doc.signer_key != query.signer.key:
            continue
        result.append(doc)
    return result


def _doc_line(doc: DocMeta, with_signer: bool = False) -> str:
    parts = [doc.label(), doc.kind or "Không rõ loại", doc.issued, doc.agency]
    if with_signer and doc.signer:
        parts.append("Ký: {}{}".format(doc.signer, " ({})".format(doc.position.describe())
                                       if doc.position.describe() else ""))
    summary = doc.summary
    if len(summary) > 140:
        summary = summary[:139].rsplit(" ", 1)[0] + "…"
    if summary:
        parts.append(summary)
    return " · ".join(part for part in parts if part)


def _by_type(docs: Sequence[DocMeta]) -> str:
    counts = Counter(doc.kind or "Không rõ loại" for doc in docs)
    return ", ".join("{} {}".format(kind, count) for kind, count in
                     sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _conditions(query: _Query, docs: Sequence[DocMeta]) -> str:
    parts = []
    if query.types:
        parts.append("loại " + ", ".join(_TYPE_NAMES.get(kind, kind) for kind in query.types))
    if query.year is not None:
        parts.append("năm ban hành {}".format(query.year))
    if query.cores:
        sample = next((doc.agency for doc in docs), "")
        parts.append("cơ quan ban hành {}".format(sample or ", ".join(query.cores)))
    if query.places:
        parts.append("cơ quan ban hành ghi {}".format(", ".join(
            sentence_case(place) if place != "hung yen" else "Hưng Yên" for place in query.places)))
    if query.signer is not None:
        parts.append("người ký {}".format(query.signer.name))
    return "; ".join(parts)


_HEADER = "THỐNG KÊ TỪ THÔNG TIN VĂN BẢN (ứng dụng đếm trên toàn kho, chính xác)"
_PEOPLE_HEADER = (
    "NGƯỜI KÝ VÀ CHỨC VỤ (ứng dụng tổng hợp từ khối chữ ký của các văn bản trong kho, chính xác). "
    "\"Ký thay\" (KT.) là cấp phó ký thay cấp trưởng: người ký thay không giữ chức vụ của cấp trưởng; "
    "\"thừa lệnh\" (TL.), \"thay mặt\" (TM.) là ký nhân danh tập thể hoặc cấp trên."
)


def _person_block(person: Person, docs: Optional[Sequence[DocMeta]] = None) -> str:
    """'Nguyễn Văn A — ký 1 văn bản. Phó chánh văn phòng (Văn phòng, HĐND
    tỉnh Hưng Yên): 110/VP-TH (Công văn, 09/05/2019; ký thay Chánh văn
    phòng)'. A document of another body than the role's first names it."""
    docs = list(docs if docs is not None else person.docs)
    roles = []
    for own, agency, group in Person(person.key, person.name, docs).roles():
        role = own or "Chức vụ không ghi"
        listed = []
        for doc in group:
            notes = [doc.kind or "Không rõ loại", doc.issued]
            if doc.agency and doc.agency != agency:
                notes.append(doc.agency)
            capacity = Position("", doc.position.capacities).describe()
            listed.append("{} ({}{})".format(doc.label(), ", ".join(note for note in notes if note),
                                             "; " + capacity if capacity else ""))
        roles.append("{}{}: {}".format(role, " ({})".format(agency) if agency else "", "; ".join(listed)))
    return "{} — ký {} văn bản. {}".format(person.name, len(docs), " | ".join(roles))


def _people_table(people: Sequence[Person], header: str) -> Aggregate:
    blocks, sources, covered = [], [], []
    for index, person in enumerate(people):
        block = _person_block(person)
        blocks.append("{}\n{}".format(header, block) if index == 0 else block)
        sources.append((person.docs[0].doc_id, person.docs[0].number))
        covered.extend(doc.doc_id for doc in person.docs)
    return Aggregate("people", blocks, sources, covered,
                     items=[(person.name, [person.name]) for person in people])


def _documents_table(found: Sequence[DocMeta], header: str, kind: str = "documents",
                     with_signer: bool = False) -> Aggregate:
    blocks = [_doc_line(doc, with_signer) for doc in found]
    blocks[0] = "{}\n{}".format(header, blocks[0])
    return Aggregate(kind, blocks, [(doc.doc_id, doc.number) for doc in found],
                     [doc.doc_id for doc in found], totals=[len(found)],
                     items=[(doc.number, [doc.number]) for doc in found if doc.number])


def _empty(header: str, note: str, kind: str) -> Aggregate:
    return Aggregate(kind, ["{}\n{}".format(header, note)], [], [], empty=True)


def _mentioned_people(text: str, people: Sequence[Person]) -> List[Person]:
    """The signers a text names, in the order it first names them."""
    folded = " {} ".format(fold(text))
    found = [(folded.find(" {} ".format(person.key)), person) for person in people]
    return [person for position, person in sorted(found, key=lambda item: item[0]) if position >= 0]


def _person_from_conversation(conversation, people: Sequence[Person]) -> Optional[Person]:
    """'ông ấy', 'bà ấy': the signer the last answer names first. When it
    names no signer the person is someone in a document's text ("đồng chí
    Nguyễn Văn B được cử đi học"), not a signer: None."""
    if conversation is None:
        return None
    turns = [turn for turn in getattr(conversation, "turns", []) or []
             if getattr(turn, "status", "") not in ("conversation", "meta")]
    if not turns:
        return None
    last = turns[-1]
    # "Ai ký nhiều nhất?" -> "Nguyễn Văn A (11), Trần Thị B (3)…":
    # "ông ấy" is the one the answer names first.
    named = _mentioned_people("{} {}".format(last.answer or "", last.question or ""), people)
    return named[0] if named else None


def _last_turn_documents(conversation) -> List[int]:
    turns = [turn for turn in getattr(conversation, "turns", []) or []
             if getattr(turn, "status", "") not in ("conversation", "meta")] if conversation else []
    if not turns:
        return []
    return list(dict.fromkeys(list(turns[-1].docs) + list(turns[-1].cited)))


# "Văn bản này do ai ký?", "kế hoạch đó": one document the conversation
# points at, not the store (lowercase text with marks: "do" is also "by").
_THAT_DOCUMENT_RE = re.compile(
    r"\b(?:văn bản|tài liệu|kế hoạch|quyết định|tờ trình|thông báo|công văn|báo cáo|chỉ thị|nghị quyết)"
    r"\s+(?:này|đó|ấy|kia|trên|nói trên|vừa nói)(?=$|[\s,.?!;:])")


def answer(question: str, rows: Iterable[Sequence], conversation=None,
           within: Optional[Sequence[int]] = None) -> Optional[Aggregate]:
    """The table that answers a question about the documents' metadata, or
    None when the question is not one (or asks about content as well).
    within: the documents the conversation pointed at ("các văn bản đó"),
    counted instead of the store."""
    if within is None and _THAT_DOCUMENT_RE.search((question or "").lower()):
        return None
    docs = build_index(rows)
    if not docs:
        return None
    people = people_of(docs)
    query = _parse(question, docs, people)
    folded = query.folded
    pronoun = _PERSON_PRONOUN_RE.search(folded) is not None
    if pronoun:
        query.words_left = [word for word in query.words_left if word not in ("ay", "do", "nay")]
    if query.words_left:
        return None
    asks_list = _LIST_WORDS_RE.search(folded) is not None
    asks_people = _PEOPLE_RE.search(folded) is not None
    signs = re.search(r"\bky\b", folded) is not None
    other = re.search(r"\bkhac\b", folded) is not None

    person = query.signer
    if person is None and pronoun:
        person = _person_from_conversation(conversation, people)
        if person is None:
            return None

    # Documents a person signed: "ông ấy còn ký văn bản nào khác", "Nguyễn Văn
    # A ký những văn bản nào", "ông ấy ký bao nhiêu công văn năm 2020".
    if person is not None and signs and (asks_list or re.search(r"\bvan ban\b|\bgi\b", folded)
                                         or query.types):
        query.signer = person
        found = _filter(docs, query, within)
        header = "{}. KẾT QUẢ: {} ký {} văn bản{}{}.".format(
            _HEADER, person.name, len(found), " có {}".format(_conditions(query, found).replace(
                "người ký {}".format(person.name), "").strip("; ")) if (query.types or query.year
                                                                          or query.cores) else "",
            " — theo loại: {}".format(_by_type(found)) if found else "")
        if other:
            before = set(_last_turn_documents(conversation))
            rest = [doc for doc in found if doc.doc_id not in before]
            if not rest:
                shown = [doc for doc in found if doc.doc_id in before]
                note = "Ngoài {} đã nói ở lượt trước, trong kho không có văn bản nào khác do {} ký.".format(
                    ", ".join(doc.label() for doc in shown) or "văn bản đã nói", person.name)
                if not shown:
                    return _empty(header, note, "signer_documents")
                table = _documents_table(shown, "{}\n{}".format(header, note), "signer_documents")
                table.empty = True
                return table
            header += " Ngoài văn bản đã nói ở lượt trước còn {} văn bản.".format(len(rest))
            table = _documents_table(rest, header, "signer_documents")
            table.totals = [len(rest), len(found)]
            return table
        if not found:
            return _empty(header, "Không có văn bản nào khớp.", "signer_documents")
        return _documents_table(found, header, "signer_documents")

    # One person's position: "Nguyễn Văn A giữ chức vụ gì", "Trần Văn B có
    # phải là chánh văn phòng không", "ông ấy giữ chức vụ gì". Not "bà ấy
    # còn có tên trong văn bản nào": a name in a text is not a signature.
    if person is not None:
        if not (query.title or re.search(r"\b(?:chuc vu|chuc danh|giu|dam nhiem|co phai)\b", folded)):
            return None
        note = ""
        if query.title:
            owns = [doc for doc in person.docs if doc.position.own_key.startswith(query.title)]
            if owns:
                note = "{} giữ chức vụ {} (khi ký {}).".format(
                    person.name, sentence_case(owns[0].position.own), ", ".join(doc.label() for doc in owns))
            else:
                held = "; ".join(sorted({doc.position.describe() for doc in person.docs
                                         if doc.position.describe()}))
                note = "{} không giữ chức vụ {} trong các văn bản trong kho; chức vụ khi ký: {}.".format(
                    person.name, _title_name(query.title), held or "không ghi")
        header = "{}\n{}".format(_PEOPLE_HEADER, note) if note else _PEOPLE_HEADER
        table = _people_table([person], header)
        table.kind = "person"
        return table

    # Who holds a position: "ai là chánh văn phòng", "Trưởng Ban Kinh tế
    # Ngân sách là ai", "Phó Chủ tịch UBND tỉnh nào ký văn bản trong kho".
    if query.title:
        if conversation is not None and len(folded.split()) <= 5 and not query.cores:
            # "Trưởng đoàn là ai?" right after a question about a document
            # asks about that document's text, not about the signers.
            turns = [turn for turn in getattr(conversation, "turns", []) or []
                     if getattr(turn, "status", "") not in ("conversation", "meta")]
            if turns and turns[-1].docs and answer(turns[-1].question, rows) is None:
                return None
        scoped = _filter(docs, query, within, use_signer=False)
        holders = people_of(doc for doc in scoped if doc.position.own_key.startswith(query.title))
        deputies = people_of(
            doc for doc in scoped
            if not doc.position.own_key.startswith(query.title)
            and any(fold(target).replace("chanh vp", "chanh van phong").startswith(query.title)
                    for word, target in doc.position.capacities if word == "ký thay"))
        title = _title_name(query.title)
        if not holders:
            if not (deputies and query.cores):
                return None
            # "Ai là Chủ tịch UBND tỉnh?": no Chủ tịch signed anything in the
            # store, a Phó Chủ tịch signed for one; say so instead of letting
            # the model name someone.
            note = ("{}\nChức vụ được hỏi: {} ({}). Không có văn bản nào trong kho do người giữ chức vụ "
                    "này ký, nên kho không cho biết ai giữ chức vụ này. Ký thay {} (cấp phó, không phải "
                    "{}): {}.").format(
                _PEOPLE_HEADER, title, scoped[0].agency if scoped else "", title, title,
                ", ".join("{} ({})".format(person.name, sentence_case(person.docs[0].position.own))
                          for person in deputies))
            table = _people_table(deputies, note)
            table.kind, table.empty = "position", True
            return table
        lines =["{}\nChức vụ được hỏi: {}. Người giữ chức vụ này khi ký văn bản: {}.".format(
            _PEOPLE_HEADER, title, ", ".join(person.name for person in holders))]
        if deputies:
            lines.append("Ký thay {} (là cấp phó, không phải {}): {}.".format(
                title, title, ", ".join("{} ({})".format(person.name, sentence_case(person.docs[0].position.own))
                                        for person in deputies)))
        blocks, sources, covered = [], [], []
        for index, person in enumerate(holders + deputies):
            block = _person_block(person)
            blocks.append("{}\n{}".format("\n".join(lines), block) if index == 0 else block)
            sources.append((person.docs[0].doc_id, person.docs[0].number))
            covered.extend(doc.doc_id for doc in person.docs)
        return Aggregate("position", blocks, sources, covered)

    # Everyone who signed: "thống kê các lãnh đạo", "ai ký nhiều văn bản
    # nhất", "có bao nhiêu người ký".
    if asks_people and not query.types and query.year is None and not query.cores:
        signed = [doc for doc in docs if doc.signer_key]
        if within is not None:
            signed = [doc for doc in signed if doc.doc_id in set(within)]
        everyone = people_of(signed)
        if not everyone:
            return None
        top = everyone[0]
        header = "{}\nTổng cộng {} người ký {} văn bản. Ký nhiều nhất: {} ({} văn bản).".format(
            _PEOPLE_HEADER, len(everyone), len(signed), top.name, len(top.docs))
        return _people_table(everyone, header)

    # Documents by type, year, issuer, place: lists and counts.
    if asks_list or query.types or query.year is not None or query.cores or query.places:
        if not (asks_list or re.search(r"\bnao\b|\bnhung\b|\bcac\b", folded)):
            return None
        found = _filter(docs, query, within)
        # The answer's number first: "Trong 26 văn bản của kho, …: 24 văn
        # bản" was answered "26 văn bản".
        scope = "kho có tổng cộng {} văn bản".format(len(docs)) if within is None else \
            "trong số {} văn bản của lượt trước".format(len(set(within)))
        conditions = _conditions(query, found)
        header = "{}. KẾT QUẢ: {} văn bản{}{} ({}).".format(
            _HEADER, len(found), " có {}".format(conditions) if conditions else "",
            " — theo loại: {}".format(_by_type(found)) if found else "", scope)
        if re.search(r"\b(?:moi|tung|theo) nam\b", folded) and found:
            years = Counter(str(doc.year) if doc.year else "không rõ năm" for doc in found)
            header += " Theo năm: {}.".format(", ".join("{} {}".format(year, count)
                                                        for year, count in sorted(years.items())))
        if not found:
            return _empty(header, "Không có văn bản nào khớp điều kiện.", "documents")
        return _documents_table(found, header)
    return None
