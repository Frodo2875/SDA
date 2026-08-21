"""SQLite repository for stable file records."""

import sqlite3
from collections.abc import Callable
from enum import Enum
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


class FileLifecycleStatus(str, Enum):
    """Canonical V3.3 document lifecycle states."""

    UPLOADED = "UPLOADED"
    DETECTING = "DETECTING"
    PARSING = "PARSING"
    OCR_PROCESSING = "OCR_PROCESSING"
    LAYOUT_PROCESSING = "LAYOUT_PROCESSING"
    INDEXING = "INDEXING"
    QUERYABLE = "QUERYABLE"
    FAILED = "FAILED"


DocumentLifecycleStatus = FileLifecycleStatus
CANONICAL_STATUSES = {status.value for status in FileLifecycleStatus}
ALLOWED_TRANSITIONS = {
    FileLifecycleStatus.UPLOADED: {
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.DETECTING: {
        FileLifecycleStatus.PARSING,
        FileLifecycleStatus.OCR_PROCESSING,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.PARSING: {
        FileLifecycleStatus.OCR_PROCESSING,
        FileLifecycleStatus.LAYOUT_PROCESSING,
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.QUERYABLE,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.OCR_PROCESSING: {
        FileLifecycleStatus.LAYOUT_PROCESSING,
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.LAYOUT_PROCESSING: {
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.QUERYABLE,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.INDEXING: {
        FileLifecycleStatus.QUERYABLE,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.QUERYABLE: {
        FileLifecycleStatus.OCR_PROCESSING,
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.FAILED,
    },
    FileLifecycleStatus.FAILED: set(),
}


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

    def transition_lifecycle(
        self,
        *,
        file_id: str,
        target_status: FileLifecycleStatus,
        updated_at: str,
        resume_status: FileLifecycleStatus | None = None,
    ) -> dict[str, Any] | None:
        """Atomically apply one legal canonical transition and mirror V2 fields."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE file_id = ?", (file_id,)
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            current_status = canonical_lifecycle_status(record)
            if current_status == target_status:
                return {
                    "changed": False,
                    "from_status": current_status.value,
                    "to_status": target_status.value,
                }
            allowed = ALLOWED_TRANSITIONS[current_status]
            is_resume = (
                current_status == FileLifecycleStatus.FAILED
                and resume_status == target_status
                and target_status != FileLifecycleStatus.FAILED
            )
            if target_status not in allowed and not is_resume:
                raise ValueError(
                    f"非法文件状态转换：{current_status.value} -> {target_status.value}"
                )

            lifecycle, parse, queryable, index = _legacy_state_fields(
                record, current_status, target_status
            )
            cursor = connection.execute(
                """
                UPDATE files
                SET status = ?, lifecycle_status = ?, parse_status = ?,
                    queryable = ?, index_status = ?, updated_at = ?
                WHERE file_id = ? AND status = ?
                """,
                (
                    target_status.value,
                    lifecycle,
                    parse,
                    int(queryable),
                    index,
                    updated_at,
                    file_id,
                    record["status"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("文件状态已被其他流程更新")
            return {
                "changed": True,
                "from_status": current_status.value,
                "to_status": target_status.value,
            }

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


def canonical_lifecycle_status(record: dict[str, Any]) -> FileLifecycleStatus:
    """Resolve canonical status for both V3.3 and legacy V2 records."""
    stored = str(record.get("status") or "").upper()
    if stored in CANONICAL_STATUSES:
        return FileLifecycleStatus(stored)
    lifecycle = record.get("lifecycle_status")
    if lifecycle == "uploaded":
        return FileLifecycleStatus.UPLOADED
    if lifecycle == "processing":
        if record.get("index_status") == "pending":
            return FileLifecycleStatus.INDEXING
        return FileLifecycleStatus.PARSING
    if lifecycle == "failed":
        return FileLifecycleStatus.FAILED
    return FileLifecycleStatus.QUERYABLE


def _legacy_state_fields(
    record: dict[str, Any],
    current_status: FileLifecycleStatus,
    target_status: FileLifecycleStatus,
) -> tuple[str, str, bool, str]:
    """Mirror canonical state into the unchanged V2 lifecycle columns."""
    if target_status == FileLifecycleStatus.UPLOADED:
        return "uploaded", "pending", False, "not_required"
    if target_status in {
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.PARSING,
        FileLifecycleStatus.OCR_PROCESSING,
        FileLifecycleStatus.LAYOUT_PROCESSING,
    }:
        return "processing", "processing", False, "not_required"
    if target_status == FileLifecycleStatus.INDEXING:
        return "ready", "parsed", False, "pending"
    if target_status == FileLifecycleStatus.QUERYABLE:
        index_status = record.get("index_status") or "not_required"
        if index_status in {"pending", "failed"}:
            index_status = (
                "indexed"
                if record.get("file_type") in {"word", "pdf"}
                else "not_required"
            )
        return "ready", "parsed", True, index_status

    index_status = record.get("index_status") or "not_required"
    if current_status in {
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.OCR_PROCESSING,
    }:
        index_status = "failed"
    return "failed", "failed", False, index_status
