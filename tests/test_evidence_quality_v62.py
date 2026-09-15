"""Deterministic quality warnings never rewrite answers or judge source truth."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend import agent, database, main
from backend.evidence import UNIFIED_EVIDENCE_FACTORY, UnifiedEvidence
from backend.schemas import ResearchTaskRequest
from backend.services import evidence_quality
from backend.services.evidence_quality import attach_evidence_quality, evaluate_evidence_quality
from backend.services.research_task import create_research_task, get_research_task, run_research_task
from backend.services.file_upload import save_uploaded_file
from backend.tools import excel_utils


def local(content: str = "安装程序需要 Python 3.10", **values: Any) -> dict[str, Any]:
    item = UNIFIED_EVIDENCE_FACTORY.local(
        evidence_id="local-1", source_type="unstructured", file_id="a" * 32,
        file_name="guide.md", chunk_id="chunk-1", value_summary=content,
    ).model_dump()
    return {**item, **values}


def web(content: str = "安装程序需要 Python 3.10", **values: Any) -> dict[str, Any]:
    item = UNIFIED_EVIDENCE_FACTORY.web(
        {"url": "https://example.com/guide", "snippet": content},
        source_type="WEB", retrieved_at="2026-09-15T08:00:00+00:00",
    ).model_dump()
    return {**item, **values}


def codes(result: dict[str, Any]) -> set[str]:
    return {warning["code"] for warning in result["warnings"]}


def test_exact_excerpt_with_complete_evidence_passes() -> None:
    evidence = UnifiedEvidence.model_validate(local())
    result = evaluate_evidence_quality("安装程序需要 Python 3.10。", [evidence])
    assert result["status"] == "PASS" and result["passed"] is True
    assert result["warnings"] == []
    assert result["coverage"]["claim_evidence"][0]["evidence_ids"] == ["local-1"]


def test_missing_evidence_is_warning() -> None:
    result = evaluate_evidence_quality("结论需要材料支撑。", [])
    assert result["status"] == "WARNING" and result["passed"] is False
    assert codes(result) == {"EVIDENCE_MISSING"}


def test_every_conclusion_requires_an_association() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10。安装后一定提升成绩。", [local()])
    assert "EVIDENCE_MISSING" in codes(result)
    assert result["warnings"][0]["claim_ids"] == ["claim-2"]


def test_unrelated_evidence_does_not_cover_answer() -> None:
    result = evaluate_evidence_quality("项目成本是120元", [local("项目仅有需求说明")])
    assert "EVIDENCE_MISSING" in codes(result)


@pytest.mark.parametrize("citation", ["[local-1]", "[evidence:local-1]"])
def test_explicit_reference_is_checked_against_real_ids(citation: str) -> None:
    result = evaluate_evidence_quality(f"相关安装要求见资料 {citation}", [local()])
    assert result["passed"]
    assert result["coverage"]["claim_evidence"][0]["method"] == "inline_citation"


def test_unknown_citation_never_counts_as_evidence() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10 [invented-id]", [local()])
    assert "EVIDENCE_MISSING" in codes(result)


def test_lightweight_explicit_mapping_does_not_invent_ids() -> None:
    draft = "资料关联的安装条件"
    result = evaluate_evidence_quality(draft, [local()], claim_evidence_map={draft: ["local-1"]})
    assert result["passed"]
    result = evaluate_evidence_quality(draft, [local()], claim_evidence_map={draft: ["missing"]})
    assert "EVIDENCE_MISSING" in codes(result)


@pytest.mark.parametrize("field", ["url", "domain", "retrieved_at"])
def test_incomplete_web_provenance_warns(field: str) -> None:
    item = web()
    item.pop(field)
    result = evaluate_evidence_quality(item["content"], [item], query="最新安装要求")
    assert "SOURCE_INCOMPLETE" in codes(result)
    warning = next(item for item in result["warnings"] if item["code"] == "SOURCE_INCOMPLETE")
    assert field in warning["fields"]
    if field == "retrieved_at":
        assert "FRESHNESS_MISSING" in codes(result)


@pytest.mark.parametrize("values", [{"url": "not-a-url"}, {"domain": "wrong.example"}, {"url": "file:///etc/passwd"}])
def test_web_locator_format_is_checked_without_fetching(values: dict[str, Any]) -> None:
    result = evaluate_evidence_quality("资料 [evidence:web-1]", [web(evidence_id="web-1", **values)])
    assert "SOURCE_INCOMPLETE" in codes(result)


def test_local_without_locator_warns() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10", [local(chunk_id=None)])
    assert "LOCATION_MISSING" in codes(result)


@pytest.mark.parametrize("locator", [
    {"page_no": 2}, {"line_number": 28}, {"block_id": "block-1"},
    {"metadata": {"heading_path": ["项目说明", "安装"]}},
    {"metadata": {"line_number": 4, "line_end": 5}},
    {"table": "Sheet1", "cell": "B2"},
    {"region_id": "r1", "bbox": [0, 0, 20, 20]},
])
def test_existing_local_locator_forms_are_reused(locator: dict[str, Any]) -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10", [local(chunk_id=None, **locator)])
    assert result["passed"]


def test_local_filename_required() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10", [local(file_name="")])
    assert "SOURCE_INCOMPLETE" in codes(result)


def test_invalid_locator_values_do_not_pass() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10", [local(chunk_id=None, line_number=0, metadata={"heading_path": [""]})])
    assert "LOCATION_MISSING" in codes(result)


@pytest.mark.parametrize("query", ["当前政策", "最新消息", "current policy", "latest news"])
def test_time_sensitive_local_source_without_retrieved_time_warns(query: str) -> None:
    item = local()
    result = evaluate_evidence_quality(item["content"], [item], query=query)
    assert codes(result) == {"FRESHNESS_MISSING"}
    assert item["freshness"] == "not_applicable"


def test_retrieved_time_is_not_replaced_by_publication_time() -> None:
    result = evaluate_evidence_quality("安装程序需要 Python 3.10", [web(retrieved_at=None, published_at="2026-09-15")], query="当前要求")
    assert "FRESHNESS_MISSING" in codes(result)


def test_invalid_timestamp_warns_without_rejecting_answer() -> None:
    result = attach_evidence_quality({"answer": "原始答案", "status": "completed", "unified_evidence": [web(retrieved_at="not a date")]}, query="最新要求")
    assert "FRESHNESS_INVALID" in codes(result["evidence_quality"])
    assert result["answer"] == "原始答案" and result["status"] == "completed"


def test_local_web_explicit_field_conflict_has_no_winner() -> None:
    left = local("预算100元", field="预算", record_key="项目A", value_summary="100")
    right = web("预算120元", metadata={"field": "预算", "record_key": "项目A", "value": 120})
    result = evaluate_evidence_quality("预算100元", [left, right])
    assert "EVIDENCE_CONFLICT" in codes(result)
    warning = next(w for w in result["warnings"] if w["code"] == "EVIDENCE_CONFLICT")
    assert set(warning["evidence_ids"]) == {left["evidence_id"], right["evidence_id"]}
    assert "winner" not in result and "truth_score" not in result and "accuracy_score" not in result


@pytest.mark.parametrize("other", [{"record_key": "项目B"}, {"metadata": {"year": 2025}}, {"metadata": {"unit": "美元"}}])
def test_different_subject_or_context_not_compared(other: dict[str, Any]) -> None:
    left = local("预算100元", field="预算", record_key="项目A", value_summary="100")
    right = web("预算120元", field="预算", record_key="项目A", value_summary="120")
    right.update(other)
    result = evaluate_evidence_quality("预算100元", [left, right])
    assert "EVIDENCE_CONFLICT" not in codes(result)


def test_equal_numeric_values_not_conflicts() -> None:
    result = evaluate_evidence_quality("预算100元", [
        local("预算100元", field="预算", value_summary="100.0"),
        web("预算100元", metadata={"field": "预算", "value": 100}),
    ])
    assert "EVIDENCE_CONFLICT" not in codes(result)


def test_existing_conflict_markers_retained_as_warning() -> None:
    result = evaluate_evidence_quality("预算100元", [local("预算100元", evidence_status="conflict")])
    assert "EVIDENCE_CONFLICT" in codes(result)


def test_gate_is_read_only_and_deterministic() -> None:
    original = {"answer": "安装程序需要 Python 3.10。", "status": "completed", "unified_evidence": [local(), web()]}
    before = deepcopy(original)
    first = attach_evidence_quality(original, query="安装要求")
    second = attach_evidence_quality(original, query="安装要求")
    assert first == second
    assert original == before
    assert first["answer"] == original["answer"]
    assert first["unified_evidence"] == original["unified_evidence"]
    assert "evidence_quality" not in original
    assert first["unified_evidence"][0]["evidence_version"] == "4.0"


def test_pending_report_content_is_checked_instead_of_waiting_message() -> None:
    result = attach_evidence_quality({
        "answer": "等待确认", "status": "confirmation_required",
        "pending_action": {"content": "安装程序需要 Python 3.10"}, "unified_evidence": [local()],
    }, query="生成报告")
    assert result["evidence_quality"]["passed"]
    assert result["answer"] == "等待确认"


def test_invalid_partial_input_yields_warning_not_exception() -> None:
    result = evaluate_evidence_quality("结论", [{"source_type": []}, {}])
    assert {"EVIDENCE_INVALID", "SOURCE_INCOMPLETE", "EVIDENCE_MISSING"} <= codes(result)


def test_input_limits_warn_instead_of_silent_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence_quality, "MAX_CLAIMS", 1)
    monkeypatch.setattr(evidence_quality, "MAX_EVIDENCE", 1)
    result = evaluate_evidence_quality("安装程序需要 Python 3.10。其他结论", [local(), web()])
    assert "QUALITY_INPUT_LIMIT" in codes(result)


@pytest.mark.parametrize("has_evidence", [True, False])
def test_research_completes_with_quality_saved_before_terminal_status(monkeypatch: pytest.MonkeyPatch, has_evidence: bool) -> None:
    async def draft(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"answer": "安装程序需要 Python 3.10", "status": "completed", "unified_evidence": [local()] if has_evidence else []}
    monkeypatch.setattr(agent, "run_agent", draft)
    task = create_research_task(ResearchTaskRequest(session_id="gate", query="生成安装报告"))
    original = database.TASK_REPOSITORY.update_research
    saved: list[str] = []
    def update(task_id: str, **kwargs: Any) -> None:
        if kwargs.get("result"):
            assert database.get_task_record(task_id)["task_status"] == "running"
            assert kwargs["result"]["evidence_quality"]["passed"] is has_evidence
            saved.append(task_id)
        original(task_id, **kwargs)
    monkeypatch.setattr(database.TASK_REPOSITORY, "update_research", update)
    run_research_task(task["id"])
    result = get_research_task(task["id"])
    assert result["status"] == "COMPLETED" and result["error"] is None
    assert saved == [task["id"]]
    assert result["result"]["answer"] == "安装程序需要 Python 3.10"
    assert result["result"]["evidence_quality"]["passed"] is has_evidence


def test_real_markdown_agent_and_chat_api_deliver_quality(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    uploaded = save_uploaded_file("guide.md", "# 安装要求\n安装程序需要 Python 3.10".encode())
    assert uploaded["ok"]
    class Client:
        async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            if messages[-1]["role"] == "user":
                return {"tool_calls": [{"id": "md-query", "type": "function", "function": {
                    "name": "retrieve_document", "arguments": json.dumps({"scope": {"file_id": uploaded["data"]["file_id"]}, "query": "安装程序"}),
                }}]}
            return {"content": "安装程序需要 Python 3.10。"}
    monkeypatch.setattr(agent, "LLMClient", Client)
    async def request() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await client.post("/api/chat", json={"session_id": "markdown-gate", "message": "安装要求是什么"})
            assert response.status_code == 200
            result = response.json()
            assert result["answer"] == "安装程序需要 Python 3.10。"
            assert result["evidence_quality"]["status"] == "PASS"
            assert result["unified_evidence"][0]["evidence_version"] == "4.0"
    asyncio.run(request())
