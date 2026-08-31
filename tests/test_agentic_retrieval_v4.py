"""V4.9 controlled Agentic Retrieval over the existing RAG 2.0 entrypoint."""

import json
from typing import Any

from backend import database
from backend.services.agentic_retrieval import (
    build_information_needs,
    evaluate_evidence_sufficiency,
    run_controlled_retrieval,
)
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE
from backend.services.student_domain_adapter import STUDENT_DOMAIN_ADAPTER


def _need(
    *,
    need_id: str = "need-rule",
    scope: dict[str, Any] | None = None,
    minimum_evidence: int = 1,
    required_terms: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "need_id": need_id,
        "type": "rule",
        "name": "专项审批规则",
        "description": "确认项目专项审批规则与阈值",
        "status": "missing",
        "scope": scope or {},
        "evidence_ids": [],
        "query": "项目专项审批阈值",
        "required_terms": required_terms or [],
        "minimum_evidence": minimum_evidence,
        "rewrite_queries": [],
    }


def _evidence(
    evidence_id: str,
    text: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_type": "unstructured",
        "file_id": "a" * 32,
        "file_name": "规则.pdf",
        "page_no": 1,
        "text_excerpt": text,
        **extra,
    }


def _retrieval_result(evidence: list[dict[str, Any]], candidates: int | None = None):
    status = "found" if evidence else "not_found"
    data = {
        "status": status,
        "evidence": evidence,
        "top_n_count": len(evidence) if candidates is None else candidates,
    }
    return {
        "ok": True,
        "data": data,
        "evidence": evidence,
        "error_code": None,
        "message": "完成",
    }


def test_ar_simple_query_does_not_create_need_or_loop() -> None:
    def forbidden_retriever(scope, query, top_k=5):
        raise AssertionError("简单查询不得进入 Agentic Retrieval loop")

    result = run_controlled_retrieval(
        "查询培养方案", retriever=forbidden_retriever
    )

    assert result["ok"] is True
    assert result["data"]["mode"] == "simple_query_no_loop"
    assert result["data"]["information_needs"] == []
    assert result["data"]["rounds"] == []
    assert result["data"]["tool_calls"] == 0


def test_ar_missing_need_is_not_treated_as_fact_nonexistence() -> None:
    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need()],
        max_rounds=2,
        retriever=lambda scope, query, top_k=5: _retrieval_result([]),
    )

    data = result["data"]
    assert data["information_needs"][0]["status"] == "missing"
    assert data["answer_status"] == "not_found"
    assert data["stop_reason"] == "no_new_evidence"
    assert "不等于相关事实不存在" in data["message"]


def test_ar_partial_evidence_keeps_need_unresolved() -> None:
    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(minimum_evidence=2)],
        max_rounds=1,
        retriever=lambda scope, query, top_k=5: _retrieval_result([
            _evidence("ev-one", "专项审批阈值为150万元")
        ]),
    )

    assert result["data"]["information_needs"][0]["status"] == "partial"
    assert result["data"]["answer_status"] == "not_found"


def test_ar_conflict_and_low_confidence_are_distinct_final_states() -> None:
    conflict_need = _need(need_id="need-conflict")
    conflict = evaluate_evidence_sufficiency(conflict_need, [
        _evidence(
            "ev-conflict", "阈值为150万元",
            conflict_sources=[{"source_model": "ocr", "text": "100万元"}],
        )
    ])
    conflict_run = run_controlled_retrieval(
        "复杂冲突规则核验",
        needs=[conflict_need],
        max_rounds=1,
        retriever=lambda scope, query, top_k=5: _retrieval_result([
            _evidence(
                "ev-conflict", "阈值为150万元",
                conflict_sources=[{"source_model": "ocr", "text": "100万元"}],
            )
        ]),
    )
    low = run_controlled_retrieval(
        "复杂手写规则核验",
        needs=[_need(need_id="need-low")],
        max_rounds=1,
        retriever=lambda scope, query, top_k=5: _retrieval_result([
            _evidence("ev-low", "疑似阈值", confidence=0.42, review_required=True)
        ]),
    )

    assert conflict["status"] == "conflict"
    assert conflict_run["data"]["answer_status"] == "conflict"
    assert low["data"]["information_needs"][0]["status"] == "low_confidence"
    assert low["data"]["answer_status"] == "low_confidence"


def test_ar_second_round_retrieves_only_unmet_need_and_stops_sufficient() -> None:
    calls: list[str] = []

    def retriever(scope, query, top_k=5):
        calls.append(query)
        if len(calls) == 1:
            return _retrieval_result([_evidence("ev-threshold", "阈值150万元")], 4)
        return _retrieval_result([_evidence("ev-approval", "需要专项审批")], 3)

    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(
            minimum_evidence=2,
            required_terms=["阈值", "专项审批"],
        )],
        retriever=retriever,
    )

    data = result["data"]
    assert data["stop_reason"] == "sufficient"
    assert data["answer_status"] == "confirmed"
    assert data["tool_calls"] == 2
    assert calls[0] != calls[1]
    assert [item["new_evidence_count"] for item in data["rounds"]] == [1, 1]


def test_ar_satisfied_need_is_not_retrieved_again_while_missing_need_retries() -> None:
    calls: list[tuple[str, str]] = []

    def retriever(scope, query, top_k=5):
        calls.append((str(scope.get("document_metadata", {}).get("kind")), query))
        if scope.get("document_metadata", {}).get("kind") == "fact":
            return _retrieval_result([_evidence("ev-fact", "项目预算220万元")])
        if sum(kind == "rule" for kind, _ in calls) == 1:
            return _retrieval_result([])
        return _retrieval_result([_evidence("ev-rule", "超过150万元需要审批")])

    result = run_controlled_retrieval(
        "项目预算并根据规则判断",
        needs=[
            _need(need_id="need-fact", scope={"document_metadata": {"kind": "fact"}}),
            _need(need_id="need-rule-2", scope={"document_metadata": {"kind": "rule"}}),
        ],
        retriever=retriever,
    )

    assert result["data"]["stop_reason"] == "sufficient"
    assert sum(kind == "fact" for kind, _ in calls) == 1
    assert sum(kind == "rule" for kind, _ in calls) == 2


def test_ar_does_not_trust_caller_claimed_sufficient_without_current_evidence() -> None:
    claimed = _need()
    claimed["status"] = "sufficient"
    claimed["evidence_ids"] = ["historical-memory-evidence"]

    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[claimed],
        max_rounds=1,
        retriever=lambda scope, query, top_k=5: _retrieval_result([]),
    )

    need = result["data"]["information_needs"][0]
    assert result["data"]["tool_calls"] == 1
    assert need["status"] == "missing"
    assert need["evidence_ids"] == []
    assert result["data"]["answer_status"] == "not_found"


def test_ar_no_new_evidence_terminates_without_repeated_search() -> None:
    same = _evidence("ev-same", "只有一条规则")
    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(minimum_evidence=2)],
        retriever=lambda scope, query, top_k=5: _retrieval_result([same]),
    )

    assert result["data"]["stop_reason"] == "no_new_evidence"
    assert result["data"]["tool_calls"] == 2
    assert result["data"]["rounds"][1]["new_evidence_count"] == 0


def test_ar_budget_and_timeout_terminate_bounded_retrieval() -> None:
    partial = lambda scope, query, top_k=5: _retrieval_result([
        _evidence("ev-budget", "部分依据")
    ])
    call_budget = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(minimum_evidence=3)],
        max_tool_calls=1,
        retriever=partial,
    )

    ticks = iter([0.0, 0.06, 0.12, 0.13, 0.14])
    timeout_budget = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(need_id="need-timeout", minimum_evidence=3)],
        timeout_seconds=0.1,
        retriever=partial,
        clock=lambda: next(ticks),
    )

    assert call_budget["data"]["stop_reason"] == "budget"
    assert call_budget["data"]["tool_calls"] == 1
    assert timeout_budget["data"]["stop_reason"] == "budget"
    assert timeout_budget["data"]["tool_calls"] == 1


def test_ar_explicit_file_year_and_metadata_scope_never_expands() -> None:
    database.register_file(
        file_name="2026管理办法.pdf",
        file_type="pdf",
        file_path="data/uploads/2026管理办法.pdf",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    file_id = database.get_file_record("2026管理办法.pdf")["file_id"]
    explicit_scope = {
        "file_id": file_id,
        "year": 2026,
        "page": 2,
        "document_metadata": {"category": "policy"},
    }
    observed: list[dict[str, Any]] = []

    def retriever(scope, query, top_k=5):
        observed.append(scope)
        return _retrieval_result([])

    result = run_controlled_retrieval(
        "根据2026年规则进行复杂核验",
        needs=[_need(scope={})],
        scope=explicit_scope,
        max_rounds=2,
        retriever=retriever,
    )

    assert result["data"]["tool_calls"] == 2
    assert observed == [explicit_scope, explicit_scope]
    assert all(item["scope"] == explicit_scope for item in result["data"]["rounds"])


def test_ar_scope_exhausted_and_retrieval_error_are_explicit() -> None:
    sequence = 0

    def unique_partial(scope, query, top_k=5):
        nonlocal sequence
        sequence += 1
        return _retrieval_result([_evidence(f"ev-{sequence}", f"部分依据{sequence}")])

    exhausted = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(minimum_evidence=10)],
        max_rounds=4,
        retriever=unique_partial,
    )
    failed = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need(need_id="need-error")],
        retriever=lambda scope, query, top_k=5: {
            "ok": False, "error_code": "RAG_UNAVAILABLE", "data": None
        },
    )

    assert exhausted["data"]["stop_reason"] == "scope_exhausted"
    assert exhausted["data"]["tool_calls"] == 3
    assert failed["data"]["stop_reason"] == "error"
    assert failed["data"]["rounds"][0]["reason_code"] == "RAG_UNAVAILABLE"


def test_ar_round_and_stop_trace_contain_control_fields() -> None:
    result = run_controlled_retrieval(
        "复杂规则核验",
        needs=[_need()],
        session_id="ar-trace",
        retriever=lambda scope, query, top_k=5: _retrieval_result([
            _evidence("ev-trace", "明确规则")
        ], candidates=7),
    )

    traces = database.get_session_trace_records("ar-trace")
    round_trace = next(item for item in traces if item["event_type"] == "agentic_retrieval_round")
    stop_trace = next(item for item in traces if item["event_type"] == "agentic_retrieval_stop")
    round_metrics = json.loads(round_trace["metrics_json"])
    stop_metrics = json.loads(stop_trace["metrics_json"])
    assert result["data"]["stop_reason"] == "sufficient"
    assert round_metrics["candidates"] == 7
    assert round_metrics["selected_evidence"] == ["ev-trace"]
    assert round_metrics["new_evidence_count"] == 1
    assert round_metrics["sufficiency"] == "sufficient"
    assert stop_metrics["stop_reason"] == "sufficient"
    assert any(item["event_type"] == "safety_policy_check" for item in traces)


def test_ar_student_and_general_complex_tasks_use_same_core_control_layer() -> None:
    counter = 0

    def retriever(scope, query, top_k=5):
        nonlocal counter
        counter += 1
        return _retrieval_result([_evidence(f"ev-domain-{counter}", query)])

    student_task = "综合分析S001的奖学金资格，并根据2026年办法判断是否符合"
    general_task = "比较两个项目预算，并根据管理办法判断哪些需要审批"
    student_needs = build_information_needs(student_task, domain="student")
    general_needs = build_information_needs(general_task, domain="general")
    student = STUDENT_DOMAIN_ADAPTER.run_controlled_retrieval(
        student_task, needs=student_needs, retriever=retriever
    )
    general = DOCUMENT_AGENT_CORE.run_controlled_retrieval(
        general_task, needs=general_needs, retriever=retriever
    )

    assert len(student_needs) >= 2
    assert all(need["type"] in {"student_fact", "rule"} for need in student_needs)
    assert all(need["scope"]["year"] == 2026 for need in student_needs)
    assert student["data"]["domain"] == "student"
    assert general["data"]["domain"] == "general"
    assert student["data"]["answer_status"] == "confirmed"
    assert general["data"]["answer_status"] == "confirmed"
    assert student["data"]["workflow_bypassed"] is False
    assert general["data"]["approval_bypassed"] is False
