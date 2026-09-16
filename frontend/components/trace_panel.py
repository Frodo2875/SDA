"""Advanced redacted Trace display."""

import json
from typing import Any

import streamlit as st


def render_trace(traces: list[dict[str, Any]]) -> None:
    if not traces:
        return
    with st.expander("详细处理记录", expanded=False):
        rows = []
        for item in traces:
            try:
                metrics = json.loads(item.get("metrics_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            rows.append({
                "Tool": item.get("tool_name") or item.get("event_type"),
                "耗时(ms)": item.get("duration_ms", 0),
                "状态": item.get("result_status"),
                "Retry": item.get("retry_count", 0),
                "Token": item.get("total_tokens", 0),
                "Cost(USD)": item.get("cost_usd", 0),
                "Retrieval": metrics.get("retrieval_mode") or "—",
                "错误": item.get("error_code") or "—",
                "结果摘要": item.get("result_summary") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
