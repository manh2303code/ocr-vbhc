"""Hybrid BM25/vector retrieval with reciprocal-rank fusion."""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from search_store import fts_search


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    doc_id: int
    chunk_text: str = ""
    score: float = 0.0
    so_ki_hieu: str = ""
    loai_vb: str = ""
    ngay_bh: str = ""
    page_number: Optional[int] = None


def rrf_fuse(rank_lists: List[List[int]], k: int = 60) -> Dict[int, float]:
    """Return ``item id -> RRF score``; ranks are one-based."""
    scores: Dict[int, float] = {}
    for rank_list in rank_lists:
        for rank, item_id in enumerate(rank_list, 1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


def hybrid_search(
    conn,
    vindex,
    embedding_model,
    reranker,
    query: str,
    top_k: int = 8,
    doc_ids: Optional[List[int]] = None,
    use_rerank: bool = True,
    rerank_top: int = 10,
) -> List[RetrievedChunk]:
    if not query or not query.strip() or top_k <= 0:
        return []

    query_vector = embedding_model.encode(query)
    fts_hits = fts_search(conn, query, top_n=50, doc_ids=doc_ids)
    vector_hits = (
        vindex.search(conn, query_vector, top_n=50, doc_ids=doc_ids)
        if query_vector is not None
        else []
    )
    scores = rrf_fuse([
        [hit[0] for hit in fts_hits],
        [hit[0] for hit in vector_hits],
    ])
    if not scores:
        return []

    candidate_ids = sorted(scores, key=scores.get, reverse=True)[
        : max(top_k, rerank_top)
    ]
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = conn.execute(
        "SELECT c.id, c.document_id, c.chunk_text, v.so_ki_hieu, "
        "v.loai_vb, v.ngay_bh, c.page_number "
        "FROM document_chunks c "
        "LEFT JOIN document_versions v ON v.document_id = c.document_id "
        "AND v.version_number = (SELECT MAX(v2.version_number) "
        "FROM document_versions v2 WHERE v2.document_id = c.document_id) "
        "WHERE c.id IN ({})".format(placeholders),
        candidate_ids,
    ).fetchall()
    by_id = {int(row[0]): row for row in rows}
    chunks = [
        RetrievedChunk(
            chunk_id=chunk_id,
            doc_id=int(by_id[chunk_id][1]),
            chunk_text=by_id[chunk_id][2] or "",
            score=scores[chunk_id],
            so_ki_hieu=by_id[chunk_id][3] or "",
            loai_vb=by_id[chunk_id][4] or "",
            ngay_bh=by_id[chunk_id][5] or "",
            page_number=by_id[chunk_id][6],
        )
        for chunk_id in candidate_ids
        if chunk_id in by_id
    ]

    can_rerank = (
        use_rerank
        and reranker is not None
        and rerank_top > 0
        and reranker.is_available()
    )
    if can_rerank:
        prefix = chunks[:rerank_top]
        rerank_scores = reranker.rerank(query, [chunk.chunk_text for chunk in prefix])
        reranked = [
            replace(chunk, score=float(score))
            for chunk, score in zip(prefix, rerank_scores)
        ]
        reranked.sort(key=lambda chunk: chunk.score, reverse=True)
        chunks = reranked + chunks[len(prefix):]
    return chunks[:top_k]
