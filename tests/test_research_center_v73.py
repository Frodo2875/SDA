"""Research task center UI and API contracts, without running task workers."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.research_center import evidence_counts, filter_tasks, timeline

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
TASK = {"task_id": "r1", "query": "学生专业就业分析", "session_id": "original-session", "status": "RUNNING",
        "created_at": "2026-09-15T10:00:00", "updated_at": "2026-09-15T10:20:00",
        "progress": {"stage": "WEB_RETRIEVAL"}, "result": None}
VIEW = {"task": {"task_id": "r1", "task_type": "async_research", "can_cancel": True,
                  "can_retry": False, "can_resume": False, "task_status": "running"}}
TRACES = [{"event_type": "research_stage", "created_at": "2026-09-15T10:01:00",
           "metrics_json": '{"stage":"LOCAL_RETRIEVAL"}', "result_status": "running"}]


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [VIEW])
    monkeypatch.setattr(api_client, "get_task", lambda _: VIEW)
    monkeypatch.setattr(api_client, "get_research_task", lambda _: dict(TASK))
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: TRACES)
    app = AppTest.from_file(APP).run(timeout=10)
    app.button(key="nav-tasks").click().run()
    assert not app.exception
    return app


def detail(ui: AppTest) -> AppTest:
    ui.button(key="research-open-r1").click().run()
    assert not ui.exception
    return ui


def test_task_list_and_detail_navigation(ui: AppTest) -> None:
    assert any(item.value == "学生专业就业分析" for item in ui.text)
    assert any("查找网页" in item.value for item in ui.caption)
    assert any("2026-09-15T10:20:00" in item.value for item in ui.caption)
    detail(ui)
    assert ui.session_state.research_selected_id == "r1"
    assert ui.metric[0].value == "—"
    ui.button(key="research-back").click().run()
    assert not ui.exception
    assert ui.session_state.research_selected_id is None


@pytest.mark.parametrize("label,visible", [("全部", True), ("运行中", True), ("已完成", False), ("失败", False)])
def test_list_filter(ui: AppTest, label: str, visible: bool) -> None:
    ui.radio(key="research-filter").set_value(label).run()
    assert not ui.exception
    assert any(button.key == "research-open-r1" for button in ui.button) is visible


def test_timeline_does_not_invent_completed_steps(ui: AppTest) -> None:
    detail(ui)
    rows = [item.value for item in ui.text]
    assert any("• 查找文档" in row for row in rows)
    assert any("→ 查找网页" in row for row in rows)
    assert not any("✓ 查找文档" in row or "核对资料" in row for row in rows)


@pytest.mark.parametrize("operation,status", [("cancel", "RUNNING"), ("retry", "FAILED"), ("resume", "CANCELLED")])
def test_actions_use_research_api(ui: AppTest, monkeypatch: pytest.MonkeyPatch, operation: str, status: str) -> None:
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": status})
    monkeypatch.setattr(api_client, "get_task", lambda _: {"task": {f"can_{operation}": True}})
    calls = []
    monkeypatch.setattr(api_client, "research_task_action", lambda *args: calls.append(args) or dict(TASK))
    detail(ui).button(key=f"research-{operation}").click().run()
    assert not ui.exception
    assert calls == [("r1", operation)]
    assert any("操作请求已提交" in item.value for item in ui.success)


def test_capability_fetch_failure_disables_actions(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: str) -> dict:
        raise RuntimeError("暂不可用")
    monkeypatch.setattr(api_client, "get_task", fail)
    detail(ui)
    assert all(ui.button(key=f"research-{operation}").disabled for operation in ["cancel", "retry", "resume"])
    assert any("操作权限暂不可用" in item.value for item in ui.warning)


def test_operation_rejected_is_visible(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> dict:
        raise RuntimeError("当前状态不支持此操作")
    monkeypatch.setattr(api_client, "research_task_action", fail)
    detail(ui).button(key="research-cancel").click().run()
    assert not ui.exception
    assert any("当前状态不支持" in item.value for item in ui.error)


def test_trace_and_result_evidence_use_original_session(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    local = {"source_type": "LOCAL", "file_name": "学生.md", "evidence_id": "ev1", "content": "学生信息"}
    web = {"source_type": "WEB", "url": "https://example.com", "domain": "example.com", "content": "就业信息"}
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": "COMPLETED",
                        "result": {"answer": "研究结论", "unified_evidence": [local, web]}})
    calls = []
    monkeypatch.setattr(api_client, "locate_evidence", lambda *args: calls.append(args) or {"file_name": "学生.md"})
    detail(ui)
    assert {metric.label: metric.value for metric in ui.metric} == {"本地引用": "1", "网页引用": "1"}
    ui.checkbox(key="research-trace-r1").check().run()
    assert any(item.label == "详细处理记录" for item in ui.expander)
    ui.checkbox(key="research-result-r1").check().run()
    assert any(item.value == "研究结论" for item in ui.markdown)
    next(button for button in ui.button if button.label == "打开原文").click().run()
    assert not ui.exception
    assert calls == [("ev1", "original-session")]


def test_rerun_polls_new_state_and_toggle_can_disable_timer(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "get_research_task", lambda identifier: calls.append(identifier) or {**TASK, "status": "COMPLETED"})
    assert ui.toggle(key="research-auto-refresh").value
    ui.run()
    assert "r1" in calls
    assert any("状态：已完成" in item.value for item in ui.caption)
    ui.toggle(key="research-auto-refresh").set_value(False).run()
    assert not ui.exception
    assert not ui.toggle(key="research-auto-refresh").value


def test_poll_failure_marks_cached_list_stale(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: str) -> dict:
        raise RuntimeError("读取失败")
    monkeypatch.setattr(api_client, "get_research_task", fail)
    ui.run()
    assert not ui.exception
    assert any("上次读取的状态" in item.value for item in ui.warning)


def test_detail_failure_has_no_action_buttons(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    detail(ui)
    def fail(_: str) -> dict:
        raise RuntimeError("任务不存在")
    monkeypatch.setattr(api_client, "get_research_task", fail)
    ui.run()
    assert any("任务不存在" in item.value for item in ui.error)
    assert not any(button.key == "research-cancel" for button in ui.button)


def test_history_sessions_are_included(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    ui.session_state.chat_history = {"previous-session": {"messages": []}}
    monkeypatch.setattr(api_client, "list_tasks", lambda session: calls.append(session) or [])
    ui.run()
    assert "previous-session" in calls
    assert ui.session_state.session_id in calls


def test_direct_task_id_entry(ui: AppTest) -> None:
    next(item for item in ui.text_input if "任务编号" in item.label).set_value("r1")
    next(item for item in ui.button if item.label == "打开任务").click().run()
    assert not ui.exception
    assert ui.session_state.research_selected_id == "r1"


def test_invalid_task_id_is_not_opened(ui: AppTest) -> None:
    next(item for item in ui.text_input if "任务编号" in item.label).set_value("../files")
    next(item for item in ui.button if item.label == "打开任务").click().run()
    assert not ui.exception
    assert any("正确的任务编号" in item.value for item in ui.error)
    assert not ui.session_state.filtered_state.get("research_selected_id")


def test_chat_can_open_task_center(ui: AppTest) -> None:
    ui.session_state.messages = [{"role": "assistant", "content": "任务已创建", "research_task_id": "r1"}]
    ui.button(key="nav-chat").click().run()
    ui.button(key="research-center-open-0").click().run()
    assert not ui.exception
    assert ui.session_state.active_page == "tasks"
    assert ui.session_state.research_selected_id == "r1"


def test_presentation_edge_cases() -> None:
    assert evidence_counts(TASK) == (None, None)
    assert evidence_counts({"result": {}}) == (0, 0)
    assert filter_tasks([{**TASK, "status": "PARTIAL_SUCCESS"}], "失败")
    assert not filter_tasks([{**TASK, "status": "CANCELLED"}], "运行中")
    rows = timeline({**TASK, "status": "COMPLETED", "progress": {"stage": "COMPLETED"}},
                    [{"event_type": "research_stage", "metrics_json": "[1]"},
                     {"event_type": "research_stage", "metrics_json": "invalid"}])
    assert len(rows) == 2 and rows[-1].startswith("✓ COMPLETED")


@pytest.mark.parametrize("operation", ["cancel", "retry", "resume"])
def test_operation_http_contract(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    calls = []
    monkeypatch.setattr(api_client, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or TASK)
    assert api_client.research_task_action("r1", operation) == TASK
    assert calls == [(("POST", f"/api/research-task/r1/{operation}"), {})]
    with pytest.raises(ValueError):
        api_client.research_task_action("r1", "delete")
