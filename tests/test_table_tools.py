"""V2.4 deterministic semantics, safe table query, aggregate, and registry tests."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend import agent, database
from backend.services.field_semantics import map_field_semantics
from backend.tool_registry import TOOL_REGISTRY
from backend.tools import excel_utils
from backend.tools.schema_tools import get_table_schema, inspect_excel
from backend.tools.table_tools import aggregate_table, query_table


@pytest.fixture
def general_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    file_name = "通用人员统计.xlsx"
    path = upload_dir / file_name
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "人员明细"
    sheet.append(
        [
            "学生学号",
            "学生姓名",
            "学院",
            "联系电话",
            "Email",
            "平均成绩",
            "编号",
            "代码",
            "类型",
            "备注",
        ]
    )
    sheet.append(
        ["S101", "张三", "计算机学院", "13800000001", "a@test.invalid", 91.5, "N1", "A", "普通", None]
    )
    sheet.append(
        ["S102", "李四", "计算机学院", None, "b@test.invalid", 88, "N2", "B", "普通", None]
    )
    sheet.append(
        ["S103", "王五", "软件学院", "13800000003", "c@test.invalid", 95, "N3", "C", "重点", None]
    )
    sheet.append(
        ["S104", "赵敏", "计算机学院", "13800000004", None, 86, "N4", "D", "普通", None]
    )
    sheet.append(
        ["S105", "钱程", "数学学院", "13800000005", "e@test.invalid", 93, "N5", "E", "重点", None]
    )
    sheet.append(
        ["S106", "孙悦", "软件学院", None, "f@test.invalid", 79, "N6", "F", "普通", None]
    )
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
    assert inspect_excel(record["file_id"])["ok"] is True
    return {"file_id": record["file_id"], "file_name": file_name, "sheet": "人员明细"}


@pytest.mark.parametrize(
    ("source_name", "canonical_name"),
    [
        ("学号", "student_id"),
        ("学生学号", "student_id"),
        ("Student ID", "student_id"),
        ("姓名", "name"),
        ("学生姓名", "name"),
        ("Name", "name"),
        ("学院", "college"),
        ("所属院系", "college"),
        ("联系电话", "phone"),
        ("移动电话", "phone"),
        ("手机号", "phone"),
        ("手机", "phone"),
        ("邮箱", "email"),
        ("Email", "email"),
    ],
)
def test_high_confidence_field_semantics(
    source_name: str,
    canonical_name: str,
) -> None:
    mapping = map_field_semantics(source_name)

    assert mapping["canonical_name"] == canonical_name
    assert mapping["semantic_type"] == canonical_name
    assert mapping["mapping_confidence"] == 0.99
    assert mapping["mapping_source"] == "deterministic_rule"


@pytest.mark.parametrize("source_name", ["编号", "代码", "类型", "备注"])
def test_uncertain_fields_are_never_forced_to_student_id(source_name: str) -> None:
    mapping = map_field_semantics(source_name)

    assert mapping["canonical_name"] is None
    assert mapping["semantic_type"] == "unknown"
    assert mapping["mapping_confidence"] == 0.0
    assert mapping["mapping_source"] == "unmapped"


def test_semantic_mapping_is_persisted_with_schema(general_table: dict[str, str]) -> None:
    schema = get_table_schema(general_table["file_id"], general_table["sheet"])
    fields = {
        field["source_name"]: field for field in schema["data"]["schemas"][0]["fields"]
    }

    assert fields["学生学号"]["canonical_name"] == "student_id"
    assert fields["学生姓名"]["canonical_name"] == "name"
    assert fields["学院"]["canonical_name"] == "college"
    assert fields["联系电话"]["canonical_name"] == "phone"
    assert fields["Email"]["canonical_name"] == "email"
    assert fields["编号"]["canonical_name"] is None
    assert fields["编号"]["semantic_type"] == "unknown"


def test_u04_name_filter_returns_real_phone_with_evidence(
    general_table: dict[str, str],
) -> None:
    result = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[{"field": "学生姓名", "op": "=", "value": "张三"}],
        select=["学生姓名", "联系电话"],
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "found"
    assert result["data"]["rows"] == [
        {"学生姓名": "张三", "联系电话": "13800000001"}
    ]
    assert result["evidence"] == {
        "file_id": general_table["file_id"],
        "file_name": general_table["file_name"],
        "sheet": general_table["sheet"],
        "fields": ["学生姓名", "联系电话"],
        "filters": [{"field": "学生姓名", "op": "=", "value": "张三"}],
        "matched_rows": 1,
    }
    assert result["warnings"]
    assert result["result_summary"] == "匹配 1 行，返回 1 行"


@pytest.mark.parametrize(
    ("field", "op", "value", "matched"),
    [
        ("学生姓名", "!=", "张三", 5),
        ("平均成绩", ">", 90, 3),
        ("平均成绩", ">=", 93, 2),
        ("平均成绩", "<", 86, 1),
        ("平均成绩", "<=", 86, 2),
        ("学院", "contains", "计算机", 3),
        ("联系电话", "is_null", None, 2),
        ("联系电话", "not_null", None, 4),
    ],
)
def test_all_filter_operators_are_allow_listed_and_computed(
    general_table: dict[str, str],
    field: str,
    op: str,
    value: object,
    matched: int,
) -> None:
    condition = {"field": field, "op": op}
    if op not in {"is_null", "not_null"}:
        condition["value"] = value

    result = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[condition],
        select=["学生学号"],
    )

    assert result["ok"] is True
    assert result["data"]["matched_rows"] == matched


def test_u07_average_score_top3_uses_python_sort_and_limit(
    general_table: dict[str, str],
) -> None:
    result = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[],
        select=["学生姓名", "平均成绩"],
        order_by={"field": "平均成绩", "direction": "desc"},
        limit=3,
    )

    assert result["ok"] is True
    assert result["data"]["matched_rows"] == 6
    assert result["data"]["returned_rows"] == 3
    assert result["data"]["rows"] == [
        {"学生姓名": "王五", "平均成绩": 95},
        {"学生姓名": "钱程", "平均成绩": 93},
        {"学生姓名": "张三", "平均成绩": 91.5},
    ]
    assert "结果已按 limit 截断" in result["warnings"]


def test_u06_college_count_and_grouped_statistics(general_table: dict[str, str]) -> None:
    filtered = aggregate_table(
        general_table["file_id"],
        general_table["sheet"],
        operation="count",
        filters=[{"field": "学院", "op": "=", "value": "计算机学院"}],
    )
    grouped = aggregate_table(
        general_table["file_id"],
        general_table["sheet"],
        operation="count",
        group_by="学院",
    )

    assert filtered["data"]["status"] == "found"
    assert filtered["data"]["results"] == [
        {"operation": "count", "field": None, "value": 3}
    ]
    grouped_values = {
        item["学院"]: item["value"] for item in grouped["data"]["results"]
    }
    assert grouped_values == {"计算机学院": 3, "软件学院": 2, "数学学院": 1}


@pytest.mark.parametrize(
    ("operation", "expected"),
    [("sum", 532.5), ("avg", 88.75), ("min", 79), ("max", 95)],
)
def test_numeric_aggregations_are_computed_by_python(
    general_table: dict[str, str],
    operation: str,
    expected: float,
) -> None:
    result = aggregate_table(
        general_table["file_id"],
        general_table["sheet"],
        operation=operation,
        field="平均成绩",
    )

    assert result["ok"] is True
    assert result["data"]["results"][0]["value"] == expected
    assert result["evidence"]["matched_rows"] == 6


def test_null_results_and_not_found_are_distinct(general_table: dict[str, str]) -> None:
    null_rows = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[{"field": "联系电话", "op": "is_null"}],
        select=["学生姓名", "联系电话"],
    )
    not_found = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[{"field": "学生姓名", "op": "=", "value": "不存在的人"}],
        select=["学生姓名"],
    )
    no_record = aggregate_table(
        general_table["file_id"],
        general_table["sheet"],
        operation="min",
        field="备注",
    )

    assert null_rows["data"]["status"] == "found"
    assert null_rows["data"]["matched_rows"] == 2
    assert all(row["联系电话"] is None for row in null_rows["data"]["rows"])
    assert not_found["data"]["status"] == "not_found"
    assert not_found["data"]["rows"] == []
    assert no_record["data"]["status"] == "no_record"
    assert no_record["data"]["results"][0]["value"] is None


def test_u08_invalid_field_sheet_op_limit_and_order_are_rejected(
    general_table: dict[str, str],
) -> None:
    missing_field = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[],
        select=["不存在字段"],
    )
    missing_sheet = query_table(
        general_table["file_id"],
        "不存在Sheet",
        filters=[],
        select=["学生姓名"],
    )
    illegal_op = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[{"field": "平均成绩", "op": "exec", "value": "1+1"}],
        select=["学生姓名"],
    )
    invalid_limit = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[],
        select=["学生姓名"],
        limit=0,
    )
    invalid_order = query_table(
        general_table["file_id"],
        general_table["sheet"],
        filters=[],
        select=["学生姓名"],
        order_by={"field": "不存在字段", "direction": "asc"},
    )

    assert missing_field["error_code"] == "FIELD_NOT_FOUND"
    assert missing_sheet["error_code"] == "SHEET_NOT_FOUND"
    assert illegal_op["error_code"] == "INVALID_QUERY_ARGUMENTS"
    assert invalid_limit["error_code"] == "INVALID_QUERY_ARGUMENTS"
    assert invalid_order["error_code"] == "FIELD_NOT_FOUND"


def test_registry_is_single_source_and_llm_arguments_reject_sql(
    general_table: dict[str, str],
) -> None:
    spec = TOOL_REGISTRY.get("query_table")
    definition = next(
        item
        for item in agent.TOOL_DEFINITIONS
        if item["function"]["name"] == "query_table"
    )
    raw_arguments = {
        "file_id": general_table["file_id"],
        "sheet": general_table["sheet"],
        "filters": [],
        "select": ["学生姓名"],
        "sql": "DROP TABLE files",
    }

    arguments, result = agent._execute_tool(
        "query_table",
        json.dumps(raw_arguments, ensure_ascii=False),
    )

    assert spec is not None
    assert spec.handler is query_table
    assert spec.read_only is True
    assert spec.requires_confirmation is False
    assert definition["function"]["parameters"]["additionalProperties"] is False
    assert arguments["sql"] == "DROP TABLE files"
    assert result["error_code"] == "INVALID_TOOL_ARGUMENTS"
