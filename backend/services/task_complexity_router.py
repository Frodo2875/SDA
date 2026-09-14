"""Deterministic scheduling hints; never changes source or tool permissions."""

import re
from typing import Any

from backend.services.source_router import route_source


def route_task_complexity(query: str) -> dict[str, Any]:
    """Keep single lookups synchronous, including a simple Web lookup."""
    text = query.strip()
    source = route_source(text)["source_strategy"]
    reasons: list[str] = []
    report = bool(re.search(r"(?:生成|撰写|编写|制作|输出).{0,12}报告|(?:write|generate|produce)\s+(?:a\s+)?report", text, re.I))
    bulk = bool(re.search(r"(?:\d{2,}|[十百千万])\s*(?:份|个|篇|files|documents)|批量|所有材料|全部材料", text, re.I))
    operations = sum(word in text for word in ("分析", "比较", "对比", "总结", "检索", "汇总", "研究"))
    predicted_calls = 1 + operations + (2 if source == "BOTH" else 0) + (3 if bulk else 0)
    if report:
        reasons.append("EXPLICIT_REPORT")
    if bulk:
        reasons.append("BULK_INPUT")
    if source == "BOTH":
        reasons.append("CROSS_SOURCE")
    if predicted_calls >= 4 or (len(text) >= 500 and operations >= 2):
        reasons.append("MULTI_STEP")
    return {
        "async_required": bool(reasons), "reason_codes": reasons or ["SIMPLE_REQUEST"],
        "source_strategy": source, "predicted_tool_calls": predicted_calls,
    }
