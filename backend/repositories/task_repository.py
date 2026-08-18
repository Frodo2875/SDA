"""SQLite persistence for single-Agent runtime tasks and steps."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class TaskRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create(self, task: dict[str, Any], steps: list[dict[str, Any]]) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, session_id, user_message, task_type, status,
                    current_step, next_action, checkpoint_data, created_at,
                    updated_at, completed_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["task_id"], task["session_id"], task["user_message"],
                    task["task_type"], task["status"], task.get("current_step"),
                    task.get("next_action"), json.dumps(task.get("checkpoint_data") or {}, ensure_ascii=False),
                    task["created_at"], task["updated_at"], task.get("completed_at"),
                    task.get("error_code"),
                ),
            )
            for step in steps:
                connection.execute(
                    """
                    INSERT INTO task_steps (
                        step_id, task_id, sequence, step_name, step_type,
                        tool_name, arguments_json, status, retry_count,
                        result_summary, failed_reason, started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step["step_id"], task["task_id"], step["sequence"],
                        step["step_name"], step["step_type"], step.get("tool_name"),
                        json.dumps(step.get("arguments") or {}, ensure_ascii=False),
                        step["status"], step.get("retry_count", 0),
                        step.get("result_summary"), step.get("failed_reason"),
                        step.get("started_at"), step.get("completed_at"),
                    ),
                )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task(row) if row is not None else None

    def steps(self, task_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [self._step(row) for row in rows]

    def update_task(self, task_id: str, **values: Any) -> bool:
        allowed = {
            "status", "current_step", "next_action", "checkpoint_data",
            "updated_at", "completed_at", "error_code",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("包含非法 Task 更新字段")
        serialized = dict(values)
        if "checkpoint_data" in serialized:
            serialized["checkpoint_data"] = json.dumps(
                serialized["checkpoint_data"] or {}, ensure_ascii=False
            )
        assignments = ", ".join(f"{name} = ?" for name in serialized)
        with self._connection_factory() as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                (*serialized.values(), task_id),
            )
        return cursor.rowcount == 1

    def update_step(self, step_id: str, **values: Any) -> bool:
        allowed = {
            "arguments_json", "status", "retry_count", "result_summary",
            "failed_reason", "started_at", "completed_at",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("包含非法 Step 更新字段")
        serialized = dict(values)
        if "arguments_json" in serialized and not isinstance(serialized["arguments_json"], str):
            serialized["arguments_json"] = json.dumps(
                serialized["arguments_json"] or {}, ensure_ascii=False
            )
        assignments = ", ".join(f"{name} = ?" for name in serialized)
        with self._connection_factory() as connection:
            cursor = connection.execute(
                f"UPDATE task_steps SET {assignments} WHERE step_id = ?",
                (*serialized.values(), step_id),
            )
        return cursor.rowcount == 1

    def waiting_for_action(self, action_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status = 'waiting_confirmation' ORDER BY created_at"
            ).fetchall()
        for row in rows:
            task = self._task(row)
            if task["checkpoint_data"].get("action_id") == action_id:
                return task
        return None

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["checkpoint_data"] = json.loads(item["checkpoint_data"] or "{}")
        return item

    @staticmethod
    def _step(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["arguments"] = json.loads(item.pop("arguments_json") or "{}")
        return item
