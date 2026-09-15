"""Offline reliability contracts, including actual Research/Agent integration."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from typing import Any, Callable

import httpx
import pytest

from backend import agent, database
from backend.schemas import ResearchTaskRequest
from backend.services.research_task import create_research_task, get_research_task, run_research_task
from backend.services.search_provider import UnavailableSearchProvider
from backend.services.search_provider_factory import build_search_provider_from_env
from backend.services.search_reliability import research_search_scope
from backend.services.tavily_search_provider import TavilySearchProvider, retry_after_seconds
from backend.services.trace_service import record_trace
from backend.services.web_retrieval import WebRetrievalService, configure_search_provider


def body(**overrides: Any) -> dict[str, Any]:
    return {"results": [{"title": "政策", "url": "https://example.com/policy", "content": "政策说明", **overrides}]}


def service(handler: Callable[[httpx.Request], httpx.Response]) -> WebRetrievalService:
    return WebRetrievalService(search_provider=TavilySearchProvider(
        api_key="secret-provider-key", client=httpx.Client(transport=httpx.MockTransport(handler)),
    ))


def test_success_observations_and_evidence_contract() -> None:
    result = service(lambda request: httpx.Response(200, json=body())).retrieve(query="查询政策")
    assert result["ok"] and result["unified_evidence"][0]["evidence_version"] == "4.0"
    metrics = result["data"]["provider_execution"]
    assert metrics["provider"] == "tavily" and metrics["status"] == "success"
    assert metrics["attempt_count"] == 1 and metrics["error_code"] is None
    assert metrics["latency_ms"] >= 0


def test_empty_keeps_legacy_not_found_and_adds_classification() -> None:
    result = service(lambda request: httpx.Response(200, json={"results": []})).retrieve(query="政策")
    assert result["ok"] and result["data"]["status"] == "not_found"
    assert result["data"]["search_error_code"] == "SEARCH_EMPTY_RESULT"
    assert result["unified_evidence"] == []


@pytest.mark.parametrize("second", [200, 429, 500, 401])
def test_429_retries_once_then_stops(monkeypatch: pytest.MonkeyPatch, second: int) -> None:
    calls: list[httpx.Request] = []
    waits: list[float] = []
    monkeypatch.setattr("backend.services.tavily_search_provider.time.sleep", waits.append)
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else second, headers={"Retry-After": "0.5"}, json=body())
    result = service(handler).retrieve(query="政策")
    assert len(calls) == 2 and waits == [0.5]
    assert result["data"]["provider_execution"]["retry_count"] == 1
    assert result["ok"] is (second == 200)
    if second != 200:
        assert result["data"]["search_error_code"] == {
            429: "SEARCH_RATE_LIMIT", 500: "SEARCH_PROVIDER_UNAVAILABLE", 401: "SEARCH_AUTH_FAILED",
        }[second]


@pytest.mark.parametrize("header,expected", [(None, 1), ("garbage", 1), ("-4", 0), ("0", 0), ("1.5", 1.5), ("500", None), ("nan", 1), ("inf", 1)])
def test_retry_after_is_bounded(header: str | None, expected: float | None) -> None:
    assert retry_after_seconds(header) == expected


def test_retry_after_supports_http_date() -> None:
    assert retry_after_seconds(format_datetime(datetime.now(timezone.utc) - timedelta(seconds=5))) == 0
    assert retry_after_seconds(format_datetime(datetime.now(timezone.utc) + timedelta(hours=1))) is None


def test_excessive_retry_after_does_not_retry_early(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "3600"})
    def no_wait(delay: float) -> None:
        pytest.fail("excessive Retry-After must return immediately")
    monkeypatch.setattr("backend.services.tavily_search_provider.time.sleep", no_wait)
    result = service(handler).retrieve(query="政策")
    assert len(calls) == 1 and result["data"]["search_error_code"] == "SEARCH_RATE_LIMIT"


@pytest.mark.parametrize("status,code", [(401, "SEARCH_AUTH_FAILED"), (403, "SEARCH_AUTH_FAILED"), (503, "SEARCH_PROVIDER_UNAVAILABLE")])
def test_http_failures_do_not_retry_or_expose_provider_body(status: int, code: str) -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, text="secret-provider-key Authorization: private")
    result = service(handler).retrieve(query="政策")
    assert len(calls) == 1 and result["data"]["search_error_code"] == code
    assert "secret-provider-key" not in json.dumps(result)


@pytest.mark.parametrize("error_type,code", [(httpx.ReadTimeout, "SEARCH_TIMEOUT"), (httpx.ConnectError, "SEARCH_NETWORK_ERROR")])
def test_transport_errors_and_explicit_timeout(error_type: type[httpx.HTTPError], code: str) -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        assert request.extensions["timeout"]["read"] == 8.0
        raise error_type("secret-provider-key raw query", request=request)
    result = service(handler).retrieve(query="政策")
    assert len(calls) == 1 and result["data"]["search_error_code"] == code
    assert "secret-provider-key" not in json.dumps(result)


@pytest.mark.parametrize("payload", [
    None, {}, {"results": {}}, {"results": [None]},
    {"results": [{"title": "title"}]}, {"results": [{"url": "https://example.com"}]},
    body(title=3), body(url=[]), body(content={}), body(score={}),
    body(published_date=[]), body(favicon={}), body(url="file:///etc/passwd"),
    body(title=""), body(url="http://user:password@example.com"),
])
def test_invalid_response_rejected_before_normalization(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid Tavily fields reached normalize_search_results")
    monkeypatch.setattr("backend.services.search_reliability.normalize_search_results", forbidden)
    result = service(lambda request: httpx.Response(200, json=payload)).retrieve(query="政策")
    assert not result["ok"] and result["data"]["search_error_code"] == "SEARCH_INVALID_RESPONSE"


def test_invalid_json_is_classified() -> None:
    result = service(lambda request: httpx.Response(200, content=b"{invalid-json")).retrieve(query="政策")
    assert result["data"]["search_error_code"] == "SEARCH_INVALID_RESPONSE"


def test_task_cache_reuses_results_but_not_other_scopes() -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=body())
    search = service(handler)
    with research_search_scope("task-A", "session"):
        first = search.retrieve(query=" 政策 ")
        second = search.retrieve(query="政策")
        assert second["data"]["provider_execution"]["cache_hit"]
        assert first["unified_evidence"][0]["retrieved_at"] == second["unified_evidence"][0]["retrieved_at"]
        second["data"]["results"][0]["title"] = "mutated"
        assert search.retrieve(query="政策")["data"]["results"][0]["title"] == "政策"
        search.retrieve(query="政策", top_k=2)
        search.retrieve(query="政策", language="en")
    assert len(calls) == 3
    with research_search_scope("task-B", "session"):
        search.retrieve(query="政策")
    search.retrieve(query="政策")
    search.retrieve(query="政策")
    assert len(calls) == 6


def test_failed_search_is_not_repeated_within_execution() -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)
    search = service(handler)
    with research_search_scope("task", "session") as context:
        search.retrieve(query="政策")
        result = search.retrieve(query="政策")
        assert len(context.failures) == 1
    assert len(calls) == 1 and result["data"]["provider_execution"]["cache_hit"]


def test_concurrent_tasks_have_isolated_contexts() -> None:
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=body())
    search = service(handler)
    async def worker(task_id: str) -> None:
        with research_search_scope(task_id, "session"):
            search.retrieve(query="政策")
            await asyncio.sleep(0)
            assert search.retrieve(query="政策")["data"]["provider_execution"]["cache_hit"]
    async def run() -> None:
        await asyncio.gather(worker("one"), worker("two"))
    asyncio.run(run())
    assert len(calls) == 2


def test_scope_resets_on_exception() -> None:
    search = service(lambda request: httpx.Response(200, json=body()))
    with pytest.raises(RuntimeError):
        with research_search_scope("task", "session"):
            search.retrieve(query="政策")
            raise RuntimeError("cancelled")
    assert not search.retrieve(query="政策")["data"]["provider_execution"]["cache_hit"]


def test_trace_records_stable_codes_and_omits_query_and_credentials() -> None:
    secret_query = "某学生的私密经历及家庭状况"
    task = create_research_task(ResearchTaskRequest(session_id="private-session", query="研究政策"))
    with research_search_scope(task["id"], "private-session"):
        result = service(lambda request: httpx.Response(401)).retrieve(query=secret_query)
    record_trace(session_id="private-session", event_type="tool_execution", result_status="failed",
                 tool_name="retrieve_web", arguments={"query": secret_query}, result=result,
                 metrics={"search_query": secret_query})
    records = database.get_session_trace_records("private-session")
    serialized = json.dumps(records, ensure_ascii=False)
    assert secret_query not in serialized and "secret-provider-key" not in serialized
    assert "Authorization" not in serialized
    event = next(item for item in records if item["event_type"] == "web_search_provider")
    assert event["error_code"] == "SEARCH_AUTH_FAILED"
    assert json.loads(event["metrics_json"])["provider"] == "tavily"


def test_unconfigured_provider_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.services.search_provider_factory.load_dotenv", lambda *args: None)
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = build_search_provider_from_env()
    assert isinstance(provider, UnavailableSearchProvider)
    result = WebRetrievalService(search_provider=provider).retrieve(query="政策")
    assert result["data"]["search_error_code"] == "SEARCH_PROVIDER_UNAVAILABLE"


def test_real_research_web_failure_preserves_waiting_and_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    task = create_research_task(ResearchTaskRequest(session_id="web-failure", query="查询最新政策并总结"))
    calls: list[int] = []
    def handler(request: httpx.Request) -> httpx.Response:
        assert get_research_task(task["id"])["status"] == "WAITING_TOOL"
        calls.append(1)
        return httpx.Response(401)
    configure_search_provider(service(handler).search_provider)
    class Client:
        async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            if messages[-1]["role"] == "user":
                return {"tool_calls": [{"id": f"web-{i}", "type": "function", "function": {
                    "name": "retrieve_web", "arguments": json.dumps({"query": "最新政策"}),
                }} for i in range(2)]}
            return {"content": "网页检索失败，当前无法提供政策结论。"}
    monkeypatch.setattr(agent, "LLMClient", Client)
    run_research_task(task["id"])
    result = get_research_task(task["id"])
    assert result["status"] == "COMPLETED" and len(calls) == 1
    assert result["result"]["web_search_warnings"][0]["code"] == "WEB_SEARCH_FAILED"
    assert result["result"]["web_search_warnings"][0]["error_code"] == "SEARCH_AUTH_FAILED"
    assert result["result"]["evidence_quality"]["status"] == "WARNING"
