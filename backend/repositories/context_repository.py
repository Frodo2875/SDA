"""Persistence for bounded, structured session context."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]
JSON_FIELDS = {
    "current_student_json": "current_student",
    "current_file_json": "current_file",
    "ambiguity_json": "ambiguity_candidates",
    "successful_steps_json": "successful_steps",
    "evidence_refs_json": "evidence_refs",
}


class ContextRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM session_contexts WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for storage_name, public_name in JSON_FIELDS.items():
            raw = result.pop(storage_name)
            try:
                result[public_name] = json.loads(raw) if raw else None
            except (TypeError, json.JSONDecodeError):
                result[public_name] = None
        return result

    def save(self, context: dict[str, Any]) -> None:
        values = {
            storage_name: json.dumps(
                context.get(public_name), ensure_ascii=False, separators=(",", ":")
            )
            for storage_name, public_name in JSON_FIELDS.items()
        }
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO session_contexts (
                    session_id, current_task_id, current_student_json,
                    current_file_json, ambiguity_json, pending_action_id,
                    successful_steps_json, evidence_refs_json, session_summary,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    current_task_id = excluded.current_task_id,
                    current_student_json = excluded.current_student_json,
                    current_file_json = excluded.current_file_json,
                    ambiguity_json = excluded.ambiguity_json,
                    pending_action_id = excluded.pending_action_id,
                    successful_steps_json = excluded.successful_steps_json,
                    evidence_refs_json = excluded.evidence_refs_json,
                    session_summary = excluded.session_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    context["session_id"], context.get("current_task_id"),
                    values["current_student_json"], values["current_file_json"],
                    values["ambiguity_json"], context.get("pending_action_id"),
                    values["successful_steps_json"], values["evidence_refs_json"],
                    context.get("session_summary", ""), context["updated_at"],
                ),
            )
