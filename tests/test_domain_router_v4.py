"""V4.8 Domain Router and evidence-bound general mixed-task tests."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend import database
from backend.document_blocks import block_to_chunks, make_document_block
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE
from backend.services.domain_router import allowed_tool_names, route_domain
from backend.tools import excel_utils
from backend.tools.general_tools import evaluate_project_approval


def test_student_route_allows_core_and_student_adapter_tools() -> None:
    route = route_domain("查询 S001 的成绩并判断奖学金资格", session_id="route-student")

    assert route == {
        "domain": "student",
        "task_type": "rule_check",
        "reason_code": "STUDENT_STRUCTURAL_ID_SIGNAL",
        "effective_domain": "student",
    }
    allowed = allowed_tool_names(route)
    assert {"query_table", "retrieve_document", "search_student", "compare_students"} <= allowed


def test_general_route_uses_schema_and_task_signals_without_student_schema() -> None:
    route = route_domain(
        "查询预算超过阈值的项目",
        context={
            "schema_fields": ["项目编号", "项目名称", "预算金额", "负责人"],
            "file_types": ["excel"],
            "requested_tools": ["query_table", "aggregate_table"],
        },
        session_id="route-general",
    )

    assert route["domain"] == "general"
    assert route["effective_domain"] == "general"
    assert route["task_type"] == "query"
    allowed = allowed_tool_names(route)
    assert {"query_table", "aggregate_table", "retrieve_document"} <= allowed
    assert "search_student" not in allowed


@pytest.mark.anyio
async def test_general_agent_rejects_student_adapter_tool_execution() -> None:
    class StudentToolClient:
        def __init__(self) -> None:
            self.round = 0

        async def create_chat_completion(self, messages, tools):
            self.round += 1
            if self.round == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "wrong-domain",
                        "type": "function",
                        "function": {
                            "name": "search_student",
                            "arguments": json.dumps({"name_or_id": "P-001"}),
                        },
                    }],
                }
            return {"role": "assistant", "content": "无法使用学生工具。", "tool_calls": []}

    from backend.agent import run_agent

    result = await run_agent(
        "查询项目预算", client=StudentToolClient(), session_id="route-general-guard"
    )

    assert result["status"] == "completed"
    assert result["route"]["domain"] == "general"
    assert result["tool_calls"][0]["result"]["error_code"] == (
        "TOOL_NOT_ALLOWED_FOR_DOMAIN"
    )


def test_unknown_and_ambiguous_routes_fall_back_to_general_core() -> None:
    unknown = route_domain("请处理这份材料", session_id="route-unknown")
    ambiguous = route_domain(
        "比较学生成绩和项目预算",
        session_id="route-ambiguous",
    )

    assert unknown["domain"] == "unknown"
    assert unknown["effective_domain"] == "general"
    assert unknown["reason_code"] == "NO_DOMAIN_SIGNAL_GENERAL_FALLBACK"
    assert ambiguous["domain"] == "unknown"
    assert ambiguous["effective_domain"] == "general"
    assert ambiguous["reason_code"] == "AMBIGUOUS_DOMAIN_SIGNALS_GENERAL_FALLBACK"


def test_invalid_router_schema_and_classifier_failure_use_validated_fallback() -> None:
    invalid = route_domain(
        "查询项目",
        classifier=lambda message, context: {
            "domain": "finance",
            "task_type": "query",
            "reason_code": "BAD",
            "effective_domain": "finance",
        },
        session_id="route-invalid",
    )

    def failed_classifier(message, context):
        raise RuntimeError("classifier unavailable")

    failed = route_domain(
        "总结材料",
        classifier=failed_classifier,
        session_id="route-failed",
    )

    assert invalid == {
        "domain": "unknown",
        "task_type": "query",
        "reason_code": "INVALID_ROUTER_SCHEMA_GENERAL_FALLBACK",
        "effective_domain": "general",
    }
    assert failed["domain"] == "unknown"
    assert failed["effective_domain"] == "general"
    assert failed["reason_code"] == "ROUTER_FAILURE_GENERAL_FALLBACK"


def test_domain_trace_records_domain_task_and_reason_code() -> None:
    route = route_domain("查询项目预算", session_id="route-trace")

    traces = database.get_session_trace_records("route-trace")
    trace = next(item for item in traces if item["event_type"] == "domain_route")
    metrics = json.loads(trace["metrics_json"])
    assert metrics["domain"] == route["domain"]
    assert metrics["effective_domain"] == route["effective_domain"]
    assert metrics["task_type"] == route["task_type"]
    assert metrics["reason_code"] == route["reason_code"]


@pytest.mark.anyio
async def test_agent_trace_extends_existing_llm_event_without_extra_trace() -> None:
    class FinalAnswerClient:
        async def create_chat_completion(self, messages, tools):
            return {"role": "assistant", "content": "已完成。", "tool_calls": []}

    from backend.agent import run_agent

    result = await run_agent(
        "查询项目预算", client=FinalAnswerClient(), session_id="route-agent-trace"
    )

    traces = database.get_session_trace_records("route-agent-trace")
    assert result["route"]["domain"] == "general"
    assert len(traces) == 1
    assert traces[0]["event_type"] == "llm_call"
    metrics = json.loads(traces[0]["metrics_json"])
    assert metrics["domain"] == "general"
    assert metrics["reason_code"] == result["route"]["reason_code"]


@pytest.fixture
def project_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    workbook_path = upload_dir / "项目预算.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "预算明细"
    worksheet.append(["项目编号", "项目名称", "预算金额（万元）", "部门"])
    worksheet.append(["P-001", "数据平台", 220, "技术部"])
    worksheet.append(["P-002", "办公改造", 120, "行政部"])
    worksheet.append(["P-003", "培训计划", 80, "人力部"])
    workbook.save(workbook_path)
    workbook.close()
    database.register_file(
        file_name=workbook_path.name,
        file_type="excel",
        file_path="data/uploads/项目预算.xlsx",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="not_required",
    )
    budget_record = database.get_file_record(workbook_path.name)
    assert DOCUMENT_AGENT_CORE.inspect_excel(budget_record["file_id"])["ok"] is True

    policy_path = upload_dir / "项目管理办法.pdf"
    policy_path.write_bytes(b"%PDF-1.4\n% deterministic indexed fixture")
    database.register_file(
        file_name=policy_path.name,
        file_type="pdf",
        file_path="data/uploads/项目管理办法.pdf",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    policy_record = database.get_file_record(policy_path.name)
    rule_text = "项目管理办法第十二条：预算超过150万元的项目需要专项审批。"
    block = make_document_block(
        file_id=policy_record["file_id"],
        sequence=0,
        block_type="paragraph",
        content=rule_text,
        page_no=2,
        source_parser="fixture-policy",
    )
    database.replace_document_chunks(
        policy_record["file_id"],
        block_to_chunks(block, source_type="pdf"),
    )
    return {
        "budget_file_id": budget_record["file_id"],
        "policy_file_id": policy_record["file_id"],
        "sheet": "预算明细",
    }


def test_non_student_excel_query_does_not_require_or_create_student_entity(
    project_materials: dict[str, str],
) -> None:
    result = DOCUMENT_AGENT_CORE.query_table(
        project_materials["budget_file_id"],
        project_materials["sheet"],
        filters=[{"field": "预算金额（万元）", "op": ">", "value": 100}],
        select=["项目编号", "项目名称", "预算金额（万元）"],
    )

    assert result["ok"] is True
    assert [row["项目编号"] for row in result["data"]["rows"]] == ["P-001", "P-002"]
    assert all(item["record_key"].startswith("row:") for item in result["evidence_chain"])
    assert all(item.get("field") != "student_id" for item in result["evidence_chain"])


def test_excel_pdf_general_mixed_task_uses_python_and_explicit_rule_evidence(
    project_materials: dict[str, str],
) -> None:
    route = route_domain(
        "找出预算超过指定阈值的项目，并根据管理办法判断哪些项目需要专项审批",
        context={
            "schema_fields": ["项目名称", "预算金额（万元）"],
            "file_types": ["excel", "pdf"],
            "requested_tools": ["query_table", "aggregate_table", "retrieve_document"],
        },
        session_id="route-mixed-task",
    )
    result = evaluate_project_approval(
        project_materials["budget_file_id"],
        project_materials["policy_file_id"],
        project_materials["sheet"],
        "项目名称",
        "预算金额（万元）",
        100,
        budget_unit="ten_thousand_yuan",
        rule_query="专项审批",
    )

    assert route["domain"] == "general"
    assert route["task_type"] == "workflow"
    assert result["ok"] is True
    data = result["data"]
    assert data["status"] == "found"
    assert data["execution_engine"] == "python"
    assert [step["tool"] for step in data["execution_chain"]] == [
        "query_table", "aggregate_table", "retrieve_document", "python_condition_check"
    ]
    assert [row["项目名称"] for row in data["projects_over_requested_threshold"]] == [
        "数据平台", "办公改造"
    ]
    decisions = {item["project"]: item for item in data["approval_decisions"]}
    assert decisions["数据平台"]["requires_special_approval"] is True
    assert decisions["办公改造"]["requires_special_approval"] is False
    assert data["explicit_rule"]["operator"] == ">"
    assert data["explicit_rule"]["threshold"] == 150
    assert data["explicit_rule"]["evidence_id"] in {
        item["evidence_id"] for item in data["evidence_chain"]
    }
    rule_evidence = next(
        item for item in data["evidence_chain"]
        if item["evidence_id"] == data["explicit_rule"]["evidence_id"]
    )
    assert rule_evidence["file_id"] == project_materials["policy_file_id"]
    assert rule_evidence["page_no"] == 2
    assert "需要专项审批：数据平台" in data["answer"]
