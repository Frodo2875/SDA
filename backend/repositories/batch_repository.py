"""SQLite persistence for Batch progress and partial item outcomes."""

import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]
STATUSES = {"pending", "running", "success", "failed", "skipped"}


class BatchRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create(self, batch: dict[str, Any]) -> None:
        self._validate_status(batch["status"])
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO batches (
                    batch_id, session_id, task_id, action_type, status, total,
                    success_count, failed_count, skipped_count, created_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["batch_id"], batch["session_id"], batch.get("task_id"),
                    batch["action_type"], batch["status"], batch.get("total", 0),
                    batch.get("success_count", 0), batch.get("failed_count", 0),
                    batch.get("skipped_count", 0), batch["created_at"],
                    batch.get("completed_at"),
                ),
            )

    def add_item(self, item: dict[str, Any]) -> None:
        self._validate_status(item["status"])
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO batch_items (
                    item_id, batch_id, target_id, status, result_summary,
                    error_code, action_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["item_id"], item["batch_id"], item["target_id"],
                    item["status"], item.get("result_summary"),
                    item.get("error_code"), item.get("action_id"),
                ),
            )

    def update(self, batch_id: str, **values: Any) -> bool:
        allowed = {
            "status", "total", "success_count", "failed_count",
            "skipped_count", "completed_at", "task_id",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("包含非法 Batch 更新字段")
        if "status" in values:
            self._validate_status(values["status"])
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connection_factory() as connection:
            cursor = connection.execute(
                f"UPDATE batches SET {assignments} WHERE batch_id = ?",
                (*values.values(), batch_id),
            )
        return cursor.rowcount == 1

    def link_action(self, batch_id: str, action_id: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE batch_items SET action_id = ?
                WHERE batch_id = ? AND status = 'success'
                """,
                (action_id, batch_id),
            )

    def reclassify_success(
        self, batch_id: str, *, status: str, error_code: str, summary: str
    ) -> None:
        if status not in {"failed", "skipped"}:
            raise ValueError("只能重分类为 failed 或 skipped")
        with self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE batch_items
                SET status = ?, error_code = ?, result_summary = ?
                WHERE batch_id = ? AND status = 'success'
                """,
                (status, error_code, summary, batch_id),
            )

    def finish_action(
        self, action_id: str, *, outcome: str, completed_at: str
    ) -> dict[str, Any] | None:
        """Finalize an entire write Batch from its one frozen action."""
        if outcome not in {"success", "failed", "skipped"}:
            raise ValueError("非法 Batch action 结果")
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            batch_row = connection.execute(
                """
                SELECT DISTINCT b.* FROM batches b
                JOIN batch_items i ON i.batch_id = b.batch_id
                WHERE i.action_id = ?
                """,
                (action_id,),
            ).fetchone()
            if batch_row is None:
                connection.rollback()
                return None
            batch = dict(batch_row)
            if batch["status"] != "pending":
                connection.rollback()
                return batch
            if outcome != "success":
                new_item_status = "skipped" if outcome == "skipped" else "failed"
                error_code = "BATCH_CANCELLED" if outcome == "skipped" else "BATCH_WRITE_FAILED"
                connection.execute(
                    """
                    UPDATE batch_items
                    SET status = ?, error_code = ?,
                        result_summary = CASE
                            WHEN ? = 'skipped' THEN '用户取消整批写入'
                            ELSE '整批写入执行失败'
                        END
                    WHERE batch_id = ? AND status = 'success'
                    """,
                    (new_item_status, error_code, new_item_status, batch["batch_id"]),
                )
            counts = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END)
                FROM batch_items WHERE batch_id = ?
                """,
                (batch["batch_id"],),
            ).fetchone()
            connection.execute(
                """
                UPDATE batches
                SET status = ?, success_count = ?, failed_count = ?,
                    skipped_count = ?, completed_at = ?
                WHERE batch_id = ?
                """,
                (
                    outcome, int(counts[0] or 0), int(counts[1] or 0),
                    int(counts[2] or 0), completed_at, batch["batch_id"],
                ),
            )
            connection.commit()
            return self.get(batch["batch_id"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if row is None:
                return None
            items = connection.execute(
                """
                SELECT * FROM batch_items
                WHERE batch_id = ? ORDER BY rowid
                """,
                (batch_id,),
            ).fetchall()
        result = dict(row)
        result["items"] = [dict(item) for item in items]
        return result

    def get_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT batch_id FROM batches WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self.get(row["batch_id"]) if row is not None else None

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"非法 Batch 状态：{status}")
