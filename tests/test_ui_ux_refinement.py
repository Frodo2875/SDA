"""Behavior regressions for collapsible controls and presentation-only changes."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [])
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [])
    return AppTest.from_file(APP).run(timeout=15)


@pytest.mark.parametrize("widget,preference", [
    ("chat-show-sources", "chat_sources_visible"),
    ("chat-show-details", "chat_details_visible"),
])
def test_display_preference_survives_hidden_widget_and_navigation(ui: AppTest, widget: str, preference: str) -> None:
    ui.button(key="nav-chat").click().run()
    ui.button(key="chat-more").click().run()
    ui.toggle(key=widget).set_value(True).run()
    ui.button(key="chat-more").click().run()
    ui.run()  # Complete widget cleanup while the controls are absent.
    ui.button(key="nav-documents").click().run()
    ui.button(key="nav-chat").click().run()
    assert not ui.exception
    assert ui.session_state[preference] is True
    ui.button(key="chat-more").click().run()
    assert ui.toggle(key=widget).value is True
    ui.toggle(key=widget).set_value(False).run()
    assert ui.session_state[preference] is False


@pytest.mark.parametrize("mode", ["深度研究", "报告生成"])
def test_mode_survives_navigation(ui: AppTest, mode: str) -> None:
    ui.button(key="nav-chat").click().run()
    ui.selectbox(key="chat_mode").set_value(mode).run()
    session = ui.session_state.session_id
    ui.button(key="nav-settings").click().run()
    ui.button(key="nav-chat").click().run()
    assert not ui.exception
    assert ui.selectbox(key="chat_mode").value == mode
    assert ui.session_state.session_id == session


def test_filtered_empty_list_is_distinct_from_no_files(ui: AppTest) -> None:
    ui.button(key="nav-documents").click().run()
    assert any("当前没有可用材料" in item.value for item in ui.info)
    ui.text_input(key="workspace_search").set_value("不存在的文件").run()
    assert any("没有符合筛选条件" in item.value for item in ui.info)


def test_backend_failure_is_not_reported_as_empty_success(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(**kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("无法连接后端服务")
    monkeypatch.setattr(api_client, "list_files", unavailable)
    ui.button(key="nav-documents").click().run()
    next(button for button in ui.button if button.label == "刷新文件列表").click().run()
    assert not ui.exception
    assert any("无法连接后端" in item.value for item in ui.warning)
    assert not any("当前没有可用材料" in item.value for item in ui.info)


def test_approval_remains_visible_with_details_hidden(ui: AppTest) -> None:
    ui.session_state.messages = [{"role": "assistant", "content": "待确认的文档操作", "pending_action": {
        "action_id": "synthetic-action", "status": "pending", "target_file": "synthetic.docx",
        "diff_preview": {"before": "旧内容", "after": "新内容", "operation_type": "append"},
    }}]
    ui.button(key="nav-chat").click().run()
    assert not ui.exception
    assert not ui.session_state.show_chat_tools
    assert ui.button(key="message-0-confirm").label == "确认"
    assert ui.button(key="message-0-cancel").label == "取消"
    assert any("synthetic.docx" in item.value for item in ui.markdown)


def test_collapsed_create_form_still_calls_existing_api(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(api_client, "create_knowledge_base", lambda *args: calls.append(args) or {})
    ui.button(key="nav-knowledge").click().run()
    assert next(item for item in ui.expander if item.label == "新建知识库").proto.expanded is False
    ui.text_input(key="knowledge-name").set_value("合成资料库")
    next(item for item in ui.button if item.label == "创建").click().run()
    assert not ui.exception
    assert calls == [("合成资料库", "")]
