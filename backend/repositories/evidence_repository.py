"""Persistent lookup records for Evidence 2.0 source navigation."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class EvidenceRepository:
    """Resolve an opaque evidence ID without trusting frontend locator fields."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def save(self, evidence: dict[str, Any], *, created_at: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO evidence_locations (
                    evidence_id, file_id, locator_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    file_id = excluded.file_id,
                    locator_json = excluded.locator_json,
                    created_at = excluded.created_at
                """,
                (
                    evidence["evidence_id"],
                    evidence["file_id"],
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT locator_json FROM evidence_locations WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return json.loads(row["locator_json"]) if row is not None else None
