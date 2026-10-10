"""
Parse Chandra OCR 2 HTML output into 12 NĐ30 document fields.

Chandra outputs structured HTML with data-bbox/data-label attributes.
This parser extracts fields by position, keywords, and label type.
No YOLO or Vintern dependency — Chandra-only pipeline.
"""
import re
import logging
from typing import Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("OCRApp.Parser")

# ─── Constants ────────────────────────────────────────────────────

LABEL_NORMALIZE = {"List": "List-Group", "Footnote": "Text"}
NOISE_LABELS = frozenset({"Page-Footer"})
STAMP_KEYWORDS = frozenset({"ĐẾN", "ĐEN", "Chuyển đ/c", "Chuyển d/c", "Lưu hồ sơ"})

QUOC_HIEU_KEYWORDS = frozenset({
    "CỘNG HÒA", "CỘNG HOÀ", "CỘNG HÓA",  # Chandra diacritics variants
    "ĐẢNG CỘNG SẢN", "ĐẢNG CỘNG SẺN",      # Đảng variant
})

LOAI_VB_KEYWORDS = {
    "QUYẾT ĐỊNH": "Quyết định",
    "QUYET DINH": "Quyết định",
    "THÔNG BÁO": "Thông báo",
    "CHỈ THỊ": "Chỉ thị",
    "TỜ TRÌNH": "Tờ trình",
    "TO TRINH": "Tờ trình",
    "KẾ HOẠCH": "Kế hoạch",
    "BÁO CÁO": "Báo cáo",
    "CÔNG VĂN": "Công văn",
    "NGHỊ QUYẾT": "Nghị quyết",
}

LOAI_VB_PREFIX_MAP = {
    "QĐ": "Quyết định", "QD": "Quyết định",
    "CV": "Công văn", "VP": "Công văn",
    "TB": "Thông báo",
    "CT": "Chỉ thị",
    "TTr": "Tờ trình", "TTR": "Tờ trình",
    "KH": "Kế hoạch",
    "BC": "Báo cáo",
    "NQ": "Nghị quyết",
    "TT": "Thông tư",
    "HD": "Hướng dẫn",
    "CĐ": "Công điện", "CD": "Công điện",
    "GM": "Giấy mời",
    "PC": "Phiếu chuyển",
    "BB": "Biên bản",
    "GCN": "Giấy chứng nhận",
}


# ─── Low-level Utilities ─────────────────────────────────────────


def _get_bbox(div: Tag) -> Optional[tuple]:
    """Extract bbox from 3 known attribute formats."""
    raw = div.get("data-bbox") or div.get("data") or ""
    if not raw or not raw.strip():
        return None
    parts = raw.strip().split()
    nums = []
    for p in parts:
        cleaned = p.replace(".", "").replace("-", "")
        if cleaned.isdigit():
            nums.append(int(p))
    if len(nums) >= 4:
        return (nums[0], nums[1], nums[2], nums[3])
    elif len(nums) == 3:
        return (nums[0], nums[1], nums[2], nums[1] + 30)
    return None


def _get_label(div: Tag) -> str:
    """Get data-label, normalize variants."""
    label = div.get("data-label", "Text")
    return LABEL_NORMALIZE.get(label, label)


def _is_noise(div: Tag) -> bool:
    """Skip: Page-Footer, empty divs, stamp forms. Keep Image divs (may have stamp alt text)."""
    label = _get_label(div)
    if label in NOISE_LABELS:
        return True
    # Image divs: keep them — stamp detection reads img alt text
    if label == "Image":
        return False
    text = div.get_text(strip=True)
    if not text:
        return True
    if label == "Form":
        return any(kw in text for kw in STAMP_KEYWORDS)
    return False


def _is_left_half(bbox: tuple, page_width: int = 1244) -> bool:
    """Check if bbox center x is in left half of page."""
    x1, _, x2, _ = bbox
    center_x = (x1 + x2) / 2
    return center_x < page_width / 2


def _split_merged_header(div: Tag) -> tuple:
    """If CQBH + quốc hiệu merged in 1 div, split by detecting quốc hiệu keywords.

    Returns:
        (cqbh_text, quoc_hieu_text) — quoc_hieu_text is "" if not merged.

    2026-04-18: Use _div_text() helpers so inline tags (<b>, <u>, <sup>)
    inside CQBH don't fragment it across extra newlines. Previously used
    `get_text(separator="\\n", strip=True)` which injected newlines between
    every text node — e.g. "<b>HĐND</b> TỈNH HƯNG YÊN" became
    "HĐND\\nTỈNH\\nHƯNG YÊN" (3 lines instead of 1).
    """
    full_text = _div_text(div)

    # Check if quốc hiệu keywords are present
    has_quoc_hieu = any(kw in full_text for kw in QUOC_HIEU_KEYWORDS)
    if not has_quoc_hieu:
        return (full_text, "")

    # Try to split by <p> tags (each <p> may be a separate block)
    p_tags = div.find_all("p")
    if len(p_tags) >= 2:
        for i, p in enumerate(p_tags):
            p_text = _div_text(p)
            if any(kw in p_text for kw in QUOC_HIEU_KEYWORDS):
                cqbh_parts = [_div_text(p_tags[j])
                              for j in range(len(p_tags)) if j != i]
                return ("\n".join(cqbh_parts), p_text)

    # Fallback: split by line, detect quốc hiệu within each line
    lines = full_text.split("\n")
    cqbh_lines = []
    quoc_hieu_lines = []
    found_quoc_hieu = False
    for line in lines:
        if not found_quoc_hieu:
            # Check if quốc hiệu starts MID-LINE (e.g., "CÔNG ĐOÀN VIÊN CHỨC TỈNH CỘNG HÒA XÃ HỘI...")
            for kw in QUOC_HIEU_KEYWORDS:
                idx = line.find(kw)
                if idx > 5:  # keyword not at start — split mid-line
                    cqbh_lines.append(line[:idx].strip())
                    quoc_hieu_lines.append(line[idx:].strip())
                    found_quoc_hieu = True
                    break
                elif idx >= 0:  # keyword at start — whole line is quốc hiệu
                    quoc_hieu_lines.append(line)
                    found_quoc_hieu = True
                    break
            else:
                cqbh_lines.append(line)
        elif found_quoc_hieu:
            # After quốc hiệu found, classify remaining lines
            is_quoc_hieu_part = any(kw in line for kw in ["Độc lập", "Doc lập", "Tự do", "Hạnh phúc"])
            if is_quoc_hieu_part:
                quoc_hieu_lines.append(line)
            else:
                cqbh_lines.append(line)

    return ("\n".join(cqbh_lines), "\n".join(quoc_hieu_lines))


def _get_divs(html: str) -> list:
    """Parse HTML and return non-noise div elements."""
    soup = BeautifulSoup(html, "html.parser")
    return [div for div in soup.find_all("div") if not _is_noise(div)]


def _div_text(div: Tag) -> str:
    """Get clean text from div, preserving ONLY intentional <br/> line breaks.

    Previous implementation used `separator="\\n"` which injected newlines
    between every text node — including across inline tags like <u>, <b>,
    <i>, <sup>. That broke parsers expecting `Số: 22 /BC-HĐND` to be on
    one line (doc 01.01.52.438.14.pdf: `Số: <u>22</u> /BC-HĐND` became
    "Số:\n22\n/BC-HĐND" and our newline-split so_line heuristic captured
    only "Số:" — with no digit to extract).

    Fix (2026-04-18 H14): serialize the div, replace <br/> with \\n
    textually, then re-parse and get_text without auto-separator. This
    preserves intentional line breaks (V/v, Về việc cases) while keeping
    decorative inline tags flat.
    """
    html = str(div)
    # Replace <br>, <br/>, <br /> (any case, any spacing) with newline.
    html = re.sub(r"<br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    flat_soup = BeautifulSoup(html, "html.parser")
    text = flat_soup.get_text(strip=False)
    # Collapse only horizontal whitespace (tabs, multiple spaces) — keep \n.
    text = re.sub(r"[ \t]+", " ", text)
    # Trim leading/trailing whitespace per line, strip overall.
    text = "\n".join(ln.strip() for ln in text.splitlines()).strip()
    return text


def _div_has_bold(div: Tag) -> bool:
    """Check if div contains bold text (<b> or <strong>)."""
    return bool(div.find(["b", "strong"]))


# ─── Header Field Extraction (Page 1) ────────────────────────────


def _extract_header_fields(html: str, page_width: int = 1244) -> dict:
    """Extract header fields from page 1 HTML."""
    fields = {
        "CQBH_tren": "",
        "CQBH_duoi": "",
        "So_Ki_Hieu": "",
        "Ngay_BH": "",
        "Loai_VB": "",
        "ND_Chinh": "",
        "Do_Khan": "",
        "Do_Mat": "",
    }

    divs = _get_divs(html)
    if not divs:
        logger.warning("No divs found in page 1 HTML")
        return fields

    logger.info(f"[HEADER] Parsing page 1: {len(divs)} content divs, page_width={page_width}, html_len={len(html)}")
    # Log full HTML for debugging (truncate at 3000 chars)

    # ── CQBH (first content div — check merged header, then left-half) ──
    for div in divs:
        label = _get_label(div)
        bbox = _get_bbox(div)
        text = _div_text(div)

        # Skip page numbers and very short noise (but keep short CQBH like "BỘ NỘI VỤ")
        if not text or text.strip().isdigit():
            continue
        if label == "Page-Header" and len(text) < 5:
            continue

        # Check if this div is a merged header (CQBH + quốc hiệu in one div)
        cqbh, quoc_hieu = _split_merged_header(div)
        if quoc_hieu:
            # Merged header found — use split result
            lines = cqbh.split("\n")
            fields["CQBH_tren"] = lines[0].strip() if lines else ""
            fields["CQBH_duoi"] = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            break

        # Not merged — check if left half (normal CQBH position)
        if bbox and _is_left_half(bbox, page_width):
            lines = text.split("\n")
            fields["CQBH_tren"] = lines[0].strip() if lines else ""
            fields["CQBH_duoi"] = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            break

    # ── Số ký hiệu (position-constrained to HEADER zone) ──
    # Per NĐ 30/2020 Phụ lục I, số ký hiệu is in Ô 4 — left side of header block,
    # above the body. Body may cite OTHER docs (e.g. "Phúc đáp Công văn số X/Y-Z")
    # which we must NOT match as our own số ký hiệu. Limit to top ~250px y-zone.
    # Also: header-div "Số:" should start with digit/slash/dash (not a citation
    # preceded by "Công văn số", "của Sở" etc.).
    # Allow letter+digit mix at start (handles OCR errors like "113"→"M3").
    # Post-filter requires the token to contain at least 1 digit AND a slash
    # or dash (admin doc format). Position constraint keeps out body citations.
    so_re = re.compile(r"Số[:\s]*\s*([\w/\-ĐđÀ-ỹ]+(?:[\s/\-][\w/\-ĐđÀ-ỹ]+)*)", re.IGNORECASE)
    HEADER_Y_MAX = 250  # Number + date zone is in top 250px of page 1
    BODY_CITATION_CUES = ("Công văn số", "Công văn Số", "Quyết định số", "Tờ trình số",
                          "Thông báo số", "Báo cáo số", "của Sở", "của Bộ", "phúc đáp")
    # Edge case 2026-04-18 (doc 01.01.78.529.27.pdf): Chandra sometimes
    # merges "Số: X/Y-Z" and the trích yếu "Về việc ..." into a single
    # <div><p>line1<br/>line2</p></div>. Previous regex collapsed all
    # whitespace including newlines, then the greedy capture group
    # "([\w/\-...]+(?:[\s/\-][\w/\-...]+)*)" extended the match across
    # the former newline, producing "64/VP-HCTCQT Về việc đề nghị...".
    # Fix: split on newline first and match so_re ONLY on the line that
    # contains "Số:". Also capture the "Về việc" line as a ND chính
    # candidate (stored for later — used if ND chính is otherwise empty).
    ve_viec_re = re.compile(r"^\s*(?:V/v|Về\s+việc|V[/\.]v)\s*:?\s*(.+)$",
                            re.IGNORECASE)

    for div in divs:
        bbox = _get_bbox(div)
        if bbox and bbox[1] > HEADER_Y_MAX:
            continue
        text = _div_text(div)
        if any(cue in text for cue in BODY_CITATION_CUES):
            continue

        # Split on all newline flavors (\n, \r\n, <br/> already converted
        # to \n by _div_text). Keep only non-empty stripped lines.
        raw_lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]

        # Find the line with "Số:" — match regex only on that line.
        so_line = None
        ve_viec_line = None
        for line in raw_lines:
            if so_line is None and re.search(r"S[ốo0][:\s]", line, re.IGNORECASE):
                so_line = line
            vm = ve_viec_re.match(line)
            if vm and ve_viec_line is None:
                ve_viec_line = vm.group(1).strip()

        # Fall back to full collapsed text when no "Số:" line found
        # (handles layouts where "Số:" spans multiple divs or is missing).
        text_for_match = so_line if so_line else re.sub(r"\s+", " ", text).strip()

        # Edge case H13 (2026-04-18 Báo cáo 01.01.52.438.13.pdf): OCR
        # picks up form-blank dotted line pre-printed as "Số: .....  /BC-HĐND".
        # After user writes "291" into the blank, OCR reads it as
        # "Số: 291...../BC-HĐND". The \w regex stops at the first dot
        # (captures only "291") and fails the has_sep validation. Fix:
        # strip runs of 2+ dots before regex match — safely preserves
        # real decimals (e.g., "1.5") which use single dots.
        text_for_match = re.sub(r"\.{2,}", "", text_for_match)

        m = so_re.search(text_for_match)
        if m:
            raw = m.group(1).strip()
            raw = raw.rstrip(".")
            # Clean up separators around / and - (any whitespace, zero or more).
            # Handles "BC - KTNS", "BC- KTNS", "BC -KTNS" all → "BC-KTNS".
            # Also "20 /BC" → "20/BC".
            raw = re.sub(r"\s*/\s*", "/", raw)
            raw = re.sub(r"\s*-\s*", "-", raw)
            raw = re.sub(r"\s+", " ", raw).strip()
            has_digit = any(ch.isdigit() for ch in raw)
            has_sep = "/" in raw or "-" in raw
            if has_digit and has_sep:
                fields["So_Ki_Hieu"] = raw
                if ve_viec_line:
                    fields["_ND_Chinh_header_hint"] = ve_viec_line
                break

    # ── Ngày ban hành ──
    ngay_re = re.compile(
        r"(?:ngày\s*)?(\d{1,2})\s*(?:tháng|[./])\s*(\d{1,2})\s*(?:năm|[./])\s*(\d{4})",
        re.IGNORECASE,
    )
    for div in divs:
        text = _div_text(div)
        bbox = _get_bbox(div)
        # Date is usually on the right side
        if bbox and not _is_left_half(bbox, page_width):
            m = ngay_re.search(text)
            if m:
                d, mo, y = m.groups()
                fields["Ngay_BH"] = f"{int(d):02d}/{int(mo):02d}/{y}"
                break
    # Fallback: search all divs if not found on right side
    if not fields["Ngay_BH"]:
        for div in divs:
            m = ngay_re.search(_div_text(div))
            if m:
                d, mo, y = m.groups()
                fields["Ngay_BH"] = f"{int(d):02d}/{int(mo):02d}/{y}"
                break

    # ── Loại VB (Section-Header keyword) ──
    # Edge case H15 (2026-04-18 doc 01.01.52.440.04.pdf): when a Section-Header
    # contains MULTIPLE keywords (e.g. "BÁO CÁO HOẠT ĐỘNG...VÀ KẾ HOẠCH HOẠT
    # ĐỘNG THÁNG 02/2017"), previous impl picked whichever keyword came first
    # in LOAI_VB_KEYWORDS dict insertion order (Kế hoạch) rather than the one
    # appearing first in the TITLE TEXT (Báo cáo at position 0). Fix: scan all
    # keywords, pick the match with earliest position.
    loai_vb_div_idx = -1
    for i, div in enumerate(divs):
        label = _get_label(div)
        if label == "Section-Header":
            text = _div_text(div).upper()
            best_pos = len(text) + 1
            best_name = None
            for keyword, vb_name in LOAI_VB_KEYWORDS.items():
                pos = text.find(keyword)
                if pos != -1 and pos < best_pos:
                    best_pos = pos
                    best_name = vb_name
            if best_name:
                fields["Loai_VB"] = best_name
                loai_vb_div_idx = i
                break
        if fields["Loai_VB"]:
            break

    # Fallback: infer from số ký hiệu prefix
    if not fields["Loai_VB"] and fields["So_Ki_Hieu"]:
        fields["Loai_VB"] = _infer_loai_vb_from_prefix(fields["So_Ki_Hieu"])

    # ── Nội dung chính ──
    # Strategy 1: "V/v" in any div
    for div in divs:
        text = _div_text(div)
        if "V/v" in text or "v/v" in text:
            # Extract everything after "V/v"
            vv_match = re.search(r"[Vv]/[Vv]\s*(.*)", text, re.DOTALL)
            if vv_match:
                fields["ND_Chinh"] = vv_match.group(1).strip()
                break

    # Strategy 2: div immediately after loại VB
    if not fields["ND_Chinh"] and loai_vb_div_idx >= 0:
        # Check if nd_chinh is in the SAME div (after <br/>)
        loai_div = divs[loai_vb_div_idx]
        loai_text = _div_text(loai_div)
        lines = loai_text.split("\n")
        # First line is loại VB name, rest might be nd_chinh
        if len(lines) > 1:
            nd_lines = [l for l in lines[1:] if l.strip()]
            if nd_lines:
                fields["ND_Chinh"] = "\n".join(nd_lines)

        # Check next div
        if not fields["ND_Chinh"] and loai_vb_div_idx + 1 < len(divs):
            next_div = divs[loai_vb_div_idx + 1]
            next_label = _get_label(next_div)
            if next_label in ("Text", "Section-Header"):
                fields["ND_Chinh"] = _div_text(next_div)

    # Strategy 3 (2026-04-18 edge case): header "Về việc ..." line stashed
    # during số ký hiệu extraction. This covers Công văn docs where Chandra
    # merged "Số: ..." and the trích yếu into a single <div>. See doc
    # 01.01.78.529.27.pdf — "Số: 64/VP-HCTCQT<br/>Về việc đề nghị thẩm định".
    if not fields["ND_Chinh"] and fields.get("_ND_Chinh_header_hint"):
        fields["ND_Chinh"] = fields["_ND_Chinh_header_hint"]

    # Clean up internal hint key so it doesn't leak out of the header dict.
    fields.pop("_ND_Chinh_header_hint", None)

    # ── Độ khẩn + Độ mật — POSITION-BASED detection (NĐ 30/2020) ──
    # Per NĐ 30 Phụ lục I:
    #   Ô 9b (Độ khẩn): góc TRÁI, DƯỚI số ký hiệu, TRÊN loại VB
    #   Ô 10a (Độ mật): góc TRÁI, DƯỚI độ khẩn
    # Stamps are small rectangular red ink: "KHẨN", "TỐI MẬT", etc.
    # Strategy: find short divs in LEFT half, BELOW CQBH line, ABOVE loại VB line
    # with matching keywords. NOT full-text search (avoids false positives).

    # Determine vertical zone: between số KH (y) and loại VB (y)
    so_kh_y = 0
    loai_vb_y = page_width  # default to bottom
    for div in divs:
        text = _div_text(div)
        bbox = _get_bbox(div)
        if not bbox:
            continue
        if re.search(r"Số[:\s]", text):
            so_kh_y = bbox[1]  # y1 of số ký hiệu
        if _get_label(div) == "Section-Header" and any(
            kw in text.upper() for kw in LOAI_VB_KEYWORDS
        ):
            loai_vb_y = bbox[1]
            break

    # Search for stamps in the zone: y between so_kh_y and loai_vb_y+50, left half
    # Also check divs slightly above/below (stamps can be adjacent to other elements)
    stamp_zone_y_min = max(0, so_kh_y - 20)
    stamp_zone_y_max = loai_vb_y + 80 if loai_vb_y < page_width else 400

    # ── Stamp detection: UPPERCASE only (stamps are always IN HOA) ──
    # Canonical values → QComboBox items:
    #   Do_Khan: "Hỏa Tốc", "Thượng Khẩn", "Khẩn"
    #   Do_Mat:  "Mật", "Tối Mật", "Tuyệt Mật"
    #
    # Red ink stamps cause OCR errors: wrong diacritics, missing chars.
    # Comprehensive patterns from research (NĐ 30/2020 + TT 24/2020/TT-BCA).
    # Longest-match-first prevents "KHẨN" matching inside "THƯỢNG KHẨN".

    # ALL patterns are UPPERCASE (stamps are always in hoa).
    # Also include patterns found in Image alt-text (Chandra describes
    # stamps as "Red ... stamp with text 'HỎA TỐC'" — text in quotes).
    # Alt text may have: HÒA TỐC (wrong diacritic), HOA TOC (ASCII), etc.

    DO_KHAN_PATTERNS = {
        "Hỏa Tốc": [
            # Direct stamp text (uppercase, various OCR errors on red ink)
            "HỎA TỐC", "HOẢ TỐC", "HOA TOC",
            "HÓA TỐC", "HỌA TỐC", "HÕA TỐC",       # wrong tone on ỏ
            "HÒA TỐC",                                 # ỏ→ò (common in alt-text!)
            "HỎA TÓC", "HỎA TỌC", "HỎA TỒC",       # wrong tone on ố
            "HỎA TOC", "HOẢ TOC", "HOA TỐC",         # partial diacritics
            "HÓA TOC", "HÒA TOC", "HỌA TOC",        # partial + wrong
            "HỎATỐC", "HỎATOC", "HOATOC",            # merged
            "HỎA TỐC HẸN GIỜ", "HOA TOC HEN GIO",   # extended (NĐ 30 Đ12K2)
            # Alt-text fragments (Chandra English descriptions)
            "HOA TOC", "HOATOC",
        ],
        "Thượng Khẩn": [
            "THƯỢNG KHẨN", "THUỢNG KHẨN", "THUONG KHAN",
            "THƯỜNG KHẨN", "THƯƠNG KHẨN", "THUỜNG KHẨN",
            "THƯỞNG KHẨN", "THƯỚNG KHẨN", "THUỠNG KHẨN",
            "THƯỢNG KHẤN", "THƯỢNG KHẦN", "THƯỢNG KHẢN", "THƯỢNG KHẪN",
            "THƯỢNG KHAN", "THUONG KHẨN",
            "THƯỢNG KHĂN",                              # ẩ→ă
            "THƯỢNGKHẨN", "THUONGKHAN",                 # merged
            # Alt-text fragments
            "THUONG KHAN", "THUONGKHAN",
        ],
        "Khẩn": [
            "KHẨN", "KHAN",
            "KHẤN", "KHẦN", "KHẢN", "KHẪN", "KHĂN",
            "KHẨÑ",                                     # garbled
        ],
    }

    DO_MAT_PATTERNS = {
        "Tuyệt Mật": [
            "TUYỆT MẬT", "TUYET MAT",
            "TUYÊT MẬT", "TUYỆT MAT", "TUYÊT MAT",
            "TUYẾT MẬT", "TUYỀT MẬT", "TUYỂT MẬT", "TUYỄT MẬT",
            "TUYỆT MẤT", "TUYỆT MÂT", "TUYỆT MẠT", "TUYỆT MẶT",
            "TUYẾT MAT", "TUYET MẬT",
            "TUYỆTMẬT", "TUYETMAT",
        ],
        "Tối Mật": [
            "TỐI MẬT", "TOI MAT", "TÔI MAT",
            "TÔI MẬT", "TOI MẬT",                      # MOST COMMON red ink error
            "TỐI MAT", "TÔI MAT",
            "TỒI MẬT", "TỔI MẬT", "TỘI MẬT", "TỎI MẬT", "TÓI MẬT",
            "TỐI MẤT", "TỐI MÂT", "TỐI MẠT", "TỐI MẶT",
            "TÔI MÂT", "TOI MÂT",
            "TỐIMẬT", "TOIMAT", "TÔIMAT",
        ],
        "Mật": [
            "MẬT", "MAT",
            "MẤT", "MÂT", "MẠT", "MẶT",
            "MẦT", "MẪT", "MẢT",
        ],
    }

    for div in divs:
        bbox = _get_bbox(div)
        if not bbox:
            continue
        x1, y1, x2, y2 = bbox
        label = _get_label(div)
        raw_text = _div_text(div).strip()

        # For Image divs: extract alt text (Chandra describes stamps in alt)
        # e.g., <img alt="Red arrow-shaped stamp with text 'HỎA TỐC'"/>
        if label == "Image":
            img_tag = div.find("img")
            if img_tag and img_tag.get("alt"):
                raw_text = img_tag["alt"]

        text = raw_text.upper() if raw_text else ""

        # Position constraint (NĐ 30/2020 Phụ lục I):
        # Stamps at LEFT side, between số KH line and loại VB line
        # Image divs: tighter zone — top 40% of page, left 60% (stamp images can be wider)
        if y1 < stamp_zone_y_min or y1 > stamp_zone_y_max:
            continue
        if label == "Image":
            # Stamp images: left 60% of page, top 40%
            if x1 > page_width * 0.6:
                continue
            if y1 > stamp_zone_y_max * 0.8:
                continue
        else:
            if not _is_left_half(bbox, page_width):
                continue
            if len(text) > 30:
                continue

        # Check Độ khẩn (longest match first)
        if not fields["Do_Khan"]:
            for canonical, patterns in DO_KHAN_PATTERNS.items():
                if any(p in text for p in patterns):
                    fields["Do_Khan"] = canonical
                    logger.debug("[HEADER] Detected Do_Khan at bbox=%s label=%s", bbox, label)
                    break

        # Check Độ mật (longest match first)
        if not fields["Do_Mat"]:
            for canonical, patterns in DO_MAT_PATTERNS.items():
                if any(p in text for p in patterns):
                    fields["Do_Mat"] = canonical
                    logger.debug("[HEADER] Detected Do_Mat at bbox=%s label=%s", bbox, label)
                    break

    return fields


# ─── Footer Field Extraction (Last Page) ─────────────────────────


def _extract_footer_fields(html: str, page_width: int = 1244) -> dict:
    """Extract footer fields from last page HTML."""
    fields = {
        "Noi_Nhan": "",
        "Chuc_Vu": "",
        "Chu_Ky": "",
    }

    divs = _get_divs(html)
    if not divs:
        logger.warning("No divs found in last page HTML")
        return fields

    logger.info(f"[FOOTER] Parsing last page: {len(divs)} content divs, page_width={page_width}")

    # ── Nơi nhận ──
    # Find "Nơi nhận:" marker, then next List-Group div
    noi_nhan_idx = -1
    for i, div in enumerate(divs):
        text = _div_text(div)
        if "Nơi nhận" in text or "Noi nhận" in text or "Nơi nhan" in text:
            noi_nhan_idx = i
            break

    if noi_nhan_idx >= 0:
        # Check if list is in same div or next div
        noi_nhan_div = divs[noi_nhan_idx]
        label = _get_label(noi_nhan_div)

        if label == "List-Group":
            # "Nơi nhận:" is in the list div itself
            items = noi_nhan_div.find_all("li")
            if items:
                fields["Noi_Nhan"] = "\n".join(li.get_text(strip=True) for li in items)

        # Check next div for list
        if not fields["Noi_Nhan"] and noi_nhan_idx + 1 < len(divs):
            next_div = divs[noi_nhan_idx + 1]
            if _get_label(next_div) == "List-Group":
                items = next_div.find_all("li")
                if items:
                    fields["Noi_Nhan"] = "\n".join(li.get_text(strip=True) for li in items)

        # Fallback: extract text from non-list div after "Nơi nhận:"
        if not fields["Noi_Nhan"]:
            noi_nhan_lines = []
            for div in divs[noi_nhan_idx + 1:]:
                bbox = _get_bbox(div)
                if bbox and _is_left_half(bbox, page_width):
                    text = _div_text(div)
                    if text and len(text) > 2:
                        noi_nhan_lines.append(text)
                else:
                    break  # Stop when we hit right-side content
            if noi_nhan_lines:
                fields["Noi_Nhan"] = "\n".join(noi_nhan_lines)

    # ── Chức vụ + Chữ ký ──
    # Strategy: find Image div (stamp/seal), then:
    # - Text with bold BEFORE Image, right half → Chuc_Vu
    # - Text with bold AFTER Image, right half → Chu_Ky (signer name)
    #
    # Edge case: Công văn Hỏa Tốc has 2 Image divs:
    #   (1) header stamp "HỎA TỐC" — NOT a signature
    #   (2) footer stamp + signature — the real one
    # Prefer LAST Image div below noi_nhan_idx (footer zone). Fallback to
    # first Image if no footer-zone Image exists (preserves old behavior).
    image_idx = -1
    for i, div in enumerate(divs):
        if _get_label(div) == "Image":
            if noi_nhan_idx < 0 or i > noi_nhan_idx:
                image_idx = i  # keep updating → final value = last in footer
    if image_idx < 0:
        # Fallback: no Image below Nơi nhận (or no Nơi nhận detected)
        # — use first Image as before
        for i, div in enumerate(divs):
            if _get_label(div) == "Image":
                image_idx = i
                break

    if image_idx >= 0:
        # Collect all right-half text divs before Image (chức vụ candidates)
        chuc_vu_candidates = []
        for i in range(image_idx - 1, max(noi_nhan_idx, -1), -1):
            div = divs[i]
            label = _get_label(div)
            bbox = _get_bbox(div)
            text = _div_text(div)
            # Skip Page-Header labels and page numbers
            if label == "Page-Header" or (text and text.strip().isdigit()):
                continue
            if bbox and not _is_left_half(bbox, page_width) and text and len(text) > 3:
                is_title = _div_has_bold(div) or text.isupper() or any(
                    kw in text for kw in ["KT.", "T/M", "TM.", "TL.", "TRƯỞNG", "GIÁM ĐỐC",
                                           "CHỦ TỊCH", "CHÁNH", "BÍ THƯ", "PHÓ"]
                )
                # Skip date strings and other non-title content
                if "ngày" in text.lower() and "tháng" in text.lower():
                    continue
                if is_title:
                    chuc_vu_candidates.insert(0, text)  # keep order top→bottom
                else:
                    break  # stop scanning when we hit non-title content

        if chuc_vu_candidates:
            fields["Chuc_Vu"] = "\n".join(chuc_vu_candidates)

        # Chữ ký: text div after Image, right half (signer name)
        for i in range(image_idx + 1, len(divs)):
            div = divs[i]
            bbox = _get_bbox(div)
            text = _div_text(div)
            if bbox and not _is_left_half(bbox, page_width) and text:
                if "signature" in text.lower() or "Handwritten" in text:
                    continue
                if len(text) < 50 and not text.startswith("["):
                    fields["Chu_Ky"] = text
                    break

        # Fallback: if no name after Image, check last right-half Text before Image
        # Some docs put name BEFORE the stamp image
        if not fields["Chu_Ky"] and chuc_vu_candidates:
            # Last candidate might actually be signer name (short, proper case)
            # Re-scan: find name-like text (short, capitalized, not a title keyword)
            for i in range(image_idx - 1, max(noi_nhan_idx, -1), -1):
                div = divs[i]
                bbox = _get_bbox(div)
                text = _div_text(div)
                if not bbox or _is_left_half(bbox, page_width) or not text:
                    continue
                is_title_keyword = any(kw in text for kw in [
                    "KT.", "T/M", "TM.", "TL.", "TRƯỞNG", "GIÁM ĐỐC",
                    "CHỦ TỊCH", "CHÁNH", "BÍ THƯ", "PHÓ", "ĐOÀN", "BAN"
                ])
                if not is_title_keyword and len(text) < 40:
                    fields["Chu_Ky"] = text
                    break
    else:
        # No Image div — scan from bottom: right-half divs = signature block
        # Pattern: last right-half short text = signer name, before that = title
        right_divs = []
        for div in reversed(divs):
            label = _get_label(div)
            bbox = _get_bbox(div)
            text = _div_text(div)
            if label == "Page-Header" or (text and text.strip().isdigit()):
                continue
            if not bbox or not text or len(text) <= 3:
                continue
            if not _is_left_half(bbox, page_width):
                right_divs.append(text)
            elif right_divs:
                break  # hit left content, stop

        if right_divs:
            # First from bottom = signer name, rest = chức vụ
            fields["Chu_Ky"] = right_divs[0]
            if len(right_divs) > 1:
                fields["Chuc_Vu"] = "\n".join(reversed(right_divs[1:]))

    return fields


# ─── Loại VB Prefix Inference ────────────────────────────────────


def _infer_loai_vb_from_prefix(so_ki_hieu: str) -> str:
    """Infer document type from số ký hiệu (e.g., 123/QĐ-UBND → Quyết định).

    Per NĐ 30/2020/NĐ-CP, Vietnamese admin docs have số ký hiệu with format:
    - Docs WITH tên loại:  Num/<TYPE_PREFIX>-<AGENCY>   e.g. '258/BC-KTNS'
    - Công văn (no tên loại): Num/<AGENCY>-<SUB_UNIT>   e.g. '418/BCA-TCAN'
                                                             '2420/UBND-KGVX'

    Detection:
    1. Match first alphabetic token bounded by / or - (not substring)
    2. If it equals a known TYPE_PREFIX → that doc type
    3. If it's an alphabetic code (length 2-5) but NOT in TYPE_PREFIX map →
       treat as agency code → document is Công văn (per NĐ 30 convention)

    False positives prevented:
    - '418/BCA-TCAN' substring 'BC' → 'Báo cáo'. BCA is agency, doc is Công văn.
    - OCR errors like 'TTTr' → returns Công văn (safer than wrong type).
    """
    pattern = re.compile(r'[/\-]([A-ZĐa-zđ]{2,5})(?=[/\-]|$)')
    for match in pattern.finditer(so_ki_hieu):
        token = match.group(1).upper().replace("Đ", "Đ")
        for key, value in LOAI_VB_PREFIX_MAP.items():
            if key.upper() == token:
                return value
        # First alphabetic token isn't a doc type prefix → agency code.
        # Per NĐ 30/2020, a số ký hiệu of form "Num/CQBH-Đơn_vị" with no
        # type prefix identifies a Công văn (văn bản không có tên loại).
        return "Công văn"
    return ""


# ─── Main Entry Point ────────────────────────────────────────────


def parse_chandra_html(pages_html: list, page_widths: list = None,
                       orientations: list = None) -> dict:
    """Parse Chandra HTML from all pages into 12 NĐ30 fields.

    Args:
        pages_html: list of raw HTML strings, one per page (ordered)
        page_widths: list of page widths in pixels (from PDF rendering).
                     If None, defaults to 1244 for all pages.
        orientations: list of "portrait"|"landscape" per page. If provided,
                      HTML comment markers are embedded in van_ban_dang_txt
                      at page boundaries so DOCX export can emit matching
                      section breaks. Ignored by GUI render and Excel strip.

    Returns:
        Dict with PascalCase keys matching add_document() contract.
    """
    if not pages_html:
        return {k: "" for k in [
            "CQBH_tren", "CQBH_duoi", "So_Ki_Hieu", "Ngay_BH",
            "Do_Khan", "Do_Mat", "Loai_VB", "ND_Chinh",
            "Noi_Nhan", "Chuc_Vu", "Chu_Ky", "Van_ban_dang_txt",
        ]}

    if page_widths is None:
        page_widths = [1244] * len(pages_html)

    # Header fields from page 1
    header = _extract_header_fields(pages_html[0], page_width=page_widths[0])

    # Footer fields: locate the page containing the authoritative "Nơi nhận:"
    # marker (NĐ 30/2020 Ô 9 — always on the page with signature block, ends
    # main doc content). Signatures on LATER pages are annex/attachment signers
    # (e.g. the person who compiled a table), not the main doc's approver.
    # Strategy:
    #   1. Find FIRST page containing "Nơi nhận" — that's where Chuc_Vu + Chu_Ky live
    #   2. If no "Nơi nhận" found, walk backward (docs without explicit marker)
    footer = {"Noi_Nhan": "", "Chuc_Vu": "", "Chu_Ky": ""}
    footer_idx = -1
    for idx, page_html in enumerate(pages_html):
        if page_html and ("Nơi nhận" in page_html or "Noi nhận" in page_html or "Nơi nhan" in page_html):
            footer_idx = idx
            break
    if footer_idx >= 0:
        footer = _extract_footer_fields(pages_html[footer_idx], page_width=page_widths[footer_idx])
        logger.info(f"[FOOTER] Anchored to 'Nơi nhận' on page {footer_idx+1}/{len(pages_html)}")
    else:
        # No "Nơi nhận" marker anywhere — fallback to backward scan for any content
        for idx in range(len(pages_html) - 1, -1, -1):
            candidate = _extract_footer_fields(pages_html[idx], page_width=page_widths[idx])
            if candidate.get("Chu_Ky") or candidate.get("Chuc_Vu") or candidate.get("Noi_Nhan"):
                footer = candidate
                logger.info(f"[FOOTER] No 'Nơi nhận' marker; fallback page {idx+1}/{len(pages_html)}")
                break

    # Concatenate all pages HTML for Van_ban_dang_txt.
    # Embed orientation markers at page boundaries — DOCX export reads these
    # to emit section breaks with matching page orientation (landscape pages
    # for wide tables). Markers are HTML comments → invisible to BeautifulSoup
    # .get_text() (Excel strip) and to GUI render (find_all('div')).
    if orientations and len(orientations) == len(pages_html):
        parts = []
        for i, page_html in enumerate(pages_html):
            parts.append(f"<!--page-orient:{orientations[i]}-->")
            parts.append(page_html or "")
        van_ban = "\n".join(parts)
    else:
        van_ban = "\n".join(pages_html)

    result = {
        "CQBH_tren": header["CQBH_tren"],
        "CQBH_duoi": header["CQBH_duoi"],
        "So_Ki_Hieu": header["So_Ki_Hieu"],
        "Ngay_BH": header["Ngay_BH"],
        "Do_Khan": header["Do_Khan"],
        "Do_Mat": header["Do_Mat"],
        "Loai_VB": header["Loai_VB"],
        "ND_Chinh": header["ND_Chinh"],
        "Noi_Nhan": footer["Noi_Nhan"],
        "Chuc_Vu": footer["Chuc_Vu"],
        "Chu_Ky": footer["Chu_Ky"],
        "Van_ban_dang_txt": van_ban,
    }

    filled = sum(1 for k, v in result.items() if v)
    lengths = {key: len(str(value)) for key, value in result.items() if value}
    logger.info("Parsed OCR fields: %s/12 populated; lengths=%s", filled, lengths)

    return result
