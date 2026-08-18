"""Advanced redacted Trace display."""

from typing import Any

import streamlit as st


def render_trace(traces: list[dict[str, Any]]) -> None:
    if not traces:
        return
    with st.expander("高级 / 调试 Trace", expanded=False):
        rows = [
            {
                "Tool": item.get("tool_name") or item.get("event_type"),
                "耗时(ms)": item.get("duration_ms", 0),
                "状态": item.get("result_status"),
                "Retry": item.get("retry_count", 0),
                "错误": item.get("error_code") or "—",
                "结果摘要": item.get("result_summary") or "—",
            }
            for item in traces
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

