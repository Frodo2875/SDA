"""Evidence provenance display without exposing hidden reasoning."""

from typing import Any

import streamlit as st


def evidence_location(item: dict[str, Any]) -> list[str]:
    """Build an observable source locator shared by chat and workspace preview."""
    location = []
    if item.get("page_no") is not None:
        location.append(f"Page：{item['page_no']}")
    if item.get("block_id"):
        location.append(f"Block：{item['block_id']}")
    if item.get("table"):
        location.append(f"Table：{item['table']}")
    if item.get("cell"):
        location.append(f"Cell：{item['cell']}")
    if item.get("bbox"):
        location.append(f"BBox：{item['bbox']}")
    if item.get("sheet"):
        location.append(f"Sheet：{item['sheet']}")
    if item.get("field"):
        location.append(f"字段：{item['field']}")
    if item.get("chunk_id"):
        location.append(f"Chunk：{item['chunk_id']}")
    if item.get("confidence") is not None:
        location.append(f"置信度：{float(item['confidence']):.2f}")
    return location


def render_evidence_preview(evidence: list[dict[str, Any]]) -> None:
    st.subheader("Evidence Preview")
    st.caption("回答引用的 file / page / block / cell / bbox")
    if not evidence:
        st.info("当前回答暂无引用。")
        return
    for index, item in enumerate(evidence, start=1):
        with st.container(border=True):
            st.markdown(
                f"**{index}. {item.get('file_name') or item.get('file_id') or '未知文件'}**"
            )
            st.caption(" · ".join(evidence_location(item)) or "文件级证据")
            if item.get("value_summary"):
                st.write(item["value_summary"])


def render_evidence(evidence: list[dict[str, Any]]) -> None:
    if not evidence:
        return
    with st.expander(f"数据来源（{len(evidence)}）", expanded=False):
        for item in evidence:
            st.markdown(f"**{item.get('file_name') or item.get('file_id') or '未知文件'}**")
            st.caption(" · ".join(evidence_location(item)) or "文件级证据")
            if item.get("value_summary"):
                st.write(item["value_summary"])
