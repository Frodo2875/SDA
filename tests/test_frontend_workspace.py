"""V3 Frontend Workspace presentation and HTTP-client contracts."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.components.evidence_panel import evidence_location
from frontend.components.file_panel import (
    FILE_TYPE_FILTERS,
    FILE_TYPES,
    SUPPORTED_UPLOAD_EXTENSIONS,
    file_card_fields,
    file_detail_sections,
    file_operation_capabilities,
    filter_files_by_lifecycle,
)
from frontend.components.task_center import (
    format_duration,
    format_progress,
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
        "deletable": True,
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


def test_v5_file_types_are_uploadable_filterable_and_presented() -> None:
    assert FILE_TYPES == {
        "excel": "Excel",
        "word": "Word",
        "pdf": "PDF",
        "image": "图片",
        "presentation": "PPT/PPTX",
        "txt": "TXT",
        "json": "JSON",
        "csv": "CSV",
    }
    assert FILE_TYPE_FILTERS["PPT/PPTX"] == "presentation"
    assert FILE_TYPE_FILTERS["TXT"] == "txt"
    assert FILE_TYPE_FILTERS["JSON"] == "json"
    assert FILE_TYPE_FILTERS["CSV"] == "csv"
    assert {"ppt", "pptx", "txt", "json", "csv"} <= set(
        SUPPORTED_UPLOAD_EXTENSIONS
    )
    for file_type in ("presentation", "txt", "json"):
        capabilities = file_operation_capabilities(_file(file_type=file_type))
        assert capabilities["reprocess"] is True
        assert capabilities["reindex"] is True
    csv_capabilities = file_operation_capabilities(_file(file_type="csv"))
    assert csv_capabilities["reprocess"] is False
    assert csv_capabilities["reindex"] is False


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


def test_api_client_distinguishes_timeout_and_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8000/api/chat")

    def raise_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(api_client.httpx, "request", raise_timeout)
    with pytest.raises(RuntimeError, match="后端处理超时"):
        api_client.chat("session", "复杂跨来源任务")

    def raise_connect(*args, **kwargs):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(api_client.httpx, "request", raise_connect)
    with pytest.raises(RuntimeError, match="无法连接后端服务"):
        api_client.chat("session", "普通任务")


def test_chat_uses_extended_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        observed.update({"method": method, "path": path, **kwargs})
        return {"answer": "ok"}

    monkeypatch.setattr(api_client, "request", fake_request)
    assert api_client.chat("session", "message") == {"answer": "ok"}
    assert observed["timeout"] == 120.0


def test_workspace_file_operation_capabilities_and_api_routes(monkeypatch) -> None:
    upload = _file()
    system = _file(source_type="system", deletable=False)
    excel = _file(file_type="excel")

    assert file_operation_capabilities(upload) == {
        "details": True,
        "preview": True,
        "reprocess": True,
        "reindex": True,
        "delete": True,
    }
    assert file_operation_capabilities(system)["delete"] is False
    assert file_operation_capabilities(excel)["reprocess"] is False
    assert file_operation_capabilities(excel)["reindex"] is False

    observed: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        observed.append((method, path, kwargs))
        return {"ok": True, "data": {"action_id": "action-1"}, "message": "ok"}

    monkeypatch.setattr(api_client, "request", fake_request)
    file_id = upload["file_id"]
    assert api_client.get_file_preview(file_id)["action_id"] == "action-1"
    assert api_client.locate_evidence("evidence-1", "session-1")["action_id"] == "action-1"
    api_client.reprocess_file(file_id)
    api_client.reindex_file(file_id)
    api_client.prepare_delete(file_id, "session-1")

    assert observed == [
        ("GET", f"/api/files/{file_id}/preview", {}),
        (
            "GET",
            "/api/evidence/evidence-1/locate",
            {"params": {"session_id": "session-1"}},
        ),
        ("POST", f"/api/files/{file_id}/reprocess", {}),
        ("POST", f"/api/files/{file_id}/reindex", {}),
        (
            "POST",
            f"/api/files/{file_id}/delete",
            {"json": {"session_id": "session-1"}},
        ),
    ]


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
    assert format_progress(
        {"unit": "pages", "processed_pages": 3, "total_pages": 10}
    ) == "3 / 10 页"
    assert format_progress({"unit": "stage", "stage": "正在原子切换"}) == "正在原子切换"
    assert format_duration(1250) == "1.2s"


def test_task_center_api_contract_routes(monkeypatch) -> None:
    observed: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        observed.append((method, path, kwargs))
        return {"ok": True, "data": [] if method == "GET" else {"task": {}}}

    monkeypatch.setattr(api_client, "request", fake_request)
    assert api_client.list_tasks("session-v320") == []
    api_client.cancel_task("task-1")
    api_client.retry_task("task-1")
    api_client.resume_task("task-1")

    assert observed == [
        ("GET", "/api/sessions/session-v320/tasks", {"params": {"limit": 100}}),
        ("POST", "/api/tasks/task-1/cancel", {}),
        ("POST", "/api/tasks/task-1/retry", {}),
        ("POST", "/api/tasks/task-1/resume", {}),
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
    assert any(button.label == "查看详情" for button in app.button)
    assert any(button.label == "快速预览" for button in app.button)
    assert any(button.label == "重新解析" for button in app.button)
    assert any(button.label == "重新索引" for button in app.button)
    assert any(button.label == "删除文件" for button in app.button)
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


def test_f12_system_file_has_no_workspace_delete_entry(monkeypatch) -> None:
    monkeypatch.setattr(
        api_client,
        "list_files",
        lambda **kwargs: [_file(source_type="system", deletable=False)],
    )

    app = AppTest.from_file(PROJECT_ROOT / "frontend" / "app.py").run(timeout=10)

    assert not app.exception
    assert "删除文件" not in {button.label for button in app.button}
    assert any("系统固定文件受保护" in caption.value for caption in app.caption)


def test_f11_workspace_delete_shows_target_and_can_be_cancelled(monkeypatch) -> None:
    item = _file(file_name="待确认删除.pdf")
    action = {
        "action_id": "delete-action",
        "action_type": "delete_file",
        "target_file": item["file_name"],
        "content": item["file_id"],
        "status": "pending",
    }
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [item])
    monkeypatch.setattr(api_client, "list_tasks", lambda session_id: [])
    monkeypatch.setattr(
        api_client,
        "prepare_delete",
        lambda file_id, session_id: {
            "ok": True,
            "data": action,
            "message": "待确认删除操作已创建",
        },
    )
    monkeypatch.setattr(
        api_client,
        "decide_action",
        lambda action_id, decision: {
            "ok": True,
            "data": {**action, "status": "cancelled"},
            "message": "操作已取消",
        },
    )

    app = AppTest.from_file(PROJECT_ROOT / "frontend" / "app.py").run(timeout=10)
    next(button for button in app.button if button.label == "删除文件").click().run(
        timeout=10
    )

    assert not app.exception
    assert any("待确认删除.pdf" in warning.value for warning in app.warning)
    next(button for button in app.button if button.label == "取消").click().run(timeout=10)
    assert not app.exception
    assert any("已取消，目标文件没有变化" in item.value for item in app.markdown)


def test_answer_evidence_click_opens_pdf_page_and_bbox_preview(monkeypatch) -> None:
    evidence = {
        "evidence_id": "evidence-r206",
        "source_type": "unstructured",
        "file_id": "a" * 32,
        "file_name": "规则.pdf",
        "page_no": 4,
        "block_id": "block-4",
        "bbox": [10, 20, 100, 60],
        "value_summary": "奖学金规则原文",
    }
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(
        api_client,
        "chat",
        lambda session_id, message: {
            "answer": "根据原文回答。",
            "status": "success",
            "evidence": [evidence],
            "tool_calls": [],
        },
    )
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    monkeypatch.setattr(
        api_client,
        "locate_evidence",
        lambda evidence_id, session_id: {
            **evidence,
            "location_type": "pdf",
            "text": "奖学金规则原文",
            "confidence": 0.91,
            "highlight": {"bbox": evidence["bbox"]},
        },
    )

    app = AppTest.from_file(PROJECT_ROOT / "frontend" / "app.py").run(timeout=10)
    app.chat_input[0].set_value("查询规则").run(timeout=10)

    assert not app.exception
    source_buttons = [button for button in app.button if button.label == "打开原文"]
    assert source_buttons
    source_buttons[0].click().run(timeout=10)
    assert not app.exception
    assert any("高亮区域 BBox" in warning.value for warning in app.warning)
    assert any("PDF · 第 4 页" in caption.value for caption in app.caption)
