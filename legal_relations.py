"""Rule-based extraction of relations between Vietnamese legal documents.

Only references carrying a document number are returned. Named laws or legal
documents without a number cannot be resolved reliably and are intentionally
ignored.
"""

import re
from dataclasses import dataclass
from typing import List, Optional


SO_RE = re.compile(
    r"số\s+(\d[\w./ĐđÀ-ỹ-]*[/-][\w./ĐđÀ-ỹ-]+)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^;.\n]+")


@dataclass(frozen=True)
class Relation:
    relation_type: str
    target_so_ki_hieu: str
    evidence: str
    char_offset: int


def normalize_so_ki_hieu(raw: str) -> str:
    """Return a stable uppercase key for matching document numbers."""
    value = raw.strip().rstrip(".").strip().upper()
    return re.sub(r"\s*([/-])\s*", r"\1", value)


def _relation_type(sentence: str) -> Optional[str]:
    lowered = sentence.casefold()
    if lowered.startswith("căn cứ"):
        return "can_cu"
    if "thay thế" in lowered:
        return "thay_the"
    if "bãi bỏ" in lowered or "bai bỏ" in lowered:
        return "bai_bo"
    if "sửa đổi, bổ sung" in lowered or "sửa đổi" in lowered:
        return "sua_doi"
    return None


def extract_relations(text: str) -> List[Relation]:
    """Extract resolvable legal relations from plain text using fixed rules."""
    relations = []
    seen = set()

    for sentence_match in _SENTENCE_RE.finditer(text or ""):
        raw_sentence = sentence_match.group(0)
        evidence = raw_sentence.strip()
        if not evidence:
            continue

        relation_type = _relation_type(evidence)
        if relation_type is None:
            continue

        offset = sentence_match.start() + len(raw_sentence) - len(raw_sentence.lstrip())
        for number_match in SO_RE.finditer(evidence):
            target = normalize_so_ki_hieu(number_match.group(1))
            if not any(char.isdigit() for char in target) or not any(
                    separator in target for separator in ("/", "-")):
                continue
            key = (relation_type, target)
            if key in seen:
                continue
            seen.add(key)
            relations.append(Relation(relation_type, target, evidence, offset))

    return relations
