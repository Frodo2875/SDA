"""Controlled retrieval orchestration above the stable RAG 2.0 pipeline.

This service never performs keyword/vector search or reranking itself. Every
retrieval call delegates to ``DocumentAgentCore.retrieve_document`` so V3
Hybrid Retrieval, hard metadata filters, rerank, and Evidence remain the only
retrieval implementation.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.runtime.safety_policy import PolicyDecision, record_safety_trace
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE
from backend.services.trace_service import record_trace
from backend.tool_models import RetrievalScope
from backend.tools.excel_utils import failure, success


SufficiencyStatus = Literal[
    "sufficient", "partial", "missing", "conflict", "low_confidence"
]
StopReason = Literal[
    "sufficient", "budget", "no_new_evidence", "scope_exhausted", "error"
]
AnswerStatus = Literal["confirmed", "not_found", "conflict", "low_confidence"]


class RetrievalCallable(Protocol):
    def __call__(
        self, scope: dict[str, Any], query: str, top_k: int = 5
    ) -> dict[str, Any]: ...


class InformationNeed(BaseModel):
    """One independently satisfiable information requirement."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    need_id: str = Field(min_length=3, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    status: SufficiencyStatus = "missing"
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    query: str = Field(min_length=1, max_length=500)
    required_terms: list[str] = Field(default_factory=list, max_length=20)
    minimum_evidence: int = Field(default=1, ge=1, le=20)
    rewrite_queries: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("evidence_ids", "required_terms", "rewrite_queries")
    @classmethod
    def unique_strings(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("列表不能包含空值")
        return list(dict.fromkeys(cleaned))


class RetrievalBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=3, ge=1, le=10)
    max_tool_calls: int = Field(default=6, ge=1, le=50)
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    top_k: int = Field(default=5, ge=1, le=20)


class SufficiencyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SufficiencyStatus
    evidence_ids: list[str]
    missing_terms: list[str]
    reason_code: str


COMPLEX_SIGNALS = (
    "并根据", "然后", "分别", "综合", "比较", "对比", "判断", "是否符合",
    "是否需要", "规则", "资格", "冲突", "跨文件", "多个文件",
)
CLAUSE_SPLIT = re.compile(r"(?:，?并根据|，?然后|；|;|，同时|，以及|，并且)")
LOW_CONFIDENCE_THRESHOLD = 0.70


def is_complex_retrieval_task(task: str) -> bool:
    """Conservatively identify tasks that need multiple evidence obligations."""
    text = str(task or "").strip()
    if not text:
        return False
    signal_count = sum(signal in text for signal in COMPLEX_SIGNALS)
    clauses = [item.strip() for item in CLAUSE_SPLIT.split(text) if item.strip()]
    return signal_count >= 1 and (len(clauses) >= 2 or signal_count >= 2)


def build_information_needs(
    task: str,
    *,
    scope: dict[str, Any] | None = None,
    domain: Literal["student", "general", "unknown"] = "general",
) -> list[dict[str, Any]]:
    """Build bounded needs only for complex requests; simple queries bypass."""
    text = str(task or "").strip()
    if not is_complex_retrieval_task(text):
        return []
    base_scope = dict(scope or {})
    explicit_year = _explicit_year(text)
    if explicit_year is not None:
        if base_scope.get("year") not in {None, explicit_year}:
            raise ValueError("任务年份与显式 scope.year 冲突")
        base_scope["year"] = explicit_year
    validated_scope = RetrievalScope.model_validate(base_scope)
    clauses = [item.strip(" ，。") for item in CLAUSE_SPLIT.split(text) if item.strip(" ，。")]
    if len(clauses) == 1:
        clauses = [text]
    needs = []
    for index, clause in enumerate(clauses[:6], start=1):
        need_type = _need_type(clause, domain)
        digest = hashlib.sha256(
            f"{domain}:{index}:{clause}:{validated_scope.model_dump_json()}".encode("utf-8")
        ).hexdigest()[:12]
        needs.append(InformationNeed(
            need_id=f"need-{digest}",
            type=need_type,
            name=clause[:128],
            description=clause,
            scope=validated_scope,
            query=clause,
        ).model_dump(exclude_none=True))
    return needs


def evaluate_evidence_sufficiency(
    need: InformationNeed | dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Classify actual Evidence without treating absence as fact negation."""
    model = need if isinstance(need, InformationNeed) else InformationNeed.model_validate(need)
    unique = _deduplicate_evidence(evidence)
    evidence_ids = [str(item["evidence_id"]) for item in unique]
    if not unique:
        return SufficiencyEvaluation(
            status="missing", evidence_ids=[], missing_terms=list(model.required_terms),
            reason_code="NO_EVIDENCE_IN_SCOPE",
        ).model_dump()
    if any(_is_conflict(item) for item in unique):
        return SufficiencyEvaluation(
            status="conflict", evidence_ids=evidence_ids, missing_terms=[],
            reason_code="CONFLICTING_EVIDENCE_REQUIRES_REVIEW",
        ).model_dump()

    safe = [
        item for item in unique
        if not _is_low_confidence(item, low_confidence_threshold)
    ]
    combined_text = "\n".join(_evidence_text(item) for item in safe)
    missing_terms = [term for term in model.required_terms if term not in combined_text]
    if len(safe) >= model.minimum_evidence and not missing_terms:
        return SufficiencyEvaluation(
            status="sufficient", evidence_ids=evidence_ids, missing_terms=[],
            reason_code="EVIDENCE_REQUIREMENTS_SATISFIED",
        ).model_dump()
    if any(_is_low_confidence(item, low_confidence_threshold) for item in unique):
        return SufficiencyEvaluation(
            status="low_confidence", evidence_ids=evidence_ids,
            missing_terms=missing_terms,
            reason_code="ONLY_LOW_CONFIDENCE_EVIDENCE_AVAILABLE",
        ).model_dump()
    return SufficiencyEvaluation(
        status="partial", evidence_ids=evidence_ids, missing_terms=missing_terms,
        reason_code="EVIDENCE_REQUIREMENTS_PARTIALLY_SATISFIED",
    ).model_dump()


def rewrite_retrieval_query(
    need: InformationNeed | dict[str, Any],
    *,
    round_no: int,
    missing_terms: list[str] | None = None,
) -> str | None:
    """Create a bounded query variant; scope is deliberately not an argument."""
    model = need if isinstance(need, InformationNeed) else InformationNeed.model_validate(need)
    if round_no <= 1:
        return model.query
    rewrite_index = round_no - 2
    if rewrite_index < len(model.rewrite_queries):
        return model.rewrite_queries[rewrite_index]
    if round_no == 2:
        additions = " ".join((missing_terms or [])[:5]) or model.name
        return _bounded_query(f"{model.query} 补充证据 {additions}")
    if round_no == 3:
        return _bounded_query(f"{model.query} 交叉核验 {model.description}")
    return None


def run_controlled_retrieval(
    task: str,
    *,
    needs: list[InformationNeed | dict[str, Any]] | None = None,
    scope: dict[str, Any] | None = None,
    domain: Literal["student", "general", "unknown"] = "general",
    max_rounds: int = 3,
    max_tool_calls: int = 6,
    timeout_seconds: float = 10.0,
    top_k: int = 5,
    session_id: str = "agentic-retrieval",
    retriever: RetrievalCallable | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Retrieve only unresolved needs under immutable scope and hard budgets."""
    try:
        budget = RetrievalBudget.model_validate({
            "max_rounds": max_rounds,
            "max_tool_calls": max_tool_calls,
            "timeout_seconds": timeout_seconds,
            "top_k": top_k,
        })
        raw_needs = needs if needs is not None else build_information_needs(
            task, scope=scope, domain=domain
        )
        need_models = [
            item if isinstance(item, InformationNeed)
            else InformationNeed.model_validate(item)
            for item in raw_needs
        ]
        if len({need.need_id for need in need_models}) != len(need_models):
            raise ValueError("need_id 不能重复")
        # A caller may describe a prior state, but this run only trusts Evidence
        # obtained through its own bounded retrieval calls.
        for need in need_models:
            need.status = "missing"
            need.evidence_ids = []
        global_scope_payload = dict(scope or {})
        explicit_year = _explicit_year(str(task or ""))
        if explicit_year is not None:
            if global_scope_payload.get("year") not in {None, explicit_year}:
                raise ValueError("任务年份与显式 scope.year 冲突")
            global_scope_payload["year"] = explicit_year
        if global_scope_payload:
            global_scope = RetrievalScope.model_validate(global_scope_payload)
            for need in need_models:
                need.scope = _constrain_scope(need.scope, global_scope)
    except (ValidationError, ValueError, TypeError) as exc:
        return failure("INVALID_AGENTIC_RETRIEVAL_ARGUMENTS", str(exc))

    if not need_models:
        return success({
            "status": "bypassed",
            "mode": "simple_query_no_loop",
            "information_needs": [],
            "rounds": [],
            "tool_calls": 0,
            "stop_reason": "sufficient",
            "answer_status": None,
            "evidence": [],
            "message": "简单查询继续使用单次 RAG 2.0，不创建 Agentic Retrieval Need。",
            "read_only": True,
            "workflow_bypassed": False,
            "approval_bypassed": False,
        }, "简单查询未进入 Agentic Retrieval 循环")

    retrieve = retriever or DOCUMENT_AGENT_CORE.retrieve_document
    started = clock()
    tool_calls = 0
    attempts: list[dict[str, Any]] = []
    evidence_by_need: dict[str, list[dict[str, Any]]] = {
        need.need_id: [] for need in need_models
    }
    attempted_queries: dict[str, set[str]] = {
        need.need_id: set() for need in need_models
    }
    stop_reason: StopReason | None = None

    for round_no in range(1, budget.max_rounds + 1):
        unresolved = [need for need in need_models if need.status != "sufficient"]
        if not unresolved:
            stop_reason = "sufficient"
            break
        round_calls = 0
        round_new_evidence = 0
        exhausted_needs = 0
        for need in unresolved:
            if tool_calls >= budget.max_tool_calls or _elapsed(started, clock) >= budget.timeout_seconds:
                stop_reason = "budget"
                break
            evaluation_before = evaluate_evidence_sufficiency(
                need, evidence_by_need[need.need_id]
            )
            query = rewrite_retrieval_query(
                need,
                round_no=round_no,
                missing_terms=evaluation_before["missing_terms"],
            )
            if query is None or query in attempted_queries[need.need_id]:
                exhausted_needs += 1
                continue
            attempted_queries[need.need_id].add(query)
            scope_payload = need.scope.model_dump(exclude_none=True)
            assessment = DOCUMENT_AGENT_CORE.assess_tool_execution(
                tool_name="retrieve_document",
                arguments={"scope": deepcopy(scope_payload), "query": query, "top_k": budget.top_k},
                read_only=True,
                requires_confirmation=False,
            )
            record_safety_trace(
                assessment,
                session_id=session_id,
                tool_name="retrieve_document",
            )
            if assessment.decision != PolicyDecision.ALLOW:
                attempt = _attempt_record(
                    round_no, need, query, scope_payload, 0, [], 0,
                    need.status, "error", "SAFETY_POLICY_BLOCKED_RETRIEVAL",
                    sufficiency_before=evaluation_before["status"],
                )
                attempts.append(attempt)
                _trace_attempt(session_id, attempt)
                stop_reason = "error"
                break
            retrieval_started = time.perf_counter()
            try:
                result = retrieve(deepcopy(scope_payload), query, budget.top_k)
            except Exception as exc:
                attempt = _attempt_record(
                    round_no, need, query, scope_payload, 0, [], 0,
                    need.status, "error", f"RETRIEVAL_ERROR:{type(exc).__name__}",
                    sufficiency_before=evaluation_before["status"],
                    latency_ms=max(0, round((time.perf_counter() - retrieval_started) * 1000)),
                )
                attempts.append(attempt)
                _trace_attempt(session_id, attempt)
                stop_reason = "error"
                break
            tool_calls += 1
            round_calls += 1
            if not isinstance(result, dict) or result.get("ok") is not True:
                result_error = (
                    result.get("error_code") if isinstance(result, dict) else None
                )
                attempt = _attempt_record(
                    round_no, need, query, scope_payload, 0, [], 0,
                    need.status, "error", str(result_error or "INVALID_RETRIEVAL_RESULT"),
                    sufficiency_before=evaluation_before["status"],
                    latency_ms=max(0, round((time.perf_counter() - retrieval_started) * 1000)),
                )
                attempts.append(attempt)
                _trace_attempt(session_id, attempt)
                stop_reason = "error"
                break
            current = _result_evidence(result)
            existing_ids = {
                str(item.get("evidence_id"))
                for item in evidence_by_need[need.need_id]
                if item.get("evidence_id")
            }
            new_items = [
                item for item in current
                if item.get("evidence_id") and str(item["evidence_id"]) not in existing_ids
            ]
            evidence_by_need[need.need_id].extend(new_items)
            round_new_evidence += len(new_items)
            evaluation = evaluate_evidence_sufficiency(
                need, evidence_by_need[need.need_id]
            )
            need.status = evaluation["status"]
            need.evidence_ids = evaluation["evidence_ids"]
            attempt = _attempt_record(
                round_no,
                need,
                query,
                scope_payload,
                _candidate_count(result, current),
                [str(item["evidence_id"]) for item in current if item.get("evidence_id")],
                len(new_items),
                evaluation["status"],
                None,
                evaluation["reason_code"],
                sufficiency_before=evaluation_before["status"],
                latency_ms=max(0, round((time.perf_counter() - retrieval_started) * 1000)),
            )
            attempts.append(attempt)
            _trace_attempt(session_id, attempt)
            if _elapsed(started, clock) >= budget.timeout_seconds:
                stop_reason = "budget"
                break
        if stop_reason is not None:
            break
        if all(need.status == "sufficient" for need in need_models):
            stop_reason = "sufficient"
            break
        if round_calls == 0 and exhausted_needs == len(unresolved):
            stop_reason = "scope_exhausted"
            break
        if round_no > 1 and round_new_evidence == 0:
            stop_reason = "no_new_evidence"
            break
        if tool_calls >= budget.max_tool_calls:
            stop_reason = "budget"
            break
    if stop_reason is None:
        stop_reason = "budget"
    if attempts and attempts[-1]["stop_reason"] is None:
        attempts[-1]["stop_reason"] = stop_reason

    all_evidence = _deduplicate_evidence([
        item for need in need_models for item in evidence_by_need[need.need_id]
    ])
    answer_status = _answer_status(need_models)
    elapsed_ms = max(0, round(_elapsed(started, clock) * 1000))
    _trace_stop(
        session_id, stop_reason, answer_status, tool_calls, elapsed_ms, need_models
    )
    message = _answer_message(answer_status)
    return success({
        "status": "completed",
        "mode": "controlled_agentic_retrieval",
        "domain": domain,
        "information_needs": [need.model_dump(exclude_none=True) for need in need_models],
        "rounds": attempts,
        "tool_calls": tool_calls,
        "budget": budget.model_dump(),
        "elapsed_ms": elapsed_ms,
        "stop_reason": stop_reason,
        "answer_status": answer_status,
        "evidence": all_evidence,
        "message": message,
        "read_only": True,
        "workflow_bypassed": False,
        "approval_bypassed": False,
    }, message)


def _need_type(clause: str, domain: str) -> str:
    if any(term in clause for term in ("规则", "办法", "资格", "条件", "审批")):
        return "rule"
    if any(term in clause for term in ("比较", "对比", "差异")):
        return "comparison"
    return "student_fact" if domain == "student" else "general_fact"


def _explicit_year(text: str) -> int | None:
    match = re.search(r"(?<!\d)(20\d{2})\s*年?", text)
    return int(match.group(1)) if match else None


def _constrain_scope(
    need_scope: RetrievalScope,
    global_scope: RetrievalScope,
) -> RetrievalScope:
    """Apply user scope as a hard upper bound while allowing narrower Needs."""
    need = need_scope.model_dump(exclude_none=True)
    global_data = global_scope.model_dump(exclude_none=True)
    global_file_id = global_data.pop("file_id", None)
    global_file_ids = global_data.pop("file_ids", None)
    need_file_id = need.get("file_id")
    need_file_ids = need.get("file_ids")
    if global_file_id is not None:
        if need_file_id is not None and need_file_id != global_file_id:
            raise ValueError("Need file_id 超出用户限定 scope")
        if need_file_ids is not None and set(need_file_ids) != {global_file_id}:
            raise ValueError("Need file_ids 超出用户限定 scope")
        need.pop("file_ids", None)
        need["file_id"] = global_file_id
    elif global_file_ids is not None:
        allowed = set(global_file_ids)
        if need_file_id is not None and need_file_id not in allowed:
            raise ValueError("Need file_id 超出用户限定 file_ids")
        if need_file_ids is not None and not set(need_file_ids).issubset(allowed):
            raise ValueError("Need file_ids 超出用户限定 file_ids")
        if need_file_id is None and need_file_ids is None:
            need["file_ids"] = global_file_ids

    global_metadata = dict(global_data.pop("document_metadata", {}) or {})
    need_metadata = dict(need.get("document_metadata", {}) or {})
    for key, value in global_metadata.items():
        if key in need_metadata and need_metadata[key] != value:
            raise ValueError(f"Need metadata.{key} 超出用户限定 scope")
        need_metadata[key] = value
    if need_metadata:
        need["document_metadata"] = need_metadata
    for key, value in global_data.items():
        if key in need and need[key] != value:
            raise ValueError(f"Need {key} 超出用户限定 scope")
        need[key] = value
    return RetrievalScope.model_validate(need)


def _bounded_query(query: str) -> str:
    return " ".join(query.split())[:500]


def _result_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        evidence = data.get("evidence")
    return [dict(item) for item in (evidence or []) if isinstance(item, dict)]


def _candidate_count(result: dict[str, Any], evidence: list[dict[str, Any]]) -> int:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    value = data.get("top_n_count")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else len(evidence)


def _deduplicate_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        evidence_id = item.get("evidence_id")
        if evidence_id:
            unique.setdefault(str(evidence_id), dict(item))
    return list(unique.values())


def _evidence_text(item: dict[str, Any]) -> str:
    return str(item.get("text_excerpt") or item.get("value_summary") or "")


def _is_conflict(item: dict[str, Any]) -> bool:
    return bool(item.get("conflict_sources")) or item.get("status") == "conflict"


def _is_low_confidence(item: dict[str, Any], threshold: float) -> bool:
    if item.get("review_required"):
        return True
    confidence = item.get("confidence")
    return isinstance(confidence, (int, float)) and float(confidence) < threshold


def _elapsed(started: float, clock: Callable[[], float]) -> float:
    return max(0.0, float(clock()) - float(started))


def _attempt_record(
    round_no: int,
    need: InformationNeed,
    query: str,
    scope: dict[str, Any],
    candidates: int,
    selected_evidence: list[str],
    new_evidence_count: int,
    sufficiency: SufficiencyStatus,
    stop_reason: StopReason | None,
    reason_code: str,
    *,
    sufficiency_before: SufficiencyStatus | None = None,
    latency_ms: int = 0,
) -> dict[str, Any]:
    before = sufficiency_before or need.status
    return {
        "round": round_no,
        "need_id": need.need_id,
        "query": query,
        "scope": deepcopy(scope),
        "candidates": candidates,
        "selected_evidence": selected_evidence,
        "new_evidence_count": new_evidence_count,
        "sufficiency": sufficiency,
        "sufficiency_before": before,
        "sufficiency_after": sufficiency,
        "sufficiency_transition": f"{before}->{sufficiency}",
        "stop_reason": stop_reason,
        "reason_code": reason_code,
        "latency_ms": max(0, int(latency_ms)),
    }


def _answer_status(needs: list[InformationNeed]) -> AnswerStatus:
    statuses = {need.status for need in needs}
    if "conflict" in statuses:
        return "conflict"
    if "low_confidence" in statuses:
        return "low_confidence"
    if statuses == {"sufficient"}:
        return "confirmed"
    return "not_found"


def _answer_message(status: AnswerStatus) -> str:
    return {
        "confirmed": "当前结论具有满足 Need 的实际 Evidence。",
        "not_found": "当前限定范围内未找到足够 Evidence；这不等于相关事实不存在。",
        "conflict": "检索到相互冲突的 Evidence，必须保留来源并核对，不能静默选边。",
        "low_confidence": "当前只有低置信度 Evidence，结论需要人工核对。",
    }[status]


def _trace_attempt(session_id: str, attempt: dict[str, Any]) -> None:
    try:
        record_trace(
            session_id=session_id,
            event_type="agentic_retrieval_round",
            tool_name="retrieve_document",
            arguments={
                "need_id": attempt["need_id"],
                "query": attempt["query"],
                "scope": attempt["scope"],
            },
            result={
                "ok": attempt["stop_reason"] != "error",
                "status": attempt["sufficiency"],
                "message": attempt["reason_code"],
            },
            result_status="failed" if attempt["stop_reason"] == "error" else "success",
            duration_ms=attempt["latency_ms"],
            metrics={
                "retrieval_round": attempt["round"],
                "query": attempt["query"],
                "scope": attempt["scope"],
                "candidate_count": attempt["candidates"],
                **{
                    key: attempt[key] for key in (
                        "round", "need_id", "candidates", "selected_evidence",
                        "new_evidence_count", "sufficiency", "sufficiency_before",
                        "sufficiency_after", "sufficiency_transition", "stop_reason",
                        "reason_code", "latency_ms",
                    )
                },
            },
        )
    except Exception:
        pass


def _trace_stop(
    session_id: str,
    stop_reason: StopReason,
    answer_status: AnswerStatus,
    tool_calls: int,
    elapsed_ms: int,
    needs: list[InformationNeed],
) -> None:
    try:
        record_trace(
            session_id=session_id,
            event_type="agentic_retrieval_stop",
            tool_name="agentic_retrieval",
            arguments={"need_ids": [need.need_id for need in needs]},
            result={"ok": stop_reason != "error", "status": answer_status, "message": stop_reason},
            result_status="failed" if stop_reason == "error" else "success",
            metrics={
                "stop_reason": stop_reason,
                "answer_status": answer_status,
                "tool_calls": tool_calls,
                "elapsed_ms": elapsed_ms,
                "sufficiency": {need.need_id: need.status for need in needs},
            },
        )
    except Exception:
        pass


__all__ = [
    "InformationNeed",
    "RetrievalBudget",
    "build_information_needs",
    "evaluate_evidence_sufficiency",
    "is_complex_retrieval_task",
    "rewrite_retrieval_query",
    "run_controlled_retrieval",
]
