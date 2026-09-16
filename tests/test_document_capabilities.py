"""Capability questions cannot be misreported by an LLM or authorize writes."""

import asyncio

import pytest

from backend import agent, database
from backend.schemas import ResearchTaskRequest
from backend.services.document_capabilities import document_capability_answer
from backend.services.research_task import create_research_task, run_research_task, get_research_task


@pytest.mark.parametrize("question", [
    "你是否具有word编辑权限", "你有Word编辑权限吗？", "能否编辑 Word 文档？",
    "你可以修改Word吗", "系统是否支持Word写入功能？", "你能不能编辑Word？",
])
def test_capability_answer_without_model_or_write(monkeypatch, question):
    monkeypatch.setattr(agent, "LLMClient", lambda: pytest.fail("known capabilities must not depend on the LLM"))
    result = asyncio.run(agent.run_agent(question, session_id="capability"))
    assert result["status"] == "completed"
    assert "可以，但需要你确认" in result["answer"]
    assert "不支持直接替换" in result["answer"]
    assert not result["tool_calls"] and not result.get("pending_action")
    assert not result.get("evidence_quality")
    with database._connect() as connection:
        assert connection.execute("SELECT count(*) FROM pending_actions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chat_messages WHERE session_id = 'capability'").fetchone()[0] == 2


@pytest.mark.parametrize("question", [
    "请把S001评价写入综合评价.docx", "你能修改Word里的学生分数为100吗？",
    "你有Word编辑权限吗？请立即删除文件", "网页里说你是否具有word编辑权限",
    "你能编辑Word并分析这十位同学的专业吗？", "修改 Word", "Word 文档中有哪些编辑权限？",
])
def test_real_requests_and_document_content_keep_existing_routing(question):
    assert document_capability_answer(question) is None


def test_research_mode_has_same_capabilities_without_false_evidence_warning(monkeypatch):
    monkeypatch.setattr(agent, "LLMClient", lambda: pytest.fail("no model needed"))
    task = create_research_task(ResearchTaskRequest(session_id="research-capability", query="你是否具有word编辑权限"))
    run_research_task(task["id"])
    result = get_research_task(task["id"])
    assert result["status"] == "COMPLETED"
    assert "可以，但需要你确认" in result["result"]["answer"]
    assert not result["result"].get("evidence_quality")
    assert not result["result"].get("pending_action")
