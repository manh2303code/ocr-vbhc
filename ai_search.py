"""
AI Search Module - Tìm kiếm ngữ nghĩa bằng Vietnamese Embedding
Sử dụng AITeamVN/Vietnamese_Embedding (568M, 1024-dim) cho semantic search offline.

Module cung cấp:
- EmbeddingModel: quản lý model embedding (lazy-load, encode, unload)
- VectorStore: lưu trữ/tìm kiếm vectors trong SQLite (chunked)
- build_embedding_text: kết hợp các field OCR thành text để embed
- chunk_text: chia text dài thành chunks với overlap
- embed_document_chunks: embed 1 tài liệu thành nhiều chunks
- embed_document: (legacy) embed 1 tài liệu thành 1 vector
"""
import os
import logging
import hashlib
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from typing import List, Tuple, Optional, Dict
from pathlib import Path

import numpy as np
from torch_compat import preferred_torch_device

# Offline mode - setdefault để không ghi đè giá trị đã set bởi vintern_ocr.py
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('HF_DATASETS_OFFLINE', '1')

logger = logging.getLogger("AISearch")

# --- Constants ---
EMBEDDING_MODEL_DIR = Path(__file__).parent / "model_embedding"
EMBEDDING_DIM = 1024
MAX_RESULTS = 10
MAX_TEXT_CHARS = 16000  # legacy, dùng cho build_embedding_text

# Chunking constants
CHUNK_SIZE = 2000       # ~500 tokens tiếng Việt per chunk
CHUNK_OVERLAP = 200     # ~50 tokens overlap giữa các chunks
SEARCH_TOP_CHUNKS = 50  # số chunks lấy trước khi gộp theo document

# Score aggregation weights
SCORE_WEIGHT_MAX = 0.6   # trọng số cho chunk score cao nhất
SCORE_WEIGHT_MEAN = 0.4  # trọng số cho trung bình chunk scores


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Chia text dài thành các chunks với overlap.

    Ưu tiên cắt tại ranh giới câu (dấu chấm, xuống dòng) thay vì cắt giữa từ.

    Args:
        text: chuỗi text cần chia
        chunk_size: kích thước mỗi chunk (ký tự)
        overlap: số ký tự overlap giữa 2 chunks liên tiếp

    Returns:
        List các chunk text. Nếu text ngắn hơn chunk_size, trả về [text].
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = chunk_size - overlap

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:])
            break

        # Tìm điểm cắt tốt nhất gần vị trí end
        # Ưu tiên: xuống dòng > dấu chấm câu > dấu phẩy > khoảng trắng
        best_break = end
        search_start = max(start + step // 2, end - 300)

        for sep in ['\n', '. ', '.\n', '; ', ', ']:
            pos = text.rfind(sep, search_start, end)
            if pos > search_start:
                best_break = pos + len(sep)
                break

        # Fallback: cắt tại khoảng trắng gần nhất
        if best_break == end:
            space_pos = text.rfind(' ', search_start, end)
            if space_pos > search_start:
                best_break = space_pos + 1

        chunks.append(text[start:best_break])

        # Start the next chunk from the chosen boundary, not the nominal
        # fixed step; otherwise an early sentence break creates a text gap.
        next_start = best_break - overlap
        start = next_start if next_start > start else best_break

    return chunks


def build_document_header(
    nd_chinh: str = "",
    loai_vb: str = "",
    cqbh_tren: str = "",
    cqbh_duoi: str = "",
    so_ki_hieu: str = "",
) -> str:
    """
    Tạo header ngắn gọn cho document (dùng prepend vào mỗi chunk).

    VD: "Công văn của UBND tỉnh Hưng Yên về hộ chiếu ngoại giao | Số 123/CV-UBND"
    """
    parts = []
    if loai_vb and loai_vb.strip():
        parts.append(loai_vb.strip())
    cq = (cqbh_tren or "").strip() or (cqbh_duoi or "").strip()
    if cq:
        parts.append(f"của {cq}")
    if nd_chinh and nd_chinh.strip():
        parts.append(f"về {nd_chinh.strip()}")

    header = " ".join(parts)
    if so_ki_hieu and so_ki_hieu.strip():
        header = f"{header} | Số {so_ki_hieu.strip()}" if header else f"Số {so_ki_hieu.strip()}"
    return header


_PAGE_MARKER_RE = re.compile(r"<!--page-orient:\w+-->", re.IGNORECASE)


def _plain_ocr_text(raw: str) -> str:
    if not raw:
        return ""
    from math_render import strip_math_tags

    raw = strip_math_tags(raw, plain=True)
    if '<div' in raw or '<p>' in raw:
        from bs4 import BeautifulSoup

        return BeautifulSoup(raw, 'html.parser').get_text(separator='\n', strip=True)
    return raw.strip()


def chunk_pages(van_ban_dang_txt: str) -> List[Tuple[str, Optional[int]]]:
    """Chunk OCR text while retaining its 1-based page marker mapping."""
    raw = (van_ban_dang_txt or "").strip()
    if not _PAGE_MARKER_RE.search(raw):
        return [(chunk, None) for chunk in chunk_text(_plain_ocr_text(raw))]

    chunks = []
    parts = _PAGE_MARKER_RE.split(raw)
    for page_number, page_raw in enumerate(parts[1:], 1):
        chunks.extend(
            (chunk, page_number)
            for chunk in chunk_text(_plain_ocr_text(page_raw))
        )
    return chunks


def build_chunk_texts(
    van_ban_dang_txt: str = "",
    nd_chinh: str = "",
    so_ki_hieu: str = "",
    loai_vb: str = "",
    cqbh_tren: str = "",
    cqbh_duoi: str = "",
) -> List[Tuple[str, Optional[int]]]:
    """
    Chia document thành nhiều chunks, mỗi chunk có header context.

    Chiến lược:
    1. Tạo header ngắn gọn (loại VB + CQ + nd_chinh + số ký hiệu)
    2. Chia van_ban_dang_txt thành chunks ~2000 ký tự với 200 chars overlap
    3. Prepend header vào mỗi chunk để giữ context

    Nếu văn bản ngắn (< CHUNK_SIZE), trả về 1 chunk duy nhất.

    Returns:
        List[(chunk_text, page_number)] sẵn sàng để embed.
    """
    header = build_document_header(
        nd_chinh=nd_chinh,
        loai_vb=loai_vb,
        cqbh_tren=cqbh_tren,
        cqbh_duoi=cqbh_duoi,
        so_ki_hieu=so_ki_hieu,
    )

    page_chunks = chunk_pages(van_ban_dang_txt)
    if not page_chunks:
        return [(header, None)] if header else []
    return [
        (f"{header}\n{text}" if header else text, page_number)
        for text, page_number in page_chunks
    ]


def build_embedding_text(
    van_ban_dang_txt: str = "",
    nd_chinh: str = "",
    so_ki_hieu: str = "",
    loai_vb: str = "",
    cqbh_tren: str = "",
    cqbh_duoi: str = "",
) -> str:
    """
    (Legacy) Kết hợp các field OCR thành một chuỗi text để embedding.
    Giữ cho backward compatibility. Dùng build_chunk_texts() cho chunked embedding.
    """
    parts = []

    summary_parts = []
    if loai_vb and loai_vb.strip():
        summary_parts.append(loai_vb.strip())
    cq = cqbh_tren.strip() if cqbh_tren and cqbh_tren.strip() else ""
    if not cq:
        cq = cqbh_duoi.strip() if cqbh_duoi and cqbh_duoi.strip() else ""
    if cq:
        summary_parts.append(f"của {cq}")
    if nd_chinh and nd_chinh.strip():
        summary_parts.append(f"về {nd_chinh.strip()}")
    if summary_parts:
        parts.append(" ".join(summary_parts))

    if nd_chinh and nd_chinh.strip() and len(nd_chinh.strip()) > 80:
        parts.append(nd_chinh.strip())

    meta_parts = []
    if cqbh_tren and cqbh_tren.strip():
        meta_parts.append(cqbh_tren.strip())
    if cqbh_duoi and cqbh_duoi.strip():
        meta_parts.append(cqbh_duoi.strip())
    if so_ki_hieu and so_ki_hieu.strip():
        meta_parts.append(f"Số {so_ki_hieu.strip()}")
    if meta_parts:
        parts.append(" - ".join(meta_parts))

    if van_ban_dang_txt and van_ban_dang_txt.strip():
        raw = van_ban_dang_txt.strip()
        # Clean LaTeX math first (see build_chunk_texts above for rationale).
        from math_render import strip_math_tags
        raw = strip_math_tags(raw, plain=True)
        # Strip HTML tags if Chandra output
        if '<div' in raw or '<p>' in raw:
            from bs4 import BeautifulSoup
            full_text = BeautifulSoup(raw, 'html.parser').get_text(separator='\n', strip=True)
        else:
            full_text = raw
        used_chars = sum(len(p) for p in parts)
        remaining = MAX_TEXT_CHARS - used_chars
        if remaining > 200:
            if len(full_text) > remaining:
                full_text = full_text[:remaining]
            parts.append(full_text)

    return "\n".join(parts)


class EmbeddingModel:
    """Quản lý Vietnamese Embedding model cho semantic search."""

    def __init__(self, model_dir: str = None):
        self.model_dir = Path(model_dir) if model_dir else EMBEDDING_MODEL_DIR
        self.model = None
        self._is_loaded = False

    def is_available(self) -> bool:
        """Kiểm tra model files có tồn tại trên disk không."""
        if not self.model_dir.exists():
            return False
        has_config = (self.model_dir / "config.json").exists()
        has_weights = (
            (self.model_dir / "model.safetensors").exists()
            or (self.model_dir / "pytorch_model.bin").exists()
        )
        return has_config and has_weights

    def load(self) -> bool:
        """
        Load embedding model vào memory. Lazy-load: chỉ gọi khi cần.

        Returns:
            True nếu load thành công.
        """
        if self._is_loaded and self.model is not None:
            return True

        if not self.is_available():
            logger.warning(
                f"Embedding model không tìm thấy tại {self.model_dir}. "
                f"Tải AITeamVN/Vietnamese_Embedding vào thư mục này."
            )
            return False

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            import sys
            logger.error(
                "sentence-transformers chưa được cài đặt. "
                f"Python đang dùng: {sys.executable} | "
                "Chạy: pip install sentence-transformers>=2.2.0"
            )
            return False

        try:
            self.model = SentenceTransformer(
                str(self.model_dir),
                local_files_only=True,
                device=preferred_torch_device(),
            )
            self.model.max_seq_length = 4096
            self._is_loaded = True
            logger.info(f"Đã load embedding model từ {self.model_dir}")
            return True
        except Exception as e:
            import traceback
            logger.error(f"Lỗi load embedding model: {e}")
            logger.error(traceback.format_exc())
            self._is_loaded = False
            self.model = None
            return False

    def encode(self, text: str) -> Optional[np.ndarray]:
        """
        Encode text thành vector 1024-dim.

        Returns:
            numpy array shape (1024,) hoặc None nếu lỗi.
        """
        if not self._is_loaded:
            if not self.load():
                return None

        if not text or not text.strip():
            logger.warning("Text rỗng, không thể embed")
            return None

        try:
            embedding = self.model.encode(
                text,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return embedding.astype(np.float32)
        except Exception as e:
            logger.error(f"Lỗi encode embedding: {e}")
            return None

    def unload(self):
        """Giải phóng model khỏi memory."""
        if self.model is not None:
            del self.model
            self.model = None
            self._is_loaded = False

            import gc
            gc.collect()

            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            logger.info("Đã unload embedding model")


class VectorStore:
    """Lưu trữ và tìm kiếm vectors trong SQLite (hỗ trợ chunked embeddings)."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # --- Legacy table (document_embeddings) ---

    @staticmethod
    def init_table(cursor: sqlite3.Cursor):
        """Tạo bảng document_embeddings (legacy). Gọi từ DocumentDatabase.init_db()."""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS document_embeddings (
                document_id INTEGER PRIMARY KEY,
                embedding BLOB NOT NULL,
                text_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_embeddings_docid '
            'ON document_embeddings(document_id)'
        )

    @staticmethod
    def delete_embedding(cursor: sqlite3.Cursor, doc_id: int):
        """Xóa embedding legacy của một tài liệu."""
        cursor.execute(
            'DELETE FROM document_embeddings WHERE document_id = ?',
            (doc_id,),
        )

    # --- Chunks table (document_chunks) ---

    @staticmethod
    def init_chunks_table(cursor: sqlite3.Cursor):
        """Tạo bảng document_chunks cho chunked embeddings."""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                page_number INTEGER,
                text_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
        ''')
        cursor.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_doc_chunk '
            'ON document_chunks(document_id, chunk_index)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_chunks_docid '
            'ON document_chunks(document_id)'
        )

    @staticmethod
    def delete_chunks(cursor: sqlite3.Cursor, doc_id: int):
        """Xóa tất cả chunks của một tài liệu."""
        cursor.execute(
            'DELETE FROM document_chunks WHERE document_id = ?',
            (doc_id,),
        )

    def upsert_chunks(
        self,
        doc_id: int,
        chunks: List[Tuple[str, np.ndarray, Optional[int]]],
    ):
        """
        Lưu nhiều chunks cho 1 document. Xóa chunks cũ trước khi insert mới.

        Args:
            doc_id: document ID
            chunks: List[(chunk_text, embedding_vector, page_number)]
        """
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            # Xóa chunks cũ
            conn.execute(
                'DELETE FROM document_chunks WHERE document_id = ?',
                (doc_id,),
            )
            # Insert chunks mới
            for idx, (text, embedding, page_number) in enumerate(chunks):
                blob = embedding.tobytes()
                text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                conn.execute(
                    '''INSERT INTO document_chunks
                        (document_id, chunk_index, chunk_text, embedding,
                         page_number, text_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                    (doc_id, idx, text, blob, page_number, text_hash),
                )
            conn.commit()

    def get_all_chunk_embeddings(self) -> List[Tuple[int, int, np.ndarray]]:
        """
        Load tất cả chunk embeddings từ database.

        Returns:
            List[(document_id, chunk_index, embedding_vector)]
        """
        results = []
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                cursor = conn.execute(
                    'SELECT document_id, chunk_index, embedding FROM document_chunks'
                )
                for doc_id, chunk_idx, blob in cursor.fetchall():
                    vec = np.frombuffer(blob, dtype=np.float32).copy()
                    results.append((doc_id, chunk_idx, vec))
        except Exception as e:
            logger.error(f"Lỗi load chunk embeddings: {e}")
        return results

    def search_chunks(
        self,
        query_embedding: np.ndarray,
        top_k: int = MAX_RESULTS,
        reranker=None,
        query_text: str = None,
    ) -> List[Tuple[int, float, str]]:
        """
        Tìm kiếm tài liệu bằng chunked cosine similarity.

        Flow:
        1. Tính cosine similarity giữa query và tất cả chunks
        2. Lấy top SEARCH_TOP_CHUNKS chunks
        3. Gộp score theo document: 0.6 * max + 0.4 * mean
        4. Trả về top_k documents

        Args:
            query_embedding: vector 1024-dim đã normalize
            top_k: số documents kết quả (default 10)

        Returns:
            List[(doc_id, document_score, best_chunk_text)]
        """
        import time as _t
        _s0 = _t.time()
        all_chunks = self.get_all_chunk_embeddings()
        _t_db = _t.time() - _s0

        if not all_chunks:
            # Fallback sang legacy search nếu chưa có chunks
            return self._search_legacy_as_chunks(query_embedding, top_k)

        _s1 = _t.time()
        doc_ids = [doc_id for doc_id, _, _ in all_chunks]
        chunk_indices = [idx for _, idx, _ in all_chunks]
        matrix = np.stack([vec for _, _, vec in all_chunks])

        # Cosine similarity
        similarities = matrix @ query_embedding
        _t_cosine = _t.time() - _s1
        logger.info(
            f"[SEARCH] chunks={len(all_chunks)} | db_load={_t_db*1000:.0f}ms | "
            f"cosine={_t_cosine*1000:.0f}ms"
        )

        # Lấy top chunks
        chunk_results = [
            (doc_ids[i], chunk_indices[i], float(similarities[i]))
            for i in range(len(similarities))
            if similarities[i] > 0
        ]
        chunk_results.sort(key=lambda x: x[2], reverse=True)
        top_chunks = chunk_results[:SEARCH_TOP_CHUNKS]

        if not top_chunks:
            return []

        # Gộp score theo document
        doc_scores: Dict[int, List[float]] = defaultdict(list)
        doc_best_chunk: Dict[int, Tuple[float, int]] = {}

        for doc_id, chunk_idx, score in top_chunks:
            doc_scores[doc_id].append(score)
            if doc_id not in doc_best_chunk or score > doc_best_chunk[doc_id][0]:
                doc_best_chunk[doc_id] = (score, chunk_idx)

        # Tính document score: weighted max + mean
        results = []
        for doc_id, scores in doc_scores.items():
            max_score = max(scores)
            mean_score = sum(scores) / len(scores)
            doc_score = SCORE_WEIGHT_MAX * max_score + SCORE_WEIGHT_MEAN * mean_score

            # Lấy text của best matching chunk
            best_chunk_idx = doc_best_chunk[doc_id][1]
            best_text = self._get_chunk_text(doc_id, best_chunk_idx)

            results.append((doc_id, doc_score, best_text))

        results.sort(key=lambda x: x[1], reverse=True)

        # Reranking step (if reranker provided)
        if reranker is not None and query_text and len(results) > 0:
            try:
                if reranker.is_available() or reranker._is_loaded:
                    _sr = _t.time()
                    candidates = results[:10]  # reduced 20→10 for CPU speed
                    texts_to_rerank = [chunk_text for _, _, chunk_text in candidates]
                    doc_ids_to_rerank = [doc_id for doc_id, _, _ in candidates]
                    original_chunks = [chunk_text for _, _, chunk_text in candidates]

                    rerank_scores = reranker.rerank(query_text, texts_to_rerank)

                    results = [
                        (doc_ids_to_rerank[i], rerank_scores[i], original_chunks[i])
                        for i in range(len(candidates))
                    ]
                    results.sort(key=lambda x: x[1], reverse=True)
                    logger.info(
                        f"[SEARCH] rerank={(_t.time()-_sr)*1000:.0f}ms "
                        f"({len(candidates)} candidates)"
                    )
            except Exception as e:
                logger.warning("Reranking failed, using cosine scores: %s", e)

        return results[:top_k]

    def _search_legacy_as_chunks(
        self,
        query_embedding: np.ndarray,
        top_k: int,
    ) -> List[Tuple[int, float, str]]:
        """Fallback: search bảng legacy document_embeddings, trả về format chunks."""
        all_embeddings = self.get_all_embeddings()
        if not all_embeddings:
            return []

        doc_ids = [doc_id for doc_id, _ in all_embeddings]
        matrix = np.stack([vec for _, vec in all_embeddings])
        similarities = matrix @ query_embedding

        results = [
            (doc_ids[i], float(similarities[i]), "")
            for i in range(len(similarities))
            if similarities[i] > 0
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _get_chunk_text(self, doc_id: int, chunk_index: int) -> str:
        """Lấy text của một chunk cụ thể."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                cursor = conn.execute(
                    'SELECT chunk_text FROM document_chunks '
                    'WHERE document_id = ? AND chunk_index = ?',
                    (doc_id, chunk_index),
                )
                row = cursor.fetchone()
                return row[0] if row else ""
        except Exception:
            return ""

    # --- Legacy methods (backward compatible) ---

    def upsert_embedding(
        self, doc_id: int, embedding: np.ndarray, text_hash: str = ""
    ):
        """(Legacy) Chèn hoặc cập nhật embedding cho một tài liệu."""
        blob = embedding.tobytes()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                '''INSERT OR REPLACE INTO document_embeddings
                    (document_id, embedding, text_hash, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)''',
                (doc_id, blob, text_hash),
            )
            conn.commit()

    def get_all_embeddings(self) -> List[Tuple[int, np.ndarray]]:
        """(Legacy) Load tất cả embeddings từ bảng document_embeddings."""
        results = []
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                cursor = conn.execute(
                    'SELECT document_id, embedding FROM document_embeddings'
                )
                for doc_id, blob in cursor.fetchall():
                    vec = np.frombuffer(blob, dtype=np.float32).copy()
                    results.append((doc_id, vec))
        except Exception as e:
            logger.error(f"Lỗi load embeddings: {e}")
        return results

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = MAX_RESULTS,
    ) -> List[Tuple[int, float]]:
        """(Legacy) Tìm kiếm trên bảng document_embeddings."""
        all_embeddings = self.get_all_embeddings()
        if not all_embeddings:
            return []

        doc_ids = [doc_id for doc_id, _ in all_embeddings]
        matrix = np.stack([vec for _, vec in all_embeddings])
        similarities = matrix @ query_embedding

        results = [
            (doc_ids[i], float(score))
            for i, score in enumerate(similarities)
            if score > 0
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_embedding_count(self) -> int:
        """Đếm số documents đã có chunks (hoặc legacy embeddings)."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                # Ưu tiên đếm từ chunks table
                cursor = conn.execute(
                    'SELECT COUNT(DISTINCT document_id) FROM document_chunks'
                )
                count = cursor.fetchone()[0]
                if count > 0:
                    return count
                # Fallback sang legacy
                cursor = conn.execute(
                    'SELECT COUNT(*) FROM document_embeddings'
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0


def embed_document_chunks(
    embedding_model: EmbeddingModel,
    vector_store: VectorStore,
    doc_id: int,
    version_row,
) -> int:
    """
    Embed một tài liệu thành nhiều chunks.

    Args:
        embedding_model: instance EmbeddingModel
        vector_store: instance VectorStore
        doc_id: document ID
        version_row: Row từ get_latest_version()
            Index: [3]=cqbh_tren, [4]=cqbh_duoi, [5]=so_ki_hieu,
                   [9]=loai_vb, [10]=nd_chinh, [16]=van_ban_dang_txt

    Returns:
        Số chunks đã embed thành công. 0 nếu lỗi.
    """
    try:
        chunk_texts = build_chunk_texts(
            van_ban_dang_txt=version_row["van_ban_dang_txt"] or "",
            nd_chinh=version_row[10] or "",
            so_ki_hieu=version_row[5] or "",
            loai_vb=version_row[9] or "",
            cqbh_tren=version_row[3] or "",
            cqbh_duoi=version_row[4] or "",
        )

        if not chunk_texts:
            logger.warning(f"Tài liệu {doc_id} không có text để embed")
            return 0

        # Embed từng chunk
        embedded_chunks = []
        for chunk_text_item, page_number in chunk_texts:
            embedding = embedding_model.encode(chunk_text_item)
            if embedding is not None:
                embedded_chunks.append((chunk_text_item, embedding, page_number))

        if not embedded_chunks:
            return 0

        # Lưu vào DB
        vector_store.upsert_chunks(doc_id, embedded_chunks)
        logger.info(
            f"Đã embed tài liệu {doc_id}: {len(embedded_chunks)} chunks "
            f"(tổng {sum(len(text) for text, _, _ in embedded_chunks)} chars)"
        )
        return len(embedded_chunks)

    except Exception as e:
        logger.error(f"Lỗi embed chunks tài liệu {doc_id}: {e}")
        return 0


def embed_document(
    embedding_model: EmbeddingModel,
    vector_store: VectorStore,
    doc_id: int,
    version_row,
) -> bool:
    """
    (Legacy) Embed một tài liệu thành 1 vector duy nhất.
    Giữ cho backward compatibility. Dùng embed_document_chunks() cho chunked.
    """
    try:
        text = build_embedding_text(
            van_ban_dang_txt=version_row["van_ban_dang_txt"] or "",
            nd_chinh=version_row[10] or "",
            so_ki_hieu=version_row[5] or "",
            loai_vb=version_row[9] or "",
            cqbh_tren=version_row[3] or "",
            cqbh_duoi=version_row[4] or "",
        )

        if not text.strip():
            logger.warning(f"Tài liệu {doc_id} không có text để embed")
            return False

        embedding = embedding_model.encode(text)
        if embedding is None:
            return False

        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        vector_store.upsert_embedding(doc_id, embedding, text_hash)
        logger.info(f"Đã embed tài liệu {doc_id} (text length: {len(text)})")
        return True

    except Exception as e:
        logger.error(f"Lỗi embed tài liệu {doc_id}: {e}")
        return False
