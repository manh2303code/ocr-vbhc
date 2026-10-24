"""SQLite persistence for local legal-assistant conversations."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple, Union

from rag_agent import Citation


def _add_meta_column(cursor) -> None:
    """chat_messages.meta_json, added to databases created before it: the
    context of each turn (scope, open document, the documents it was
    about, how it was answered). Additive; older rows keep NULL and older
    versions of the app ignore the column."""
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(chat_messages)")}
    if columns and "meta_json" not in columns:
        try:
            cursor.execute("ALTER TABLE chat_messages ADD COLUMN meta_json TEXT")
        except sqlite3.OperationalError as exc:  # added meanwhile by another connection
            if "duplicate column" not in str(exc).lower():
                raise


def init_tables(cursor) -> None:
    """Create the chat schema on a caller-owned SQLite transaction."""
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            citations_json TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta_json TEXT,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_session
            ON chat_messages(session_id, id);
        """
    )
    _add_meta_column(cursor)


class ChatHistory:
    """Small connection-per-operation store suitable for UI persistence."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self._migrated = False

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            if not self._migrated:
                # The app migrates on open (DocumentDatabase); a database
                # opened another way gets the column on first use.
                _add_meta_column(conn.cursor())
                self._migrated = True
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _encode_citations(citations: Optional[List[Citation]]) -> Optional[str]:
        if not citations:
            return None
        if not all(isinstance(citation, Citation) for citation in citations):
            raise TypeError("citations phải là danh sách Citation")
        return json.dumps(
            [
                {
                    "n": item.n,
                    "doc_id": item.doc_id,
                    "page": item.page,
                    "so_ki_hieu": item.so_ki_hieu,
                    "chunk_id": item.chunk_id,
                }
                for item in citations
            ],
            ensure_ascii=False,
        )

    @staticmethod
    def _decode_citations(value: Optional[str]) -> List[Citation]:
        if not value:
            return []
        try:
            raw_items = json.loads(value)
            if not isinstance(raw_items, list):
                return []
        except (TypeError, ValueError):
            return []

        citations = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                citations.append(
                    Citation(
                        n=int(item["n"]),
                        doc_id=int(item["doc_id"]),
                        page=int(item["page"]) if item.get("page") is not None else None,
                        so_ki_hieu=str(item.get("so_ki_hieu") or ""),
                        chunk_id=(
                            int(item["chunk_id"])
                            if item.get("chunk_id") is not None
                            else None
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return citations

    @staticmethod
    def _encode_meta(meta: Optional[dict]) -> Optional[str]:
        if not meta:
            return None
        return json.dumps(meta, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_meta(value: Optional[str]) -> dict:
        """The turn context saved with a message; {} for rows saved before
        it was recorded, or unreadable ones."""
        if not value:
            return {}
        try:
            meta = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def new_session(self, title: str) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO chat_sessions (title) VALUES (?)", (str(title),)
            )
            return int(cursor.lastrowid)

    def append(
        self,
        session_id: int,
        role: str,
        content: str,
        citations: Optional[List[Citation]] = None,
        meta: Optional[dict] = None,
    ) -> None:
        """meta: the turn's context as JSON-able values (see chat_panel)."""
        with self._connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM chat_sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if exists is None:
                raise ValueError("Phiên trò chuyện không tồn tại")
            conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, citations_json, meta_json) VALUES (?, ?, ?, ?, ?)",
                (
                    int(session_id),
                    str(role),
                    str(content),
                    self._encode_citations(citations),
                    self._encode_meta(meta),
                ),
            )

    def load_last_session(self) -> Tuple[Optional[int], List[dict]]:
        with self._connection() as conn:
            session = conn.execute(
                "SELECT id FROM chat_sessions ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if session is None:
                return None, []
            return self._load_session(conn, int(session["id"]))

    def _load_session(self, conn, session_id):
        exists = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id = ?", (int(session_id),)
        ).fetchone()
        if exists is None:
            return None, []
        rows = conn.execute(
                "SELECT id, role, content, citations_json, created_at, meta_json "
                "FROM chat_messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return int(session_id), [
            {
                "id": int(row["id"]),
                "role": row["role"],
                "content": row["content"],
                "citations": self._decode_citations(row["citations_json"]),
                "created_at": row["created_at"],
                "meta": self._decode_meta(row["meta_json"]),
            }
            for row in rows
        ]

    def load_session(self, session_id: int) -> Tuple[Optional[int], List[dict]]:
        with self._connection() as conn:
            return self._load_session(conn, int(session_id))

    def delete_session(self, session_id: int) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_sessions WHERE id = ?", (int(session_id),)
            )
            return cursor.rowcount > 0

    def delete_last_message(self, session_id: int, role: str) -> bool:
        """Drop the session's newest message if it has this role.

        Regenerating an answer replaces it; without this the old answer would
        come back as a second consecutive reply when the session is reopened.
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, role FROM chat_messages WHERE session_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (int(session_id),),
            ).fetchone()
            if row is None or row["role"] != role:
                return False
            conn.execute("DELETE FROM chat_messages WHERE id = ?", (int(row["id"]),))
            return True

    def list_sessions(self, limit: int = 20) -> List[dict]:
        if limit < 1:
            raise ValueError("limit phải lớn hơn 0")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at FROM chat_sessions "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "title": row["title"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
