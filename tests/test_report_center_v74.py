"""Report UI reuses stored reports, binary downloads and approved rollback."""

from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
REPORT = {"task_id": "t1", "report_id": "r1", "title": "专业分析报告", "created_at": "2026-09-15",
          "status": "EXPORTED", "summary": "# 研究背景\n\n- 计算机专业\n\n> 已保存内容",
          "sections": [{"title": "限制说明", "content": "需核对"}], "warnings": [{"message": "时间缺失"}],
          "evidence_refs": [{"source_type": "LOCAL", "file_name": "学生.xlsx", "sheet": "Sheet1", "row_number": 20}],
          "exports": {"md": {"file_id": "md-file", "status": "executed"}, "docx": {"file_id": "word-file", "status": "executed"}}}
TASK = {"task_id": "t1", "session_id": "original", "status": "COMPLETED", "result": {"reports": {"r1": REPORT}}}


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [{"task": {"task_id": "t1", "task_type": "async_research"}}])
    monkeypatch.setattr(api_client, "get_research_task", lambda _: TASK)
    monkeypatch.setattr(api_client, "get_report", lambda *args: REPORT)
    monkeypatch.setattr(api_client, "list_versions", lambda _: [{"version_id": "v1", "version_number": 1, "created_at": "2026-09-15", "status": "available"}])
    app = AppTest.from_file(APP).run(timeout=10)
    app.button(key="nav-reports").click().run()
    assert not app.exception
    return app


def detail(ui: AppTest) -> AppTest:
    ui.button(key="report-open-t1-r1").click().run()
    assert not ui.exception
    return ui


def test_list_detail_markdown_evidence_warning_and_versions(ui: AppTest) -> None:
    assert any(item.value == "专业分析报告" for item in ui.subheader)
    assert any("来源任务：t1" in item.value for item in ui.caption)
    detail(ui)
    assert any(item.value == REPORT["summary"] for item in ui.markdown)
    assert any("学生.xlsx" in item.value for item in ui.markdown)
    assert any("时间缺失" in item.value for item in ui.warning)
    assert any("v1" in item.value and "available" in item.value for item in ui.caption)
    assert len([item for item in ui.button if item.label == "恢复此版本"]) == 1
    ui.button(key="report-back").click().run()
    assert not ui.exception and ui.session_state.selected_report is None


@pytest.mark.parametrize("format", ["md", "docx"])
def test_download_uses_api_bytes(ui: AppTest, monkeypatch: pytest.MonkeyPatch, format: str) -> None:
    calls = []
    monkeypatch.setattr(api_client, "download_report", lambda *args: calls.append(args) or b"# Stored report")
    detail(ui).button(key=f"report-load-{format}").click().run()
    assert not ui.exception
    assert calls == [("t1", "r1", format)]
    assert len(ui.get("download_button")) == 1


def test_unapproved_report_cannot_download(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "get_report", lambda *args: {**REPORT, "status": "DRAFT", "exports": {}})
    detail(ui)
    assert not any(item.key in {"report-load-md", "report-load-docx"} for item in ui.button)
    assert any("尚未批准" in item.value for item in ui.caption)


def test_rollback_requires_confirmation_and_original_session(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, decisions = [], []
    monkeypatch.setattr(api_client, "prepare_rollback", lambda *args: calls.append(args) or {
        "ok": True, "data": {"action_id": "a1", "status": "pending", "target_version_id": "v1"}})
    monkeypatch.setattr(api_client, "decide_action", lambda *args: decisions.append(args) or {"ok": True, "data": {}})
    detail(ui).button(key="report-rollback-v1").click().run()
    assert calls == [("word-file", "v1", "original")]
    assert decisions == []
    ui.button(key="report-action-t1-r1-confirm").click().run()
    assert not ui.exception
    assert decisions == [("a1", "confirm")]
    assert any("操作已确认执行" in item.value for item in ui.success)


@pytest.mark.parametrize("decision", ["confirm", "cancel"])
def test_export_approval(ui: AppTest, monkeypatch: pytest.MonkeyPatch, decision: str) -> None:
    calls = []
    monkeypatch.setattr(api_client, "preview_report", lambda *args: {"ok": True, "data": {"action_id": "a1", "status": "pending"}})
    monkeypatch.setattr(api_client, "decide_action", lambda *args: calls.append(args) or {"ok": True})
    detail(ui).button(key="report-preview-md").click().run()
    ui.button(key=f"report-action-t1-r1-{decision}").click().run()
    assert not ui.exception and calls == [("a1", decision)]


def test_approval_failure_preserves_pending(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "preview_report", lambda *args: {"ok": True, "data": {"action_id": "a1", "status": "pending"}})
    monkeypatch.setattr(api_client, "decide_action", lambda *args: {"ok": False, "message": "审批失败"})
    detail(ui).button(key="report-preview-md").click().run()
    ui.button(key="report-action-t1-r1-confirm").click().run()
    assert not ui.exception
    assert any("审批失败" in item.value for item in ui.error)
    assert ui.session_state["report-action-t1-r1"]["status"] == "pending"


def test_create_calls_existing_generator_once(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "create_report", lambda *args: calls.append(args) or REPORT)
    ui.button(key="report-new").click().run()
    ui.run()
    assert not ui.exception and calls == [("t1", "证据研究报告")]


def test_detail_failure_no_download(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    detail(ui)
    def fail(*args: object) -> dict:
        raise RuntimeError("报告不可用")
    monkeypatch.setattr(api_client, "get_report", fail)
    ui.run()
    assert not ui.exception
    assert any("报告不可用" in item.value for item in ui.error)
    assert not ui.get("download_button")


@pytest.mark.parametrize("format,mime", [("md", "text/markdown; charset=utf-8"), ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
def test_binary_download_contract(monkeypatch: pytest.MonkeyPatch, format: str, mime: str) -> None:
    calls = []
    def get(url: str, **kwargs: object) -> httpx.Response:
        calls.append((url, kwargs))
        return httpx.Response(200, content=b"original bytes", headers={"content-type": mime})
    monkeypatch.setattr(httpx, "get", get)
    assert api_client.download_report("t1", "r1", format) == b"original bytes"
    assert calls[0][0].endswith("/api/research-task/t1/reports/r1/download")
    assert calls[0][1]["params"] == {"format": format}


@pytest.mark.parametrize("status,mime", [(409, "application/json"), (200, "application/json")])
def test_download_error_never_returned_as_file(monkeypatch: pytest.MonkeyPatch, status: int, mime: str) -> None:
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(status, content=b"{}", headers={"content-type": mime}))
    with pytest.raises(RuntimeError):
        api_client.download_report("t1", "r1", "md")
