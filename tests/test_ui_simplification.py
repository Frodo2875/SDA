"""Sources are opt-in presentation; answers and source data remain intact."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend import api_client


def test_chat_hides_sources_until_requested_and_can_hide_them_again(monkeypatch) -> None:
    evidence = {"evidence_id": "source-1", "source_type": "LOCAL", "file_name": "来源文件.md", "content": "原始资料"}
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "chat", lambda *args: {"answer": "这是回答。", "unified_evidence": [evidence]})
    ui = AppTest.from_file(Path(__file__).resolve().parents[1] / "frontend" / "app.py").run()
    assert not ui.session_state.show_insight_panel
    ui.button(key="nav-chat").click().run()
    assert not ui.toggle(key="chat-show-sources").value
    assert not ui.toggle(key="chat-show-details").value
    ui.chat_input[0].set_value("请分析材料").run()
    assert not ui.exception
    assert any(item.value == "这是回答。" for item in ui.markdown)
    assert not any("来源文件.md" in item.value for elements in [ui.text, ui.caption, ui.markdown] for item in elements)
    assert not any(item.value in {"参考资料", "Evidence Preview", "Agent Chat"} for item in ui.subheader)
    assert not any(button.key == "answer-sources-1" for button in ui.button)
    assert ui.session_state.latest_evidence == [evidence]
    ui.toggle(key="chat-show-sources").set_value(True).run()
    assert any(item.value == "参考资料" for item in ui.subheader)
    assert ui.button(key="answer-sources-1")
    ui.toggle(key="chat-show-sources").set_value(False).run()
    assert not ui.exception
    assert not any(item.value == "参考资料" for item in ui.subheader)
    assert ui.session_state.messages[-1]["evidence"] == [evidence]


def test_chat_hides_quality_warning_and_trace_until_details_are_requested(monkeypatch) -> None:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [
        {"event_type": "tool_execution", "result_status": "success"}
    ])
    monkeypatch.setattr(api_client, "chat", lambda *args: {
        "answer": "你好。",
        "evidence_quality": {"warnings": [{"message": "部分结论没有关联到可用 Evidence，请核对引用。"}]},
    })
    ui = AppTest.from_file(Path(__file__).resolve().parents[1] / "frontend" / "app.py").run()
    ui.button(key="nav-chat").click().run()
    ui.chat_input[0].set_value("你好").run()
    assert not ui.warning
    assert not any(item.label == "详细处理记录" for item in ui.expander)
    ui.toggle(key="chat-show-details").set_value(True).run()
    assert any("部分结论" in item.value for item in ui.warning)
    assert any(item.label == "详细处理记录" for item in ui.expander)


def test_product_logo_is_loaded_in_shell(monkeypatch) -> None:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    ui = AppTest.from_file(Path(__file__).resolve().parents[1] / "frontend" / "app.py").run()
    assert not ui.exception
    assert any("学生材料助手" in item.value for item in ui.sidebar.markdown)
