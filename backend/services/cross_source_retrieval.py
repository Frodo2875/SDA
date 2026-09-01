"""Bounded V5.3 orchestration across local, Web and direct URL sources.

The module coordinates existing retrieval implementations.  It does not search,
fetch, parse, rank, or create Evidence itself.  Source routing, Tool scope and
UnifiedEvidence remain the authoritative boundaries for those responsibilities.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from copy import deepcopy
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.evidence import UnifiedEvidence
from backend.runtime.safety_policy import PolicyDecision, record_safety_trace
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE
from backend.services.source_router import SourceRoute, SourceStrategy, route_source
from backend.services.tool_scope_resolver import (
    resolve_source_tool_scope,
    source_tool_violation,
)
from backend.services.trace_service import record_trace
from backend.services.web_retrieval import WEB_RETRIEVAL_SERVICE
from backend.tools.excel_utils import failure, success


class LocalRetriever(Protocol):
    def __call__(
        self, scope: dict[str, Any], query: str, top_k: int = 5
    ) -> dict[str, Any]: ...


class WebRetriever(Protocol):
    def __call__(
        self,
        *,
        query: str | None = None,
        url: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]: ...


class SufficiencyStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"


class RetrievalSource(str, Enum):
    LOCAL = "LOCAL"
    WEB = "WEB"
    URL = "URL"


class CrossSourceInformationNeed(BaseModel):
    """Validated, model-fillable information requirement without free-form state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    need: str = Field(min_length=1, max_length=500)
    missing: list[str] = Field(default_factory=list, max_length=20)
    source_needed: SourceStrategy
    required_fields: list[str] = Field(default_factory=list, max_length=20)
    required_terms: list[str] = Field(default_factory=list, max_length=20)
    minimum_evidence: int = Field(default=1, ge=1, le=20)

    @field_validator("missing", "required_fields", "required_terms")
    @classmethod
    def unique_nonempty_strings(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Information Need 列表字段不能包含空值")
        return list(dict.fromkeys(cleaned))


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_strategy: SourceStrategy
    ordered_sources: list[RetrievalSource] = Field(min_length=1, max_length=7)
    urls: list[str] = Field(default_factory=list, max_length=5)
    reason_code: str


class CrossSourceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=3, ge=1, le=8)
    max_tool_calls: int = Field(default=4, ge=1, le=20)
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    top_k: int = Field(default=5, ge=1, le=10)


class SufficiencyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SufficiencyStatus
    evidence_ids: list[str]
    missing: list[str]
    reason_code: str


_SOURCE_SEQUENCE = {
    SourceStrategy.LOCAL: [RetrievalSource.LOCAL],
    SourceStrategy.WEB: [RetrievalSource.WEB],
    SourceStrategy.BOTH: [RetrievalSource.LOCAL, RetrievalSource.WEB],
    SourceStrategy.DIRECT_URL: [RetrievalSource.URL],
}
_PUBLISHED_AT_SIGNALS = ("发布时间", "发布日期", "何时发布")
_QUERY_CONTROL_WORDS = (
    "结合本地", "结合材料", "对照材料", "本地和网页", "本地与网页",
    "材料和最新", "联网", "网页", "互联网",
)


def build_information_need(
    task: str,
    *,
    source_plan: SourcePlan | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic fallback for the LLM-fillable Need contract."""
    clean = " ".join(str(task or "").split())
    if not clean:
        raise ValueError("Information Need 不能为空")
    plan = (
        plan_sources(clean)
        if source_plan is None
        else source_plan
        if isinstance(source_plan, SourcePlan)
        else SourcePlan.model_validate(source_plan)
    )
    required_fields = (
        ["published_at"]
        if any(signal in clean for signal in _PUBLISHED_AT_SIGNALS)
        else []
    )
    return CrossSourceInformationNeed(
        need=clean,
        missing=list(required_fields),
        source_needed=plan.source_strategy,
        required_fields=required_fields,
    ).model_dump(mode="json")


def plan_sources(
    task: str,
    *,
    source_route: SourceRoute | dict[str, Any] | None = None,
) -> SourcePlan:
    """Translate the existing Source Router decision into an execution order."""
    route = (
        SourceRoute.model_validate(route_source(task))
        if source_route is None
        else source_route
        if isinstance(source_route, SourceRoute)
        else SourceRoute.model_validate(source_route)
    )
    sources = list(_SOURCE_SEQUENCE[route.source_strategy])
    if route.source_strategy == SourceStrategy.DIRECT_URL and len(route.urls) > 1:
        sources = [RetrievalSource.URL] * len(route.urls)
    return SourcePlan(
        source_strategy=route.source_strategy,
        ordered_sources=sources,
        urls=list(route.urls),
        reason_code=route.reason_code,
    )


def evaluate_evidence_sufficiency(
    information_need: CrossSourceInformationNeed | dict[str, Any],
    evidence: list[UnifiedEvidence | dict[str, Any]],
) -> dict[str, Any]:
    """Use explicit fields and terms only; no model truth or quality score."""
    need = (
        information_need
        if isinstance(information_need, CrossSourceInformationNeed)
        else CrossSourceInformationNeed.model_validate(information_need)
    )
    canonical = merge_unified_evidence(evidence)
    missing: list[str] = []
    if len(canonical) < need.minimum_evidence:
        missing.append("minimum_evidence")
    for field_name in need.required_fields:
        if not any(_has_explicit_field(item, field_name) for item in canonical):
            missing.append(field_name)
    combined = "\n".join(item.content for item in canonical)
    missing.extend(term for term in need.required_terms if term not in combined)
    missing = list(dict.fromkeys(missing))
    if missing:
        reason = "NO_EVIDENCE" if not canonical else "EXPLICIT_REQUIREMENTS_MISSING"
        status = SufficiencyStatus.INSUFFICIENT
    else:
        reason = "EXPLICIT_REQUIREMENTS_SATISFIED"
        status = SufficiencyStatus.SUFFICIENT
    return SufficiencyEvaluation(
        status=status,
        evidence_ids=[item.evidence_id for item in canonical],
        missing=missing,
        reason_code=reason,
    ).model_dump(mode="json")


def rewrite_query(
    query: str,
    *,
    round_no: int,
    missing: list[str] | None = None,
) -> str | None:
    """Return at most three deterministic variants and never expand URL scope."""
    clean = " ".join(str(query or "").split())
    if not clean or round_no < 1:
        return None
    if round_no == 1:
        return clean[:500]
    base = clean
    for phrase in _QUERY_CONTROL_WORDS:
        base = base.replace(phrase, " ")
    base = " ".join(base.split()) or clean
    if round_no == 2:
        additions = " ".join((missing or [])[:5])
        return " ".join(f"{base} {additions}".split())[:500]
    if round_no == 3:
        return " ".join(f"{base} 官方 原文".split())[:500]
    return None


def merge_unified_evidence(
    *collections: list[UnifiedEvidence | dict[str, Any]],
) -> list[UnifiedEvidence]:
    """Strictly validate and deduplicate canonical Evidence 4.0 records."""
    merged: dict[str, UnifiedEvidence] = {}
    for collection in collections:
        for item in collection:
            model = (
                item
                if isinstance(item, UnifiedEvidence)
                else UnifiedEvidence.model_validate(item)
            )
            merged.setdefault(model.evidence_id, model)
    return list(merged.values())


def run_cross_source_retrieval(
    task: str,
    *,
    information_need: CrossSourceInformationNeed | dict[str, Any] | None = None,
    source_route: SourceRoute | dict[str, Any] | None = None,
    local_scope: dict[str, Any] | None = None,
    max_rounds: int = 3,
    max_tool_calls: int = 4,
    timeout_seconds: float = 10.0,
    top_k: int = 5,
    session_id: str = "cross-source-retrieval",
    local_retriever: LocalRetriever | None = None,
    web_retriever: WebRetriever | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute a Source Router plan under hard rounds, calls and time budgets."""
    try:
        budget = CrossSourceBudget(
            max_rounds=max_rounds,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
            top_k=top_k,
        )
        plan = plan_sources(task, source_route=source_route)
        need = (
            CrossSourceInformationNeed.model_validate(
                build_information_need(task, source_plan=plan)
            )
            if information_need is None
            else information_need
            if isinstance(information_need, CrossSourceInformationNeed)
            else CrossSourceInformationNeed.model_validate(information_need)
        )
        if need.source_needed != plan.source_strategy:
            raise ValueError("Information Need 与 Source Router 策略不一致")
    except (ValidationError, ValueError, TypeError) as exc:
        return failure("INVALID_CROSS_SOURCE_RETRIEVAL_ARGUMENTS", str(exc))

    local = local_retriever or DOCUMENT_AGENT_CORE.retrieve_document
    web = web_retriever or WEB_RETRIEVAL_SERVICE.retrieve
    started = clock()
    attempts: list[dict[str, Any]] = []
    canonical: list[UnifiedEvidence] = []
    attempted: set[tuple[str, str]] = set()
    zero_progress = 0
    stop_reason = "BUDGET_EXHAUSTED"

    for round_no in range(1, budget.max_rounds + 1):
        if len(attempts) >= budget.max_tool_calls:
            stop_reason = "BUDGET_EXHAUSTED"
            break
        if _elapsed(started, clock) >= budget.timeout_seconds:
            stop_reason = "TIMEOUT"
            break
        source = _source_for_round(plan, round_no)
        if source is None:
            stop_reason = "SOURCES_EXHAUSTED"
            break
        evaluation_before = evaluate_evidence_sufficiency(need, canonical)
        query = None
        url = None
        if source == RetrievalSource.URL:
            url_index = min(round_no - 1, len(plan.urls) - 1)
            url = plan.urls[url_index] if plan.urls else None
            fingerprint = (source.value, str(url or ""))
        else:
            query = rewrite_query(
                need.need,
                round_no=round_no,
                missing=evaluation_before["missing"],
            )
            if query is None:
                stop_reason = "QUERY_EXHAUSTED"
                break
            fingerprint = (source.value, query.casefold())
        if fingerprint in attempted:
            stop_reason = "LOOP_PREVENTED"
            break
        attempted.add(fingerprint)

        tool_name, arguments = _tool_call(source, query, url, local_scope, budget.top_k)
        violation = _scope_violation(plan, tool_name, arguments)
        if violation is not None:
            return failure("TOOL_NOT_ALLOWED_FOR_SOURCE", violation)
        assessment = DOCUMENT_AGENT_CORE.assess_tool_execution(
            tool_name=tool_name,
            arguments=deepcopy(arguments),
            read_only=True,
            requires_confirmation=False,
        )
        record_safety_trace(
            assessment,
            session_id=session_id,
            tool_name=tool_name,
        )
        if assessment.decision != PolicyDecision.ALLOW:
            return failure(
                "SAFETY_POLICY_BLOCKED_RETRIEVAL",
                "Safety Policy 阻止了跨来源检索调用",
            )
        call_started = time.perf_counter()
        try:
            result = (
                local(deepcopy(local_scope or {}), str(query), budget.top_k)
                if source == RetrievalSource.LOCAL
                else web(query=query, url=url, top_k=budget.top_k)
            )
        except Exception as exc:
            result = failure(
                "CROSS_SOURCE_RETRIEVAL_ERROR",
                f"{tool_name} 调用失败：{type(exc).__name__}",
            )
        latency_ms = max(0, round((time.perf_counter() - call_started) * 1000))
        current = _extract_unified_evidence(result)
        previous_ids = {item.evidence_id for item in canonical}
        canonical = merge_unified_evidence(canonical, current)
        new_ids = [
            item.evidence_id for item in canonical
            if item.evidence_id not in previous_ids
        ]
        zero_progress = zero_progress + 1 if not new_ids else 0
        evaluation = evaluate_evidence_sufficiency(need, canonical)
        attempt = {
            "round": round_no,
            "source": source.value,
            "tool_name": tool_name,
            "query": query,
            "url": url,
            "status": "success" if result.get("ok") is True else "failed",
            "error_code": result.get("error_code"),
            "latency_ms": latency_ms,
            "new_evidence_ids": new_ids,
            "sufficiency_before": evaluation_before["status"],
            "sufficiency_after": evaluation["status"],
        }
        attempts.append(attempt)
        _trace_attempt(session_id, attempt)
        need.missing = list(evaluation["missing"])
        if evaluation["status"] == SufficiencyStatus.SUFFICIENT.value:
            stop_reason = "SUFFICIENT"
            break
        if zero_progress >= 2:
            stop_reason = "NO_NEW_EVIDENCE"
            break
    else:
        stop_reason = "BUDGET_EXHAUSTED"

    final = evaluate_evidence_sufficiency(need, canonical)
    elapsed_ms = max(0, round(_elapsed(started, clock) * 1000))
    _trace_stop(session_id, stop_reason, final, len(attempts), elapsed_ms)
    payload = {
        "status": final["status"],
        "information_need": need.model_dump(mode="json"),
        "source_plan": plan.model_dump(mode="json"),
        "attempts": attempts,
        "tool_calls": len(attempts),
        "budget": budget.model_dump(),
        "elapsed_ms": elapsed_ms,
        "stop_reason": stop_reason,
        "unified_evidence": [
            item.model_dump(mode="json", exclude_none=True) for item in canonical
        ],
        "read_only": True,
    }
    return success(payload, _result_message(final["status"], stop_reason))


def _source_for_round(plan: SourcePlan, round_no: int) -> RetrievalSource | None:
    if round_no <= len(plan.ordered_sources):
        return plan.ordered_sources[round_no - 1]
    if plan.source_strategy == SourceStrategy.DIRECT_URL:
        return None
    return plan.ordered_sources[-1]


def _tool_call(
    source: RetrievalSource,
    query: str | None,
    url: str | None,
    local_scope: dict[str, Any] | None,
    top_k: int,
) -> tuple[str, dict[str, Any]]:
    if source == RetrievalSource.LOCAL:
        return "retrieve_document", {
            "scope": deepcopy(local_scope or {}), "query": query, "top_k": top_k,
        }
    if source == RetrievalSource.URL:
        return "retrieve_web", {"url": url, "top_k": top_k}
    return "retrieve_web", {"query": query, "top_k": top_k}


def _scope_violation(
    plan: SourcePlan,
    tool_name: str,
    arguments: dict[str, Any],
) -> str | None:
    route = SourceRoute(
        source_strategy=plan.source_strategy,
        reason_code=plan.reason_code,
        urls=plan.urls,
    )
    scope = resolve_source_tool_scope(
        route, {"retrieve_document", "retrieve_web"}
    )
    return source_tool_violation(scope, tool_name, arguments)


def _extract_unified_evidence(result: dict[str, Any]) -> list[UnifiedEvidence]:
    if not isinstance(result, dict) or result.get("ok") is not True:
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    values = result.get("unified_evidence")
    if not isinstance(values, list):
        values = data.get("unified_evidence")
    if not isinstance(values, list):
        return []
    canonical = []
    for value in values:
        try:
            canonical.append(UnifiedEvidence.model_validate(value))
        except (ValidationError, TypeError):
            continue
    return canonical


def _has_explicit_field(evidence: UnifiedEvidence, field_name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", field_name):
        return False
    if field_name in UnifiedEvidence.model_fields:
        value = getattr(evidence, field_name)
    else:
        value = evidence.metadata.get(field_name)
    return value is not None and value != "" and value != []


def _elapsed(started: float, clock: Callable[[], float]) -> float:
    return max(0.0, float(clock()) - float(started))


def _trace_attempt(session_id: str, attempt: dict[str, Any]) -> None:
    try:
        record_trace(
            session_id=session_id,
            event_type="cross_source_retrieval_round",
            tool_name=attempt["tool_name"],
            arguments={
                "query": attempt["query"],
                "url": attempt["url"],
                "source": attempt["source"],
            },
            result={
                "ok": attempt["status"] == "success",
                "status": attempt["sufficiency_after"],
                "error_code": attempt["error_code"],
            },
            duration_ms=attempt["latency_ms"],
            result_status=attempt["status"],
            error_code=attempt["error_code"],
            metrics={
                "round": attempt["round"],
                "new_evidence_count": len(attempt["new_evidence_ids"]),
                "sufficiency_before": attempt["sufficiency_before"],
                "sufficiency_after": attempt["sufficiency_after"],
            },
        )
    except Exception:
        pass


def _trace_stop(
    session_id: str,
    stop_reason: str,
    evaluation: dict[str, Any],
    tool_calls: int,
    elapsed_ms: int,
) -> None:
    try:
        record_trace(
            session_id=session_id,
            event_type="cross_source_retrieval_stop",
            tool_name="cross_source_retrieval",
            arguments={"tool_calls": tool_calls},
            result={
                "ok": True,
                "status": evaluation["status"],
                "message": stop_reason,
            },
            duration_ms=elapsed_ms,
            result_status="success",
            metrics={
                "stop_reason": stop_reason,
                "evidence_count": len(evaluation["evidence_ids"]),
            },
        )
    except Exception:
        pass


def _result_message(status: str, stop_reason: str) -> str:
    if status == SufficiencyStatus.SUFFICIENT.value:
        return "跨来源检索已获得满足明确要求的 Evidence。"
    if stop_reason in {"BUDGET_EXHAUSTED", "TIMEOUT"}:
        return "检索预算已停止继续调用，当前 Evidence 仍不充分。"
    return "当前来源中未获得足够 Evidence；这不表示相关事实不存在。"


__all__ = [
    "CrossSourceBudget",
    "CrossSourceInformationNeed",
    "RetrievalSource",
    "SourcePlan",
    "SufficiencyStatus",
    "build_information_need",
    "evaluate_evidence_sufficiency",
    "merge_unified_evidence",
    "plan_sources",
    "rewrite_query",
    "run_cross_source_retrieval",
]
