"""Evidence-bound Python checks for general, non-student document tasks."""

import re
from collections import OrderedDict
from typing import Any

from pydantic import ValidationError

from backend.services.document_agent_core import DOCUMENT_AGENT_CORE
from backend.tool_models import ProjectApprovalArguments
from backend.tools.excel_utils import failure, success


RULE_PATTERN = re.compile(
    r"预算(?:金额)?\s*(超过|高于|大于|不低于|不少于|至少|达到|不超过|低于|>=|<=|>|<|≥|≤)"
    r"\s*(\d+(?:\.\d+)?)\s*(万元|万|元)?"
)
OPERATORS = {
    "超过": ">", "高于": ">", "大于": ">", ">": ">",
    "不低于": ">=", "不少于": ">=", "至少": ">=", "达到": ">=",
    ">=": ">=", "≥": ">=", "不超过": "<=", "<=": "<=", "≤": "<=",
    "低于": "<", "<": "<",
}


def evaluate_project_approval(
    budget_file_id: str,
    policy_file_id: str,
    sheet: str,
    project_field: str,
    budget_field: str,
    budget_threshold: float,
    budget_unit: str = "yuan",
    rule_query: str = "专项审批",
) -> dict[str, Any]:
    """Filter in query_table and evaluate an explicit policy rule in Python."""
    try:
        arguments = ProjectApprovalArguments.model_validate({
            "budget_file_id": budget_file_id,
            "policy_file_id": policy_file_id,
            "sheet": sheet,
            "project_field": project_field,
            "budget_field": budget_field,
            "budget_threshold": budget_threshold,
            "budget_unit": budget_unit,
            "rule_query": rule_query,
        })
    except ValidationError as exc:
        return failure("INVALID_PROJECT_APPROVAL_ARGUMENTS", _validation_message(exc))

    filters = [{
        "field": arguments.budget_field,
        "op": ">",
        "value": arguments.budget_threshold,
    }]
    queried = DOCUMENT_AGENT_CORE.query_table(
        arguments.budget_file_id,
        arguments.sheet,
        filters,
        [arguments.project_field, arguments.budget_field],
        order_by={"field": arguments.budget_field, "direction": "desc"},
    )
    if not queried["ok"]:
        return queried
    aggregated = DOCUMENT_AGENT_CORE.aggregate_table(
        arguments.budget_file_id,
        arguments.sheet,
        "count",
        filters=filters,
    )
    if not aggregated["ok"]:
        return aggregated
    retrieved = DOCUMENT_AGENT_CORE.retrieve_document(
        {"file_id": arguments.policy_file_id},
        arguments.rule_query,
        top_k=5,
    )
    if not retrieved["ok"]:
        return retrieved

    budget_evidence = list(queried.get("evidence_chain") or [])
    rule = _explicit_rule(retrieved.get("evidence") or [])
    execution_chain = [
        {"tool": "query_table", "status": queried["data"]["status"]},
        {"tool": "aggregate_table", "status": aggregated["data"]["status"]},
        {"tool": "retrieve_document", "status": retrieved["data"]["status"]},
        {"tool": "python_condition_check", "status": "pending" if rule is None else "success"},
    ]
    if rule is None:
        message = "管理办法中未找到可可靠执行的明确预算审批阈值。"
        return success(
            {
                "status": "insufficient_evidence",
                "projects_over_requested_threshold": queried["data"]["rows"],
                "requested_threshold": arguments.budget_threshold,
                "requested_threshold_unit": arguments.budget_unit,
                "approval_decisions": [],
                "execution_engine": "python",
                "execution_chain": execution_chain,
                "evidence_chain": budget_evidence,
                "used_tools": ["query_table", "aggregate_table", "retrieve_document"],
                "answer": message,
            },
            message,
        )

    rule_value = _convert_rule_value(
        rule["threshold"], rule["unit"], arguments.budget_unit
    )
    by_record: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in budget_evidence:
        by_record.setdefault(str(item.get("record_key")), []).append(item)
    record_evidence = list(by_record.values())
    decisions = []
    used_budget_evidence: list[dict[str, Any]] = []
    for index, row in enumerate(queried["data"]["rows"]):
        raw_budget = row.get(arguments.budget_field)
        if isinstance(raw_budget, bool) or not isinstance(raw_budget, (int, float)):
            return failure(
                "NON_NUMERIC_BUDGET_VALUE",
                f"字段 {arguments.budget_field} 包含非数值预算，禁止猜测",
            )
        requires_approval = _compare(float(raw_budget), rule["operator"], rule_value)
        row_evidence = record_evidence[index] if index < len(record_evidence) else []
        used_budget_evidence.extend(row_evidence)
        decisions.append({
            "project": row.get(arguments.project_field),
            "budget": raw_budget,
            "budget_unit": arguments.budget_unit,
            "requires_special_approval": requires_approval,
            "operator": rule["operator"],
            "rule_threshold": rule_value,
            "evidence_ids": [
                *[item["evidence_id"] for item in row_evidence],
                rule["evidence"]["evidence_id"],
            ],
        })
    evidence_chain = _deduplicate_evidence([
        *used_budget_evidence, rule["evidence"]
    ])
    approval_names = [
        str(item["project"]) for item in decisions
        if item["requires_special_approval"]
    ]
    count_result = (aggregated["data"].get("results") or [{}])[0].get("value", 0)
    answer = (
        f"预算超过 {arguments.budget_threshold:g} 的项目共 {count_result} 个；"
        + (
            "需要专项审批：" + "、".join(approval_names) + "。"
            if approval_names else "其中没有项目触发当前管理办法的专项审批条件。"
        )
    )
    return success(
        {
            "status": "found",
            "projects_over_requested_threshold": queried["data"]["rows"],
            "requested_threshold": arguments.budget_threshold,
            "requested_threshold_unit": arguments.budget_unit,
            "explicit_rule": {
                "operator": rule["operator"],
                "threshold": rule_value,
                "threshold_unit": arguments.budget_unit,
                "text": rule["text"],
                "evidence_id": rule["evidence"]["evidence_id"],
            },
            "approval_decisions": decisions,
            "execution_engine": "python",
            "execution_chain": execution_chain,
            "evidence_chain": evidence_chain,
            "used_tools": [
                "query_table", "aggregate_table", "retrieve_document",
                "python_condition_check",
            ],
            "answer": answer,
        },
        "项目专项审批条件已由 Python 根据实际 Evidence 完成判断",
    )


def _explicit_rule(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in evidence:
        text = str(item.get("text_excerpt") or item.get("value_summary") or "")
        match = RULE_PATTERN.search(text)
        if match is None:
            continue
        operator_text, threshold, unit = match.groups()
        return {
            "operator": OPERATORS[operator_text],
            "threshold": float(threshold),
            "unit": unit or "元",
            "text": match.group(0),
            "evidence": item,
        }
    return None


def _convert_rule_value(value: float, unit: str, budget_unit: str) -> float:
    in_yuan = value * 10_000 if unit in {"万元", "万"} else value
    return in_yuan / 10_000 if budget_unit == "ten_thousand_yuan" else in_yuan


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        ">": actual > expected,
        ">=": actual >= expected,
        "<": actual < expected,
        "<=": actual <= expected,
    }[operator]


def _deduplicate_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in items:
        if item.get("evidence_id"):
            deduplicated[str(item["evidence_id"])] = item
    return list(deduplicated.values())


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(item) for item in first.get("loc", []))
    return f"参数 {location or 'root'} 无效：{first.get('msg', '校验失败')}"


__all__ = ["evaluate_project_approval"]
