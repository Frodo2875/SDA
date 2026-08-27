"""Safe structured conditions for Workflow nodes; never executes user code."""

from typing import Any


ALLOWED_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists"})
REFERENCE_MISSING = object()


def evaluate_condition(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate one allow-listed comparison against a dotted structured reference."""
    if not isinstance(condition, dict):
        raise ValueError("Condition 必须是结构化对象")
    reference = condition.get("ref")
    operator = condition.get("operator")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("Condition ref 必须是非空字符串")
    if operator not in ALLOWED_OPERATORS:
        raise ValueError(f"不支持的 Condition operator：{operator}")
    actual = resolve_reference(context, reference)
    expected = condition.get("value")
    if operator == "exists":
        wanted = True if "value" not in condition else bool(expected)
        return (actual is not REFERENCE_MISSING and actual is not None) is wanted
    if actual is REFERENCE_MISSING:
        return False
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "in":
        return isinstance(expected, (list, tuple, set, frozenset)) and actual in expected
    if operator == "not_in":
        return isinstance(expected, (list, tuple, set, frozenset)) and actual not in expected
    try:
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        return actual <= expected
    except TypeError as exc:
        raise ValueError("Condition 两侧类型不可比较") from exc


def resolve_reference(context: dict[str, Any], reference: str) -> Any:
    current: Any = context
    for part in reference.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return REFERENCE_MISSING
    return current
