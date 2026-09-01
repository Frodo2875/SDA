"""Resolve the Agent Tool boundary from a validated source route."""

from dataclasses import dataclass
from typing import Any, Literal

from backend.services.source_router import SourceRoute, SourceStrategy


WEB_TOOL_NAMES = frozenset({"retrieve_web"})


@dataclass(frozen=True)
class SourceToolScope:
    source_strategy: SourceStrategy
    allowed_tool_names: frozenset[str]
    web_mode: Literal["none", "search", "direct_url", "any"]


def resolve_source_tool_scope(
    source_route: dict[str, Any] | SourceRoute,
    candidate_tool_names: frozenset[str] | set[str],
) -> SourceToolScope:
    """Return the least-privilege Tool scope for a source strategy."""
    route = (
        source_route
        if isinstance(source_route, SourceRoute)
        else SourceRoute.model_validate(source_route)
    )
    candidates = frozenset(candidate_tool_names)
    if route.source_strategy == SourceStrategy.LOCAL:
        return SourceToolScope(
            source_strategy=route.source_strategy,
            allowed_tool_names=candidates - WEB_TOOL_NAMES,
            web_mode="none",
        )
    if route.source_strategy == SourceStrategy.WEB:
        return SourceToolScope(
            source_strategy=route.source_strategy,
            allowed_tool_names=candidates & WEB_TOOL_NAMES,
            web_mode="search",
        )
    if route.source_strategy == SourceStrategy.DIRECT_URL:
        return SourceToolScope(
            source_strategy=route.source_strategy,
            allowed_tool_names=candidates & WEB_TOOL_NAMES,
            web_mode="direct_url",
        )
    return SourceToolScope(
        source_strategy=route.source_strategy,
        allowed_tool_names=candidates,
        web_mode="any",
    )


def source_tool_violation(
    scope: SourceToolScope,
    tool_name: str,
    arguments: dict[str, Any],
) -> str | None:
    """Return a rejection reason when a Tool call escapes its source scope."""
    if tool_name not in scope.allowed_tool_names:
        return f"{scope.source_strategy.value} Source 策略不允许调用工具：{tool_name}"
    if tool_name != "retrieve_web":
        return None
    if scope.web_mode == "search" and not arguments.get("query"):
        return "WEB Source 策略只允许 Web Search 模式"
    if scope.web_mode == "direct_url" and not arguments.get("url"):
        return "DIRECT_URL Source 策略只允许 URL Fetch 模式"
    return None


__all__ = [
    "SourceToolScope",
    "WEB_TOOL_NAMES",
    "resolve_source_tool_scope",
    "source_tool_violation",
]
