"""Bounded, execution-local search reuse and query-free provider observations."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import time
from typing import Any, Iterator

from backend.services.search_provider import SearchProvider, SearchProviderError, normalize_search_results
from backend.services.web_models import WebSearchResult, WebSearchScope


MESSAGES = {
    "SEARCH_PROVIDER_UNAVAILABLE": "Web Search Provider 暂不可用",
    "SEARCH_AUTH_FAILED": "Web Search 鉴权失败，请检查配置",
    "SEARCH_RATE_LIMIT": "Web Search 请求受限，请稍后重试",
    "SEARCH_TIMEOUT": "Web Search 请求超时",
    "SEARCH_NETWORK_ERROR": "Web Search 网络连接失败",
    "SEARCH_INVALID_RESPONSE": "Web Search 返回格式无效",
    "SEARCH_EMPTY_RESULT": "Web Search 未返回结果",
}
MAX_TASK_SEARCHES = 128


@dataclass
class SearchScope:
    task_id: str
    session_id: str
    cache: dict[tuple[Any, ...], tuple[list[WebSearchResult], dict[str, Any], str | None]] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)


_SCOPE: ContextVar[SearchScope | None] = ContextVar("research_search_scope", default=None)
_ATTEMPT: ContextVar[dict[str, Any] | None] = ContextVar("search_attempt", default=None)


@contextmanager
def research_search_scope(task_id: str, session_id: str) -> Iterator[SearchScope]:
    """No global cache; discard entries on completion, cancellation or error."""
    scope = SearchScope(task_id, session_id)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def note_attempt(retry_count: int) -> None:
    metrics = _ATTEMPT.get()
    if metrics is not None:
        metrics.update(attempt_count=retry_count + 1, retry_count=retry_count)


def _observe(metrics: dict[str, Any]) -> None:
    from backend.services.trace_service import record_trace

    scope = _SCOPE.get()
    code = metrics["error_code"]
    if scope is not None and metrics["status"] == "failed":
        warning = {"code": "WEB_SEARCH_FAILED", "error_code": code, "message": MESSAGES[code]}
        if warning not in scope.failures:
            scope.failures.append(warning)
    try:
        record_trace(
            session_id=scope.session_id if scope else "web-search",
            task_id=scope.task_id if scope else None,
            event_type="web_search_provider", result_status=metrics["status"],
            duration_ms=metrics["latency_ms"], retry_count=metrics["retry_count"],
            error_code=code, metrics=metrics,
        )
    except Exception:
        # Observability storage must not turn a search response into a failure.
        pass


def reliable_search(
    provider: SearchProvider, query: str, scope: WebSearchScope,
) -> tuple[list[WebSearchResult], dict[str, Any]]:
    """Keep the legacy error contract and add stable V6.3 classifications."""
    started = time.perf_counter()
    from backend import database
    task = _SCOPE.get()
    key = (id(provider), query.strip(), scope.top_k, scope.language, scope.region)
    metrics: dict[str, Any] = {
        "provider": "tavily" if provider.name == "tavily" else "unconfigured" if provider.name == "unconfigured" else "custom",
        "latency_ms": 0, "status": "success", "error_code": None,
        "attempt_count": 0, "retry_count": 0, "cache_hit": False,
        "retrieved_at": database.utc_now(),
    }
    if task is not None and key in task.cache:
        results, previous, legacy_code = deepcopy(task.cache[key])
        metrics.update(previous, cache_hit=True, attempt_count=0, retry_count=0, latency_ms=0)
        _observe(metrics)
        if legacy_code:
            error = SearchProviderError(MESSAGES[metrics["error_code"]], error_code=legacy_code,
                                        search_error_code=metrics["error_code"])
            error.provider_execution = metrics
            raise error
        return results, metrics
    token = _ATTEMPT.set(metrics)
    error: SearchProviderError | None = None
    results: list[WebSearchResult] = []
    try:
        raw = provider.search(query, scope)
        try:
            results = normalize_search_results(raw, provider_name=provider.name, limit=scope.top_k)
        except SearchProviderError as exc:
            raise SearchProviderError("Web Search 返回格式无效", error_code=exc.error_code,
                                      search_error_code="SEARCH_INVALID_RESPONSE") from exc
        if not results:
            metrics.update(status="empty", error_code="SEARCH_EMPTY_RESULT")
    except SearchProviderError as exc:
        code = exc.search_error_code if exc.search_error_code in MESSAGES else "SEARCH_PROVIDER_UNAVAILABLE"
        error = SearchProviderError(MESSAGES[code], error_code=exc.error_code, search_error_code=code)
    except TimeoutError:
        error = SearchProviderError(MESSAGES["SEARCH_TIMEOUT"], error_code="WEB_SEARCH_TIMEOUT", search_error_code="SEARCH_TIMEOUT")
    except OSError:
        error = SearchProviderError(MESSAGES["SEARCH_NETWORK_ERROR"], search_error_code="SEARCH_NETWORK_ERROR")
    except Exception:
        error = SearchProviderError(MESSAGES["SEARCH_PROVIDER_UNAVAILABLE"])
    finally:
        _ATTEMPT.reset(token)
    if error:
        metrics.update(status="failed", error_code=error.search_error_code)
    metrics["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
    if task is not None and len(task.cache) < MAX_TASK_SEARCHES:
        task.cache[key] = deepcopy((results, metrics, error.error_code if error else None))
    _observe(metrics)
    if error:
        error.provider_execution = metrics
        raise error
    return results, metrics
