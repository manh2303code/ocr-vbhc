"""FTS5 (BM25) full-text index over document_chunks — Vietnamese-aware.

remove_diacritics 0: dấu thanh tiếng Việt mang nghĩa ("hòa" != "hoa").
Triggers giữ FTS đồng bộ với MỌI đường ghi vào document_chunks (kể cả
VectorStore.upsert_chunks hiện có) — không cần sửa code ghi.
"""
import logging
import re
from typing import List, Optional, Tuple


logger = logging.getLogger("SearchStore")

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


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
