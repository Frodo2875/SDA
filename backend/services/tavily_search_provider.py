"""Production Tavily adapter for the replaceable Web Search provider port."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.services.search_provider import SearchProviderError
from backend.services.web_models import WebSearchScope


DEFAULT_TAVILY_BASE_URL = "https://api.tavily.com"
DEFAULT_TAVILY_TIMEOUT_SECONDS = 8.0
MAX_RETRY_AFTER_SECONDS = 2.0


def retry_after_seconds(value: str | None) -> float | None:
    """Return a bounded delay; excessive valid delays decline the retry."""
    try:
        delay = float(value or "1")
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value or "")
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            delay = (stamp - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 1.0
    if not math.isfinite(delay):
        delay = 1.0
    return None if delay > MAX_RETRY_AFTER_SECONDS else max(0.0, delay)


class TavilySearchProvider:
    """Call Tavily Search without accepting its optional generated answer."""

    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_TAVILY_BASE_URL,
        timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
        max_results: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        clean_key = str(api_key or "").strip()
        if not clean_key:
            raise ValueError("TAVILY_API_KEY 不能为空")
        self.api_key = clean_key
        self.base_url = _canonical_base_url(base_url)
        self.timeout_seconds = max(1.0, min(30.0, float(timeout_seconds)))
        self.max_results = max(1, min(10, int(max_results)))
        self.client = client

    def search(
        self, query: str, scope: WebSearchScope
    ) -> list[dict[str, Any]]:
        clean_query = str(query or "").strip()
        if not clean_query:
            raise SearchProviderError(
                "Web Search 查询不能为空", error_code="INVALID_WEB_SEARCH_QUERY"
            )
        if len(clean_query) > 400:
            raise SearchProviderError(
                "Web Search 查询超过长度限制", error_code="INVALID_WEB_SEARCH_QUERY"
            )
        payload = {
            "query": clean_query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": min(scope.top_k, self.max_results),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
        }
        response = self._post(payload)
        try:
            body = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                "Tavily 返回了无法识别的响应",
                error_code="WEB_SEARCH_INVALID_RESPONSE",
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise SearchProviderError(
                "Tavily 响应缺少 results 列表",
                error_code="WEB_SEARCH_INVALID_RESPONSE",
            )
        return [
            _normalize_tavily_result(item, scope=scope)
            for item in body["results"]
        ]

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        from backend.services.search_reliability import note_attempt

        for attempt in range(2):
            note_attempt(attempt)
            response = self._request(payload)
            if response.status_code != 429:
                return self._check_status(response)
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            response.close()
            if attempt == 1 or delay is None:
                break
            time.sleep(delay)
        raise SearchProviderError(
            "Tavily API 请求已达到限流或额度限制",
            error_code="WEB_SEARCH_RATE_LIMITED",
        )

    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self.client is not None:
                response = self.client.post(
                    f"{self.base_url}/search",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/search", headers=headers, json=payload
                    )
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                "Tavily Search 超时", error_code="WEB_SEARCH_TIMEOUT"
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError(
                "无法连接 Tavily Search", error_code="WEB_SEARCH_PROVIDER_ERROR",
                search_error_code="SEARCH_NETWORK_ERROR",
            ) from exc
        return response

    @staticmethod
    def _check_status(response: httpx.Response) -> httpx.Response:
        if response.status_code in {401, 403}:
            raise SearchProviderError(
                "Tavily API 鉴权失败", error_code="WEB_SEARCH_AUTH_ERROR"
            )
        if response.status_code == 429:
            raise SearchProviderError(
                "Tavily API 请求已达到限流或额度限制",
                error_code="WEB_SEARCH_RATE_LIMITED",
            )
        if not 200 <= response.status_code < 300:
            raise SearchProviderError(
                f"Tavily Search 返回 HTTP {response.status_code}",
                error_code="WEB_SEARCH_PROVIDER_ERROR",
            )
        return response


def _canonical_base_url(value: str) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
        port = parts.port
    except ValueError as exc:
        raise ValueError("TAVILY_BASE_URL 格式无效") from exc
    if parts.scheme.casefold() != "https" or not parts.hostname:
        raise ValueError("TAVILY_BASE_URL 必须是 HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("TAVILY_BASE_URL 不允许包含认证信息")
    if parts.query or parts.fragment:
        raise ValueError("TAVILY_BASE_URL 不允许包含查询参数或片段")
    host = parts.hostname.casefold().rstrip(".")
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def _normalize_tavily_result(
    raw: Any, *, scope: WebSearchScope
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SearchProviderError(
            "Tavily 返回了无效搜索结果",
            error_code="WEB_SEARCH_INVALID_RESPONSE",
        )
    if (any(not isinstance(raw.get(key), str) or not raw[key].strip()
            for key in ("title", "url"))
            or len(raw["title"]) > 500 or len(raw["url"]) > 4096
            or (raw.get("content") is not None and (
                not isinstance(raw["content"], str) or len(raw["content"]) > 4000))
            or any(raw.get(key) is not None and not isinstance(raw[key], str)
                   for key in ("published_date", "favicon"))
            or (raw.get("score") is not None and (
                isinstance(raw["score"], bool) or not isinstance(raw["score"], (int, float))
                or not math.isfinite(raw["score"])))):
        raise SearchProviderError("Tavily 搜索结果字段无效", error_code="WEB_SEARCH_INVALID_RESPONSE")
    try:
        parts = urlsplit(raw["url"])
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("invalid URL")
        parts.port
    except ValueError as exc:
        raise SearchProviderError("Tavily 搜索结果 URL 无效", error_code="WEB_SEARCH_INVALID_RESPONSE") from exc
    metadata = {
        "provider_score": raw.get("score"),
        "published_at": raw.get("published_date"),
        "favicon": raw.get("favicon"),
        "requested_language": scope.language,
        "requested_region": scope.region,
    }
    return {
        "title": raw.get("title"),
        "url": raw.get("url"),
        "snippet": raw.get("content"),
        "source": "tavily",
        "metadata": {
            key: value for key, value in metadata.items() if value not in {None, ""}
        },
    }


__all__ = [
    "DEFAULT_TAVILY_BASE_URL",
    "DEFAULT_TAVILY_TIMEOUT_SECONDS",
    "TavilySearchProvider",
]
