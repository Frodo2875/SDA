"""Lightweight, transactional SQLite schema migrations."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from uuid import uuid4


MigrationFunction = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    """One ordered database migration."""

    version: int
    name: str
    apply: MigrationFunction


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if table_name != "files":
        raise ValueError("不允许检查未知数据表")
    return {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(files)").fetchall()
    }


def _add_files_v2_foundation(connection: sqlite3.Connection) -> None:
    """Add stable file identity and the first V2 lifecycle fields."""
    columns = _column_names(connection, "files")
    additions = (
        ("file_id", "TEXT"),
        ("source_type", "TEXT"),
        ("lifecycle_status", "TEXT"),
        ("parse_status", "TEXT"),
        ("queryable", "INTEGER"),
        ("index_status", "TEXT"),
        ("updated_at", "TEXT"),
        ("deletable", "INTEGER"),
    )
    for column_name, column_type in additions:
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE files ADD COLUMN {column_name} {column_type}"
            )

    now = _utc_now()
    rows = connection.execute(
        """
        SELECT id, file_id, file_path, created_at, source_type,
               lifecycle_status, parse_status, queryable, index_status,
               updated_at, deletable
        FROM files
        """
    ).fetchall()
    for row in rows:
        source_type = row[4] or (
            "upload" if str(row[2]).startswith("data/uploads/") else "system"
        )
        connection.execute(
            """
            UPDATE files
            SET file_id = ?, source_type = ?, lifecycle_status = ?,
                parse_status = ?, queryable = ?, index_status = ?,
                updated_at = ?, deletable = ?
            WHERE id = ?
            """,
            (
                row[1] or uuid4().hex,
                source_type,
                row[5] or "active",
                row[6] or "not_parsed",
                int(source_type == "system") if row[7] is None else row[7],
                row[8] or "not_indexed",
                row[9] or row[3] or now,
                int(source_type == "upload") if row[10] is None else row[10],
                row[0],
            ),
        )

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_files_file_id ON files(file_id)"
    )


MIGRATIONS = (
    Migration(1, "add_files_v2_foundation", _add_files_v2_foundation),
)


def run_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[Migration] = MIGRATIONS,
) -> list[int]:
    """Apply pending migrations in order and rollback any failed migration."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()

    applied_versions = {
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }
    executed: list[int] = []
    ordered = sorted(migrations, key=lambda item: item.version)
    if len({item.version for item in ordered}) != len(ordered):
        raise ValueError("Migration version 不能重复")

    for migration in ordered:
        if migration.version in applied_versions:
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.apply(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.name, _utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {int(migration.version)}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        applied_versions.add(migration.version)
        executed.append(migration.version)
    return executed
