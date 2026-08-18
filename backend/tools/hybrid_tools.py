"""Evidence-bound Python evaluation for structured and unstructured materials."""

import re
from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from backend import database
from backend.tool_models import ScholarshipEvaluationArguments
from backend.tools.excel_utils import failure, success


INSUFFICIENT_MESSAGE = "当前材料不足以判断。"
FIELD_ALIASES = {
    "average_score": {"平均分", "平均成绩", "average_score", "averagescore"},
    "rank": {"专业排名", "排名", "rank"},
    "papers": {"论文数", "论文数量", "papers"},
    "patents": {"专利数", "专利数量", "patents"},
    "competitions": {"竞赛数", "竞赛数量", "competitions"},
}
FIELD_LABELS = {
    "average_score": "平均成绩",
    "rank": "专业排名",
    "papers": "论文数",
    "patents": "专利数",
    "competitions": "竞赛数",
    "research_total": "科研成果总数",
}
SCORE_FIELDS = {"average_score", "rank"}
RESEARCH_FIELDS = {"papers", "patents", "competitions", "research_total"}


def evaluate_scholarship_eligibility(
    student_id: str,
    award_name: str = "一等奖学金",
    *,
    evidence_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate only trusted prior Tool outputs; numeric inputs are never model-provided."""
    try:
        arguments = ScholarshipEvaluationArguments.model_validate(
            {"student_id": student_id, "award_name": award_name}
        )
    except ValidationError as exc:
        return failure("INVALID_EVALUATION_ARGUMENTS", _validation_message(exc))
    if evidence_context is None:
        return _insufficient(arguments, ["成绩", "科研", "规则"], [], [])

    structured = _collect_structured_values(
        arguments.student_id, evidence_context.get("query_calls") or []
    )
    rules = _extract_rules(
        arguments.award_name, evidence_context.get("retrieval_calls") or []
    )
    missing: list[str] = []
    if not structured["score_call_seen"]:
        missing.append("成绩")
    if not structured["research_call_seen"]:
        missing.append("科研")
    if not rules["conditions"]:
        missing.append("规则")
    if structured["conflicts"]:
        missing.extend(f"{FIELD_LABELS.get(field, field)}存在冲突" for field in structured["conflicts"])
    if rules["ambiguous"]:
        missing.append("规则存在冲突或包含当前版本无法可靠执行的逻辑")

    condition_results = []
    used_evidence: dict[str, dict[str, Any]] = {}
    for condition in rules["conditions"]:
        field = condition["field"]
        actual, evidence = _actual_value(field, structured)
        if actual is None:
            category = "成绩" if field in SCORE_FIELDS else "科研"
            missing.append(f"{category}：{FIELD_LABELS[field]}")
            continue
        passed = _compare(actual, condition["operator"], condition["threshold"])
        for item in evidence + [condition["evidence"]]:
            used_evidence[item["evidence_id"]] = item
        condition_results.append(
            {
                "field": field,
                "field_label": FIELD_LABELS[field],
                "actual_value": actual,
                "operator": condition["operator"],
                "threshold": condition["threshold"],
                "passed": passed,
                "evidence_ids": [
                    item["evidence_id"]
                    for item in evidence + [condition["evidence"]]
                ],
            }
        )

    missing = list(dict.fromkeys(missing))
    used_tools = _used_tools(condition_results, rules, structured, evidence_context)
    if missing:
        return _insufficient(
            arguments,
            missing,
            condition_results,
            list(used_evidence.values()),
            used_tools,
        )

    eligible = all(item["passed"] for item in condition_results)
    conclusion = "eligible" if eligible else "not_eligible"
    conclusion_text = (
        f"根据当前材料，{arguments.student_id}满足{arguments.award_name}条件。"
        if eligible
        else f"根据当前材料，{arguments.student_id}不满足{arguments.award_name}条件。"
    )
    evidence_chain = list(used_evidence.values())
    answer = _answer_text(conclusion_text, condition_results, evidence_chain, used_tools)
    return success(
        {
            "status": "found",
            "student_id": arguments.student_id,
            "award_name": arguments.award_name,
            "conclusion": conclusion,
            "conclusion_text": conclusion_text,
            "conditions": condition_results,
            "missing": [],
            "evidence_chain": evidence_chain,
            "used_tools": used_tools,
            "answer": answer,
        },
        "奖学金条件已由 Python 根据实际 Evidence 完成判断",
    )


def _collect_structured_values(
    student_id: str, query_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    values: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    score_call_seen = False
    research_call_seen = False
    for call in query_calls:
        result = call.get("result") or {}
        data = result.get("data") or {}
        rows = data.get("rows") if isinstance(data, dict) else None
        if result.get("ok") is not True or not isinstance(rows, list):
            continue
        chain = result.get("evidence_chain") or []
        fields_in_call = {item.get("field") for item in chain}
        fields_in_call.update(call.get("arguments", {}).get("select") or [])
        canonical_in_call = {
            canonical
            for field in fields_in_call
            for canonical in [_canonical_field(field)]
            if canonical
        }
        score_call_seen = score_call_seen or bool(canonical_in_call & SCORE_FIELDS)
        research_call_seen = research_call_seen or bool(canonical_in_call & RESEARCH_FIELDS)
        for evidence in chain:
            canonical = _canonical_field(evidence.get("field"))
            if canonical is None or evidence.get("record_key") != student_id:
                continue
            number = _number(evidence.get("value_summary"))
            if number is not None:
                values[canonical].append((number, evidence))

    resolved: dict[str, float] = {}
    evidence_by_field: dict[str, list[dict[str, Any]]] = {}
    conflicts = []
    for field, entries in values.items():
        unique_values = {item[0] for item in entries}
        if len(unique_values) > 1:
            conflicts.append(field)
            continue
        resolved[field] = entries[0][0]
        evidence_by_field[field] = [item[1] for item in entries]
    return {
        "values": resolved,
        "evidence": evidence_by_field,
        "conflicts": conflicts,
        "score_call_seen": score_call_seen,
        "research_call_seen": research_call_seen,
    }


def _extract_rules(
    award_name: str, retrieval_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    conditions = []
    ambiguous = False
    seen = set()
    for call in retrieval_calls:
        result = call.get("result") or {}
        if result.get("ok") is not True:
            continue
        for evidence in result.get("evidence") or []:
            text = _chunk_text(evidence)
            section = _award_section(text, award_name)
            if not section:
                continue
            if "或" in section:
                ambiguous = True
                continue
            for field, operator, threshold in _rule_matches(section):
                key = (field, operator, threshold)
                if key in seen:
                    continue
                seen.add(key)
                conditions.append(
                    {
                        "field": field,
                        "operator": operator,
                        "threshold": threshold,
                        "evidence": _formal_evidence(evidence),
                    }
                )
    by_field: dict[str, set[tuple[str, float]]] = defaultdict(set)
    for item in conditions:
        by_field[item["field"]].add((item["operator"], item["threshold"]))
    if any(len(values) > 1 for values in by_field.values()):
        ambiguous = True
    return {"conditions": conditions, "ambiguous": ambiguous}


def _rule_matches(text: str) -> list[tuple[str, str, float]]:
    matches: list[tuple[str, str, float]] = []
    field_patterns = {
        "average_score": r"(?:平均成绩|平均分|average(?:_score)?)",
        "rank": r"(?:专业排名|排名|rank)",
        "papers": r"(?:论文数|论文数量|papers?)",
        "patents": r"(?:专利数|专利数量|patents?)",
        "competitions": r"(?:竞赛数|竞赛数量|competitions?)",
        "research_total": r"(?:科研成果总数|科研成果|research)",
    }
    at_least = r"(?:不低于|不少于|至少|达到|>=|≥)\s*(\d+(?:\.\d+)?)"
    at_most = r"(?:不超过|不高于|至多|<=|≤|(?:在|位于)?前)\s*(\d+(?:\.\d+)?)"
    for field, prefix in field_patterns.items():
        for match in re.finditer(prefix + r"\s*" + at_least, text, re.I):
            matches.append((field, ">=", float(match.group(1))))
        for match in re.finditer(prefix + r"\s*" + at_most, text, re.I):
            matches.append((field, "<=", float(match.group(1))))
        for match in re.finditer(prefix + r"\s*(\d+(?:\.\d+)?)\s*(?:分|项|名)?以上", text, re.I):
            matches.append((field, ">=", float(match.group(1))))
    return matches


def _actual_value(
    field: str, structured: dict[str, Any]
) -> tuple[float | None, list[dict[str, Any]]]:
    if field != "research_total":
        return (
            structured["values"].get(field),
            structured["evidence"].get(field, []),
        )
    components = ("papers", "patents", "competitions")
    if any(item not in structured["values"] for item in components):
        return None, []
    value = sum(structured["values"][item] for item in components)
    evidence = [
        item
        for component in components
        for item in structured["evidence"].get(component, [])
    ]
    return value, evidence


def _chunk_text(evidence: dict[str, Any]) -> str:
    file_id = evidence.get("file_id")
    chunk_id = evidence.get("chunk_id")
    if not isinstance(file_id, str) or not isinstance(chunk_id, str):
        return ""
    return next(
        (
            item["chunk_text"]
            for item in database.get_document_chunks(file_id)
            if item["chunk_id"] == chunk_id
        ),
        "",
    )


def _formal_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "evidence_id",
        "task_id",
        "source_type",
        "file_id",
        "file_name",
        "sheet",
        "page_no",
        "chunk_id",
        "field",
        "record_key",
        "value_summary",
    )
    return {key: evidence.get(key) for key in keys}


def _award_section(text: str, award_name: str) -> str:
    start = text.find(award_name)
    if start < 0:
        return ""
    remainder = text[start:]
    next_heading = re.search(r"(?:特|一|二|三)等奖学金", remainder[len(award_name) :])
    if next_heading:
        return remainder[: len(award_name) + next_heading.start()]
    return remainder


def _canonical_field(field: Any) -> str | None:
    normalized = re.sub(r"[\s_\-]", "", str(field or "")).casefold()
    for canonical, aliases in FIELD_ALIASES.items():
        if normalized in {
            re.sub(r"[\s_\-]", "", alias).casefold() for alias in aliases
        }:
            return canonical
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _compare(actual: float, operator: str, threshold: float) -> bool:
    return actual >= threshold if operator == ">=" else actual <= threshold


def _used_tools(
    conditions: list[dict[str, Any]],
    rules: dict[str, Any],
    structured: dict[str, Any],
    context: dict[str, Any],
) -> list[str]:
    tools = []
    if context.get("identity_call"):
        tools.append("search_student")
    if any(item["field"] in SCORE_FIELDS for item in conditions):
        tools.append("query_table")
    if any(item["field"] in RESEARCH_FIELDS for item in conditions):
        tools.append("query_table")
    if rules["conditions"]:
        tools.append("retrieve_document")
    tools.append("evaluate_scholarship_eligibility")
    return list(dict.fromkeys(tools))


def _insufficient(
    arguments: ScholarshipEvaluationArguments,
    missing: list[str],
    conditions: list[dict[str, Any]],
    evidence_chain: list[dict[str, Any]],
    used_tools: list[str] | None = None,
) -> dict[str, Any]:
    used_tools = used_tools or ["evaluate_scholarship_eligibility"]
    answer = (
        f"{INSUFFICIENT_MESSAGE}\n\n缺少：" + "、".join(missing) + "。"
    )
    return success(
        {
            "status": "insufficient_evidence",
            "student_id": arguments.student_id,
            "award_name": arguments.award_name,
            "conclusion": "insufficient_evidence",
            "conclusion_text": INSUFFICIENT_MESSAGE,
            "conditions": conditions,
            "missing": missing,
            "evidence_chain": evidence_chain,
            "used_tools": used_tools,
            "answer": answer,
        },
        INSUFFICIENT_MESSAGE,
    )


def _answer_text(
    conclusion: str,
    conditions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    used_tools: list[str],
) -> str:
    lines = [conclusion, "", "依据："]
    for item in conditions:
        relation = "满足" if item["passed"] else "不满足"
        lines.append(
            f"- {item['field_label']}：实际 {item['actual_value']:g}，"
            f"要求 {item['operator']} {item['threshold']:g}，{relation}。"
        )
    sources = list(
        dict.fromkeys(
            f"{item['file_name']}"
            + (f" / {item['sheet']}" if item.get("sheet") else "")
            + (f" / 第{item['page_no']}页" if item.get("page_no") else "")
            for item in evidence
        )
    )
    lines.extend(["", "本次结论使用了："])
    lines.extend(f"- {source}" for source in sources)
    lines.append("- Tools：" + "、".join(used_tools))
    return "\n".join(lines)


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first["loc"])
    return f"参数 {location} 无效：{first['msg']}"
