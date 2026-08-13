"""Centralized SQLite persistence for chats, files, operations, and actions."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.tools import excel_utils


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "app.db"
SUPPORTED_FILES = {".xlsx": ("excel", 0), ".docx": ("word", 1)}

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


def initialize_database() -> None:
    """Create the database and all tables, then register current data files."""
    with _connect() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    sync_files()


def sync_files() -> None:
    """Upsert supported project data files into the files table."""
    data_dir = excel_utils.DATA_DIR
    if not data_dir.is_dir():
        return
    with _connect() as connection:
        for path in sorted(data_dir.iterdir(), key=lambda item: item.name):
            metadata = SUPPORTED_FILES.get(path.suffix.lower())
            if not path.is_file() or metadata is None:
                continue
            file_type, writable = metadata
            connection.execute(
                """
                INSERT INTO files (
                    file_name, file_type, file_path, created_at, status, writable
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_name) DO UPDATE SET
                    file_type = excluded.file_type,
                    file_path = excluded.file_path,
                    status = excluded.status,
                    writable = excluded.writable
                """,
                (
                    path.name,
                    file_type,
                    f"data/{path.name}",
                    utc_now(),
                    "active",
                    writable,
                ),
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
                student_name, content, status, created_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    }
    query = queries.get(table_name)
    if query is None:
        raise ValueError("不允许读取该数据表")
    with _connect() as connection:
        rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


initialize_database()
