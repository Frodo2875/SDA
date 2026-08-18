"""Tests for centralized SQLite persistence."""

import json
import sqlite3
from pathlib import Path

import pytest

from backend import database
from backend.agent import run_agent
from backend.services import confirmation


EXPECTED_COLUMNS = {
    "chat_messages": [
        "id", "session_id", "role", "content", "created_at", "used_tools", "status"
    ],
    "files": [
        "id", "file_name", "file_type", "file_path", "created_at", "status", "writable",
        "file_id", "source_type", "lifecycle_status", "parse_status", "queryable",
        "index_status", "updated_at", "deletable",
    ],
    "operation_logs": [
        "id", "session_id", "action_type", "target_file", "student_id",
        "confirmed", "success", "error_message", "created_at",
    ],
    "pending_actions": [
        "action_id", "session_id", "action_type", "target_file", "student_id",
        "student_name", "content", "status", "created_at", "executed_at",
        "file_id", "operation_json", "diff_json", "target_version_id", "task_id",
    ],
    "table_schemas": [
        "schema_id", "file_id", "sheet_name", "header_row", "data_start_row",
        "row_count", "column_count", "detection_status", "confidence",
        "detection_message", "created_at",
    ],
    "schema_fields": [
        "field_id", "schema_id", "source_name", "source_index", "inferred_type",
        "nullable", "null_count", "null_ratio", "unique_count", "semantic_type",
        "confidence", "sensitive",
        "canonical_name", "mapping_confidence", "mapping_source",
    ],
    "document_chunks": [
        "chunk_id", "file_id", "page_no", "chunk_index", "chunk_text",
        "text_hash", "metadata_json",
    ],
    "tasks": [
        "task_id", "session_id", "user_message", "task_type", "status",
        "current_step", "next_action", "checkpoint_data", "created_at",
        "updated_at", "completed_at", "error_code",
    ],
    "task_steps": [
        "step_id", "task_id", "sequence", "step_name", "step_type",
        "tool_name", "arguments_json", "status", "retry_count",
        "result_summary", "failed_reason", "started_at", "completed_at",
    ],
    "file_versions": [
        "version_id", "file_id", "version_number", "parent_version_id",
        "storage_path", "content_hash", "size", "task_id", "session_id",
        "change_type", "created_at", "status",
    ],
    "batches": [
        "batch_id", "session_id", "task_id", "action_type", "status",
        "total", "success_count", "failed_count", "skipped_count",
        "created_at", "completed_at",
    ],
    "batch_items": [
        "item_id", "batch_id", "target_id", "status", "result_summary",
        "error_code", "action_id",
    ],
    "traces": [
        "trace_id", "task_id", "session_id", "step_id", "event_type",
        "tool_name", "arguments_summary", "result_summary", "duration_ms",
        "retry_count", "result_status", "error_code", "created_at",
    ],
    "session_contexts": [
        "session_id", "current_task_id", "current_student_json",
        "current_file_json", "ambiguity_json", "pending_action_id",
        "successful_steps_json", "evidence_refs_json", "session_summary",
        "updated_at",
    ],
}


class FinalAnswerClient:
    async def create_chat_completion(self, messages, tools):
        return {"role": "assistant", "content": "测试回答", "tool_calls": []}


def test_first_run_creates_database_tables_and_file_records(
    isolated_database: Path,
) -> None:
    assert isolated_database.is_file()
    with sqlite3.connect(isolated_database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }
        for table_name, expected in EXPECTED_COLUMNS.items():
            assert table_name in tables
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            ]
            assert columns == expected
        assert "schema_migrations" in tables
        migrations = connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [(row[0], row[1]) for row in migrations] == [
            (1, "add_files_v2_foundation"),
            (2, "normalize_file_lifecycle"),
            (3, "create_excel_schema_tables"),
            (4, "add_field_semantic_mapping"),
            (5, "create_document_chunks"),
            (6, "create_runtime_tasks"),
            (7, "create_file_versions"),
            (8, "create_batches"),
            (9, "create_traces_and_context"),
        ]
        assert all(row[2] for row in migrations)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9

    file_rows = database.fetch_all("files")
    assert {
        "学生基本信息.xlsx",
        "学生成绩.xlsx",
        "科研成果.xlsx",
        "综合评价.docx",
    } <= {row["file_name"] for row in file_rows}
    writable = {row["file_name"]: row["writable"] for row in file_rows}
    assert writable["综合评价.docx"] == 1
    assert writable["学生成绩.xlsx"] == 0
    assert all(row["file_id"] for row in file_rows)
    assert len({row["file_id"] for row in file_rows}) == len(file_rows)
    fixed_names = {
        "学生基本信息.xlsx",
        "学生成绩.xlsx",
        "科研成果.xlsx",
        "综合评价.docx",
    }
    fixed_rows = [row for row in file_rows if row["file_name"] in fixed_names]
    upload_rows = [row for row in file_rows if row["file_path"].startswith("data/uploads/")]
    assert all(row["source_type"] == "system" for row in fixed_rows)
    assert all(row["deletable"] == 0 for row in fixed_rows)
    assert all(row["lifecycle_status"] == "ready" for row in fixed_rows)
    assert all(row["parse_status"] == "not_required" for row in fixed_rows)
    assert all(row["queryable"] == 1 for row in fixed_rows)
    assert all(row["source_type"] == "upload" for row in upload_rows)
    assert all(row["deletable"] == 1 for row in upload_rows)


@pytest.mark.anyio
async def test_agent_saves_user_and_assistant_chat_messages() -> None:
    result = await run_agent(
        "普通问候，不包含学生信息",
        client=FinalAnswerClient(),
        session_id="session-'parameterized",
    )

    assert result["status"] == "completed"
    rows = database.fetch_all("chat_messages")
    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "普通问候，不包含学生信息"),
        ("assistant", "测试回答"),
    ]
    assert all(row["session_id"] == "session-'parameterized" for row in rows)
    assert json.loads(rows[0]["used_tools"]) == []
    assert rows[0]["status"] == "received"
    assert rows[1]["status"] == "completed"


def test_pending_actions_and_operation_logs_are_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = confirmation.create_pending_action(
        session_id="cancel-session",
        target_file="综合评价.docx",
        student_id="S001",
        student_name="张三",
        content="取消内容",
    )
    assert first["ok"] is True
    assert confirmation.cancel_action(first["data"]["action_id"])["ok"] is True

    second = confirmation.create_pending_action(
        session_id="confirm-session",
        target_file="综合评价.docx",
        student_id="S002",
        student_name="李四",
        content="确认内容",
    )
    monkeypatch.setattr(
        confirmation,
        "write_word",
        lambda file_name, content: {
            "ok": True,
            "data": {"file_name": file_name, "content": content},
            "error_code": None,
            "message": "模拟成功",
        },
    )
    assert confirmation.confirm_action(second["data"]["action_id"])["ok"] is True
    repeated = confirmation.confirm_action(second["data"]["action_id"])
    assert repeated["error_code"] == "ACTION_NOT_PENDING"

    database.initialize_database()
    actions = database.fetch_all("pending_actions")
    assert [row["status"] for row in actions] == ["cancelled", "executed"]
    assert actions[0]["executed_at"] is None
    assert actions[1]["executed_at"] is not None

    logs = database.fetch_all("operation_logs")
    assert [row["action_type"] for row in logs] == [
        "create_pending_write",
        "cancel_write",
        "create_pending_write",
        "confirm_write",
        "confirm_write",
    ]
    assert logs[1]["confirmed"] == 0 and logs[1]["success"] == 1
    assert logs[3]["confirmed"] == 1 and logs[3]["success"] == 1
    assert logs[4]["confirmed"] == 1 and logs[4]["success"] == 0
