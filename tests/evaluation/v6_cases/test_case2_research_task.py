"""Case 2: one fixed Research task crosses local and Web retrieval into a report."""

import json
from pathlib import Path
from typing import Any

from backend import agent
from backend.schemas import ResearchTaskRequest
from backend.services.evidence_report import generate_report
from backend.services.file_upload import save_uploaded_file
from backend.services.research_task import (
    create_research_task,
    get_research_task,
    run_research_task,
)
from backend.services.web_retrieval import configure_search_provider
from backend.tools import excel_utils


class FixedWebProvider:
    name = "evaluation-web"

    def search(self, query: str, scope: Any) -> list[dict[str, Any]]:
        return [{
            "title": "就业方向",
            "url": "https://example.com/careers",
            "snippet": "计算机专业就业方向包括软件工程。",
            "source": self.name,
        }]


def test_research_local_web_quality_and_report(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    uploaded = save_uploaded_file(
        "student_info.md",
        "# 学生资料\n\n## 专业分布\n计算机专业有2名学生。\n".encode(),
    )
    configure_search_provider(FixedWebProvider())

    class FixedClient:
        async def create_chat_completion(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        ) -> dict[str, Any]:
            if messages[-1]["role"] == "user":
                return {"tool_calls": [
                    {"id": "local", "type": "function", "function": {
                        "name": "retrieve_document",
                        "arguments": json.dumps({
                            "scope": {"file_id": uploaded["data"]["file_id"]},
                            "query": "计算机专业 学生",
                        }),
                    }},
                    {"id": "web", "type": "function", "function": {
                        "name": "retrieve_web",
                        "arguments": json.dumps({"query": "计算机专业就业方向"}),
                    }},
                ]}
            return {"content": (
                "计算机专业有2名学生。"
                "计算机专业就业方向包括软件工程。"
            )}

    monkeypatch.setattr(agent, "LLMClient", FixedClient)
    task = create_research_task(ResearchTaskRequest(
        session_id="v6-final-research",
        query="根据本地学生材料统计专业分布，并结合网页资料分析就业方向和生成报告",
    ))
    assert task["status"] == "CREATED"
    run_research_task(task["id"])

    completed = get_research_task(task["id"])
    assert completed["status"] == "COMPLETED"
    types = {item["source_type"] for item in completed["result"]["unified_evidence"]}
    assert {"LOCAL", "WEB"} <= types
    assert completed["result"]["evidence_quality"]["status"] == "PASS"

    report = generate_report(task["id"])
    assert report["ok"] is True
    assert report["data"]["quality"]["status"] == "PASS"
    assert {ref["source_type"] for ref in report["data"]["evidence_refs"]} >= {"LOCAL", "WEB"}
