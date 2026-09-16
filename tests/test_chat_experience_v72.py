"""Chat product flows reuse HTTP contracts and retain observable provenance."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.presentation import label
from frontend.components.evidence_panel import evidence_location, source_labels, web_url

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
LOCAL = {"evidence_id": "local", "source_type": "LOCAL", "file_name": "学生名单.xlsx",
         "sheet": "Sheet1", "row_number": 20, "metadata": {"row_end": 35}, "content": "计算机专业", "locator_type": "excel"}
WEB = {"evidence_id": "web", "source_type": "WEB", "url": "https://example.com/research",
       "domain": "example.com", "retrieved_at": "2026-09-15", "content": "就业方向"}
TASK = {"task_id": "research-1", "status": "CREATED", "progress": {"stage": "CREATED"}, "result": None}


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [])
    monkeypatch.setattr(api_client, "chat", lambda *args: {"answer": "专业分析", "unified_evidence": [LOCAL, WEB]})
    monkeypatch.setattr(api_client, "create_research_task", lambda *args: dict(TASK))
    app = AppTest.from_file(APP).run(timeout=10)
    app.button(key="nav-chat").click().run()
    assert not app.exception
    return app


def research(ui: AppTest, mode: str = "深度研究") -> None:
    ui.selectbox(key="chat_mode").set_value(mode).run()
    ui.chat_input[0].set_value("分析专业就业方向").run()
    assert not ui.exception


def test_chat_layout_and_modes(ui: AppTest) -> None:
    assert ui.selectbox(key="chat_mode").options == ["普通问答", "深度研究", "报告生成"]
    assert not [item for item in ui.radio if item.key == "chat_mode"]
    assert len(ui.chat_input) == 1
    assert not ui.toggle(key="chat-show-sources").value
    assert not any(item.value == "参考资料" for item in ui.subheader)
    assert any("当前知识库：全部文档" in item.value for item in ui.caption)


def test_normal_chat_sources_and_evidence_cards(ui: AppTest) -> None:
    ui.toggle(key="chat-show-sources").set_value(True).run()
    ui.chat_input[0].set_value("分析专业").run()
    assert not ui.exception
    assert any(item.value == "专业分析" for item in ui.markdown)
    assert any("[1] 本地文件" in item.value for item in ui.text)
    assert any("[2] Web 来源" in item.value for item in ui.text)
    assert any("Row：20-35" in item.value for item in ui.caption)
    assert any("Retrieved At：2026-09-15" in item.value for item in ui.caption)
    assert any("https://example.com/research" in item.value for item in ui.text)


def test_deep_mode_calls_existing_task_api_only(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "create_research_task", lambda *args: calls.append(args) or dict(TASK))
    def forbidden(*args: object) -> None:
        pytest.fail("deep mode must use the research API")
    monkeypatch.setattr(api_client, "chat", forbidden)
    research(ui)
    assert calls == [(ui.session_state.session_id, "分析专业就业方向")]
    assert any("状态：待开始" in item.value for item in ui.markdown)


@pytest.mark.parametrize("status,stage", [("RUNNING", "PLANNING"), ("WAITING_TOOL", "WEB_RETRIEVAL"),
                                          ("RUNNING", "EVIDENCE_CHECK"), ("CANCELLED", "CANCELLED")])
def test_research_displays_actual_state(ui: AppTest, monkeypatch: pytest.MonkeyPatch, status: str, stage: str) -> None:
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": status, "progress": {"stage": stage}})
    research(ui)
    ui.button(key="research-refresh-1").click().run()
    assert not ui.exception
    assert any(f"状态：{label(status)} · 阶段：{label(stage)}" in item.value for item in ui.markdown)
    assert ui.session_state.messages[-1]["research"]["status"] == status


def test_research_completed_result_and_evidence(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": "COMPLETED",
                        "progress": {"stage": "COMPLETED"}, "result": {"answer": "研究完成", "unified_evidence": [LOCAL, WEB]}})
    research(ui)
    ui.button(key="research-refresh-1").click().run()
    ui.button(key="research-refresh-1").click().run()
    assert not ui.exception
    assert len(ui.session_state.messages) == 2
    assert ui.session_state.messages[-1]["content"] == "研究完成"
    assert ui.session_state.latest_evidence == [LOCAL, WEB]


def test_failed_research_and_poll_error_are_visible(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": "FAILED",
                        "error": {"error_message": "研究执行失败"}})
    research(ui)
    ui.button(key="research-refresh-1").click().run()
    assert any("研究执行失败" in item.value for item in ui.error)
    def unavailable(_: str) -> dict:
        raise RuntimeError("网络暂不可用")
    monkeypatch.setattr(api_client, "get_research_task", unavailable)
    ui.button(key="research-refresh-1").click().run()
    assert not ui.exception
    assert any("网络暂不可用" in item.value for item in ui.warning)
    assert ui.session_state.messages[-1]["research"]["status"] == "FAILED"


def test_report_requires_completion_and_uses_draft_and_preview_api(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "create_report", lambda *args: calls.append(args) or {
        "report_id": "report-1", "title": "证据研究报告", "summary": "报告正文", "warnings": [{"message": "时间缺失"}]})
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": "COMPLETED", "result": {"answer": "研究结果"}})
    previews = []
    monkeypatch.setattr(api_client, "preview_report", lambda *args: previews.append(args) or {
        "ok": True, "message": "报告待审批", "data": {"action_id": "approve-1", "status": "pending", "action_type": "write_report"}})
    research(ui, "报告生成")
    assert ui.button(key="report-create-1").disabled
    assert not calls
    ui.button(key="research-refresh-1").click().run()
    ui.button(key="report-create-1").click().run()
    assert not ui.exception
    assert calls == [("research-1", "证据研究报告")]
    assert any("时间缺失" in item.value for item in ui.warning)
    ui.selectbox(key="report-format-1").set_value("docx").run()
    ui.button(key="report-export-1").click().run()
    assert not ui.exception
    assert previews == [("research-1", "report-1", "docx")]
    assert ui.session_state.messages[-1]["pending_action"]["action_id"] == "approve-1"
    ui.run()
    assert len(calls) == 1


def test_history_restores_session_and_evidence(ui: AppTest) -> None:
    first = ui.session_state.session_id
    ui.chat_input[0].set_value("历史问题").run()
    ui.button(key="chat-new").click().run()
    assert ui.session_state.session_id != first
    assert ui.session_state.messages == []
    assert any(item.value == "历史问题" for item in ui.text)
    ui.button(key=f"history-{first}").click().run()
    assert not ui.exception
    assert ui.session_state.session_id == first
    assert ui.session_state.latest_evidence == [LOCAL, WEB]
    assert ui.session_state.messages[0]["content"] == "历史问题"


def test_knowledge_selection_groups_history(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [{"knowledge_base_id": "kb-a", "name": "学生库"}])
    first = ui.session_state.session_id
    ui.chat_input[0].set_value("全局会话").run()
    ui.button(key="chat-load-knowledge").click().run()
    next(item for item in ui.selectbox if item.label == "知识库").set_value("kb-a").run()
    ui.button(key="chat-new").click().run()
    assert not ui.exception
    assert ui.session_state.knowledge_base_id == "kb-a"
    assert ui.session_state.knowledge_base_name == "学生库"
    assert not any(item.key == f"history-{first}" for item in ui.button)
    assert any("可能参考其他知识库" in item.value for item in ui.info)


def test_creation_failure_clears_previous_evidence(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    ui.chat_input[0].set_value("上一次问题").run()
    def fail(*args: object) -> dict:
        raise RuntimeError("创建失败")
    monkeypatch.setattr(api_client, "create_research_task", fail)
    research(ui)
    assert ui.session_state.latest_evidence == []
    assert any("创建失败" in item.value for item in ui.error)
    ui.toggle(key="chat-show-sources").set_value(True).run()
    ui.button(key="answer-sources-1").click().run()
    assert ui.session_state.latest_evidence == [LOCAL, WEB]


def test_normal_chat_complex_response_offers_research_refresh(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "chat", lambda *args: {"answer": "已创建研究任务", "task_id": "research-1", "status": "research_task_created"})
    monkeypatch.setattr(api_client, "get_task", lambda _: {"task": {"task_id": "research-1", "task_type": "research"}})
    ui.chat_input[0].set_value("复杂查询").run()
    assert not ui.exception
    assert ui.button(key="research-refresh-1")


def test_trace_and_task_detail_failures_preserve_created_task(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "chat", lambda *args: {"answer": "已创建研究任务", "task_id": "research-1", "status": "research_task_created"})
    def fail(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("暂不可用")
    monkeypatch.setattr(api_client, "get_task", fail)
    monkeypatch.setattr(api_client, "get_traces", fail)
    ui.chat_input[0].set_value("复杂查询").run()
    assert not ui.exception
    assert ui.session_state.messages[-1]["content"] == "已创建研究任务"
    assert ui.button(key="research-refresh-1")
    assert not any("Trace 暂不可用" in item.value for item in ui.warning)
    ui.toggle(key="chat-show-details").set_value(True).run()
    assert any("Trace 暂不可用" in item.value for item in ui.warning)


def test_report_failure_can_be_retried(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {**TASK, "status": "COMPLETED", "result": {"answer": "研究结果"}})
    def fail(*args: object) -> dict:
        raise RuntimeError("报告暂不可用")
    monkeypatch.setattr(api_client, "create_report", fail)
    research(ui, "报告生成")
    ui.button(key="research-refresh-1").click().run()
    ui.button(key="report-create-1").click().run()
    assert not ui.exception
    assert any("报告暂不可用" in item.value for item in ui.warning)
    assert not ui.button(key="report-create-1").disabled


def test_markdown_locator_and_source_labels_preserve_input() -> None:
    item = {"source_type": "LOCAL", "file_name": "a.md", "metadata": {"heading_path": ["项目", "安装"], "line_number": 28, "line_end": 35}}
    assert "Heading：项目 > 安装" in evidence_location(item)
    assert "Line：28-35" in evidence_location(item)
    assert source_labels([item]) == ["[1] 本地文件 · a.md"]
    assert item["metadata"]["heading_path"] == ["项目", "安装"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp/x", "https://user:password@example.com", "https://["])
def test_unsafe_evidence_urls_not_clickable(url: str) -> None:
    assert web_url({"url": url}) is None


def test_research_and_report_http_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    def request(method: str, path: str, **kwargs: Any) -> dict:
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"report_id": "report"}, **TASK}
    monkeypatch.setattr(api_client, "request", request)
    api_client.create_research_task("session", "query")
    api_client.get_research_task("task")
    api_client.create_report("task", "title")
    api_client.preview_report("task", "report", "md")
    assert calls == [("POST", "/api/research-task", {"json": {"session_id": "session", "query": "query"}}),
                     ("GET", "/api/research-task/task", {}),
                     ("POST", "/api/research-task/task/reports", {"json": {"title": "title"}}),
                     ("POST", "/api/research-task/task/reports/report/preview", {"json": {"format": "md"}})]
