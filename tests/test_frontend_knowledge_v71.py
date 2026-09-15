"""Streamlit interactions and HTTP contracts for the knowledge management page."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.pages.knowledge import file_status

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
RECORD = {"knowledge_base_id": "a" * 32, "name": "学生资料库", "description": "分析专业",
          "file_count": 1, "created_at": "2026-09-15", "updated_at": "2026-09-15", "status": "Ready"}
FILE = {"file_id": "b" * 32, "file_name": "学生.txt", "file_type": "txt", "source_type": "upload",
        "size": 10, "queryable": True, "index_status": "indexed", "lifecycle_status": "ready", "deletable": True}


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [FILE])
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [RECORD])
    monkeypatch.setattr(api_client, "get_knowledge_base", lambda _: RECORD)
    monkeypatch.setattr(api_client, "list_knowledge_files", lambda _: [FILE])
    return AppTest.from_file(APP).run(timeout=10)


def enter(ui: AppTest) -> AppTest:
    ui.button(key="nav-knowledge").click().run()
    ui.button(key=f"knowledge-enter-{RECORD['knowledge_base_id']}").click().run()
    assert not ui.exception
    return ui


def test_list_detail_files_upload_and_back(ui: AppTest) -> None:
    enter(ui)
    assert ui.session_state.selected_knowledge_base_id == RECORD["knowledge_base_id"]
    assert ui.dataframe[0].value.iloc[0]["状态"] == "Indexed"
    assert ui.dataframe[0].value.iloc[0]["文件名"] == "学生.txt"
    assert len(ui.get("file_uploader")) == 1
    assert any(button.label == "查看详情" for button in ui.button)
    assert any(button.label == "删除文件" for button in ui.button)
    ui.button(key="knowledge-back").click().run()
    assert not ui.exception and ui.session_state.selected_knowledge_base_id is None
    assert any(item.value == "学生资料库" for item in ui.subheader)


def test_create_form_submits_management_api(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "create_knowledge_base", lambda *args: calls.append(args) or RECORD)
    ui.button(key="nav-knowledge").click().run()
    ui.text_input(key="knowledge-name").set_value(" 新资料库 ")
    ui.text_area(key="knowledge-description").set_value(" 描述 ")
    next(button for button in ui.button if button.label == "创建").click().run()
    assert not ui.exception and calls == [("新资料库", "描述")]
    assert any("创建成功" in item.value for item in ui.success)


def test_empty_create_does_not_call_api(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object) -> None:
        pytest.fail("empty name must not submit")
    monkeypatch.setattr(api_client, "create_knowledge_base", forbidden)
    ui.button(key="nav-knowledge").click().run()
    next(button for button in ui.button if button.label == "创建").click().run()
    assert not ui.exception
    assert any("请输入" in item.value for item in ui.error)


def test_chat_carries_context_and_preserves_existing_chat_contract(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    previous_session = ui.session_state.session_id
    calls = []
    monkeypatch.setattr(api_client, "chat", lambda *args: calls.append(args) or {"answer": "回答"})
    monkeypatch.setattr(api_client, "get_traces", lambda **kwargs: [])
    enter(ui).button(key="knowledge-chat").click().run()
    assert ui.session_state.active_page == "chat"
    assert ui.session_state.session_id != previous_session
    assert ui.session_state.knowledge_base_id == RECORD["knowledge_base_id"]
    assert any("尚未限定" in item.value for item in ui.info)
    ui.chat_input[0].set_value("分析专业").run()
    assert not ui.exception
    assert calls == [(ui.session_state.session_id, "分析专业")]
    assert ui.session_state.messages[-1]["content"] == "回答"


def test_detail_failure_does_not_show_global_documents(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    enter(ui)
    def fail(_: str) -> list[dict]:
        raise RuntimeError("文件列表暂不可用")
    monkeypatch.setattr(api_client, "list_knowledge_files", fail)
    ui.run()
    assert not ui.exception
    assert not ui.dataframe and not ui.get("file_uploader")
    assert any("暂不可用" in item.value for item in ui.error)


def test_attach_existing_file(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(api_client, "list_knowledge_files", lambda _: [])
    monkeypatch.setattr(api_client, "attach_knowledge_file", lambda *args: calls.append(args) or FILE)
    enter(ui).button(key="knowledge-attach").click().run()
    assert not ui.exception
    assert calls == [(RECORD["knowledge_base_id"], FILE["file_id"])]


@pytest.mark.parametrize("item,expected", [
    ({"lifecycle_status": "UPLOADED"}, "Uploaded"),
    ({"lifecycle_status": "PARSING"}, "Processing"),
    ({"lifecycle_status": "REINDEXING", "queryable": True}, "Processing"),
    ({"lifecycle_status": "QUERYABLE", "queryable": True}, "Indexed"),
    ({"lifecycle_status": "FAILED"}, "Failed"),
])
def test_status_mapping(item: dict, expected: str) -> None:
    assert file_status(item) == expected


def test_management_client_http_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    def request(method: str, path: str, **kwargs: Any) -> dict:
        calls.append((method, path, kwargs))
        return {"ok": True, "data": RECORD}
    monkeypatch.setattr(api_client, "request", request)
    api_client.create_knowledge_base("name", "description")
    api_client.attach_knowledge_file("kb", "file")
    assert calls == [("POST", "/api/knowledge-bases", {"json": {"name": "name", "description": "description"}}),
                     ("POST", "/api/knowledge-bases/kb/files", {"json": {"file_id": "file"}})]
    monkeypatch.setattr(api_client, "request", lambda *args, **kwargs: {"ok": False, "message": "失败"})
    with pytest.raises(RuntimeError, match="失败"):
        api_client.list_knowledge_bases()
