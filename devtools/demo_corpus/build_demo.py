"""Build the fictional demo corpus: PDFs, then a demo database for screenshots.

    runtime\\python.exe -X utf8 devtools\\demo_corpus\\build_demo.py pdf
    runtime\\python.exe -X utf8 devtools\\demo_corpus\\build_demo.py db  DB_PATH
    runtime\\python.exe -X utf8 devtools\\demo_corpus\\build_demo.py all DB_PATH

pdf  writes html/<key>.html (A4, NĐ30 layout, Times New Roman) and prints
     each to pdf/<file> with Edge headless (a scratch profile), finds the
     page each block starts on in the printed text and writes that to
     pdf/layout.json (it fails when a block or the signer is missing, or a
     block lands on a page of the wrong orientation).
db   creates DB_PATH from scratch with the app's own DocumentDatabase, adds
     every document the way the OCR step does (Chandra-style page HTML ->
     parse_chandra_html -> add_document), keeps the reviewed NĐ30 fields,
     sets when each entered the store and was last reviewed, then indexes
     them with the app's reindex (chunks, bge-m3 vectors on the CPU,
     full-text, legal relations).

Every document is invented (see corpus.py). DB_PATH belongs outside the
repository, e.g. ..\\readme_demo\\demo.db.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402

HTML_DIR = HERE / "html"
PDF_DIR = HERE / "pdf"
LAYOUT = PDF_DIR / "layout.json"  # the page each block starts on, measured
EDGE = Path(os.environ.get("EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))

# When each document entered the store (documents.created_at, local time):
# a few days after it was issued, so the analytics show a year of intake.
ADDED = {
    "nq15": "2025-12-15 09:12", "ttr47": "2026-02-04 14:30", "qd210": "2026-02-23 08:45",
    "kh125": "2026-03-16 10:05", "hd12": "2026-03-27 15:20", "cv33": "2026-04-09 09:40",
    "ct07": "2026-04-24 16:10", "bc52": "2026-07-08 10:25", "tb18": "2026-07-30 14:05",
    "cv1475": "2026-10-05 08:50",
}

# When each was last reviewed (documents.last_modified), relative to the
# build: the home page lists the eight most recent. Oldest first.
SCANNED_AGO = {
    "nq15": timedelta(days=21, hours=3),
    "ttr47": timedelta(days=16, hours=6),
    "qd210": timedelta(days=12, hours=2),
    "kh125": timedelta(days=9, hours=5),
    "hd12": timedelta(days=7, hours=1),
    "cv33": timedelta(days=5, hours=4),
    "ct07": timedelta(days=3, hours=2),
    "bc52": timedelta(days=1, hours=2),
    "tb18": timedelta(hours=3, minutes=10),
    "cv1475": timedelta(minutes=26),
}


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def build_pdfs():
    import fitz

    HTML_DIR.mkdir(exist_ok=True)
    PDF_DIR.mkdir(exist_ok=True)
    profile = tempfile.mkdtemp(prefix="edge-demo-")
    problems = 0
    layout = {}
    for doc in corpus.DOCS:
        html_path = HTML_DIR / "{}.html".format(doc["key"])
        with open(html_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(corpus.print_html(doc))
        pdf_path = PDF_DIR / doc["file"]
        if pdf_path.exists():
            pdf_path.unlink()
        subprocess.run(
            [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--user-data-dir={}".format(profile),
             "--no-pdf-header-footer", "--print-to-pdf-no-header",
             "--print-to-pdf={}".format(pdf_path), html_path.resolve().as_uri()],
            check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with fitz.open(str(pdf_path)) as pdf:
            texts = [_norm(page.get_text()) for page in pdf]
            sizes = ["L" if page.rect.width > page.rect.height else "P" for page in pdf]
        # Where each block starts: the first page, from the previous block's
        # place onwards, whose text holds the block's probe.
        pages, status = [], "ok"
        page, offset = 0, 0
        for orient, block in corpus.flow(doc):
            probe = corpus.probe(block)
            while page < len(texts):
                found = texts[page].find(probe, offset)
                if found >= 0:
                    offset = found + 1
                    break
                page, offset = page + 1, 0
            if page >= len(texts):
                status = "not found: {!r}".format(probe)
                break
            if sizes[page] != orient[0].upper():
                status = "page {} is {} for a {} block".format(page + 1, sizes[page], orient)
            pages.append(page + 1)
        if status == "ok" and _norm(doc["signer"]) not in " ".join(texts):
            status = "signer missing"
        if status != "ok":
            problems += 1
        layout[doc["key"]] = pages
        print("{:<8} {:<18} {} page(s) {:<4} {:>6} KB  {}".format(
            doc["key"], doc["so"], len(texts), "".join(sizes),
            pdf_path.stat().st_size // 1024, status))
    with open(LAYOUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("{\n" + ",\n".join(
            ' "{}": {}'.format(key, json.dumps(pages)) for key, pages in layout.items()) + "\n}\n")
    return problems


def load_layout():
    with open(LAYOUT, encoding="utf-8") as handle:
        return json.load(handle)


def _utc(delta, now):
    return (now - delta).strftime("%Y-%m-%d %H:%M:%S")


def build_db(db_path):
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")  # the embedding runs on the CPU
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    db_path = Path(db_path).resolve()
    if ROOT in db_path.parents:
        raise SystemExit("keep the demo database out of the repository: {}".format(db_path))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(db_path) + suffix)
        if stale.exists():
            stale.unlink()

    from chandra_field_parser import parse_chandra_html
    from document_repository import DocumentDatabase

    db = DocumentDatabase(str(db_path))
    now = datetime.now(timezone.utc)
    agreed = total = 0
    if not LAYOUT.is_file():
        raise SystemExit("run the pdf step first: {} is missing".format(LAYOUT))
    layout = load_layout()
    for doc in corpus.DOCS:
        pdf_path = PDF_DIR / doc["file"]
        if not pdf_path.is_file():
            raise SystemExit("run the pdf step first: {} is missing".format(pdf_path))
        pages, widths, orients = corpus.chandra_pages(doc, layout[doc["key"]])
        parsed = parse_chandra_html(pages, page_widths=widths, orientations=orients)
        reviewed = corpus.metadata(doc)
        diffs = []
        for key, value in reviewed.items():
            total += 1
            if _norm(parsed.get(key)) == _norm(value):
                agreed += 1
            else:
                diffs.append("{}: {!r} -> {!r}".format(key, parsed.get(key), value))
        result = dict(parsed, **reviewed)
        doc_id = db.add_document(str(pdf_path), result, page_count=len(pages),
                                 model_key="chandra-q4")
        reviewed_at = _utc(SCANNED_AGO[doc["key"]], now)
        added = datetime.strptime(ADDED[doc["key"]], "%Y-%m-%d %H:%M").astimezone(
            timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(str(db_path))) as conn, conn:
            conn.execute("UPDATE documents SET created_at = ?, last_modified = ? WHERE id = ?",
                         (added, reviewed_at, doc_id))
            conn.execute("UPDATE document_versions SET created_at = ? WHERE document_id = ?",
                         (added, doc_id))
        print("#{:<2} {:<18} {} page(s)  parser kept {}/12{}".format(
            doc_id, doc["so"], len(pages), 12 - len(diffs),
            "".join("\n      corrected " + d for d in diffs)))
    print("parser agreement before review: {}/{} fields".format(agreed, total))
    db.conn_pool.close_all() if hasattr(db.conn_pool, "close_all") else None

    from ai_search import EmbeddingModel
    from background_workers import reindex_documents

    model = EmbeddingModel()
    if not model.load():
        print("embedding model unavailable: documents stored without chunks")
        return 1
    done = reindex_documents(str(db_path), model)
    with closing(sqlite3.connect(str(db_path))) as conn:
        counts = {table: conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
                  for table in ("documents", "document_chunks", "doc_relations")}
        vectors = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL").fetchone()[0]
    print("indexed {} documents: {} chunks ({} with vectors), {} legal relations".format(
        done, counts["document_chunks"], vectors, counts["doc_relations"]))
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] not in ("pdf", "db", "all"):
        print(__doc__)
        return 2
    if argv[1] in ("pdf", "all"):
        if build_pdfs():
            print("some pages did not come out as planned")
            return 1
    if argv[1] in ("db", "all"):
        if len(argv) < 3:
            print(__doc__)
            return 2
        return build_db(argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
