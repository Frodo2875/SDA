"""Chat messages with evidence, task, trace, Batch and HITL cards."""

from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend.components.confirmation_card import render_confirmation
from frontend.components.evidence_panel import render_evidence, source_labels
from frontend.components.task_progress import render_batch, render_task
from frontend.components.trace_panel import render_trace


COMPARISON_COLUMNS = {
    "student_id": "学号", "name": "姓名", "average_score": "平均成绩",
    "rank": "专业排名", "paper_count": "论文数", "patent_count": "专利数",
    "competition_count": "竞赛数", "score_status": "成绩状态",
    "research_status": "科研状态",
}
TOOL_STATUS_LABELS = {
    "search_student": "正在查询学生身份…",
    "get_student_info": "正在查询学生身份…",
    "get_student_scores": "正在查询数据…",
    "get_student_research": "正在查询数据…",
    "query_table": "正在查询数据…",
    "retrieve_document": "正在检索规则…",
    "find_cross_file_conflicts": "正在检查数据冲突…",
    "preview_word_diff": "正在生成 Diff…",
}


def tool_statuses(response: dict[str, Any]) -> list[str]:
    labels = []
    for call in response.get("tool_calls") or []:
        label = TOOL_STATUS_LABELS.get(call.get("name"))
        if label and label not in labels:
            labels.append(label)
        if int(call.get("retry_count") or 0) > 0 and "正在重试…" not in labels:
            labels.append("正在重试…")
    if response.get("status") == "confirmation_required":
        labels.append("正在生成 Diff…")
    return labels


def comparison_tables(response: dict[str, Any]) -> list[list[dict[str, Any]]]:
    tables = []
    for call in response.get("tool_calls") or []:
        if call.get("name") != "compare_students":
            continue
        students = ((call.get("result") or {}).get("data") or {}).get("students") or []
        if students:
            tables.append(
                [{COMPARISON_COLUMNS[key]: row.get(key) for key in COMPARISON_COLUMNS} for row in students]
            )
    return tables


def render_messages(
    messages: list[dict[str, Any]],
    on_action: Callable[[int, str], None],
    on_evidence: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    for index, message in enumerate(messages):
        avatar = "🎓" if message["role"] == "assistant" else "👤"
        with st.chat_message(message["role"], avatar=avatar):
            for status in message.get("statuses") or []:
                st.caption(f"✓ {status}")
            if message.get("error"):
                st.error(message["content"])
            else:
                st.markdown(message["content"])
            warnings = [*(message.get("display_warnings") or []),
                        *((message.get("evidence_quality") or {}).get("warnings") or []),
                        *(message.get("web_search_warnings") or [])]
            for warning in warnings:
                st.warning(str(warning.get("message") or warning) if isinstance(warning, dict) else str(warning))
            for table in message.get("comparison_tables") or []:
                st.dataframe(table, use_container_width=True, hide_index=True)
            render_task(message.get("task"))
            if message.get("evidence"):
                st.caption("来源：")
                for label in source_labels(message["evidence"]):
                    st.text(label)
                if st.button("查看本条回答证据", key=f"answer-sources-{index}"):
                    st.session_state.latest_evidence = message["evidence"]
                    st.session_state.selected_evidence_location = None
                    st.session_state.evidence_location_error = None
                    st.rerun()
            render_batch(message.get("batch"))
            render_evidence(
                message.get("evidence") or [],
                on_locate=on_evidence,
                key_prefix=f"message-evidence-{index}",
            )
            render_trace(message.get("traces") or [])
            if message.get("version_id"):
                st.success(f"新版本：{message['version_id']}")
            if message.get("pending_action"):
                render_confirmation(
                    message["pending_action"], key_prefix=f"message-{index}",
                    on_decision=lambda decision, current=index: on_action(current, decision),
                )
