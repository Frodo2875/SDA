"""Case 4: Tavily failures are bounded and normalized without network access."""

from typing import Callable

import httpx
import pytest

from backend.services.tavily_search_provider import TavilySearchProvider
from backend.services.web_retrieval import WebRetrievalService


def service(handler: Callable[[httpx.Request], httpx.Response]) -> WebRetrievalService:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WebRetrievalService(search_provider=TavilySearchProvider(
        api_key="evaluation-key", client=client,
    ))


def test_429_retries_once_and_returns_stable_error(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    monkeypatch.setattr("backend.services.tavily_search_provider.time.sleep", lambda _: None)
    result = service(handler).retrieve(query="就业方向")
    assert calls == 2
    assert result["ok"] is False
    assert result["data"]["search_error_code"] == "SEARCH_RATE_LIMIT"


@pytest.mark.parametrize(
    ("handler", "error_code"),
    [
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)), "SEARCH_TIMEOUT"),
        (lambda request: httpx.Response(200, content=b"{invalid"), "SEARCH_INVALID_RESPONSE"),
    ],
)
def test_web_failure_does_not_crash(handler, error_code: str) -> None:
    result = service(handler).retrieve(query="就业方向")
    assert result["ok"] is False
    assert result["data"]["status"] == "failed"
    assert result["data"]["search_error_code"] == error_code
