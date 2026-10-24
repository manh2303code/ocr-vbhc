"""FTS5 (BM25) full-text index over document_chunks — Vietnamese-aware.

remove_diacritics 0: dấu thanh tiếng Việt mang nghĩa ("hòa" != "hoa").
Triggers giữ FTS đồng bộ với MỌI đường ghi vào document_chunks (kể cả
VectorStore.upsert_chunks hiện có) — không cần sửa code ghi.
"""
import logging
import re
import unicodedata
from typing import List, Optional, Tuple


logger = logging.getLogger("SearchStore")

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MARKS_RE = re.compile(r"[̀-ͯ]")


def init_fts(cursor) -> None:
    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_text,
            content='document_chunks',
            content_rowid='id',
            tokenize="unicode61 remove_diacritics 0"
        )
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ai
        AFTER INSERT ON document_chunks BEGIN
            INSERT INTO chunks_fts(rowid, chunk_text)
            VALUES (new.id, new.chunk_text);
        END
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ad
        AFTER DELETE ON document_chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text)
            VALUES ('delete', old.id, old.chunk_text);
        END
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS chunks_fts_au
        AFTER UPDATE ON document_chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text)
            VALUES ('delete', old.id, old.chunk_text);
            INSERT INTO chunks_fts(rowid, chunk_text)
            VALUES (new.id, new.chunk_text);
        END
    ''')


def rebuild_fts(conn) -> None:
    """Đồng bộ lại toàn bộ FTS từ document_chunks (dùng khi migrate DB cũ)."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    conn.commit()


def build_match_query(query: str) -> str:
    """User text -> FTS5 MATCH string an toàn: mỗi token bọc nháy kép, nối OR."""
    tokens = _TOKEN_RE.findall(query or "")
    if not tokens:
        return '""'
    return " OR ".join('"{}"'.format(token) for token in tokens)


def fts_search(
    conn,
    query: str,
    top_n: int = 50,
    doc_ids: Optional[List[int]] = None,
) -> List[Tuple[int, int, float]]:
    match = build_match_query(query)
    sql = (
        "SELECT f.rowid, c.document_id, -bm25(chunks_fts) AS score "
        "FROM chunks_fts f JOIN document_chunks c ON c.id = f.rowid "
        "WHERE chunks_fts MATCH ?"
    )
    params = [match]
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        sql += " AND c.document_id IN ({})".format(placeholders)
        params.extend(doc_ids)
    sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
    params.append(top_n)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        logger.warning("FTS query failed: %s", exc)
        return []
    return [(int(row[0]), int(row[1]), float(row[2])) for row in rows]


# ── Accent-insensitive search ───────────────────────────────────────────
#
# The main index keeps marks because they carry meaning. A second, folded
# index catches what that one cannot: questions typed without marks ("do xuan
# chuong") and recognition errors that flip a mark ("khoản" read for "khoán").
# Its rows are written from Python, so it follows document_chunks through a
# cheap signature check rather than triggers: chunks are only ever deleted and
# re-inserted under new AUTOINCREMENT ids, never updated in place.

def fold_vietnamese(text: str) -> str:
    """Lowercase and strip every mark, đ included: "Đỗ Xuân" -> "do xuan"."""
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    return _MARKS_RE.sub("", unicodedata.normalize("NFD", text)).lower()


def ensure_fold_index(conn) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fold "
        "USING fts5(text, tokenize='unicode61 remove_diacritics 2')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks_fold_state ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), n INTEGER, s INTEGER, m INTEGER)"
    )
    signature = tuple(conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(id), 0), COALESCE(MAX(id), 0) FROM document_chunks"
    ).fetchone())
    stored = conn.execute("SELECT n, s, m FROM chunks_fold_state WHERE id = 1").fetchone()
    if stored is not None and tuple(stored) == signature:
        return
    rows = conn.execute("SELECT id, chunk_text FROM document_chunks").fetchall()
    conn.execute("DELETE FROM chunks_fold")
    conn.executemany(
        "INSERT INTO chunks_fold(rowid, text) VALUES (?, ?)",
        ((row[0], fold_vietnamese(row[1])) for row in rows),
    )
    conn.execute("INSERT OR REPLACE INTO chunks_fold_state VALUES (1, ?, ?, ?)", signature)
    conn.commit()


def fts_search_folded(
    conn,
    query: str,
    top_n: int = 50,
    doc_ids: Optional[List[int]] = None,
) -> List[Tuple[int, int, float]]:
    """BM25 over the folded index; same row shape as fts_search."""
    try:
        ensure_fold_index(conn)
    except Exception as exc:
        logger.warning("Folded index unavailable: %s", exc)
        return []
    match = build_match_query(fold_vietnamese(query))
    sql = (
        "SELECT f.rowid, c.document_id, -bm25(chunks_fold) AS score "
        "FROM chunks_fold f JOIN document_chunks c ON c.id = f.rowid "
        "WHERE chunks_fold MATCH ?"
    )
    params = [match]
    if doc_ids:
        sql += " AND c.document_id IN ({})".format(",".join("?" for _ in doc_ids))
        params.extend(doc_ids)
    sql += " ORDER BY bm25(chunks_fold) LIMIT ?"
    params.append(top_n)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        logger.warning("Folded FTS query failed: %s", exc)
        return []
    return [(int(row[0]), int(row[1]), float(row[2])) for row in rows]


# ── Query-focused passages ─────────────────────────────────────────────
#
# A chunk is about 2000 characters. The reranker reads 500 of them and the
# chat model used to be shown 600, always the first ones, so an answer further
# down a chunk was retrieved but never read. best_passage picks the window the
# question is about instead.

# Question words and particles that say nothing about where the answer is.
_PASSAGE_STOPWORDS = frozenset(
    "la cua va co gi nao cac nhung duoc cho ve trong theo voi nay ai bao nhieu "
    "khong mot thi ma de tai tu den hay nhu sao bi o ra vao khi nay hoi".split()
)


def best_passage(text: str, query: str, size: int = 900) -> str:
    """The `size`-character window of `text` that covers most of the query.

    Matching is accent-insensitive; adjacent query words count double so a
    name or a phrase beats scattered single words. The window starts and ends
    on word boundaries. Text that already fits is returned unchanged.
    """
    text = text or ""
    if len(text) <= size:
        return text
    words = [(m.start(), m.end(), fold_vietnamese(m.group())) for m in _TOKEN_RE.finditer(text)]
    if not words:
        return text[:size].strip()
    query_words = [fold_vietnamese(w) for w in _TOKEN_RE.findall(query or "")]
    terms = {w for w in query_words if w not in _PASSAGE_STOPWORDS and len(w) > 1}
    bigrams = {(a, b) for a, b in zip(query_words, query_words[1:])}

    best_score, best_matches = 0, None
    end_index = 0
    for start_index, (start, _, _) in enumerate(words):
        end_index = max(end_index, start_index)
        while end_index + 1 < len(words) and words[end_index + 1][1] - start <= size:
            end_index += 1
        window = words[start_index:end_index + 1]
        found = {w for _, _, w in window if w in terms}
        pairs = {(a[2], b[2]) for a, b in zip(window, window[1:]) if (a[2], b[2]) in bigrams}
        score = len(found) + 2 * len(pairs)
        if score > best_score:
            # Anchor on the matched phrases when there are any: a word that
            # is common in the text ("đầu") should not drag the window back.
            hits = [(a[0], b[1]) for a, b in zip(window, window[1:]) if (a[2], b[2]) in bigrams] \
                or [(s, e) for s, e, w in window if w in terms]
            best_score, best_matches = score, (hits[0][0], hits[-1][1])
        if end_index == len(words) - 1:
            break
    if best_matches is None:
        return text[:size].rsplit(" ", 1)[0].strip()
    # The question names the subject; the answer usually follows it. Open the
    # window a little before the first match and give the rest to what comes after.
    first, last = best_matches
    lead = max(0, min(150, (size - (last - first)) // 4))
    start = max(0, first - lead)
    end = min(len(text), start + size)
    start = max(0, end - size)
    span = [w for w in words if w[0] >= start and w[1] <= end]
    return text[span[0][0]:span[-1][1]]
