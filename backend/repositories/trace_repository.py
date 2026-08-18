"""Parameterized SQLite persistence for observable, redacted runtime traces."""

import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class TraceRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def add(self, trace: dict[str, Any]) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO traces (
                    trace_id, task_id, session_id, step_id, event_type,
                    tool_name, arguments_summary, result_summary, duration_ms,
                    retry_count, result_status, error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace["trace_id"], trace.get("task_id"), trace["session_id"],
                    trace.get("step_id"), trace["event_type"], trace.get("tool_name"),
                    trace.get("arguments_summary"), trace.get("result_summary"),
                    max(0, int(trace.get("duration_ms", 0))),
                    max(0, int(trace.get("retry_count", 0))),
                    trace["result_status"], trace.get("error_code"), trace["created_at"],
                ),
            )

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM traces WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_for_session(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM traces WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (session_id, bounded),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
