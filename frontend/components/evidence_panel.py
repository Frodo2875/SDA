"""Evidence provenance display without exposing hidden reasoning."""

from typing import Any

import streamlit as st


def render_evidence(evidence: list[dict[str, Any]]) -> None:
    if not evidence:
        return
    with st.expander(f"数据来源（{len(evidence)}）", expanded=False):
        for item in evidence:
            location = []
            if item.get("sheet"):
                location.append(f"Sheet：{item['sheet']}")
            if item.get("field"):
                location.append(f"字段：{item['field']}")
            if item.get("page_no") is not None:
                location.append(f"页码：{item['page_no']}")
            if item.get("chunk_id"):
                location.append(f"Chunk：{item['chunk_id']}")
            if item.get("block_id"):
                location.append(f"Block：{item['block_id']}")
            if item.get("table"):
                location.append(f"表：{item['table']}")
            if item.get("cell"):
                location.append(f"单元格：{item['cell']}")
            if item.get("bbox"):
                location.append(f"BBox：{item['bbox']}")
            if item.get("confidence") is not None:
                location.append(f"置信度：{item['confidence']:.2f}")
            st.markdown(f"**{item.get('file_name') or item.get('file_id') or '未知文件'}**")
            st.caption(" · ".join(location) or "文件级证据")
            if item.get("value_summary"):
                st.write(item["value_summary"])
