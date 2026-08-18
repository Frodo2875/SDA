"""Final Demo A: one previously unseen Excel through only generic V2 capabilities."""

import json
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import pytest
from openpyxl import Workbook

from backend import database
from backend.agent import run_agent
from backend.services import file_upload
from backend.services.file_view import list_file_views
from backend.services.relation_service import resolve_entity
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel
from backend.tools.table_tools import aggregate_table, query_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILE_NAME = "2026学生综合信息登记表.xlsx"
SHEET_NAME = "综合登记"


def _unseen_excel() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.merge_cells("A1:F1")
    sheet["A1"] = "2026 学生综合信息登记表（虚拟数据）"
    sheet.append([])
    sheet.append(["学生学号", "学生姓名", "所属院系", "移动电话", "培养层次", "行政班"])
    sheet.append(["S001", "张三", "计算机学院", "13812345678", "硕士一年级", "计科1班"])
    sheet.append(["S002", "李四", "计算机学院", None, "硕士一年级", "计科1班"])
    sheet.append(["S003", "王五", "软件学院", "13900001111", "硕士二年级", "软件2班"])
    sheet.append(["S005", "赵敏", "计算机学院", None, "硕士一年级", "计科2班"])
    sheet.append(["S006", "孙悦", "人工智能学院", "13722223333", "硕士一年级", "智能1班"])
    sheet.append(["S007", "周强", "软件学院", "13644445555", "硕士二年级", "软件2班"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class OneToolClient:
    def __init__(
        self,
        name: str,
        arguments: dict[str, Any],
        formatter: Callable[[dict[str, Any]], str],
    ) -> None:
        self.name = name
        self.arguments = arguments
        self.formatter = formatter
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "demo-a-tool",
                        "type": "function",
                        "function": {
                            "name": self.name,
                            "arguments": json.dumps(self.arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        tool_result = json.loads(
            next(item["content"] for item in reversed(messages) if item["role"] == "tool")
        )
        return {
            "role": "assistant",
            "content": self.formatter(tool_result),
            "tool_calls": [],
        }


@pytest.mark.anyio
async def test_final_demo_a_unseen_excel_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the demo isolated while retaining the actual fixed V1 sources for relation.
    for name in ("学生基本信息.xlsx", "学生成绩.xlsx", "科研成果.xlsx", "综合评价.docx"):
        shutil.copy2(PROJECT_ROOT / "data" / name, tmp_path / name)
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)

    lifecycle_observations: list[dict[str, Any]] = []
    real_process = file_upload.process_uploaded_file

    def observe_process(file_id, path, parser):
        initial = database.get_file_record_by_id(file_id)
        lifecycle_observations.append(
            {
                "lifecycle_status": initial["lifecycle_status"],
                "parse_status": initial["parse_status"],
                "queryable": bool(initial["queryable"]),
            }
        )

        def observe_parser(current_path, suffix):
            processing = database.get_file_record_by_id(file_id)
            lifecycle_observations.append(
                {
                    "lifecycle_status": processing["lifecycle_status"],
                    "parse_status": processing["parse_status"],
                    "queryable": bool(processing["queryable"]),
                }
            )
            return parser(current_path, suffix)

        return real_process(file_id, path, observe_parser)

    monkeypatch.setattr(file_upload, "process_uploaded_file", observe_process)
    uploaded = file_upload.save_uploaded_file(FILE_NAME, _unseen_excel())

    assert uploaded["ok"] is True
    assert lifecycle_observations == [
        {"lifecycle_status": "uploaded", "parse_status": "pending", "queryable": False},
        {"lifecycle_status": "processing", "parse_status": "processing", "queryable": False},
    ]
    file_id = uploaded["data"]["file_id"]
    parsed = inspect_excel(file_id)
    record = database.get_file_record_by_id(file_id)

    assert parsed["ok"] is True
    assert record["lifecycle_status"] == "ready"
    assert record["parse_status"] == "parsed"
    assert bool(record["queryable"]) is True
    schema = parsed["data"]["schemas"][0]
    assert schema["sheet_name"] == SHEET_NAME
    assert schema["header_row"] == 3
    assert schema["column_count"] == 6
    assert schema["row_count"] == 6
    semantic_mapping = {
        field["source_name"]: field["canonical_name"] for field in schema["fields"]
    }
    assert {
        key: semantic_mapping[key]
        for key in ("学生学号", "学生姓名", "所属院系", "移动电话")
    } == {
        "学生学号": "student_id",
        "学生姓名": "name",
        "所属院系": "college",
        "移动电话": "phone",
    }

    file_view = next(
        item for item in list_file_views()["data"] if item["file_id"] == file_id
    )
    assert file_view["schema_summary"] == {
        "sheet_count": 1,
        "field_count": 6,
        "row_count": 6,
        "sheets": [{"sheet_name": SHEET_NAME, "field_count": 6, "row_count": 6}],
    }

    person_args = {
        "file_id": file_id,
        "sheet": SHEET_NAME,
        "filters": [{"field": "学生姓名", "op": "=", "value": "张三"}],
        "select": ["学生学号", "学生姓名", "所属院系", "移动电话"],
    }
    person = await run_agent(
        "张三哪个学院、联系电话是多少？",
        session_id="demo-a-person",
        client=OneToolClient(
            "query_table",
            person_args,
            lambda result: (
                f"张三所在学院为{result['data']['rows'][0]['所属院系']}，"
                f"联系电话为{result['data']['rows'][0]['移动电话']}。"
            ),
        ),
    )
    assert person["tool_calls"][0]["name"] == "query_table"
    assert person["tool_calls"][0]["result"]["data"]["rows"] == [
        {"学生学号": "S001", "学生姓名": "张三", "所属院系": "计算机学院", "移动电话": "13812345678"}
    ]
    assert "计算机学院" in person["answer"] and "13812345678" in person["answer"]
    assert {
        (item["file_name"], item["sheet"], item["field"])
        for item in person["evidence"]
    } == {
        (FILE_NAME, SHEET_NAME, field)
        for field in ("学生学号", "学生姓名", "所属院系", "移动电话")
    }

    count_args = {
        "file_id": file_id,
        "sheet": SHEET_NAME,
        "operation": "count",
        "filters": [{"field": "所属院系", "op": "=", "value": "计算机学院"}],
    }
    count = await run_agent(
        "计算机学院有多少人？",
        session_id="demo-a-count",
        client=OneToolClient(
            "aggregate_table",
            count_args,
            lambda result: f"计算机学院共有{result['data']['results'][0]['value']}人。",
        ),
    )
    assert count["tool_calls"][0]["name"] == "aggregate_table"
    assert count["tool_calls"][0]["result"]["data"]["results"][0]["value"] == 3
    assert "3人" in count["answer"]

    null_args = {
        "file_id": file_id,
        "sheet": SHEET_NAME,
        "filters": [{"field": "移动电话", "op": "is_null", "value": None}],
        "select": ["学生学号", "学生姓名", "所属院系", "移动电话"],
    }
    missing_phone = await run_agent(
        "找出联系电话为空的学生。",
        session_id="demo-a-null",
        client=OneToolClient(
            "query_table",
            null_args,
            lambda result: "联系电话为空的学生："
            + "、".join(row["学生姓名"] for row in result["data"]["rows"]),
        ),
    )
    assert missing_phone["tool_calls"][0]["arguments"]["filters"] == [
        {"field": "移动电话", "op": "is_null", "value": None}
    ]
    assert {row["学生姓名"] for row in missing_phone["tool_calls"][0]["result"]["data"]["rows"]} == {"李四", "赵敏"}

    relation = resolve_entity("S001")
    uploaded_record = next(item for item in relation["records"] if item["file_id"] == file_id)
    score_record = next(
        item for item in relation["records"] if item["file_name"] == "学生成绩.xlsx"
    )
    combined = {
        "student_id": "S001",
        "college": uploaded_record["row"]["所属院系"],
        "average_score": score_record["row"]["平均分"],
        "relation_method": {
            "uploaded": uploaded_record["relation_method"],
            "scores": score_record["relation_method"],
        },
    }
    assert relation["status"] == "found"
    assert combined == {
        "student_id": "S001",
        "college": "计算机学院",
        "average_score": 90.67,
        "relation_method": {"uploaded": "student_id", "scores": "student_id"},
    }
    print(
        "DEMO_A_RESULT="
        + json.dumps(
            {
                "file_name": FILE_NAME,
                "lifecycle_observations": lifecycle_observations,
                "final_state": {
                    "lifecycle_status": record["lifecycle_status"],
                    "parse_status": record["parse_status"],
                    "queryable": bool(record["queryable"]),
                },
                "schema": {
                    "sheet": SHEET_NAME, "header_row": 3,
                    "field_count": 6, "row_count": 6,
                    "semantic_mapping": semantic_mapping,
                },
                "person_rows": person["tool_calls"][0]["result"]["data"]["rows"],
                "person_evidence_count": len(person["evidence"]),
                "college_count": count["tool_calls"][0]["result"]["data"]["results"][0]["value"],
                "missing_phone_students": sorted(
                    row["学生姓名"]
                    for row in missing_phone["tool_calls"][0]["result"]["data"]["rows"]
                ),
                "cross_file_result": combined,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
