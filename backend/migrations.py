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
    if table_name not in {"files", "schema_fields"}:
        raise ValueError("不允许检查未知数据表")
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
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


def _normalize_file_lifecycle(connection: sqlite3.Connection) -> None:
    """Move V2.1 records to the explicit V2.2 lifecycle vocabulary."""
    connection.execute(
        """
        UPDATE files
        SET lifecycle_status = 'ready',
            parse_status = CASE
                WHEN source_type = 'system' THEN 'not_required'
                ELSE 'parsed'
            END,
            queryable = 1,
            index_status = 'not_required',
            updated_at = COALESCE(updated_at, created_at, ?),
            deletable = CASE WHEN source_type = 'upload' THEN 1 ELSE 0 END
        WHERE lifecycle_status IS NULL
           OR lifecycle_status = 'active'
        """,
        (_utc_now(),),
    )
    connection.execute(
        """
        UPDATE files
        SET index_status = 'not_required',
            updated_at = COALESCE(updated_at, created_at, ?)
        WHERE index_status IS NULL OR index_status = 'not_indexed'
        """,
        (_utc_now(),),
    )


def _create_excel_schema_tables(connection: sqlite3.Connection) -> None:
    """Create normalized per-sheet schema discovery storage."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS table_schemas (
            schema_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            header_row INTEGER,
            data_start_row INTEGER,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            detection_status TEXT NOT NULL,
            confidence REAL NOT NULL,
            detection_message TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (file_id, sheet_name),
            FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_fields (
            field_id TEXT PRIMARY KEY,
            schema_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_index INTEGER NOT NULL,
            inferred_type TEXT NOT NULL,
            nullable INTEGER NOT NULL CHECK (nullable IN (0, 1)),
            null_count INTEGER NOT NULL,
            null_ratio REAL NOT NULL,
            unique_count INTEGER NOT NULL,
            semantic_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            sensitive INTEGER NOT NULL CHECK (sensitive IN (0, 1)),
            FOREIGN KEY (schema_id) REFERENCES table_schemas(schema_id)
                ON DELETE CASCADE,
            UNIQUE (schema_id, source_index)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_table_schemas_file_id "
        "ON table_schemas(file_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_schema_fields_schema_id "
        "ON schema_fields(schema_id)"
    )


def _add_field_semantic_mapping(connection: sqlite3.Connection) -> None:
    """Add explicit canonical mapping provenance to discovered fields."""
    columns = _column_names(connection, "schema_fields")
    additions = (
        ("canonical_name", "TEXT"),
        ("mapping_confidence", "REAL"),
        ("mapping_source", "TEXT"),
    )
    for column_name, column_type in additions:
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE schema_fields ADD COLUMN {column_name} {column_type}"
            )
    connection.execute(
        """
        UPDATE schema_fields
        SET canonical_name = CASE
                WHEN semantic_type = 'unknown' THEN NULL
                ELSE semantic_type
            END,
            mapping_confidence = COALESCE(mapping_confidence, confidence, 0),
            mapping_source = COALESCE(
                mapping_source,
                CASE
                    WHEN semantic_type = 'unknown' THEN 'unmapped'
                    ELSE 'deterministic_rule'
                END
            )
        """
    )


def _create_document_chunks(connection: sqlite3.Connection) -> None:
    """Create local unstructured chunks and their replaceable FTS5 index."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            page_no INTEGER,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE,
            UNIQUE (file_id, chunk_index)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_file_id "
        "ON document_chunks(file_id)"
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            chunk_text,
            tokenize='trigram'
        )
        """
    )
MIGRATIONS = (
    Migration(1, "add_files_v2_foundation", _add_files_v2_foundation),
    Migration(2, "normalize_file_lifecycle", _normalize_file_lifecycle),
    Migration(3, "create_excel_schema_tables", _create_excel_schema_tables),
    Migration(4, "add_field_semantic_mapping", _add_field_semantic_mapping),
    Migration(5, "create_document_chunks", _create_document_chunks),
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
