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
    if table_name not in {"files", "schema_fields", "pending_actions"}:
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


def _create_runtime_tasks(connection: sqlite3.Connection) -> None:
    """Create durable single-Agent task plans and their observable steps."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_message TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'pending', 'running', 'waiting_confirmation',
                    'success', 'failed', 'cancelled'
                )
            ),
            current_step INTEGER,
            next_action TEXT,
            checkpoint_data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            error_code TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_steps (
            step_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            step_name TEXT NOT NULL,
            step_type TEXT NOT NULL,
            tool_name TEXT,
            arguments_json TEXT NOT NULL,
            status TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            result_summary TEXT,
            failed_reason TEXT,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
            UNIQUE (task_id, sequence)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_tasks_session_id ON tasks(session_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_task_steps_task_id ON task_steps(task_id)"
    )


def _create_file_versions(connection: sqlite3.Connection) -> None:
    """Add immutable Word snapshots and frozen V2.9 action metadata."""
    columns = _column_names(connection, "pending_actions")
    additions = (
        ("file_id", "TEXT"),
        ("operation_json", "TEXT"),
        ("diff_json", "TEXT"),
        ("target_version_id", "TEXT"),
        ("task_id", "TEXT"),
    )
    for column_name, column_type in additions:
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE pending_actions ADD COLUMN {column_name} {column_type}"
            )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS file_versions (
            version_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            parent_version_id TEXT,
            storage_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            size INTEGER NOT NULL,
            task_id TEXT,
            session_id TEXT NOT NULL,
            change_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE,
            FOREIGN KEY (parent_version_id) REFERENCES file_versions(version_id),
            FOREIGN KEY (task_id) REFERENCES tasks(task_id),
            UNIQUE (file_id, version_number),
            UNIQUE (storage_path)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_file_versions_file_id "
        "ON file_versions(file_id, version_number)"
    )


def _create_batches(connection: sqlite3.Connection) -> None:
    """Create durable Batch progress and per-target outcomes."""
    status_check = "'pending', 'running', 'success', 'failed', 'skipped'"
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            task_id TEXT,
            action_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({status_check})),
            total INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id)
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS batch_items (
            item_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({status_check})),
            result_summary TEXT,
            error_code TEXT,
            action_id TEXT,
            FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE,
            FOREIGN KEY (action_id) REFERENCES pending_actions(action_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_batches_session_id ON batches(session_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_batch_items_batch_id ON batch_items(batch_id)"
    )


def _create_traces_and_context(connection: sqlite3.Connection) -> None:
    """Create observable runtime traces and bounded session context state."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS traces (
            trace_id TEXT PRIMARY KEY,
            task_id TEXT,
            session_id TEXT NOT NULL,
            step_id TEXT,
            event_type TEXT NOT NULL,
            tool_name TEXT,
            arguments_summary TEXT,
            result_summary TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            result_status TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id),
            FOREIGN KEY (step_id) REFERENCES task_steps(step_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_traces_task_id ON traces(task_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_traces_session_id ON traces(session_id, created_at)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_contexts (
            session_id TEXT PRIMARY KEY,
            current_task_id TEXT,
            current_student_json TEXT,
            current_file_json TEXT,
            ambiguity_json TEXT NOT NULL DEFAULT '[]',
            pending_action_id TEXT,
            successful_steps_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            session_summary TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (current_task_id) REFERENCES tasks(task_id),
            FOREIGN KEY (pending_action_id) REFERENCES pending_actions(action_id)
        )
        """
    )


MIGRATIONS = (
    Migration(1, "add_files_v2_foundation", _add_files_v2_foundation),
    Migration(2, "normalize_file_lifecycle", _normalize_file_lifecycle),
    Migration(3, "create_excel_schema_tables", _create_excel_schema_tables),
    Migration(4, "add_field_semantic_mapping", _add_field_semantic_mapping),
    Migration(5, "create_document_chunks", _create_document_chunks),
    Migration(6, "create_runtime_tasks", _create_runtime_tasks),
    Migration(7, "create_file_versions", _create_file_versions),
    Migration(8, "create_batches", _create_batches),
    Migration(9, "create_traces_and_context", _create_traces_and_context),
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
