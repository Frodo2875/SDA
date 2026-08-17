"""V2.3 deterministic unknown-Excel Schema Discovery tests."""

from datetime import date
from pathlib import Path
from typing import Callable

import pytest
from openpyxl import Workbook

from backend import database
from backend.tools import excel_utils
from backend.tools.schema_tools import get_table_schema, inspect_excel


WorkbookBuilder = Callable[[Workbook], None]


@pytest.fixture
def discovery_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _register_workbook(
    data_dir: Path,
    file_name: str,
    builder: WorkbookBuilder,
) -> tuple[str, Path]:
    workbook = Workbook()
    builder(workbook)
    path = data_dir / "uploads" / file_name
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
    return record["file_id"], path


def _register_corrupt_file(data_dir: Path, file_name: str) -> tuple[str, Path]:
    path = data_dir / "uploads" / file_name
    path.write_bytes(b"not an xlsx archive")
    database.register_file(
        file_name=file_name,
        file_type="excel",
        file_path=f"data/uploads/{file_name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    return database.get_file_record(file_name)["file_id"], path


def _normal_sheet(workbook: Workbook) -> None:
    sheet = workbook.active
    sheet.title = "人员数据"
    sheet.append(["编号", "姓名", "年龄", "邮箱", "入学日期"])
    sheet.append(["P001", "林晓", 20, "lin@example.test", date(2025, 9, 1)])
    sheet.append(["P002", "周宁", 21, "zhou@example.test", date(2025, 9, 1)])
    sheet.append(["P003", "许安", 22, "xu@example.test", date(2024, 9, 1)])


def test_u01_single_sheet_discovers_fields_types_nulls_and_semantics(
    discovery_data_dir: Path,
) -> None:
    file_id, _ = _register_workbook(
        discovery_data_dir,
        "未知结构甲.xlsx",
        _normal_sheet,
    )

    result = inspect_excel(file_id)

    assert result["ok"] is True
    assert result["data"]["sheet_count"] == 1
    assert result["data"]["sheet_names"] == ["人员数据"]
    schema = result["data"]["schemas"][0]
    assert schema["header_row"] == 1
    assert schema["data_start_row"] == 2
    assert schema["row_count"] == 3
    assert schema["column_count"] == 5
    assert schema["detection_status"] == "detected"
    fields = {field["source_name"]: field for field in schema["fields"]}
    assert fields["年龄"]["inferred_type"] == "integer"
    assert fields["年龄"]["nullable"] is False
    assert fields["年龄"]["null_count"] == 0
    assert fields["入学日期"]["inferred_type"] == "datetime"
    assert fields["姓名"]["semantic_type"] == "name"
    assert fields["姓名"]["sensitive"] is True
    assert fields["邮箱"]["semantic_type"] == "email"
    assert "编号" in schema["possible_unique_fields"]
    assert schema["possible_name_fields"] == ["姓名"]
    assert {"姓名", "邮箱"} <= set(schema["possible_sensitive_fields"])
    file_record = database.get_file_record_by_id(file_id)
    assert file_record["parse_status"] == "parsed"
    assert file_record["queryable"] == 1


def test_u02_multi_sheet_schemas_are_saved_independently(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        first = workbook.active
        first.title = "课程"
        first.append(["课程号", "课程名", "学分"])
        first.append(["C01", "算法", 3])
        first.append(["C02", "数据库", 2])
        second = workbook.create_sheet("教师")
        second.append(["工号", "姓名", "院系", "电话"])
        second.append(["T01", "高老师", "计算机", "13800000001"])
        second.append(["T02", "严老师", "软件", "13800000002"])

    file_id, _ = _register_workbook(discovery_data_dir, "多页材料.xlsx", build)

    result = inspect_excel(file_id)
    teacher_schema = get_table_schema(file_id, "教师")

    assert result["ok"] is True
    assert result["data"]["sheet_count"] == 2
    assert result["data"]["sheet_names"] == ["课程", "教师"]
    schemas = {item["sheet_name"]: item for item in result["data"]["schemas"]}
    assert schemas["课程"]["column_count"] == 3
    assert schemas["教师"]["column_count"] == 4
    assert schemas["课程"]["schema_id"] != schemas["教师"]["schema_id"]
    assert teacher_schema["ok"] is True
    assert [item["sheet_name"] for item in teacher_schema["data"]["schemas"]] == [
        "教师"
    ]
    assert get_table_schema(file_id, "不存在Sheet")["error_code"] == (
        "SHEET_SCHEMA_NOT_FOUND"
    )


def test_u03_header_on_third_row_after_merged_title_is_detected(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "延迟表头"
        sheet["A1"] = "以下为 2026 年度汇总材料"
        sheet.append([])
        sheet.append(["项目编号", "项目名称", "金额", "负责人"])
        sheet.append(["X01", "项目甲", 1200.5, "何静"])
        sheet.append(["X02", "项目乙", 900, "宋明"])

    file_id, _ = _register_workbook(discovery_data_dir, "第三行表头.xlsx", build)

    result = inspect_excel(file_id)

    assert result["ok"] is True
    schema = result["data"]["schemas"][0]
    assert schema["header_row"] == 3
    assert schema["data_start_row"] == 4
    assert [field["source_name"] for field in schema["fields"]] == [
        "项目编号",
        "项目名称",
        "金额",
        "负责人",
    ]


def test_null_counts_and_ratios_are_computed_from_real_cells(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "空值统计"
        sheet.append(["记录号", "备注", "金额"])
        sheet.append(["R01", "完整", 10])
        sheet.append(["R02", None, 20])
        sheet.append(["R03", "待补充", None])

    file_id, _ = _register_workbook(discovery_data_dir, "含空值材料.xlsx", build)

    result = inspect_excel(file_id)

    assert result["ok"] is True
    fields = {
        field["source_name"]: field
        for field in result["data"]["schemas"][0]["fields"]
    }
    assert fields["备注"]["null_count"] == 1
    assert fields["备注"]["null_ratio"] == pytest.approx(1 / 3, abs=1e-6)
    assert fields["金额"]["nullable"] is True


def test_merged_title_is_rejected_as_header_but_following_header_is_used(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "合并标题"
        sheet.merge_cells("A1:D1")
        sheet["A1"] = "合并单元格标题"
        sheet.append(["序号", "名称", "类别", "数量"])
        sheet.append(["M01", "材料甲", "A类", 3])
        sheet.append(["M02", "材料乙", "B类", 5])

    file_id, _ = _register_workbook(discovery_data_dir, "合并单元格.xlsx", build)

    result = inspect_excel(file_id)

    assert result["ok"] is True
    schema = result["data"]["schemas"][0]
    assert schema["header_row"] == 2
    assert schema["column_count"] == 4


def test_wide_sheet_with_25_fields_is_not_truncated(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "宽表"
        sheet.append([f"字段{i:02d}" for i in range(1, 26)])
        sheet.append(list(range(1, 26)))
        sheet.append(list(range(101, 126)))

    file_id, _ = _register_workbook(discovery_data_dir, "宽字段材料.xlsx", build)

    result = inspect_excel(file_id)

    assert result["ok"] is True
    schema = result["data"]["schemas"][0]
    assert schema["column_count"] == 25
    assert len(schema["fields"]) == 25
    assert schema["fields"][-1]["source_index"] == 25


def test_u09_schema_is_persisted_and_survives_restart_without_excel_reread(
    discovery_data_dir: Path,
) -> None:
    file_id, path = _register_workbook(
        discovery_data_dir,
        "持久化结构.xlsx",
        _normal_sheet,
    )
    assert inspect_excel(file_id)["ok"] is True
    assert inspect_excel(file_id)["ok"] is True

    database.initialize_database()
    path.unlink()
    stored = get_table_schema(file_id)

    assert stored["ok"] is True
    assert stored["message"] == "已读取真实保存的 Excel Schema"
    assert stored["data"]["schemas"][0]["row_count"] == 3
    assert len(database.fetch_all("table_schemas")) == 1
    assert len(database.fetch_all("schema_fields")) == 5


def test_empty_sheet_fails_without_forced_header_guess(
    discovery_data_dir: Path,
) -> None:
    file_id, _ = _register_workbook(
        discovery_data_dir,
        "空白工作簿.xlsx",
        lambda workbook: setattr(workbook.active, "title", "空Sheet"),
    )

    result = inspect_excel(file_id)

    assert result["ok"] is False
    assert result["error_code"] == "HEADER_DETECTION_FAILED"
    assert "Sheet 为空" in result["message"]
    stored = get_table_schema(file_id, "空Sheet")
    assert stored["ok"] is True
    assert stored["data"]["schemas"][0]["detection_status"] == "failed"
    record = database.get_file_record_by_id(file_id)
    assert record["parse_status"] == "failed"
    assert record["queryable"] == 0


def test_corrupt_excel_sets_failed_and_removes_stale_schema(
    discovery_data_dir: Path,
) -> None:
    file_id, _ = _register_corrupt_file(discovery_data_dir, "损坏结构.xlsx")

    result = inspect_excel(file_id)

    assert result["ok"] is False
    assert result["error_code"] == "EXCEL_PARSE_ERROR"
    assert get_table_schema(file_id)["error_code"] == "SCHEMA_NOT_FOUND"
    record = database.get_file_record_by_id(file_id)
    assert record["lifecycle_status"] == "failed"
    assert record["parse_status"] == "failed"
    assert record["queryable"] == 0


def test_irregular_duplicate_header_is_rejected_instead_of_using_data_row(
    discovery_data_dir: Path,
) -> None:
    def build(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "不规则"
        sheet.append(["编号", "姓名", "姓名"])
        sheet.append(["A01", "张甲", "一班"])
        sheet.append(["A02", "李乙", "二班"])

    file_id, _ = _register_workbook(discovery_data_dir, "不规则表头.xlsx", build)

    result = inspect_excel(file_id)

    assert result["ok"] is False
    assert result["error_code"] == "HEADER_DETECTION_FAILED"
    assert "重复字段" in result["message"]
    assert database.get_file_record_by_id(file_id)["queryable"] == 0


def test_schema_tool_validates_file_id_and_rejects_word_file() -> None:
    invalid = inspect_excel("../not-an-id")
    word_record = database.get_file_record("综合评价.docx")
    unsupported = inspect_excel(word_record["file_id"])

    assert invalid["error_code"] == "INVALID_FILE_ID"
    assert unsupported["error_code"] == "UNSUPPORTED_FILE_TYPE"
