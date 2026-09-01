"""Replaceable search provider port and normalized result adapter."""

from typing import Any, Protocol

from backend.services.web_models import WebSearchResult, WebSearchScope
from backend.services.url_fetch import URLFetchError, canonicalize_url


class SearchProviderError(RuntimeError):
    """A provider is unavailable or returned an invalid response."""


class SearchProvider(Protocol):
    name: str

    def search(
        self, query: str, scope: WebSearchScope
    ) -> list[WebSearchResult | dict[str, Any]]: ...


class UnavailableSearchProvider:
    """Safe default: Web search is explicit and never silently fabricated."""

    name = "unconfigured"

    def search(
        self, query: str, scope: WebSearchScope
    ) -> list[WebSearchResult | dict[str, Any]]:
        raise SearchProviderError("Web Search Provider 尚未配置")


def normalize_search_results(
    raw_results: list[WebSearchResult | dict[str, Any]],
    *,
    provider_name: str,
    limit: int,
) -> list[WebSearchResult]:
    if not isinstance(raw_results, list):
        raise SearchProviderError("Search Provider 返回值必须是列表")
    normalized: list[WebSearchResult] = []
    seen: set[str] = set()
    for raw in raw_results:
        try:
            item = raw if isinstance(raw, WebSearchResult) else WebSearchResult.model_validate({
                **raw,
                "source": str(raw.get("source") or provider_name),
            })
        except (TypeError, ValueError) as exc:
            raise SearchProviderError("Search Provider 返回了无效结果") from exc
        if not item.url.lower().startswith(("http://", "https://")):
            raise SearchProviderError("Search Provider 返回了非 HTTP(S) URL")
        try:
            canonical_url = canonicalize_url(item.url)
        except URLFetchError as exc:
            raise SearchProviderError("Search Provider 返回了无效 URL") from exc
        item = item.model_copy(update={"url": canonical_url})
        if item.url in seen:
            continue
        seen.add(item.url)
        normalized.append(item)
        if len(normalized) >= limit:
            break
    return normalized


__all__ = [
    "SearchProvider",
    "SearchProviderError",
    "UnavailableSearchProvider",
    "normalize_search_results",
]
