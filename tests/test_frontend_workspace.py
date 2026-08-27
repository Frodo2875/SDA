"""V3 Frontend Workspace presentation and HTTP-client contracts."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.components.evidence_panel import evidence_location
from frontend.components.file_panel import (
    file_card_fields,
    file_detail_sections,
    filter_files_by_lifecycle,
)
from frontend.components.task_center import (
    task_category,
    task_center_groups,
    task_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _file(**values: Any) -> dict[str, Any]:
    return {
        "file_id": "a" * 32,
        "file_name": "材料.pdf",
        "file_type": "pdf",
        "size": 1536,
        "created_time": "2026-08-21T10:00:00+08:00",
        "lifecycle_status": "queryable",
        "index_status": "indexed",
        "queryable": True,
        "source_type": "upload",
        **values,
    }


def test_file_cards_filters_and_type_specific_details() -> None:
    item = _file()
    fields = file_card_fields(item)

    assert fields == {
        "file_name": "材料.pdf",
        "file_type": "PDF",
        "size": "1.5 KB",
        "created_time": "2026-08-21T10:00:00+08:00",
        "lifecycle_status": "可查询",
        "index_status": "indexed",
        "queryable": "是",
    }
    assert filter_files_by_lifecycle([item], "QUERYABLE") == [item]
    assert filter_files_by_lifecycle([item], "FAILED") == []
    assert file_detail_sections(
        {
            **item,
            "page_count": 3,
            "ocr_status": "success",
            "layout_status": "success",
            "block_count": 12,
            "chunk_count": 8,
        }
    )["PDF"] == {
        "页数": 3,
        "OCR状态": "success",
        "Layout状态": "success",
        "Block数量": 12,
        "Chunk数量": 8,
    }
    excel = file_detail_sections(
        {
            "file_type": "excel",
            "schema_summary": {
                "field_count": 6,
                "sheets": [{"sheet_name": "学生"}, {"sheet_name": "成绩"}],
            },
            "quality_status": "passed",
        }
    )["Excel"]
    assert excel == {
        "Sheet": "学生, 成绩",
        "Schema": "已识别",
        "字段": 6,
        "质量检查结果": "passed",
    }
    word = file_detail_sections(
        {"file_type": "word"},
        [{"version_number": 1}, {"version_number": 2}],
    )["Word"]
    assert word["版本"] == 2
    assert word["Diff"] == "操作确认时展示"
    assert word["写入状态"] == "无待执行写入"


def test_api_client_sends_workspace_filters_and_multi_upload_statuses(
    monkeypatch,
) -> None:
    observed: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        observed.append((method, path, kwargs))
        return {"ok": True, "data": [_file()], "message": "ok"}

    monkeypatch.setattr(api_client, "request", fake_request)
    files = api_client.list_files(
        search="材料",
        file_type="pdf",
        lifecycle_status=None,
        sort_by="created_time",
        sort_order="desc",
    )

    assert files[0]["file_name"] == "材料.pdf"
    assert observed == [
        (
            "GET",
            "/api/files",
            {
                "params": {
                    "search": "材料",
                    "file_type": "pdf",
                    "sort_by": "created_time",
                    "sort_order": "desc",
                }
            },
        )
    ]

    uploads = [
        SimpleNamespace(name="成功.pdf"),
        SimpleNamespace(name="失败.docx"),
    ]

    def fake_upload(uploaded_file: Any) -> dict[str, Any]:
        if uploaded_file.name.startswith("失败"):
            raise RuntimeError("格式错误")
        return {"message": "上传完成", "data": {"file_name": uploaded_file.name}}

    monkeypatch.setattr(api_client, "upload_file", fake_upload)
    outcomes = api_client.upload_files(uploads)

    assert [(item["file_name"], item["status"]) for item in outcomes] == [
        ("成功.pdf", "success"),
        ("失败.docx", "failed"),
    ]
    assert outcomes[1]["message"] == "格式错误"


def test_task_center_statuses_and_evidence_source_location() -> None:
    tasks = [
        {"task": {"task_id": "1", "task_type": "async_ocr", "task_status": "created"}},
        {"task": {"task_id": "2", "task_type": "async_index", "task_status": "running"}},
        {"task": {"task_id": "3", "task_type": "scholarship", "status": "waiting_confirmation"}},
    ]
    groups = task_center_groups(tasks)

    assert task_category(tasks[0]) == "OCR任务"
    assert task_category(tasks[1]) == "索引任务"
    assert task_category(tasks[2]) == "Workflow任务"
    assert [len(groups[key]) for key in ("OCR任务", "索引任务", "Workflow任务")] == [1, 1, 1]
    assert [task_status(item) for item in tasks] == [
        "pending", "running", "waiting_confirmation"
    ]
    assert evidence_location(
        {
            "page_no": 2,
            "block_id": "block-2",
            "cell": "B4",
            "bbox": [1, 2, 3, 4],
        }
    ) == [
        "Page：2",
        "Block：block-2",
        "Cell：B4",
        "BBox：[1, 2, 3, 4]",
    ]


def test_streamlit_page_renders_three_workspace_areas(monkeypatch) -> None:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [_file()])

    app = AppTest.from_file(PROJECT_ROOT / "frontend" / "app.py").run(timeout=10)

    assert not app.exception
    subheaders = {item.value for item in app.subheader}
    assert {"Document Workspace", "Agent Chat", "Evidence Preview", "Task Center"} <= subheaders
    assert [toggle.label for toggle in app.toggle] == ["显示左栏", "显示右栏"]
    assert any(button.label == "上传所选文件" for button in app.button)
    assert any(button.label == "应用搜索 / 筛选 / 排序" for button in app.button)
    assert any(expander.label.startswith("OCR任务") for expander in app.expander)
    assert any(expander.label.startswith("索引任务") for expander in app.expander)
    assert any(expander.label.startswith("Workflow任务") for expander in app.expander)


def test_streamlit_side_panels_can_be_hidden_independently(monkeypatch) -> None:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [_file()])
    app = AppTest.from_file(PROJECT_ROOT / "frontend" / "app.py").run(timeout=10)

    app.toggle[0].set_value(False).run(timeout=10)
    subheaders = {item.value for item in app.subheader}
    assert "Document Workspace" not in subheaders
    assert {"Agent Chat", "Evidence Preview", "Task Center"} <= subheaders

    app.toggle[1].set_value(False).run(timeout=10)
    subheaders = {item.value for item in app.subheader}
    assert subheaders == {"Agent Chat"}
