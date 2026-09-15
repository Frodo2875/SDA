"""Real Streamlit rendering/navigation contracts; no live backend requests."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.shell import MENU

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda session_id: [])
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    return AppTest.from_file(APP).run(timeout=10)


def test_default_dashboard_header_and_menu(ui: AppTest) -> None:
    assert not ui.exception
    assert ui.session_state.active_page == "dashboard"
    assert "工作空间概览" in [item.value for item in ui.title]
    assert [item.label for item in ui.sidebar.button] == list(MENU.values())
    assert any("Workspace · 默认 Workspace" in item.value for item in ui.caption)
    assert any("用户 · 本地用户" in item.value for item in ui.caption)
    assert {item.label: item.value for item in ui.metric} == {
        "知识库数量": "—", "文件数量": "0", "任务数量": "0", "报告数量": "—",
    }


@pytest.mark.parametrize("page,title", [
    ("dashboard", "工作空间概览"), ("chat", "聊天"),
    ("knowledge", "知识库"), ("documents", "我的文档"),
    ("tasks", "研究任务"), ("reports", "报告中心"),
    ("usage", "使用统计"), ("settings", "系统设置"),
])
def test_navigation_renders_independent_page(ui: AppTest, page: str, title: str) -> None:
    session = ui.session_state.session_id
    ui.button(key=f"nav-{page}").click().run(timeout=10)
    assert not ui.exception
    assert ui.session_state.active_page == page
    assert title in [item.value for item in ui.title]
    assert ui.session_state.session_id == session
    if page not in {"dashboard", "chat"}:
        assert len(ui.chat_input) == 0


def test_chat_evidence_and_trace_survive_navigation(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = {"evidence_id": "ev-v70", "file_name": "规则.pdf", "file_id": "a" * 32,
                "source_type": "unstructured", "page_no": 2, "value_summary": "原始规则"}
    calls = []
    def chat(session_id: str, message: str) -> dict:
        calls.append((session_id, message))
        return {"answer": "规则说明", "evidence": [evidence], "tool_calls": []}
    monkeypatch.setattr(api_client, "chat", chat)
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [{"event_type": "tool_execution", "result_status": "success"}])
    monkeypatch.setattr(api_client, "locate_evidence", lambda *args: {**evidence, "location_type": "pdf", "text": "原始规则"})
    ui.button(key="nav-chat").click().run()
    ui.chat_input[0].set_value("查询规则").run()
    assert not ui.exception
    assert calls == [(ui.session_state.session_id, "查询规则")]
    assert ui.session_state.messages[-1]["traces"][0]["event_type"] == "tool_execution"
    next(button for button in ui.button if button.label == "打开原文").click().run()
    assert ui.session_state.selected_evidence_location["page_no"] == 2
    ui.button(key="nav-documents").click().run()
    ui.button(key="nav-chat").click().run()
    assert not ui.exception
    assert any(item.value == "规则说明" for item in ui.markdown)
    assert ui.session_state.selected_evidence_location["page_no"] == 2
    assert len(calls) == 1


def test_documents_upload_entry_and_filters_survive_navigation(ui: AppTest) -> None:
    ui.button(key="nav-documents").click().run()
    assert not ui.exception
    assert len(ui.get("file_uploader")) == 1
    assert any(item.label == "上传所选文件" for item in ui.button)
    ui.text_input(key="workspace_search").set_value("专业材料").run()
    ui.button(key="nav-settings").click().run()
    ui.button(key="nav-documents").click().run()
    assert not ui.exception
    assert ui.text_input(key="workspace_search").value == "专业材料"


def test_task_page_uses_existing_refresh(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    task = {"task": {"task_id": "task-v70", "task_type": "async_index", "task_status": "success"}}
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [task])
    monkeypatch.setattr(api_client, "get_task", lambda _: task)
    ui.button(key="nav-tasks").click().run()
    ui.button(key="refresh-task-center").click().run()
    assert not ui.exception
    assert "Task Center" in [item.value for item in ui.subheader]
    assert ui.session_state.known_tasks["task-v70"] == task
    ui.button(key="nav-dashboard").click().run()
    assert next(item for item in ui.metric if item.label == "任务数量").value == "1"


@pytest.mark.parametrize("page", ["knowledge", "reports", "usage"])
def test_navigation_uses_available_business_contracts(ui: AppTest, monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("placeholder must not call a new business endpoint")
    monkeypatch.setattr(api_client, "request", forbidden)
    ui.button(key=f"nav-{page}").click().run()
    assert not ui.exception
    assert len(ui.info) == 1
    assert ("暂无知识库" if page == "knowledge" else "尚未开放") in ui.info[0].value
    if page == "knowledge":
        assert any(item.label == "创建" for item in ui.button)
    assert not ui.get("file_uploader")


def test_overview_count_unfiltered_and_failure_is_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    def files(**kwargs):
        observed.append(kwargs)
        return [{"file_name": "学生.txt", "file_id": "a" * 32, "file_type": "txt"}] if not kwargs else []
    monkeypatch.setattr(api_client, "list_files", files)
    ui = AppTest.from_file(APP).run(timeout=10)
    assert not ui.exception
    assert {} in observed
    assert next(item for item in ui.metric if item.label == "文件数量").value == "1"
    def unavailable(**kwargs):
        raise RuntimeError("connection failed")
    monkeypatch.setattr(api_client, "list_files", unavailable)
    ui.button(key="refresh-overview").click().run()
    assert not ui.exception
    assert next(item for item in ui.metric if item.label == "文件数量").value == "—"
    assert any("暂不可用" in item.value for item in ui.warning)


def test_invalid_route_falls_back_to_dashboard(ui: AppTest) -> None:
    ui.session_state.active_page = "../../backend"
    ui.run()
    assert not ui.exception
    assert ui.session_state.active_page == "dashboard"
