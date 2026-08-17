"""V2.5 D01-D06 deterministic relation, conflict, and quality tests."""

import json
from pathlib import Path

from openpyxl import Workbook

from backend import agent, database
from backend.services import relation_service
from backend.tool_registry import TOOL_REGISTRY
from backend.tools import excel_utils
from backend.tools.data_quality_tools import (
    find_cross_file_conflicts,
    find_duplicate_records,
    validate_document,
    validate_student_data,
)
from backend.tools.schema_tools import inspect_excel


def _create_table(
    data_dir: Path,
    monkeypatch,
    file_name: str,
    headers: list[str],
    rows: list[list[object]],
    sheet: str = "数据",
) -> str:
    monkeypatch.setattr(excel_utils, "DATA_DIR", data_dir)
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(exist_ok=True)
    path = upload_dir / file_name
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
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
    assert inspect_excel(record["file_id"])["ok"] is True
    return record["file_id"]


def test_d01_same_student_id_different_name_is_conflict(tmp_path, monkeypatch) -> None:
    headers = ["学号", "姓名", "学院", "年级", "班级"]
    _create_table(tmp_path, monkeypatch, "来源甲.xlsx", headers, [["S201", "周宁", "计算机学院", "研一", "1班"]])
    _create_table(tmp_path, monkeypatch, "来源乙.xlsx", headers, [["S201", "周凝", "计算机学院", "研一", "1班"]])

    result = find_cross_file_conflicts("S201")

    assert result["ok"] is True
    assert result["data"]["status"] == "conflict"
    conflict = next(item for item in result["data"]["conflicts"] if item["conflict_type"] == "same_id_different_name")
    assert conflict["severity"] == "error"
    assert {item["value"] for item in conflict["values"]} == {"周宁", "周凝"}
    assert "真的" not in conflict["suggested_action"]


def test_d02_same_student_id_different_college_never_selects_truth(tmp_path, monkeypatch) -> None:
    headers = ["学生学号", "学生姓名", "所属院系", "年级", "班级", "状态"]
    _create_table(tmp_path, monkeypatch, "学院甲.xlsx", headers, [["S202", "孙岚", "计算机学院", "研二", "2班", "在读"]])
    _create_table(tmp_path, monkeypatch, "学院乙.xlsx", headers, [["S202", "孙岚", "软件学院", "研二", "2班", "休学"]])

    result = validate_student_data("S202")
    conflicts = find_cross_file_conflicts("S202")

    assert result["data"]["status"] == "conflict"
    assert any(item["issue_type"] == "same_id_different_college" for item in result["data"]["issues"])
    assert any(
        item["conflict_type"] == "same_field_different_value"
        and item["values"][0]["field"] == "状态"
        for item in conflicts["data"]["conflicts"]
    )


def test_d03_duplicate_and_explicit_time_version_conflicts(tmp_path, monkeypatch) -> None:
    file_id = _create_table(
        tmp_path,
        monkeypatch,
        "版本记录.xlsx",
        ["学号", "姓名", "学院", "年级", "班级", "更新时间", "平均成绩"],
        [
            ["S203", "陈一", "数学学院", "研一", "1班", "2026-01-01", 80],
            ["S203", "陈一", "数学学院", "研一", "1班", "2026-02-01", 85],
        ],
    )

    conflicts = find_cross_file_conflicts("S203")
    duplicates = find_duplicate_records(file_id, ["学号"])

    types = {item["conflict_type"] for item in conflicts["data"]["conflicts"]}
    assert {"duplicate_record", "time_version_conflict"}.issubset(types)
    assert duplicates["data"]["status"] == "conflict"
    assert duplicates["data"]["duplicates"][0]["count"] == 2
    assert duplicates["data"]["duplicates"][0]["row_numbers"] == [2, 3]


def test_d04_document_quality_detects_required_null_type_range_and_contact(tmp_path, monkeypatch) -> None:
    file_id = _create_table(
        tmp_path,
        monkeypatch,
        "质量异常.xlsx",
        ["姓名", "学院", "联系电话", "Email", "平均成绩", "备注"],
        [
            ["吴一", "计算机学院", "abc", "bad-email", 120, None],
            ["吴二", "计算机学院", None, "ok@test.invalid", "未知", None],
        ],
    )

    result = validate_document(file_id)

    assert result["data"]["status"] == "invalid_data"
    issue_types = {item["issue_type"] for item in result["data"]["issues"]}
    assert {
        "missing_required_field",
        "null_value",
        "high_missing_ratio",
        "type_anomaly",
        "numeric_range_anomaly",
        "invalid_contact_format",
    }.issubset(issue_types)


def test_d05_incomplete_or_non_unique_auxiliary_identity_is_ambiguous(tmp_path, monkeypatch) -> None:
    _create_table(
        tmp_path,
        monkeypatch,
        "主身份.xlsx",
        ["学号", "姓名", "学院", "年级", "班级"],
        [["S205", "林晓", "文学院", "研一", "3班"]],
    )
    _create_table(
        tmp_path,
        monkeypatch,
        "无学号重复.xlsx",
        ["姓名", "学院", "年级", "班级", "备注"],
        [
            ["林晓", "文学院", "研一", "3班", "记录甲"],
            ["林晓", "文学院", "研一", "3班", "记录乙"],
        ],
    )

    result = find_cross_file_conflicts("S205")

    assert result["data"]["status"] == "ambiguous"
    assert result["data"]["relation_issues"]
    assert "多条" in result["data"]["relation_issues"][0]["reason"]


def test_d06_statuses_registry_and_v1_duplicate_name_remain_distinct(tmp_path, monkeypatch) -> None:
    file_id = _create_table(
        tmp_path,
        monkeypatch,
        "单一材料.xlsx",
        ["学号", "姓名", "学院", "年级", "班级"],
        [["S206", "张三", "法学院", "研一", "1班"], ["S207", "张三", "法学院", "研二", "2班"]],
    )

    missing = find_cross_file_conflicts("S999")
    single = find_cross_file_conflicts("S206")
    no_duplicates = find_duplicate_records(file_id, ["学号"])
    bad_field = find_duplicate_records(file_id, ["不存在字段"])
    arguments, rejected = agent._execute_tool(
        "find_duplicate_records",
        json.dumps({"file_id": file_id, "keys": ["学号"], "sql": "DELETE FROM files"}),
    )

    assert missing["data"]["status"] == "not_found"
    assert single["data"]["status"] == "conflict"
    assert any(item["conflict_type"] == "single_source_only" for item in single["data"]["conflicts"])
    assert no_duplicates["data"]["status"] == "no_record"
    assert bad_field["data"]["status"] == "invalid_data"
    assert bad_field["error_code"] == "FIELD_NOT_FOUND"
    assert rejected["error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert arguments["sql"] == "DELETE FROM files"
    assert relation_service.PRIMARY_KEY == "student_id"
    assert TOOL_REGISTRY.get("find_cross_file_conflicts").read_only is True


def test_v1_fixed_tables_are_available_through_controlled_compatibility_adapter() -> None:
    result = validate_student_data("S001")

    assert result["ok"] is True
    assert result["data"]["status"] == "found"
    assert {item["file_name"] for item in result["data"]["sources"]} == {
        "学生基本信息.xlsx",
        "学生成绩.xlsx",
        "科研成果.xlsx",
    }
