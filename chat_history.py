"""SQLite persistence for local legal-assistant conversations."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple, Union

from rag_agent import Citation


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
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_session
            ON chat_messages(session_id, id);
        """
    )


class ChatHistory:
    """Small connection-per-operation store suitable for UI persistence."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
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
    ) -> None:
        with self._connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM chat_sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
            if exists is None:
                raise ValueError("Phiên trò chuyện không tồn tại")
            conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, citations_json) VALUES (?, ?, ?, ?)",
                (
                    int(session_id),
                    str(role),
                    str(content),
                    self._encode_citations(citations),
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
                "SELECT id, role, content, citations_json, created_at "
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
