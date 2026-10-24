"""Small read-only dashboard query used before OCR or LLM models start."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from contextlib import closing

from ui_commands import landing_action_specs

# The home page lists this many of the most recently changed documents.
RECENT_LIMIT = 8
# Enough for a tooltip; the register cell itself clips to one line.
SUMMARY_LIMIT = 300
# What each home-page action does, shown as its tooltip above the shortcut.
# The page and the native fallback both read these from the snapshot.
ACTION_HINTS = {
    "open_file": "Nhận dạng một tệp PDF hoặc ảnh và trích các trường thông tin",
    "open_files": "Chọn nhiều tệp cùng lúc; ứng dụng xử lý lần lượt theo lô",
    "import_folder": "Nhập mọi tệp PDF và ảnh trong một thư mục, kể cả thư mục con",
    "open_library": "Duyệt, tìm và chỉnh sửa mọi văn bản trong kho",
    "open_chat": "Hỏi về nội dung, căn cứ, hiệu lực văn bản trong kho;\ncâu trả lời kèm nguồn trích dẫn",
}


def landing_commands():
    """The catalogue's landing actions, each with its tooltip text."""
    return [
        dict(spec, hint=ACTION_HINTS.get(spec["action"], ""))
        for spec in landing_action_specs()
    ]


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
            "commands": landing_commands(),
        }

    @staticmethod
    def _count(conn, table):
        try:
            return int(conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0])
        except sqlite3.Error:
            return 0

    @staticmethod
    def _version_columns(conn):
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(document_versions)")}
        except sqlite3.Error:
            return set()

    @classmethod
    def _recent(cls, conn):
        # nd_chinh (trích yếu) arrived with the NĐ30 fields; an older or
        # partial database without it still lists its documents.
        summary = "v.nd_chinh" if "nd_chinh" in cls._version_columns(conn) else "''"
        try:
            rows = conn.execute(
                """
                SELECT d.id, d.file_name, d.last_modified, d.page_count,
                       COALESCE(v.so_ki_hieu, '') AS so_ki_hieu,
                       COALESCE({summary}, '') AS trich_yeu
                FROM documents AS d
                LEFT JOIN document_versions AS v
                  ON v.document_id = d.id
                 AND v.version_number = (
                     SELECT MAX(v2.version_number)
                     FROM document_versions AS v2
                     WHERE v2.document_id = d.id
                 )
                ORDER BY d.last_modified DESC, d.id DESC
                LIMIT ?
                """.format(summary=summary),
                (RECENT_LIMIT,),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "id": int(row["id"]),
                "title": row["file_name"] or "Văn bản không tên",
                "symbol": " ".join(str(row["so_ki_hieu"] or "").split()),
                "summary": " ".join(str(row["trich_yeu"] or "").split())[:SUMMARY_LIMIT],
                "updatedAt": local_timestamp(row["last_modified"]),
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


def local_timestamp(value):
    """documents.last_modified is SQLite CURRENT_TIMESTAMP, which is UTC.

    The page shows it as "Hôm nay 11:48", so it needs the local wall clock;
    printed raw it was seven hours off in Việt Nam. A value that is not a
    full timestamp (a bare date, or anything else) passes through unchanged.
    """
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            moment = datetime.strptime(text, pattern)
        except ValueError:
            continue
        local = moment.replace(tzinfo=timezone.utc).astimezone()
        return local.strftime("%Y-%m-%dT%H:%M:%S")
    return text
