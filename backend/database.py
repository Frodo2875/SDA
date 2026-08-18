"""Centralized SQLite persistence for chats, files, operations, and actions."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.migrations import run_migrations
from backend.repositories.file_repository import FileRepository
from backend.repositories.document_repository import DocumentRepository
from backend.repositories.schema_repository import SchemaRepository
from backend.repositories.task_repository import TaskRepository
from backend.repositories.version_repository import VersionRepository
from backend.tools import excel_utils


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "app.db"
SUPPORTED_FILES = {
    ".xlsx": ("excel", 0),
    ".docx": ("word", 1),
    ".pdf": ("pdf", 0),
}

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        used_tools TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT NOT NULL UNIQUE,
        file_type TEXT NOT NULL,
        file_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        writable INTEGER NOT NULL CHECK (writable IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operation_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target_file TEXT NOT NULL,
        student_id TEXT NOT NULL,
        confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
        success INTEGER NOT NULL CHECK (success IN (0, 1)),
        error_message TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_actions (
        action_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target_file TEXT NOT NULL,
        student_id TEXT NOT NULL,
        student_name TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'confirmed', 'cancelled', 'executed', 'failed')
        ),
        created_at TEXT NOT NULL,
        executed_at TEXT
    )
    """,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


FILE_REPOSITORY = FileRepository(_connect)
SCHEMA_REPOSITORY = SchemaRepository(_connect)
DOCUMENT_REPOSITORY = DocumentRepository(_connect)
TASK_REPOSITORY = TaskRepository(_connect)
VERSION_REPOSITORY = VersionRepository(_connect)


def create_base_schema(connection: sqlite3.Connection) -> None:
    """Create the original V1 tables before applying ordered migrations."""
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)


def initialize_database() -> None:
    """Create the database and all tables, then register current data files."""
    with _connect() as connection:
        create_base_schema(connection)
        connection.commit()
        run_migrations(connection)
    sync_files()


def sync_files() -> None:
    """Upsert supported project data files into the files table."""
    data_dir = excel_utils.DATA_DIR
    if not data_dir.is_dir():
        return
    paths = [
        path for path in data_dir.iterdir() if path.is_file() and not path.name.startswith(".")
    ]
    upload_dir = data_dir / "uploads"
    if upload_dir.is_dir():
        paths.extend(
            path
            for path in upload_dir.iterdir()
            if path.is_file() and not path.name.startswith(".")
        )
    for path in sorted(paths, key=lambda item: (item.name, str(item.parent))):
        metadata = SUPPORTED_FILES.get(path.suffix.lower())
        if not path.is_file() or metadata is None:
            continue
        file_type, writable = metadata
        relative_path = path.relative_to(data_dir).as_posix()
        source_type = "upload" if relative_path.startswith("uploads/") else "system"
        FILE_REPOSITORY.upsert_discovered(
            file_name=path.name,
            file_type=file_type,
            file_path=f"data/{relative_path}",
            timestamp=utc_now(),
            writable=bool(writable),
            source_type=source_type,
            deletable=source_type == "upload",
        )


def register_file(
    *,
    file_name: str,
    file_type: str,
    file_path: str,
    status: str = "active",
    writable: bool = False,
    lifecycle_status: str | None = None,
    parse_status: str | None = None,
    queryable: bool | None = None,
    index_status: str = "not_required",
) -> int:
    """Insert one validated file record without replacing an existing record."""
    is_upload = file_path.startswith("data/uploads/")
    return FILE_REPOSITORY.register(
        file_name=file_name,
        file_type=file_type,
        file_path=file_path,
        created_at=utc_now(),
        status=status,
        writable=writable,
        source_type="upload" if is_upload else "system",
        lifecycle_status=lifecycle_status or ("uploaded" if is_upload else "ready"),
        parse_status=parse_status or ("pending" if is_upload else "not_required"),
        queryable=(not is_upload) if queryable is None else queryable,
        index_status=index_status,
        deletable=is_upload,
    )


def get_file_record(file_name: str) -> dict[str, Any] | None:
    """Fetch one registered file by its plain file name."""
    return FILE_REPOSITORY.get_by_name(file_name)


def get_file_record_by_id(file_id: str) -> dict[str, Any] | None:
    """Fetch one registered file by its stable V2 identifier."""
    return FILE_REPOSITORY.get_by_file_id(file_id)


def update_file_state(
    *,
    file_id: str,
    lifecycle_status: str,
    parse_status: str,
    queryable: bool,
    index_status: str = "not_required",
    expected_lifecycle: str | None = None,
) -> bool:
    """Persist one validated lifecycle transition through the repository."""
    return FILE_REPOSITORY.update_state(
        file_id=file_id,
        lifecycle_status=lifecycle_status,
        parse_status=parse_status,
        queryable=queryable,
        index_status=index_status,
        updated_at=utc_now(),
        expected_lifecycle=expected_lifecycle,
    )


def replace_table_schemas(file_id: str, schemas: list[dict[str, Any]]) -> None:
    """Atomically persist all discovered Sheet schemas for one file."""
    SCHEMA_REPOSITORY.replace_for_file(
        file_id=file_id,
        schemas=schemas,
        created_at=utc_now(),
    )


def get_table_schema_records(
    file_id: str,
    sheet_name: str | None = None,
) -> list[dict[str, Any]]:
    """Read persisted Sheet schemas through the compatibility facade."""
    return SCHEMA_REPOSITORY.get_for_file(file_id=file_id, sheet_name=sheet_name)


def delete_table_schemas(file_id: str) -> None:
    """Delete stale discovery output for one file."""
    SCHEMA_REPOSITORY.delete_for_file(file_id)


def replace_document_chunks(file_id: str, chunks: list[dict[str, Any]]) -> None:
    """Atomically replace local chunks and their FTS entries for one file."""
    DOCUMENT_REPOSITORY.replace_for_file(file_id, chunks)


def get_document_chunks(file_id: str) -> list[dict[str, Any]]:
    return DOCUMENT_REPOSITORY.get_for_file(file_id)


def delete_document_chunks(file_id: str) -> None:
    DOCUMENT_REPOSITORY.delete_for_file(file_id)


def search_document_chunks(
    *, file_ids: list[str], query: str, limit: int
) -> list[dict[str, Any]]:
    return DOCUMENT_REPOSITORY.search(file_ids=file_ids, query=query, limit=limit)


def create_task_record(task: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    TASK_REPOSITORY.create(task, steps)


def get_task_record(task_id: str) -> dict[str, Any] | None:
    return TASK_REPOSITORY.get(task_id)


def get_task_step_records(task_id: str) -> list[dict[str, Any]]:
    return TASK_REPOSITORY.steps(task_id)


def update_task_record(task_id: str, **values: Any) -> bool:
    return TASK_REPOSITORY.update_task(task_id, **values)


def update_task_step_record(step_id: str, **values: Any) -> bool:
    return TASK_REPOSITORY.update_step(step_id, **values)


def find_task_waiting_for_action(action_id: str) -> dict[str, Any] | None:
    return TASK_REPOSITORY.waiting_for_action(action_id)


def list_file_versions(file_id: str) -> list[dict[str, Any]]:
    return VERSION_REPOSITORY.list_for_file(file_id)


def get_file_version(version_id: str) -> dict[str, Any] | None:
    return VERSION_REPOSITORY.get(version_id)


def get_latest_file_version(file_id: str) -> dict[str, Any] | None:
    return VERSION_REPOSITORY.latest(file_id)


def commit_file_version_transition(
    *,
    file_id: str,
    before_version: dict[str, Any],
    after_version: dict[str, Any],
) -> dict[str, Any]:
    return VERSION_REPOSITORY.commit_transition(
        file_id=file_id,
        before_version=before_version,
        after_version=after_version,
    )


def save_chat_message(
    *,
    session_id: str,
    role: str,
    content: str,
    used_tools: list[dict[str, Any]] | None,
    status: str,
) -> int:
    """Persist one chat message and return its generated ID."""
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO chat_messages (
                session_id, role, content, created_at, used_tools, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content,
                utc_now(),
                json.dumps(used_tools or [], ensure_ascii=False),
                status,
            ),
        )
        return int(cursor.lastrowid)


def save_operation_log(
    *,
    session_id: str,
    action_type: str,
    target_file: str,
    student_id: str,
    confirmed: bool,
    success: bool,
    error_message: str | None = None,
) -> int:
    """Persist one write/cancel operation event."""
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO operation_logs (
                session_id, action_type, target_file, student_id,
                confirmed, success, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                action_type,
                target_file,
                student_id,
                int(confirmed),
                int(success),
                error_message,
                utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def insert_pending_action(action: dict[str, Any]) -> None:
    """Persist a newly frozen pending action."""
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO pending_actions (
                action_id, session_id, action_type, target_file, student_id,
                student_name, content, status, created_at, executed_at,
                file_id, operation_json, diff_json, target_version_id, task_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action["action_id"],
                action["session_id"],
                action["action_type"],
                action["target_file"],
                action["student_id"],
                action["student_name"],
                action["content"],
                action["status"],
                action["created_at"],
                action.get("executed_at"),
                action.get("file_id"),
                action.get("operation_json"),
                action.get("diff_json"),
                action.get("target_version_id"),
                action.get("task_id"),
            ),
        )


def get_pending_action_record(action_id: str) -> dict[str, Any] | None:
    """Fetch one pending action by ID."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def reserve_pending_action(action_id: str) -> tuple[str, dict[str, Any] | None]:
    """Atomically change pending to confirmed and return the frozen action."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if row is None:
            connection.rollback()
            return "not_found", None
        action = dict(row)
        if action["status"] != "pending":
            connection.rollback()
            return "not_pending", action
        connection.execute(
            "UPDATE pending_actions SET status = ? WHERE action_id = ?",
            ("confirmed", action_id),
        )
        connection.commit()
        action["status"] = "confirmed"
        return "reserved", action
    finally:
        connection.close()


def cancel_pending_action(action_id: str) -> tuple[str, dict[str, Any] | None]:
    """Atomically cancel an action only while it is pending."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if row is None:
            connection.rollback()
            return "not_found", None
        action = dict(row)
        if action["status"] != "pending":
            connection.rollback()
            return "not_pending", action
        connection.execute(
            "UPDATE pending_actions SET status = ? WHERE action_id = ?",
            ("cancelled", action_id),
        )
        connection.commit()
        action["status"] = "cancelled"
        return "cancelled", action
    finally:
        connection.close()


def finish_pending_action(action_id: str, status: str) -> dict[str, Any] | None:
    """Set the terminal action status and execution timestamp."""
    if status not in {"executed", "failed"}:
        raise ValueError("待确认操作只能结束为 executed 或 failed")
    executed_at = utc_now()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE pending_actions
            SET status = ?, executed_at = ?
            WHERE action_id = ? AND status = ?
            """,
            (status, executed_at, action_id, "confirmed"),
        )
        row = connection.execute(
            "SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def fetch_all(table_name: str) -> list[dict[str, Any]]:
    """Read rows from a fixed allow-list for tests and diagnostics."""
    queries = {
        "chat_messages": "SELECT * FROM chat_messages ORDER BY id",
        "files": "SELECT * FROM files ORDER BY id",
        "operation_logs": "SELECT * FROM operation_logs ORDER BY id",
        "pending_actions": "SELECT * FROM pending_actions ORDER BY created_at",
        "table_schemas": "SELECT * FROM table_schemas ORDER BY rowid",
        "schema_fields": "SELECT * FROM schema_fields ORDER BY rowid",
        "document_chunks": "SELECT * FROM document_chunks ORDER BY file_id, chunk_index",
        "tasks": "SELECT * FROM tasks ORDER BY created_at",
        "task_steps": "SELECT * FROM task_steps ORDER BY task_id, sequence",
        "file_versions": "SELECT * FROM file_versions ORDER BY file_id, version_number",
    }
    query = queries.get(table_name)
    if query is None:
        raise ValueError("不允许读取该数据表")
    with _connect() as connection:
        rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


initialize_database()
