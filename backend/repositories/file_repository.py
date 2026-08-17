"""SQLite repository for stable file records."""

import sqlite3
from collections.abc import Callable
from typing import Any
from uuid import uuid4


ConnectionFactory = Callable[[], sqlite3.Connection]
LIFECYCLE_STATUSES = {
    "uploaded",
    "processing",
    "ready",
    "failed",
    "deleted",
    "cleanup_failed",
}
PARSE_STATUSES = {"pending", "processing", "parsed", "failed", "not_required"}
INDEX_STATUSES = {"not_required", "pending", "indexed", "failed"}


class FileRepository:
    """Persist files while preserving V1 file-name compatibility."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def register(
        self,
        *,
        file_name: str,
        file_type: str,
        file_path: str,
        created_at: str,
        status: str = "active",
        writable: bool = False,
        source_type: str,
        lifecycle_status: str | None = None,
        parse_status: str | None = None,
        queryable: bool | None = None,
        index_status: str = "not_required",
        deletable: bool,
        file_id: str | None = None,
    ) -> int:
        stable_file_id = file_id or uuid4().hex
        is_system = source_type == "system"
        resolved_lifecycle = lifecycle_status or ("ready" if is_system else "uploaded")
        resolved_parse = parse_status or ("not_required" if is_system else "pending")
        resolved_queryable = is_system if queryable is None else queryable
        self._validate_states(resolved_lifecycle, resolved_parse, index_status)
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                INSERT INTO files (
                    file_name, file_type, file_path, created_at, status, writable,
                    file_id, source_type, lifecycle_status, parse_status,
                    queryable, index_status, updated_at, deletable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_name,
                    file_type,
                    file_path,
                    created_at,
                    status,
                    int(writable),
                    stable_file_id,
                    source_type,
                    resolved_lifecycle,
                    resolved_parse,
                    int(resolved_queryable),
                    index_status,
                    created_at,
                    int(deletable),
                ),
            )
            return int(cursor.lastrowid)

    def upsert_discovered(
        self,
        *,
        file_name: str,
        file_type: str,
        file_path: str,
        timestamp: str,
        writable: bool,
        source_type: str,
        deletable: bool,
    ) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO files (
                    file_name, file_type, file_path, created_at, status, writable,
                    file_id, source_type, lifecycle_status, parse_status,
                    queryable, index_status, updated_at, deletable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_name) DO UPDATE SET
                    file_type = excluded.file_type,
                    file_path = excluded.file_path,
                    writable = excluded.writable,
                    updated_at = CASE
                        WHEN files.file_type != excluded.file_type
                          OR files.file_path != excluded.file_path
                          OR files.writable != excluded.writable
                        THEN excluded.updated_at
                        ELSE files.updated_at
                    END
                """,
                (
                    file_name,
                    file_type,
                    file_path,
                    timestamp,
                    "active",
                    int(writable),
                    uuid4().hex,
                    source_type,
                    "ready" if source_type == "system" else "uploaded",
                    "not_required" if source_type == "system" else "pending",
                    int(source_type == "system"),
                    "not_required",
                    timestamp,
                    int(deletable),
                ),
            )

    def update_state(
        self,
        *,
        file_id: str,
        lifecycle_status: str,
        parse_status: str,
        queryable: bool,
        index_status: str,
        updated_at: str,
        expected_lifecycle: str | None = None,
    ) -> bool:
        """Update lifecycle fields, optionally guarding the previous state."""
        self._validate_states(lifecycle_status, parse_status, index_status)
        with self._connection_factory() as connection:
            if expected_lifecycle is None:
                cursor = connection.execute(
                    """
                    UPDATE files
                    SET lifecycle_status = ?, parse_status = ?, queryable = ?,
                        index_status = ?, updated_at = ?
                    WHERE file_id = ?
                    """,
                    (
                        lifecycle_status,
                        parse_status,
                        int(queryable),
                        index_status,
                        updated_at,
                        file_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE files
                    SET lifecycle_status = ?, parse_status = ?, queryable = ?,
                        index_status = ?, updated_at = ?
                    WHERE file_id = ? AND lifecycle_status = ?
                    """,
                    (
                        lifecycle_status,
                        parse_status,
                        int(queryable),
                        index_status,
                        updated_at,
                        file_id,
                        expected_lifecycle,
                    ),
                )
            return cursor.rowcount == 1

    @staticmethod
    def _validate_states(
        lifecycle_status: str,
        parse_status: str,
        index_status: str,
    ) -> None:
        if lifecycle_status not in LIFECYCLE_STATUSES:
            raise ValueError(f"非法 lifecycle_status：{lifecycle_status}")
        if parse_status not in PARSE_STATUSES:
            raise ValueError(f"非法 parse_status：{parse_status}")
        if index_status not in INDEX_STATUSES:
            raise ValueError(f"非法 index_status：{index_status}")

    def get_by_name(self, file_name: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE file_name = ?", (file_name,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_file_id(self, file_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE file_id = ?", (file_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def rename_record(
        self,
        *,
        file_id: str,
        file_name: str,
        file_path: str,
        updated_at: str,
    ) -> bool:
        """Update file metadata by stable ID without changing that ID."""
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE files
                SET file_name = ?, file_path = ?, updated_at = ?
                WHERE file_id = ?
                """,
                (file_name, file_path, updated_at, file_id),
            )
            return cursor.rowcount == 1
