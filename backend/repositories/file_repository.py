"""SQLite repository for stable file records."""

import sqlite3
from collections.abc import Callable
from typing import Any
from uuid import uuid4


ConnectionFactory = Callable[[], sqlite3.Connection]


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
        lifecycle_status: str = "active",
        parse_status: str = "not_parsed",
        queryable: bool = True,
        index_status: str = "not_indexed",
        deletable: bool,
        file_id: str | None = None,
    ) -> int:
        stable_file_id = file_id or uuid4().hex
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
                    lifecycle_status,
                    parse_status,
                    int(queryable),
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
                    "active",
                    "not_parsed",
                    int(source_type == "system"),
                    "not_indexed",
                    timestamp,
                    int(deletable),
                ),
            )

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
