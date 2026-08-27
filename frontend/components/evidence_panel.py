"""Clickable Evidence provenance without exposing hidden reasoning."""

from collections.abc import Callable
from html import escape
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


EvidenceHandler = Callable[[dict[str, Any]], None]


def render_evidence_preview(
    evidence: list[dict[str, Any]],
    *,
    location: dict[str, Any] | None = None,
    location_error: str | None = None,
    on_locate: EvidenceHandler | None = None,
) -> None:
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
            if item.get("evidence_id") and st.button(
                "打开原文",
                key=f"evidence-preview-{item['evidence_id']}-{index}",
                use_container_width=True,
                disabled=on_locate is None,
            ):
                on_locate(item)
    if location_error:
        st.warning(location_error)
    if location:
        render_location_preview(location)


def render_evidence(
    evidence: list[dict[str, Any]],
    *,
    on_locate: EvidenceHandler | None = None,
    key_prefix: str = "answer",
) -> None:
    if not evidence:
        return
    with st.expander(f"数据来源（{len(evidence)}）", expanded=False):
        for index, item in enumerate(evidence, start=1):
            st.markdown(f"**{item.get('file_name') or item.get('file_id') or '未知文件'}**")
            st.caption(" · ".join(evidence_location(item)) or "文件级证据")
            if item.get("value_summary"):
                st.write(item["value_summary"])
            if item.get("evidence_id") and st.button(
                "打开原文",
                key=f"{key_prefix}-{item['evidence_id']}-{index}",
                disabled=on_locate is None,
            ):
                on_locate(item)


def render_location_preview(location: dict[str, Any]) -> None:
    """Render the smallest stable highlight supported by current Streamlit UI."""
    location_type = location.get("location_type")
    with st.container(border=True):
        st.markdown(f"**原文预览：{location.get('file_name', '未知文件')}**")
        if location_type == "pdf":
            st.caption(
                f"PDF · 第 {location.get('page_no')} 页"
                + (f" · Block {location['block_id']}" if location.get("block_id") else "")
            )
            if location.get("bbox"):
                st.warning(f"高亮区域 BBox：{location['bbox']}")
            if location.get("confidence") is not None:
                st.caption(f"OCR置信度：{float(location['confidence']):.2f}")
            st.markdown(
                '<div style="border-left:4px solid #f2c94c;padding:.5rem '
                '1rem;background:#fff8d6"><mark>'
                f"{escape(str(location.get('text') or '（空）'))}</mark></div>",
                unsafe_allow_html=True,
            )
        elif location_type == "excel":
            st.caption(
                f"Excel · Sheet {location.get('sheet')} · Cell {location.get('cell_id')}"
            )
            headers = location.get("headers") or []
            values = location.get("row_values") or []
            st.dataframe(
                [{str(header): values[index] if index < len(values) else None
                  for index, header in enumerate(headers)}],
                use_container_width=True,
                hide_index=True,
            )
            st.success(
                f"目标记录：{location.get('record_identifier') or location.get('row_index')} · "
                f"字段：{location.get('field') or location.get('column_index')} · "
                f"值：{location.get('target_value')}"
            )
        elif location_type == "word":
            position = (
                f"段落 {location['paragraph_no']}"
                if location.get("paragraph_no") is not None
                else f"Block {location.get('block_id') or '—'}"
            )
            st.caption(f"Word · {position}")
            st.markdown(f"> {escape(str(location.get('text') or '（空）'))}")
