"""Transactional metadata repository for immutable file snapshots."""

import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class VersionRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def list_for_file(self, file_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_versions
                WHERE file_id = ? ORDER BY version_number
                """,
                (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, version_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM file_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def latest(self, file_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM file_versions
                WHERE file_id = ? ORDER BY version_number DESC LIMIT 1
                """,
                (file_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def commit_transition(
        self,
        *,
        file_id: str,
        before_version: dict[str, Any],
        after_version: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit before/after metadata together; reuse an identical latest snapshot."""
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest_row = connection.execute(
                """
                SELECT * FROM file_versions
                WHERE file_id = ? ORDER BY version_number DESC LIMIT 1
                """,
                (file_id,),
            ).fetchone()
            latest = dict(latest_row) if latest_row is not None else None
            next_number = int(latest["version_number"]) + 1 if latest else 1
            reused_before = bool(
                latest
                and latest["content_hash"] == before_version["content_hash"]
                and latest["status"] == "available"
            )
            if reused_before:
                parent = latest
            else:
                before_record = {
                    **before_version,
                    "file_id": file_id,
                    "version_number": next_number,
                    "parent_version_id": latest["version_id"] if latest else None,
                }
                self._insert(connection, before_record)
                parent = before_record
                next_number += 1

            after_record = {
                **after_version,
                "file_id": file_id,
                "version_number": next_number,
                "parent_version_id": parent["version_id"],
            }
            self._insert(connection, after_record)
            connection.commit()
            return {
                "before_version": parent,
                "new_version": after_record,
                "reused_before": reused_before,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _insert(connection: sqlite3.Connection, version: dict[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO file_versions (
                version_id, file_id, version_number, parent_version_id,
                storage_path, content_hash, size, task_id, session_id,
                change_type, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version["version_id"], version["file_id"],
                version["version_number"], version.get("parent_version_id"),
                version["storage_path"], version["content_hash"], version["size"],
                version.get("task_id"), version["session_id"],
                version["change_type"], version["created_at"], version["status"],
            ),
        )
