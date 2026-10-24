"""Re-OCR documents of an OCR VBHC database with the current pipeline.

    runtime\\python.exe tools\\reocr_documents.py --db database\\documents.db --doc-ids 2 9

For every id: OCR the stored source file again through DocumentOCR (thinking
off, one KV slot per worker, page quality gate), save the result as a new
version, then rebuild the document's chunks, chunk_vectors and legal graph
(background_workers.reindex_documents). Prints one JSON report per document.

Meant for documents OCR'd before the 2026-10 quality gate, whose pages may
hold the model's English reasoning instead of the page text. Close the app
first and keep a backup of the database. The tool starts its own Chandra
server on --port (default 9989, never the app's 9998/9999).
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

# Embedded Python ignores the script directory; the app modules live one up.
APP_DIR = Path(__file__).resolve().parents[1]
APP_PORTS = (9998, 9999)


def _prose_chunks(db_path, doc_id):
    from llama_ocr import looks_like_reasoning

    with closing(sqlite3.connect(str(db_path))) as conn:
        texts = [row[0] for row in conn.execute(
            "SELECT chunk_text FROM document_chunks WHERE document_id = ?", (doc_id,))]
    return len(texts), sum(1 for text in texts if looks_like_reasoning(text))


def _vector_gaps(db_path):
    from vector_index import init_vec

    with closing(sqlite3.connect(str(db_path))) as conn:
        if not init_vec(conn):
            return None
        chunks = {r[0] for r in conn.execute(
            "SELECT id FROM document_chunks WHERE embedding IS NOT NULL")}
        vectors = {r[0] for r in conn.execute("SELECT rowid FROM chunk_vectors")}
    return {"missing": len(chunks - vectors), "orphans": len(vectors - chunks)}


def _copy_db(src_path, dst_path):
    with closing(sqlite3.connect(str(src_path))) as src, \
            closing(sqlite3.connect(str(dst_path))) as dst:
        src.backup(dst)


def _backup_path(db_path):
    """<db>.before-reocr, or .before-reocr.2, .3, ... so a rerun never
    overwrites the copy taken before the first run."""
    backup = db_path.with_name(db_path.name + ".before-reocr")
    n = 2
    while backup.exists():
        backup = db_path.with_name("{}.before-reocr.{}".format(db_path.name, n))
        n += 1
    return backup


def _source_path(stored, source_dir):
    path = Path(stored)
    if source_dir:
        path = Path(source_dir) / path.name
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="documents.db to update")
    parser.add_argument("--doc-ids", type=int, nargs="+", required=True)
    parser.add_argument("--source-dir", help="look source files up by name here")
    parser.add_argument("--port", type=int, default=9989)
    parser.add_argument("--no-backup", action="store_true",
                        help="do not copy the database to <db>.before-reocr[.N] first")
    parser.add_argument("--dry-run", action="store_true",
                        help="OCR and report on a temporary copy; the database is not written")
    args = parser.parse_args(argv)
    if args.port in APP_PORTS:
        parser.error("ports 9998/9999 belong to the running app; pick another")

    os.environ["CHANDRA_PORT"] = str(args.port)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    sys.path.insert(0, str(APP_DIR))

    from document_repository import DocumentDatabase

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        parser.error("database not found: {}".format(db_path))
    scratch = None
    if args.dry_run:
        # Opening a database migrates it and resyncs chunk_vectors, so a dry
        # run reads a throwaway copy and the real file is never written.
        scratch = tempfile.mkdtemp(prefix="reocr-dry-")
        copy = Path(scratch) / db_path.name
        _copy_db(db_path, copy)
        print("dry run on a copy:", copy, flush=True)
        db_path = copy
    elif not args.no_backup:
        backup = _backup_path(db_path)
        _copy_db(db_path, backup)
        print("backup:", backup, flush=True)
    try:
        return _run(args, parser, db_path, DocumentDatabase)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _run(args, parser, db_path, DocumentDatabase):
    db = DocumentDatabase(db_path)  # opening also resyncs chunk_vectors
    jobs = []
    for doc_id in args.doc_ids:
        info = db.get_document_info(doc_id)
        problem = None
        if not info:
            problem = "document {} not found".format(doc_id)
        else:
            source = _source_path(info["file_path"], args.source_dir)
            if not source.is_file():
                problem = "source of document {} not found: {}".format(doc_id, source)
        if problem:
            db.close()
            parser.error(problem)
        jobs.append((doc_id, source))

    from ocr_pipeline import DocumentOCR

    reports = []
    ocr = DocumentOCR()
    try:
        for doc_id, source in jobs:
            chunks_before, prose_before = _prose_chunks(db_path, doc_id)
            started = time.time()
            result, _pages = ocr.process_document_chandra(str(source))
            report = {
                "doc_id": doc_id, "source": source.name,
                "ocr_seconds": round(time.time() - started, 1),
                "chunks_before": chunks_before, "prose_chunks_before": prose_before,
            }
            if result is None:
                report["status"] = "ocr_failed"
            else:
                report["failed_pages"] = result.get("OCR_Failed_Pages", [])
                report["warning"] = result.get("OCR_Warning", "")
                if args.dry_run:
                    report["status"] = "dry_run"
                else:
                    version = db.replace_ocr_result(doc_id, result, ocr.ocr_model.current_model_key,
                                                    modified_by_suffix="lại")
                    report["status"] = "saved"
                    report["version"] = version
            reports.append(report)
    finally:
        try:
            ocr.fullpage_ocr.close()
        except Exception:
            pass
        ocr._chandra_server.stop()

    saved = [r["doc_id"] for r in reports if r.get("status") == "saved"]
    if saved:
        from ai_search import EmbeddingModel
        from background_workers import reindex_documents

        embedding = EmbeddingModel()
        if not embedding.load():
            raise SystemExit("embedding model failed to load; run Xây lại chỉ mục in the app")
        db.close()
        reindex_documents(str(db_path), embedding, doc_ids=saved)
        for report in reports:
            if report["doc_id"] in saved:
                after, prose_after = _prose_chunks(db_path, report["doc_id"])
                report["chunks_after"] = after
                report["prose_chunks_after"] = prose_after
    else:
        db.close()

    gaps = _vector_gaps(db_path)
    for report in reports:
        print(json.dumps(report, ensure_ascii=False), flush=True)
    print(json.dumps({"chunk_vectors": gaps}), flush=True)
    return 0 if all(r.get("status") in ("saved", "dry_run") for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
