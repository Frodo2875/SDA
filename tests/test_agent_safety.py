"""V3.9 Agent safety policy tests for writes, deletion, and unknown files."""

import json
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend import agent, database
from backend.runtime.safety_policy import (
    FileTrust,
    PolicyDecision,
    assess_high_risk_action,
    classify_file_record,
)
from backend.services.confirmation import (
    create_pending_action,
    create_pending_delete_action,
)
from backend.tools import excel_utils
from backend.tools.file_tools import list_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def safety_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copy2(PROJECT_ROOT / "data" / "综合评价.docx", tmp_path / "综合评价.docx")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "学生"
    worksheet.append(["学号", "姓名"])
    worksheet.append(["S001", "张三"])
    workbook.save(upload_dir / "未知来源.xlsx")
    workbook.close()
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    database.register_file(
        file_name="未知来源.xlsx",
        file_type="excel",
        file_path="data/uploads/未知来源.xlsx",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    return tmp_path


def test_dangerous_write_requires_confirmation_and_records_safety_trace(
    safety_data_dir: Path,
) -> None:
    target = safety_data_dir / "综合评价.docx"
    before = target.read_bytes()

    result = create_pending_action(
        session_id="safety-write",
        target_file="综合评价.docx",
        student_id="S001",
        student_name="张三",
        content="待确认的安全测试内容",
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "pending"
    assert target.read_bytes() == before
    traces = database.get_session_trace_records("safety-write")
    safety = next(item for item in traces if item["event_type"] == "safety_policy_check")
    assert safety["tool_name"] == "write_word"
    assert safety["result_status"] == "confirmation_required"
    assert '"file_trust": "trusted"' in safety["arguments_summary"]
    assert '"risk_level": "high"' in safety["arguments_summary"]


def test_delete_requires_confirmation_and_keeps_unknown_upload(
    safety_data_dir: Path,
) -> None:
    record = database.get_file_record("未知来源.xlsx")
    target = safety_data_dir / "uploads" / "未知来源.xlsx"

    result = create_pending_delete_action(
        session_id="safety-delete", file_id=record["file_id"]
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "pending"
    assert target.exists()
    assert database.get_file_record_by_id(record["file_id"])["status"] == "active"
    safety = database.get_session_trace_records("safety-delete")[0]
    assert safety["event_type"] == "safety_policy_check"
    assert safety["tool_name"] == "delete_file"
    assert safety["result_status"] == "confirmation_required"
    assert '"file_trust": "unknown"' in safety["arguments_summary"]


def test_unknown_registered_file_is_distinguished_and_read_only_tool_is_allowed(
    safety_data_dir: Path,
) -> None:
    record = database.get_file_record("未知来源.xlsx")
    system_record = database.get_file_record("综合评价.docx")

    arguments, result = agent._execute_tool(
        "inspect_excel",
        json.dumps({"file_id": record["file_id"]}),
        policy_context={"session_id": "safety-unknown"},
    )

    assert arguments == {"file_id": record["file_id"]}
    assert result["ok"] is True
    assert classify_file_record(record) == FileTrust.UNKNOWN
    assert classify_file_record(system_record) == FileTrust.TRUSTED
    listed = next(
        item for item in list_files()["data"] if item["file_name"] == "未知来源.xlsx"
    )
    assert listed["trust_level"] == "unknown"
    safety = database.get_session_trace_records("safety-unknown")[0]
    assert safety["result_status"] == "allow"
    assert '"file_trust": "unknown"' in safety["arguments_summary"]
    assert '"risk_level": "medium"' in safety["arguments_summary"]


def test_unregistered_file_is_blocked_before_tool_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_handler(**arguments):
        nonlocal called
        called = True
        raise AssertionError("Policy 阻止后不得执行 Tool")

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "inspect_excel", forbidden_handler)
    _, result = agent._execute_tool(
        "inspect_excel",
        json.dumps({"file_id": "b" * 32}),
        policy_context={"session_id": "safety-block"},
    )

    assert result["ok"] is False
    assert result["error_code"] == "SAFETY_POLICY_BLOCKED"
    assert called is False
    safety = database.get_session_trace_records("safety-block")[0]
    assert safety["result_status"] == "block"
    assert safety["error_code"] == "SAFETY_POLICY_BLOCKED"


def test_overwrite_policy_requires_confirmation() -> None:
    record = database.get_file_record("综合评价.docx")
    pending = assess_high_risk_action(
        action_type="overwrite",
        file_id=record["file_id"],
        confirmation_granted=False,
    )
    confirmed = assess_high_risk_action(
        action_type="overwrite",
        file_id=record["file_id"],
        confirmation_granted=True,
    )

    assert pending.decision == PolicyDecision.CONFIRMATION_REQUIRED
    assert pending.requires_confirmation is True
    assert confirmed.decision == PolicyDecision.ALLOW
