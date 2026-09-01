"""Controlled Web Search and Direct URL service behind one read-only Tool."""

import time
from typing import Any

from backend.services.search_provider import (
    SearchProvider,
    SearchProviderError,
    UnavailableSearchProvider,
    normalize_search_results,
)
from backend.services.url_fetch import URLFetchError, URLFetcher
from backend.services.web_models import WebSearchScope
from backend.tools.excel_utils import failure, success


class WebRetrievalService:
    def __init__(
        self,
        *,
        search_provider: SearchProvider | None = None,
        url_fetcher: URLFetcher | None = None,
    ) -> None:
        self.search_provider = search_provider or UnavailableSearchProvider()
        self.url_fetcher = url_fetcher or URLFetcher()

    def retrieve(
        self,
        *,
        query: str | None = None,
        url: str | None = None,
        top_k: int = 5,
        language: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if (query is None) == (url is None):
            return failure("INVALID_WEB_RETRIEVAL_ARGUMENTS", "query 与 url 必须且只能提供一个")
        if url is not None:
            try:
                document = self.url_fetcher.fetch(url)
            except URLFetchError as exc:
                return failure(
                    exc.error_code,
                    str(exc),
                    {
                        "status": "failed",
                        "mode": "direct_url",
                        "url": str(url),
                        "latency_ms": _elapsed_ms(started),
                    },
                )
            return success(
                {
                    "status": "found",
                    "mode": "direct_url",
                    "url": document.url,
                    "document": document.model_dump(mode="json"),
                    "latency_ms": _elapsed_ms(started),
                    "untrusted_data": True,
                },
                "网页获取并解析完成",
            )

        clean_query = str(query or "").strip()
        try:
            scope = WebSearchScope(
                top_k=top_k, language=language, region=region
            )
            raw_results = self.search_provider.search(clean_query, scope)
            results = normalize_search_results(
                raw_results,
                provider_name=str(getattr(self.search_provider, "name", "unknown")),
                limit=scope.top_k,
            )
        except SearchProviderError as exc:
            return failure(
                "WEB_SEARCH_PROVIDER_ERROR",
                str(exc),
                {
                    "status": "failed",
                    "mode": "search",
                    "query": clean_query,
                    "latency_ms": _elapsed_ms(started),
                },
            )
        except TimeoutError:
            return failure(
                "WEB_SEARCH_TIMEOUT",
                "Web Search 超时",
                {
                    "status": "failed",
                    "mode": "search",
                    "query": clean_query,
                    "latency_ms": _elapsed_ms(started),
                },
            )
        except Exception:
            return failure(
                "WEB_SEARCH_PROVIDER_ERROR",
                "Web Search Provider 调用失败",
                {
                    "status": "failed",
                    "mode": "search",
                    "query": clean_query,
                    "latency_ms": _elapsed_ms(started),
                },
            )
        status = "found" if results else "not_found"
        return success(
            {
                "status": status,
                "mode": "search",
                "query": clean_query,
                "provider": str(getattr(self.search_provider, "name", "unknown")),
                "results": [item.model_dump(mode="json") for item in results],
                "result_count": len(results),
                "latency_ms": _elapsed_ms(started),
                "untrusted_data": True,
            },
            "Web Search 完成" if results else "Web Search 未返回结果",
        )


WEB_RETRIEVAL_SERVICE = WebRetrievalService()


def configure_search_provider(provider: SearchProvider) -> None:
    """Replace only the provider adapter; fetch and safety policy stay intact."""
    WEB_RETRIEVAL_SERVICE.search_provider = provider


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


__all__ = [
    "WEB_RETRIEVAL_SERVICE",
    "WebRetrievalService",
    "configure_search_provider",
]
