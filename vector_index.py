"""Vector KNN index dùng sqlite-vec, với NumPy làm fallback air-gap."""

import logging
import os
import sqlite3
from typing import List, Optional, Tuple

import numpy as np


logger = logging.getLogger("VectorIndex")
EMBEDDING_DIM = 1024
# float32 BLOB size of one usable embedding; anything else is skipped by
# _as_vector and must not count as "missing from the index" either.
_EMBEDDING_BYTES = EMBEDDING_DIM * 4


def init_vec(conn) -> bool:
    """Nạp sqlite-vec và tạo chỉ mục; trả False nếu extension không khả dụng."""
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors "
            "USING vec0(embedding float[{}] distance_metric=cosine)".format(
                EMBEDDING_DIM
            )
        )
        return True
    except Exception as exc:
        logger.warning("sqlite-vec unavailable (%s) - using NumPy fallback", exc)
        return False


class VectorIndex:
    """Search facade returning ``(chunk_id, document_id, cosine_score)``."""

    def __init__(self, db_path_or_conn, force_fallback: bool = False):
        if isinstance(db_path_or_conn, (str, bytes, os.PathLike)):
            probe_conn = sqlite3.connect(db_path_or_conn)
            try:
                self._native = (not force_fallback) and init_vec(probe_conn)
            finally:
                probe_conn.close()
        else:
            self._native = (not force_fallback) and init_vec(db_path_or_conn)
        self._ids: List[int] = []
        self._docs: List[int] = []
        self._matrix: Optional[np.ndarray] = None

    def _use_native(self, conn) -> bool:
        if self._native and not init_vec(conn):
            self._native = False
        return self._native

    def is_native(self) -> bool:
        return self._native

    @staticmethod
    def _as_vector(value) -> Optional[np.ndarray]:
        if value is None:
            return None
        vector = np.frombuffer(value, dtype=np.float32) if isinstance(
            value, (bytes, bytearray, memoryview)
        ) else np.asarray(value, dtype=np.float32)
        if vector.size != EMBEDDING_DIM:
            return None
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def sync_from_chunks(self, conn) -> int:
        rows = conn.execute(
            "SELECT id, document_id, embedding FROM document_chunks "
            "WHERE embedding IS NOT NULL"
        ).fetchall()
        valid = []
        for chunk_id, doc_id, embedding in rows:
            vector = self._as_vector(embedding)
            if vector is not None:
                valid.append((int(chunk_id), int(doc_id), vector))

        if self._use_native(conn):
            conn.execute("DELETE FROM chunk_vectors")
            conn.executemany(
                "INSERT INTO chunk_vectors(rowid, embedding) VALUES (?, ?)",
                [(chunk_id, vector.tobytes()) for chunk_id, _, vector in valid],
            )
        else:
            self._ids = [chunk_id for chunk_id, _, _ in valid]
            self._docs = [doc_id for _, doc_id, _ in valid]
            self._matrix = (
                np.vstack([vector for _, _, vector in valid]).astype(np.float32)
                if valid else np.empty((0, EMBEDDING_DIM), dtype=np.float32)
            )
        return len(valid)

    def upsert(self, conn, rows) -> None:
        vectors = []
        for chunk_id, value in rows:
            vector = self._as_vector(value)
            if vector is not None:
                vectors.append((int(chunk_id), vector))
        if not vectors:
            return
        if self._use_native(conn):
            conn.executemany(
                "INSERT OR REPLACE INTO chunk_vectors(rowid, embedding) VALUES (?, ?)",
                [(chunk_id, vector.tobytes()) for chunk_id, vector in vectors],
            )
            return

        doc_map = dict(conn.execute(
            "SELECT id, document_id FROM document_chunks WHERE id IN ({})".format(
                ",".join("?" for _ in vectors)
            ),
            [chunk_id for chunk_id, _ in vectors],
        ).fetchall())
        by_id = dict(zip(self._ids, zip(self._docs, self._matrix))) if self._matrix is not None else {}
        for chunk_id, vector in vectors:
            if chunk_id in doc_map:
                by_id[chunk_id] = (int(doc_map[chunk_id]), vector)
        self._ids = list(by_id)
        self._docs = [by_id[chunk_id][0] for chunk_id in self._ids]
        self._matrix = (
            np.vstack([by_id[chunk_id][1] for chunk_id in self._ids]).astype(np.float32)
            if by_id else np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        )

    def replace_chunks(self, conn, removed_ids, rows) -> None:
        """Mirror one chunk rewrite: drop the replaced rowids, index the new.

        Called inside the transaction that rewrote ``document_chunks`` so the
        two tables commit (or roll back) together.
        """
        self.remove_chunks(conn, removed_ids)
        self.upsert(conn, rows)

    def out_of_sync(self, conn) -> bool:
        """True when chunk_vectors does not hold exactly the indexable chunks.

        Compares rowid sets, not counts. The live DB had 53 vectors for 107
        chunks (documents added after the first sync were never indexed),
        but re-indexing documents that were indexed once leaves as many
        orphan vectors as it creates unindexed chunks, so the counts of a
        broken index can match (eval corpus after re-indexing 5 documents:
        173 = 173 rows, 54 missing and 54 orphans).
        """
        if not self._use_native(conn):
            return False
        chunk_ids = {
            int(row[0]) for row in conn.execute(
                "SELECT id FROM document_chunks "
                "WHERE embedding IS NOT NULL AND length(embedding) = ?",
                (_EMBEDDING_BYTES,),
            )
        }
        vector_ids = {
            int(row[0]) for row in conn.execute("SELECT rowid FROM chunk_vectors")
        }
        return chunk_ids != vector_ids

    def ensure_synced(self, conn) -> Optional[int]:
        """Rebuild the native index if it drifted; returns vectors written."""
        if not self.out_of_sync(conn):
            return None
        count = self.sync_from_chunks(conn)
        logger.info("chunk_vectors was out of sync; rebuilt %d vectors", count)
        return count

    def remove_chunks(self, conn, chunk_ids) -> None:
        ids = [int(chunk_id) for chunk_id in chunk_ids]
        if not ids:
            return
        if self._use_native(conn):
            conn.execute(
                "DELETE FROM chunk_vectors WHERE rowid IN ({})".format(
                    ",".join("?" for _ in ids)
                ),
                ids,
            )
            return
        removed = set(ids)
        keep = [index for index, chunk_id in enumerate(self._ids) if chunk_id not in removed]
        self._ids = [self._ids[index] for index in keep]
        self._docs = [self._docs[index] for index in keep]
        self._matrix = (
            self._matrix[keep]
            if self._matrix is not None
            else np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        )

    def search(self, conn, query_vec, top_n=50, doc_ids=None):
        query = self._as_vector(query_vec)
        if query is None or top_n <= 0:
            return []
        allowed = set(int(doc_id) for doc_id in doc_ids) if doc_ids is not None else None
        if self._use_native(conn):
            candidate_count = max(top_n, top_n * 4 if allowed is not None else top_n)
            candidates = conn.execute(
                "SELECT rowid, distance FROM chunk_vectors "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (query.tobytes(), candidate_count),
            ).fetchall()
            if not candidates:
                return []
            doc_map = dict(conn.execute(
                "SELECT id, document_id FROM document_chunks WHERE id IN ({})".format(
                    ",".join("?" for _ in candidates)
                ),
                [row[0] for row in candidates],
            ).fetchall())
            return [
                (int(chunk_id), int(doc_map[chunk_id]), 1.0 - float(distance))
                for chunk_id, distance in candidates
                if chunk_id in doc_map and (allowed is None or doc_map[chunk_id] in allowed)
            ][:top_n]

        if self._matrix is None or not len(self._ids):
            return []
        scores = self._matrix @ query
        order = np.argsort(-scores)
        hits = []
        for index in order:
            doc_id = self._docs[int(index)]
            if allowed is None or doc_id in allowed:
                hits.append((self._ids[int(index)], doc_id, float(scores[int(index)])))
                if len(hits) == top_n:
                    break
        return hits
