"""Real UI/API/database demo; only external LLM and search responses are fixed."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from docx import Document
import httpx
from streamlit.testing.v1 import AppTest

from backend import agent, database
from backend.main import app
from backend.services.web_retrieval import configure_search_provider
from backend.tools import excel_utils
from frontend import api_client


def test_knowledge_to_research_to_approved_report(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    file_id = ""

    class FixedSearch:
        name = "evaluation-web"

        def search(self, query: str, scope: Any) -> list[dict[str, Any]]:
            return [{"title": "就业方向", "url": "https://example.com/careers",
                     "snippet": "计算机专业就业方向包括软件工程。", "source": self.name}]

    class FixedClient:
        async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            if messages[-1]["role"] == "user":
                return {"tool_calls": [
                    {"id": "local", "type": "function", "function": {"name": "retrieve_document",
                     "arguments": json.dumps({"scope": {"file_id": file_id}, "query": "计算机专业 学生"})}},
                    {"id": "web", "type": "function", "function": {"name": "retrieve_web",
                     "arguments": json.dumps({"query": "计算机专业就业方向"})}},
                ]}
            return {"content": "计算机专业有2名学生。计算机专业就业方向包括软件工程。"}

    monkeypatch.setattr(agent, "LLMClient", FixedClient)
    configure_search_provider(FixedSearch())
    calls: list[tuple[str, str]] = []

    def call(method: str, path: str, **kwargs: Any) -> httpx.Response:
        kwargs.pop("timeout", None)
        calls.append((method, path))
        async def execute() -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evaluation") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(execute())

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = call(method, path, **kwargs)
        assert not response.is_error, response.text
        return response.json()

    downloads: dict[str, bytes] = {}
    def get(url: str, **kwargs: Any) -> httpx.Response:
        response = call("GET", urlsplit(url).path, **kwargs)
        if not response.is_error:
            downloads[kwargs["params"]["format"]] = response.content
        return response

    monkeypatch.setattr(api_client, "request", request)
    monkeypatch.setattr(httpx, "get", get)
    ui = AppTest.from_file(Path(__file__).resolve().parents[2] / "frontend" / "app.py").run(timeout=20)
    ui.button(key="nav-knowledge").click().run(timeout=20)
    ui.text_input(key="knowledge-name").set_value("V7验收学生库")
    next(button for button in ui.button if button.label == "创建").click().run(timeout=20)
    assert not ui.exception
    knowledge = request("GET", "/api/knowledge-bases")["data"][0]
    kb_id = knowledge["knowledge_base_id"]
    # AppTest does not automate the file chooser: send the actual multipart API.
    uploaded = request("POST", f"/api/knowledge-bases/{kb_id}/upload",
                       files={"file": ("student_info.md", "# 学生资料\n\n## 专业分布\n计算机专业有2名学生。\n".encode(), "text/markdown")})
    file_id = uploaded["data"]["file_id"]
    assert database.get_file_record_by_id(file_id)["queryable"]
    assert database.get_document_chunks(file_id)
    ui.button(key=f"knowledge-enter-{kb_id}").click().run(timeout=20)
    assert ui.dataframe[0].value.iloc[0]["状态"] == "可查询"
    ui.button(key="knowledge-chat").click().run(timeout=20)
    assert ui.session_state.knowledge_base_id == kb_id
    ui.radio(key="chat_mode").set_value("报告生成").run(timeout=20)
    ui.chat_input[0].set_value("根据本地学生材料统计专业分布，并结合网页分析就业方向生成报告").run(timeout=30)
    ui.button(key="research-refresh-1").click().run(timeout=20)
    assert not ui.exception
    task_id = ui.session_state.messages[-1]["research_task_id"]
    task = request("GET", f"/api/research-task/{task_id}")
    assert task["status"] == "COMPLETED"
    assert {item["source_type"] for item in task["result"]["unified_evidence"]} == {"LOCAL", "WEB"}
    assert task["result"]["evidence_quality"]["status"] == "PASS"
    assert len(ui.session_state.latest_evidence) >= 2
    ui.button(key="report-create-1").click().run(timeout=20)
    report_id = ui.session_state.messages[-1]["report"]["report_id"]
    ui.button(key="nav-reports").click().run(timeout=20)
    ui.button(key=f"report-open-{task_id}-{report_id}").click().run(timeout=20)
    for format in ["md", "docx"]:
        path = f"/api/research-task/{task_id}/reports/{report_id}"
        assert call("GET", f"{path}/download", params={"format": format}).status_code == 409
        ui.button(key=f"report-preview-{format}").click().run(timeout=20)
        assert not ui.exception
        ui.button(key=f"report-action-{task_id}-{report_id}-confirm").click().run(timeout=20)
        assert not ui.exception
        ui.button(key=f"report-load-{format}").click().run(timeout=20)
        assert not ui.exception and ui.get("download_button")
        report = request("GET", path)["data"]
        export = report["exports"][format]
        assert export["status"] == "executed"
        assert len(request("GET", f"/api/files/{export['file_id']}/versions")["data"]) >= 2
    assert "计算机" in downloads["md"].decode()
    assert "计算机" in "\n".join(paragraph.text for paragraph in Document(BytesIO(downloads["docx"])).paragraphs)
    assert any(path.endswith("/api/research-task") and method == "POST" for method, path in calls)
