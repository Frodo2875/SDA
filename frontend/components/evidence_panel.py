"""Clickable Evidence provenance without exposing hidden reasoning."""

import base64
from collections.abc import Callable
from html import escape
from typing import Any
from urllib.parse import urlsplit

import streamlit as st


def evidence_location(item: dict[str, Any]) -> list[str]:
    """Build an observable source locator shared by chat and workspace preview."""
    location = []
    metadata = item.get("metadata") or {}
    if metadata.get("heading_path"):
        location.append("Heading：" + " > ".join(str(value) for value in metadata["heading_path"]))
    line = item.get("line_number") or metadata.get("line_number")
    if line is not None:
        location.append(f"Line：{line}" + (f"-{metadata['line_end']}" if metadata.get("line_end") else ""))
    row = item.get("row_number") if item.get("row_number") is not None else item.get("row_index")
    if row is not None:
        location.append(f"Row：{row}" + (f"-{metadata['row_end']}" if metadata.get("row_end") is not None else ""))
    if item.get("locator_type"):
        location.append(f"类型：{item['locator_type']}")
    if item.get("page_no") is not None:
        location.append(f"Page：{item['page_no']}")
    if item.get("image_no") is not None:
        location.append(f"Image：{item['image_no']}")
    if item.get("region_id"):
        location.append(f"Region：{item['region_id']}")
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
    if item.get("handwriting_confidence") is not None:
        location.append(f"手写置信度：{float(item['handwriting_confidence']):.2f}")
    if item.get("review_required"):
        location.append("需要核对")
    if item.get("domain"):
        location.append(f"Domain：{item['domain']}")
    if item.get("publisher"):
        location.append(f"Publisher：{item['publisher']}")
    if item.get("published_at"):
        location.append(f"Published：{item['published_at']}")
    if item.get("retrieved_at"):
        location.append(f"Retrieved：{item['retrieved_at']}")
    if item.get("authority"):
        location.append(f"Authority：{item['authority']}")
    return location


def _evidence_label(item: dict[str, Any]) -> str:
    return str(
        item.get("title")
        or item.get("file_name")
        or item.get("domain")
        or item.get("file_id")
        or "未知来源"
    )


def _is_web_evidence(item: dict[str, Any]) -> bool:
    return item.get("source_type") in {"WEB", "URL"}


def web_url(item: dict[str, Any]) -> str | None:
    value = str(item.get("url") or "")
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def source_labels(evidence: list[dict[str, Any]]) -> list[str]:
    return [f"[{index}] {'Web 来源' if _is_web_evidence(item) else '本地文件'} · {_evidence_label(item)}"
            for index, item in enumerate(evidence, 1)]


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
        st.info("暂无证据。")
        return
    for index, item in enumerate(evidence, start=1):
        with st.container(border=True):
            st.markdown(
                f"**{index}. {_evidence_label(item)}**"
            )
            st.caption(" · ".join(evidence_location(item)) or "来源级证据")
            st.caption("Web Evidence" if _is_web_evidence(item) else "Local Evidence")
            if item.get("content") or item.get("value_summary"):
                st.write(item.get("content") or item["value_summary"])
            if _is_web_evidence(item):
                st.text(f"URL：{item.get('url') or '未提供'}")
                st.caption(f"Domain：{item.get('domain') or '未提供'} · Retrieved At：{item.get('retrieved_at') or '未提供'}")
                if url := web_url(item):
                    st.link_button("打开来源网页", url)
            if not _is_web_evidence(item) and item.get("evidence_id") and st.button(
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
            st.markdown(f"**{_evidence_label(item)}**")
            st.caption(" · ".join(evidence_location(item)) or "来源级证据")
            if item.get("content") or item.get("value_summary"):
                st.write(item.get("content") or item["value_summary"])
            if _is_web_evidence(item) and (url := web_url(item)):
                st.link_button("打开来源网页", url)
            if not _is_web_evidence(item) and item.get("evidence_id") and st.button(
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
            _render_visual_review(location)
        elif location_type == "image":
            st.caption(
                f"Image · 第 {location.get('image_no') or 1} 张"
                + (f" · Region {location['region_id']}" if location.get("region_id") else "")
            )
            preview = location.get("preview") or {}
            encoded = preview.get("content_base64")
            if encoded:
                st.image(
                    base64.b64decode(encoded),
                    caption=f"高亮区域 BBox：{location.get('bbox')}",
                    use_container_width=True,
                )
            if location.get("bbox"):
                st.warning(f"高亮区域 BBox：{location['bbox']}")
            if location.get("text"):
                st.markdown(f"> {escape(str(location['text']))}")
            _render_visual_review(location)
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


def _render_visual_review(location: dict[str, Any]) -> None:
    if location.get("handwriting_confidence") is not None:
        st.caption(
            f"手写识别置信度：{float(location['handwriting_confidence']):.2f}"
        )
    if location.get("review_required"):
        st.warning("该识别结果置信度较低或存在冲突，需要人工核对。")
    conflicts = location.get("conflict_sources") or []
    if conflicts:
        st.error("识别来源存在冲突，系统未静默选择任一结果。")
        st.json(conflicts)
