"""Small read-only dashboard query used before OCR or LLM models start."""
from pathlib import Path
import sqlite3
from contextlib import closing

from ui_commands import landing_action_specs


class LandingSnapshotService:
    def __init__(self, db_path, base_dir=None, theme_getter=None):
        self.db_path = Path(db_path)
        self.base_dir = Path(base_dir or self.db_path.parent.parent)
        self.theme_getter = theme_getter or (lambda: "light")

    def get_snapshot(self):
        documents, chunks, recent = 0, 0, []
        theme = self.theme_getter()
        if self.db_path.is_file():
            try:
                uri = "file:{}?mode=ro".format(self.db_path.resolve().as_posix())
                with closing(sqlite3.connect(uri, uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    documents = self._count(conn, "documents")
                    chunks = self._count(conn, "document_chunks")
                    recent = self._recent(conn)
            except sqlite3.Error:
                pass

        return {
            "documentCount": documents,
            "chunkCount": chunks,
            "recentDocuments": recent,
            "models": {
                "ocr": "available" if self._ocr_available() else "missing",
                "chat": "available" if self._chat_available() else "missing",
            },
            "theme": theme if theme in ("light", "dark") else "light",
            "offline": True,
            "commands": landing_action_specs(),
        }

    @staticmethod
    def _count(conn, table):
        try:
            return int(conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0])
        except sqlite3.Error:
            return 0

    @staticmethod
    def _recent(conn):
        try:
            rows = conn.execute(
                """
                SELECT d.id, d.file_name, d.last_modified, d.page_count,
                       COALESCE(v.so_ki_hieu, '') AS so_ki_hieu
                FROM documents AS d
                LEFT JOIN document_versions AS v
                  ON v.document_id = d.id
                 AND v.version_number = (
                     SELECT MAX(v2.version_number)
                     FROM document_versions AS v2
                     WHERE v2.document_id = d.id
                 )
                ORDER BY d.last_modified DESC, d.id DESC
                LIMIT 5
                """
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "id": int(row["id"]),
                "title": row["file_name"] or "Văn bản không tên",
                "symbol": row["so_ki_hieu"] or "",
                "updatedAt": row["last_modified"] or "",
                "pageCount": int(row["page_count"] or 0),
            }
            for row in rows
        ]

    def _ocr_available(self):
        root = self.base_dir / "gguf" / "chandra"
        return all((root / name).is_file() for name in (
            "chandra-ocr-2-q4_k_m.gguf",
            "chandra-ocr-2.mmproj-q8_0.gguf",
        ))

    def _chat_available(self):
        return any((self.base_dir / "gguf" / "chat").glob("*.gguf"))
