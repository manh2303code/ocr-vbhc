"""SQLite-backed legal relation and entity graph."""

import sqlite3
import unicodedata
from typing import Dict, List, Optional, Tuple

from legal_relations import Relation, normalize_so_ki_hieu


def init_tables(cursor) -> None:
    """Create legal graph tables and lookup indexes idempotently."""
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS doc_relations (
            id INTEGER PRIMARY KEY,
            source_doc_id INTEGER NOT NULL,
            target_so_ki_hieu TEXT NOT NULL,
            target_doc_id INTEGER NULL,
            relation_type TEXT NOT NULL,
            evidence TEXT,
            char_offset INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_relations_source_doc_id
            ON doc_relations(source_doc_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target_doc_id
            ON doc_relations(target_doc_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target_so_ki_hieu
            ON doc_relations(target_so_ki_hieu);

        CREATE TABLE IF NOT EXISTS doc_entities (
            id INTEGER PRIMARY KEY,
            doc_id INTEGER,
            entity_type TEXT,
            value_norm TEXT,
            value_raw TEXT,
            UNIQUE(doc_id, entity_type, value_norm)
        );

        CREATE TABLE IF NOT EXISTS doc_validity (
            doc_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'khong_xac_dinh',
            reason_doc_id INTEGER NULL,
            computed_at TIMESTAMP
        );
        """
    )


def _row_value(row, name):
    if isinstance(row, dict):
        return row.get(name)
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _normalize_entity(value: str) -> str:
    upper = value.strip().upper().replace("Đ", "D")
    without_marks = "".join(
        char
        for char in unicodedata.normalize("NFD", upper)
        if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_marks.split())


def compute_validity(
    conn: sqlite3.Connection, affected_doc_ids: Optional[List[int]] = None
) -> Dict[int, str]:
    """Infer and persist validity for all documents or the requested subset."""
    params = tuple(dict.fromkeys(affected_doc_ids or ()))
    if affected_doc_ids is not None and not params:
        return {}
    where = " WHERE id IN ({})".format(",".join("?" * len(params))) if params else ""
    doc_ids = [row[0] for row in conn.execute("SELECT id FROM documents" + where, params)]
    if not doc_ids:
        return {}

    placeholders = ",".join("?" * len(doc_ids))
    reasons = {}
    for target_id, source_id, relation_type in conn.execute(
        "SELECT target_doc_id, source_doc_id, relation_type FROM doc_relations "
        "WHERE target_doc_id IN ({}) AND relation_type IN "
        "('thay_the', 'bai_bo', 'sua_doi') AND source_doc_id != target_doc_id "
        "ORDER BY CASE relation_type WHEN 'sua_doi' THEN 1 ELSE 0 END, id".format(
            placeholders
        ),
        doc_ids,
    ):
        reasons.setdefault(target_id, (relation_type, source_id))

    rows = []
    statuses = {}
    for doc_id in doc_ids:
        relation_type, reason_id = reasons.get(doc_id, (None, None))
        status = (
            "sua_doi_mot_phan"
            if relation_type == "sua_doi"
            else "het_hieu_luc"
            if relation_type
            else "con_hieu_luc"
        )
        statuses[doc_id] = status
        rows.append((doc_id, status, reason_id))

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO doc_validity "
            "(doc_id, status, reason_doc_id, computed_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )
    return statuses


def get_validity(conn: sqlite3.Connection, doc_id: int) -> Tuple[str, Optional[int]]:
    row = conn.execute(
        "SELECT status, reason_doc_id FROM doc_validity WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return (row[0], row[1]) if row else ("khong_xac_dinh", None)


class GraphStore:
    """Persistence facade for legal relations, targets, and entities."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def replace_relations(self, doc_id: int, relations: List[Relation]) -> None:
        rows = [
            (
                doc_id,
                relation.target_so_ki_hieu,
                relation.relation_type,
                relation.evidence,
                relation.char_offset,
            )
            for relation in relations
        ]
        with self.conn:
            self.conn.execute(
                "DELETE FROM doc_relations WHERE source_doc_id = ?", (doc_id,)
            )
            self.conn.executemany(
                "INSERT INTO doc_relations "
                "(source_doc_id, target_so_ki_hieu, relation_type, evidence, "
                "char_offset) VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def resolve_targets(self) -> int:
        """Resolve currently-unmatched relation numbers to latest document versions."""
        latest_by_doc = {}
        versions = self.conn.execute(
            "SELECT document_id, so_ki_hieu FROM document_versions "
            "WHERE so_ki_hieu IS NOT NULL AND TRIM(so_ki_hieu) != '' "
            "ORDER BY document_id, COALESCE(version_number, 0) DESC, id DESC"
        )
        for version in versions:
            latest_by_doc.setdefault(version["document_id"], version["so_ki_hieu"])

        documents_by_number = {
            normalize_so_ki_hieu(number): doc_id
            for doc_id, number in latest_by_doc.items()
        }
        updates = []
        for relation in self.conn.execute(
            "SELECT id, target_so_ki_hieu FROM doc_relations "
            "WHERE target_doc_id IS NULL"
        ):
            target_doc_id = documents_by_number.get(
                normalize_so_ki_hieu(relation["target_so_ki_hieu"])
            )
            if target_doc_id is not None:
                updates.append((target_doc_id, relation["id"]))

        with self.conn:
            self.conn.executemany(
                "UPDATE doc_relations SET target_doc_id = ? WHERE id = ?", updates
            )
        return len(updates)

    def upsert_entities_from_version(self, doc_id: int, version_row) -> None:
        fields = (
            ("co_quan", "cqbh_tren"),
            ("co_quan", "cqbh_duoi"),
            ("nguoi_ky", "chu_ky"),
            ("chuc_vu", "chuc_vu"),
            ("loai_vb", "loai_vb"),
        )
        entities = []
        for entity_type, field_name in fields:
            raw = _row_value(version_row, field_name)
            if raw is None or not str(raw).strip():
                continue
            raw = str(raw).strip()
            entities.append((doc_id, entity_type, _normalize_entity(raw), raw))

        with self.conn:
            self.conn.executemany(
                "INSERT INTO doc_entities "
                "(doc_id, entity_type, value_norm, value_raw) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doc_id, entity_type, value_norm) "
                "DO UPDATE SET value_raw = excluded.value_raw",
                entities,
            )

    def get_relations(
        self, doc_id: int, direction: str = "out"
    ) -> List[sqlite3.Row]:
        columns = {"out": "source_doc_id", "in": "target_doc_id"}
        try:
            column = columns[direction]
        except KeyError:
            raise ValueError("direction must be 'out' or 'in'")
        return self.conn.execute(
            "SELECT * FROM doc_relations WHERE {} = ? ORDER BY id".format(column),
            (doc_id,),
        ).fetchall()

    def delete_doc(self, doc_id: int) -> None:
        """Remove owned graph data while retaining unresolved external references."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM doc_relations WHERE source_doc_id = ?", (doc_id,)
            )
            self.conn.execute(
                "UPDATE doc_relations SET target_doc_id = NULL "
                "WHERE target_doc_id = ?",
                (doc_id,),
            )
            self.conn.execute("DELETE FROM doc_entities WHERE doc_id = ?", (doc_id,))
            self.conn.execute("DELETE FROM doc_validity WHERE doc_id = ?", (doc_id,))
            self.conn.execute(
                "UPDATE doc_validity SET reason_doc_id = NULL "
                "WHERE reason_doc_id = ?",
                (doc_id,),
            )
