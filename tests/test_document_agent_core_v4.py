"""V4.7 General Document Core and Student Domain Adapter compatibility tests."""

import inspect
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from backend import database
from backend.services.document_agent_core import (
    CORE_CAPABILITY_MODULES,
    DOCUMENT_AGENT_CORE,
    GENERAL_CORE_TOOL_NAMES,
)
from backend.services.student_domain_adapter import (
    STUDENT_DOMAIN_ADAPTER,
    STUDENT_DOMAIN_TOOL_NAMES,
    StudentDomainAdapter,
)
from backend.tool_registry import TOOL_REGISTRY
from backend.tools import excel_utils
from backend.tools.analysis_tools import compare_students
from backend.tools.data_quality_tools import validate_student_data
from backend.tools.hybrid_tools import evaluate_scholarship_eligibility
from backend.tools.schema_tools import get_table_schema, inspect_excel
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)
from backend.tools.table_tools import aggregate_table, query_table


@pytest.fixture
def unknown_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    file_name = "仓库物料清单.xlsx"
    path = upload_dir / file_name
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "库存"
    worksheet.append(["物料编码", "品名", "类别", "仓库", "库存数量", "单价"])
    worksheet.append(["M-001", "打印纸", "办公用品", "上海", 120, 25.5])
    worksheet.append(["M-002", "签字笔", "办公用品", "北京", 80, 3.0])
    worksheet.append(["M-003", "网线", "IT耗材", "上海", 35, 18.0])
    worksheet.append(["M-004", "扩展坞", "IT耗材", "深圳", 15, 199.0])
    workbook.save(path)
    workbook.close()
    database.register_file(
        file_name=file_name,
        file_type="excel",
        file_path=f"data/uploads/{file_name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(file_name)
    assert record is not None
    return {"file_id": record["file_id"], "sheet": "库存"}


def test_general_core_unknown_excel_field_filter_and_aggregate(
    unknown_inventory: dict[str, str],
) -> None:
    file_id = unknown_inventory["file_id"]
    sheet = unknown_inventory["sheet"]

    inspected = DOCUMENT_AGENT_CORE.inspect_excel(file_id)
    schema = DOCUMENT_AGENT_CORE.get_table_schema(file_id, sheet)
    filtered = DOCUMENT_AGENT_CORE.query_table(
        file_id,
        sheet,
        filters=[{"field": "类别", "op": "=", "value": "办公用品"}],
        select=["物料编码", "品名", "库存数量"],
        order_by={"field": "库存数量", "direction": "desc"},
    )
    aggregated = DOCUMENT_AGENT_CORE.aggregate_table(
        file_id,
        sheet,
        "sum",
        field="库存数量",
        filters=[{"field": "仓库", "op": "=", "value": "上海"}],
    )

    assert inspected["ok"] is True
    assert schema["ok"] is True
    fields = schema["data"]["schemas"][0]["fields"]
    assert {field["source_name"] for field in fields} == {
        "物料编码", "品名", "类别", "仓库", "库存数量", "单价"
    }
    assert all(field.get("canonical_name") != "student_id" for field in fields)
    assert filtered["data"] == {
        "status": "found",
        "rows": [
            {"物料编码": "M-001", "品名": "打印纸", "库存数量": 120},
            {"物料编码": "M-002", "品名": "签字笔", "库存数量": 80},
        ],
        "matched_rows": 2,
        "returned_rows": 2,
    }
    assert filtered["evidence_chain"]
    assert aggregated["data"]["results"] == [{
        "operation": "sum", "field": "库存数量", "value": 155
    }]
    assert "student_id" not in inspect.signature(DOCUMENT_AGENT_CORE.query_table).parameters


def test_core_is_a_thin_facade_and_keeps_legacy_tool_handlers() -> None:
    legacy_handlers = {
        "query_table": query_table,
        "aggregate_table": aggregate_table,
        "search_student": search_student,
        "get_student_info": get_student_info,
        "get_student_scores": get_student_scores,
        "get_student_research": get_student_research,
        "compare_students": compare_students,
        "validate_student_data": validate_student_data,
        "evaluate_scholarship_eligibility": evaluate_scholarship_eligibility,
    }

    assert set(CORE_CAPABILITY_MODULES) == {
        "file_schema_parser",
        "ocr_vision",
        "table_query_aggregate",
        "retrieval_rerank",
        "evidence",
        "workflow_async",
        "safety_policy",
        "diff_version_rollback",
        "trace_evaluation",
        "validation",
    }
    assert GENERAL_CORE_TOOL_NAMES.isdisjoint(STUDENT_DOMAIN_TOOL_NAMES)
    assert "query_table" in GENERAL_CORE_TOOL_NAMES
    assert "search_student" in STUDENT_DOMAIN_TOOL_NAMES
    assert "find_cross_file_conflicts" not in GENERAL_CORE_TOOL_NAMES
    assert "find_cross_file_conflicts" in STUDENT_DOMAIN_TOOL_NAMES
    for name, handler in legacy_handlers.items():
        spec = TOOL_REGISTRY.get(name)
        assert spec is not None
        assert spec.handler is handler


def test_student_adapter_delegates_legacy_identity_without_behavior_change() -> None:
    assert STUDENT_DOMAIN_ADAPTER.search_student("S001") == search_student("S001")
    assert STUDENT_DOMAIN_ADAPTER.resolve_student_identity("S001") == search_student("S001")
    assert STUDENT_DOMAIN_ADAPTER.get_student_info("S001") == get_student_info("S001")


def test_student_adapter_can_consume_general_core_without_a_domain_router() -> None:
    class SpyCore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def query_table(self, file_id, sheet, filters, select, order_by=None, limit=None):
            self.calls.append({
                "file_id": file_id,
                "sheet": sheet,
                "filters": filters,
                "select": select,
                "order_by": order_by,
                "limit": limit,
            })
            return {"ok": True, "data": {"status": "found", "rows": []}}

        def aggregate_table(self, *args, **kwargs):
            return {"ok": True, "data": {"results": []}}

        def retrieve_document(self, scope, query, top_k=5):
            return {"ok": True, "data": {"status": "not_found"}}

    spy = SpyCore()
    adapter = StudentDomainAdapter(core=spy)

    result = adapter.query_domain_table(
        "a" * 32,
        "任意业务表",
        filters=[{"field": "状态", "op": "=", "value": "有效"}],
        select=["名称"],
    )

    assert result["ok"] is True
    assert spy.calls == [{
        "file_id": "a" * 32,
        "sheet": "任意业务表",
        "filters": [{"field": "状态", "op": "=", "value": "有效"}],
        "select": ["名称"],
        "order_by": None,
        "limit": None,
    }]
