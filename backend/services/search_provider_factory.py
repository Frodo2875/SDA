"""Environment-based Web Search provider composition at the application edge."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from backend.services.search_provider import SearchProvider, UnavailableSearchProvider
from backend.services.tavily_search_provider import (
    DEFAULT_TAVILY_BASE_URL,
    DEFAULT_TAVILY_TIMEOUT_SECONDS,
    TavilySearchProvider,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def build_search_provider_from_env() -> SearchProvider:
    """Build one configured adapter without exposing credentials in errors."""
    load_dotenv(ENV_FILE)
    provider_name = os.getenv("WEB_SEARCH_PROVIDER", "").strip().casefold()
    if not provider_name:
        return UnavailableSearchProvider()
    if provider_name != "tavily":
        return UnavailableSearchProvider(
            f"不支持的 Web Search Provider：{provider_name}"
        )
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return UnavailableSearchProvider("TAVILY_API_KEY 尚未配置")
    try:
        return TavilySearchProvider(
            api_key=api_key,
            base_url=os.getenv(
                "TAVILY_BASE_URL", DEFAULT_TAVILY_BASE_URL
            ).strip(),
            timeout_seconds=_float_setting(
                "WEB_SEARCH_TIMEOUT_SECONDS", DEFAULT_TAVILY_TIMEOUT_SECONDS
            ),
            max_results=_int_setting("WEB_SEARCH_DEFAULT_TOP_K", 5),
        )
    except (TypeError, ValueError):
        return UnavailableSearchProvider("Tavily Search 配置无效")


def _float_setting(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return default if not raw else float(raw)


def _int_setting(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return default if not raw else int(raw)


__all__ = ["build_search_provider_from_env"]
