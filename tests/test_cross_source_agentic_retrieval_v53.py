"""V5.3 bounded Cross-Source Agentic Retrieval tests."""

from typing import Any

import pytest

from backend.evidence import UNIFIED_EVIDENCE_FACTORY
from backend.services import cross_source_retrieval as cross_source
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE


def _local_evidence(evidence_id: str = "local-1", content: str = "本地政策依据"):
    return UNIFIED_EVIDENCE_FACTORY.local(
        evidence_id=evidence_id,
        source_type="unstructured",
        file_id="a" * 32,
        file_name="policy.pdf",
        page_no=1,
        chunk_id=f"chunk-{evidence_id}",
        text_excerpt=content,
    ).model_dump(mode="json", exclude_none=True)


def _web_evidence(evidence_id: str = "web-1", content: str = "网页政策依据"):
    return {
        "evidence_id": evidence_id,
        "evidence_version": "4.0",
        "source_type": "WEB",
        "content": content,
        "authority": "unknown",
        "freshness": "retrieval_time_only",
        "metadata": {},
        "url": f"https://example.com/{evidence_id}",
        "domain": "example.com",
        "retrieved_at": "2026-09-01T00:00:00+00:00",
        "trust_level": "untrusted_web_data",
        "instruction_authority": "none",
        "approval_authority": "none",
        "can_trigger_tool": False,
        "can_change_tool_risk": False,
        "can_approve": False,
    }


def _url_evidence(url: str):
    item = _web_evidence("url-1", "指定网页正文")
    item.update({"source_type": "URL", "url": url})
    return item


def _result(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "status": "found" if evidence else "not_found",
            "unified_evidence": evidence,
        },
        "unified_evidence": evidence,
        "error_code": None,
        "message": "完成",
    }


def test_local_evidence_sufficient_does_not_call_web() -> None:
    calls: list[str] = []

    def local(scope, query, top_k=5):
        calls.append("LOCAL")
        return _result([_local_evidence()])

    def forbidden_web(**kwargs):
        raise AssertionError("本地 Evidence 已充分时不得调用 Web")

    result = cross_source.run_cross_source_retrieval(
        "结合材料和最新公开信息进行分析",
        local_retriever=local,
        web_retriever=forbidden_web,
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "SUFFICIENT"
    assert result["data"]["stop_reason"] == "SUFFICIENT"
    assert calls == ["LOCAL"]


def test_local_insufficient_uses_web_with_bounded_rewrite() -> None:
    calls: list[tuple[str, str]] = []

    def local(scope, query, top_k=5):
        calls.append(("LOCAL", query))
        return _result([])

    def web(*, query=None, url=None, top_k=5):
        calls.append(("WEB", query))
        return _result([_web_evidence()])

    result = cross_source.run_cross_source_retrieval(
        "结合材料和最新公开信息进行分析",
        local_retriever=local,
        web_retriever=web,
    )

    assert result["data"]["status"] == "SUFFICIENT"
    assert [source for source, _ in calls] == ["LOCAL", "WEB"]
    assert calls[0][1] != calls[1][1]
    assert {item["source_type"] for item in result["data"]["unified_evidence"]} == {
        "WEB"
    }


def test_web_strategy_calls_web_directly_without_local() -> None:
    calls: list[dict[str, Any]] = []

    def forbidden_local(scope, query, top_k=5):
        raise AssertionError("WEB 策略不得调用本地检索")

    def web(**kwargs):
        calls.append(kwargs)
        return _result([_web_evidence()])

    result = DOCUMENT_AGENT_CORE.run_cross_source_retrieval(
        "查询2026年最新政策",
        local_retriever=forbidden_local,
        web_retriever=web,
    )

    assert result["data"]["source_plan"]["source_strategy"] == "WEB"
    assert result["data"]["attempts"][0]["tool_name"] == "retrieve_web"
    assert calls[0]["url"] is None


def test_direct_url_strategy_only_fetches_explicit_url() -> None:
    url = "https://example.com/policy"
    calls: list[dict[str, Any]] = []

    def forbidden_local(scope, query, top_k=5):
        raise AssertionError("DIRECT_URL 不得调用本地检索")

    def web(**kwargs):
        calls.append(kwargs)
        return _result([_url_evidence(kwargs["url"])])

    result = cross_source.run_cross_source_retrieval(
        f"请读取 {url}",
        local_retriever=forbidden_local,
        web_retriever=web,
    )

    assert result["data"]["status"] == "SUFFICIENT"
    assert calls == [{"query": None, "url": url, "top_k": 5}]
    assert result["data"]["unified_evidence"][0]["source_type"] == "URL"


def test_partial_local_evidence_continues_and_merges_web_evidence() -> None:
    need = {
        "need": "结合材料和最新公开信息核验政策",
        "missing": [],
        "source_needed": "BOTH",
        "required_fields": [],
        "required_terms": [],
        "minimum_evidence": 2,
    }
    result = cross_source.run_cross_source_retrieval(
        need["need"],
        information_need=need,
        local_retriever=lambda scope, query, top_k=5: _result([
            _local_evidence()
        ]),
        web_retriever=lambda **kwargs: _result([_web_evidence()]),
    )

    assert result["data"]["status"] == "SUFFICIENT"
    assert result["data"]["tool_calls"] == 2
    assert [item["source_type"] for item in result["data"]["unified_evidence"]] == [
        "LOCAL", "WEB"
    ]


def test_tool_call_budget_stops_before_web_supplement() -> None:
    web_calls = 0

    def web(**kwargs):
        nonlocal web_calls
        web_calls += 1
        return _result([])

    result = cross_source.run_cross_source_retrieval(
        "结合材料和最新公开信息进行分析",
        max_tool_calls=1,
        local_retriever=lambda scope, query, top_k=5: _result([]),
        web_retriever=web,
    )

    assert result["data"]["status"] == "INSUFFICIENT"
    assert result["data"]["stop_reason"] == "BUDGET_EXHAUSTED"
    assert result["data"]["tool_calls"] == 1
    assert web_calls == 0


def test_repeated_query_is_stopped_by_loop_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def web(**kwargs):
        nonlocal calls
        calls += 1
        return _result([])

    monkeypatch.setattr(
        cross_source,
        "rewrite_query",
        lambda query, *, round_no, missing=None: "same query",
    )
    result = cross_source.run_cross_source_retrieval(
        "查询最新政策",
        max_rounds=5,
        web_retriever=web,
    )

    assert result["data"]["stop_reason"] == "LOOP_PREVENTED"
    assert result["data"]["tool_calls"] == 1
    assert calls == 1


def test_timeout_stops_before_next_tool_call() -> None:
    ticks = iter([0.0, 0.01, 0.06, 0.07, 0.08])
    result = cross_source.run_cross_source_retrieval(
        "查询最新政策",
        timeout_seconds=0.05,
        web_retriever=lambda **kwargs: _result([]),
        clock=lambda: next(ticks),
    )

    assert result["data"]["stop_reason"] == "TIMEOUT"
    assert result["data"]["tool_calls"] == 1
