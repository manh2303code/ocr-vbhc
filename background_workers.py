"""Qt workers for OCR, embedding, batch processing and file-path repair."""
import logging
import os
import sqlite3
import threading
import traceback
from contextlib import closing
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from ai_search import VectorStore, embed_document_chunks
from legal_graph import GraphStore, compute_validity, init_tables
from legal_relations import extract_relations


logger = logging.getLogger("BackgroundWorkers")
BACKUP_DIR = Path(__file__).parent / "backup"


def has_ocr_content(result) -> bool:
    return isinstance(result, dict) and bool(
        str(result.get("Van_ban_dang_txt", "")).strip()
    )


class EmbeddingWorker(QThread):
    done = pyqtSignal(int, int)
    error = pyqtSignal(int, str)

    def __init__(self, embedding_model, vector_store, doc_id, version_row):
        super().__init__()
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.doc_id = doc_id
        self.version_row = version_row

    def run(self):
        try:
            count = embed_document_chunks(
                self.embedding_model, self.vector_store, self.doc_id, self.version_row
            )
            self.done.emit(self.doc_id, count or 0)
        except Exception as exc:
            logger.warning("Embedding doc %s failed: %s", self.doc_id, exc)
            logger.debug(traceback.format_exc())
            self.error.emit(self.doc_id, str(exc))


def _version_mapping(conn, version_row):
    if isinstance(version_row, dict):
        return version_row
    if hasattr(version_row, "keys"):
        return dict(version_row)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(document_versions)")]
    return dict(zip(columns, version_row))


def index_document_relations(db_path, doc_id, version_row) -> int:
    """Extract and persist one document's legal graph data."""
    from bs4 import BeautifulSoup

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.row_factory = sqlite3.Row
        init_tables(conn.cursor())
        version = _version_mapping(conn, version_row)
        text = BeautifulSoup(
            str(version.get("van_ban_dang_txt") or ""), "html.parser"
        ).get_text(separator="\n", strip=True)
        relations = extract_relations(text)
        store = GraphStore(conn)
        old_targets = {
            row[0]
            for row in conn.execute(
                "SELECT target_doc_id FROM doc_relations "
                "WHERE source_doc_id = ? AND target_doc_id IS NOT NULL",
                (doc_id,),
            )
        }
        store.replace_relations(doc_id, relations)
        store.upsert_entities_from_version(doc_id, version)
        store.resolve_targets()
        new_targets = {
            row[0]
            for row in conn.execute(
                "SELECT target_doc_id FROM doc_relations "
                "WHERE source_doc_id = ? AND target_doc_id IS NOT NULL",
                (doc_id,),
            )
        }
        compute_validity(conn, [doc_id, *old_targets, *new_targets])
        return len(relations)


class RelationWorker(QThread):
    done = pyqtSignal(int, int)
    error = pyqtSignal(int, str)

    def __init__(self, db_path, doc_id, version_row):
        super().__init__()
        self.db_path = db_path
        self.doc_id = doc_id
        self.version_row = version_row

    def run(self):
        try:
            count = index_document_relations(
                self.db_path, self.doc_id, self.version_row
            )
            self.done.emit(self.doc_id, count)
        except Exception as exc:
            logger.warning("Relation indexing for doc %s failed: %s", self.doc_id, exc)
            logger.debug(traceback.format_exc())
            self.error.emit(self.doc_id, str(exc))


def reindex_documents(
    db_path,
    embedding_model,
    doc_ids=None,
    cancel_check=None,
    progress_cb=None,
) -> int:
    """Rebuild chunks, vector index and legal graph without depending on Qt."""
    db_path = str(db_path)
    cancel_check = cancel_check or (lambda: False)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        if doc_ids is None:
            rows = conn.execute(
                "SELECT id, file_name FROM documents ORDER BY id"
            ).fetchall()
        else:
            requested = list(dict.fromkeys(int(doc_id) for doc_id in doc_ids))
            if not requested:
                rows = []
            else:
                placeholders = ",".join("?" for _ in requested)
                found = dict(
                    conn.execute(
                        "SELECT id, file_name FROM documents WHERE id IN ({})".format(
                            placeholders
                        ),
                        requested,
                    ).fetchall()
                )
                missing = [doc_id for doc_id in requested if doc_id not in found]
                if missing:
                    raise ValueError(
                        "Không tìm thấy tài liệu ID: {}".format(
                            ", ".join(map(str, missing))
                        )
                    )
                rows = [(doc_id, found[doc_id]) for doc_id in requested]

    total = len(rows)
    completed = 0
    vector_store = VectorStore(db_path)
    try:
        for index, (doc_id, file_name) in enumerate(rows):
            if cancel_check():
                break
            if progress_cb:
                progress_cb(index, total, file_name or "ID {}".format(doc_id))
            # A row is created and consumed in this iteration only; no pooled
            # repository connection crosses the worker-thread boundary.
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.row_factory = sqlite3.Row
                version = conn.execute(
                    "SELECT * FROM document_versions WHERE document_id = ? "
                    "ORDER BY version_number DESC, id DESC LIMIT 1",
                    (doc_id,),
                ).fetchone()
            if version is None:
                raise ValueError("Tài liệu ID {} chưa có phiên bản OCR".format(doc_id))
            chunk_count = embed_document_chunks(
                embedding_model, vector_store, doc_id, version
            )
            index_document_relations(db_path, doc_id, version)
            if chunk_count:
                completed += 1
            if progress_cb:
                progress_cb(index + 1, total, file_name or "ID {}".format(doc_id))
    finally:
        # VectorStore writes the canonical chunks. Rebuild sqlite-vec once so
        # replaced rowids and cooperative partial runs cannot leave stale hits.
        from vector_index import VectorIndex

        with closing(sqlite3.connect(db_path)) as conn, conn:
            VectorIndex(conn).sync_from_chunks(conn)
            conn.commit()
    return completed


class ReindexWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db_path, embedding_model, doc_ids=None):
        super().__init__()
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.doc_ids = doc_ids
        self.canceled = False

    def run(self):
        try:
            count = reindex_documents(
                self.db_path,
                self.embedding_model,
                self.doc_ids,
                cancel_check=lambda: self.canceled or self.isInterruptionRequested(),
                progress_cb=self.progress.emit,
            )
            self.finished_ok.emit(count)
        except Exception as exc:
            logger.warning("Reindex failed: %s", exc)
            logger.debug(traceback.format_exc())
            self.error.emit(str(exc))

    def cancel(self):
        self.canceled = True
        self.requestInterruption()


class _StreamCancelled(Exception):
    """Raised from the token callback to abandon an answer mid-stream."""


class AgentWorker(QThread):
    """Run an injected RAG agent and bridge its callbacks to Qt signals."""

    step = pyqtSignal(str)
    token = pyqtSignal(str)
    sources = pyqtSignal(object)
    finished_result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, agent, question, history=None):
        super().__init__()
        self.agent = agent
        self.question = question
        self.history = history
        self.canceled = False

    def run(self):
        old_step = self.agent.step_cb
        old_token = self.agent.token_cb
        old_cancel = self.agent.cancel_check
        # Agents without the hook (older fakes, other agents) keep working.
        has_sources = hasattr(self.agent, "sources_cb")
        old_sources = getattr(self.agent, "sources_cb", None)

        def emit_step(value):
            if old_step:
                old_step(value)
            self.step.emit(value)

        def emit_token(value):
            # The agent only checks cancel_check between model calls, so a
            # Stop during a long answer used to wait for the whole answer.
            # Raising here leaves the HTTP stream, and closing it is what
            # makes llama-server stop generating.
            if self.canceled:
                raise _StreamCancelled()
            if old_token:
                old_token(value)
            self.token.emit(value)

        def emit_sources(value):
            if old_sources:
                old_sources(value)
            self.sources.emit(list(value))

        self.agent.step_cb = emit_step
        self.agent.token_cb = emit_token
        self.agent.cancel_check = lambda: self.canceled or bool(
            old_cancel and old_cancel()
        )
        if has_sources:
            self.agent.sources_cb = emit_sources
        try:
            self.finished_result.emit(
                self.agent.ask(self.question, history=self.history)
            )
        except _StreamCancelled:
            from rag_agent import AgentResult

            self.finished_result.emit(AgentResult("", [], [], "cancelled"))
        except Exception as exc:
            logger.warning("Agent execution failed")
            self.error.emit(str(exc))
        finally:
            self.agent.step_cb = old_step
            self.agent.token_cb = old_token
            self.agent.cancel_check = old_cancel
            if has_sources:
                self.agent.sources_cb = old_sources

    def cancel(self):
        self.canceled = True


class OCRWorker(QThread):
    finished = pyqtSignal(dict, list)
    progress = pyqtSignal(int, int, str)
    error = pyqtSignal(str)

    def __init__(self, ocr_system, file_path):
        super().__init__()
        self.ocr_system = ocr_system
        self.file_path = file_path
        self.canceled = False

    def run(self):
        if not os.path.exists(self.file_path):
            self.error.emit(f"File not found: {self.file_path}")
            return

        def progress_callback(fraction, _total, message=""):
            self.progress.emit(int(min(100, fraction * 100)), 100, message)
            return not self.canceled

        try:
            result, detections = self.ocr_system.process_document_chandra(
                self.file_path, progress_callback=progress_callback
            )
            if self.canceled:
                return
            if not has_ocr_content(result):
                self.error.emit("OCR không trả về nội dung văn bản hợp lệ")
                return
            self.finished.emit(result, detections or [])
        except Exception as exc:
            logger.error("OCR document processing error: %s", exc)
            logger.error(traceback.format_exc())
            if not self.canceled:
                self.error.emit(str(exc))

    def cancel(self):
        self.canceled = True


class BatchProcessWorker(QThread):
    progress = pyqtSignal(int, int, str)
    document_ready = pyqtSignal(str, object, object)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, ocr_system, file_paths, collect_results=True):
        super().__init__()
        self.ocr_system = ocr_system
        self.file_paths = file_paths
        self.collect_results = bool(collect_results)
        self.canceled = False

    def run(self):
        results = []
        total = len(self.file_paths)
        try:
            for index, file_path in enumerate(self.file_paths):
                if self.canceled:
                    break
                if not os.path.exists(file_path):
                    logger.warning("File not found: %s", file_path)
                    continue

                filename = os.path.basename(file_path)

                def progress_callback(fraction, _total, message=""):
                    percent = int(min(100, (index + fraction) / total * 100))
                    self.progress.emit(
                        percent, 100, f"Tệp {index + 1}/{total}: {filename} — {message}"
                    )
                    return not self.canceled

                self.progress.emit(
                    int(index / total * 100), 100,
                    f"Tệp {index + 1}/{total}: {filename}",
                )
                result, detections = self.ocr_system.process_document_chandra(
                    file_path, progress_callback=progress_callback
                )
                if has_ocr_content(result):
                    pages = detections or []
                    self.document_ready.emit(file_path, result, pages)
                    if self.collect_results:
                        results.append((file_path, result, pages))
                else:
                    logger.error("OCR returned no content for %s", file_path)
            if not self.canceled:
                self.finished.emit(results)
        except Exception as exc:
            if not self.canceled:
                logger.error("Batch processing error: %s", exc)
                self.error.emit(str(exc))

    def cancel(self):
        self.canceled = True


class FileRepairWorker(QThread):
    progress = pyqtSignal(int, int, str)
    file_path_request = pyqtSignal(str, str)
    finished = pyqtSignal(list, list)
    error = pyqtSignal(str)

    def __init__(self, db, missing_files):
        super().__init__()
        self.db = db
        self.missing_files = missing_files
        self.canceled = False
        self.file_path_response = None
        self.wait_condition = threading.Condition()

    def run(self):
        fixed, remaining = [], []
        total = len(self.missing_files)
        try:
            for index, (doc_id, old_path) in enumerate(self.missing_files, 1):
                if self.canceled:
                    break
                self.progress.emit(index, total, f"Checking: {os.path.basename(old_path)}")
                if os.path.exists(old_path):
                    fixed.append((doc_id, old_path))
                    continue

                backup_path = BACKUP_DIR / Path(old_path).name
                if backup_path.exists() and self.db.update_file_path(doc_id, str(backup_path)):
                    fixed.append((doc_id, str(backup_path)))
                    continue

                self.file_path_response = None
                with self.wait_condition:
                    self.file_path_request.emit(str(doc_id), old_path)
                    self.wait_condition.wait(30)
                new_path = self.file_path_response
                if new_path and os.path.exists(new_path):
                    if self.db.update_file_path(doc_id, new_path):
                        fixed.append((doc_id, new_path))
                        continue
                remaining.append((doc_id, old_path))
            if not self.canceled:
                self.finished.emit(fixed, remaining)
        except Exception as exc:
            if not self.canceled:
                logger.error("File repair error: %s", exc)
                self.error.emit(str(exc))

    def set_file_path_response(self, file_path):
        with self.wait_condition:
            self.file_path_response = file_path
            self.wait_condition.notify()

    def cancel(self):
        self.canceled = True
        with self.wait_condition:
            self.wait_condition.notify()
