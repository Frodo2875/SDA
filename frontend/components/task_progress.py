"""User-readable Task and Batch progress components."""

from typing import Any

import streamlit as st


STEP_LABELS = {
    "inspect_excel": "正在识别文件结构…",
    "index_document": "正在解析并索引文档…",
    "query_table": "正在查询数据…",
    "aggregate_table": "正在统计数据…",
    "retrieve_document": "正在检索规则…",
    "find_cross_file_conflicts": "正在检查数据冲突…",
    "preview_word_diff": "正在生成 Diff…",
}


def render_task(task_data: dict[str, Any] | None) -> None:
    if not task_data:
        return
    task = task_data.get("task") or {}
    steps = task_data.get("steps") or []
    with st.expander("任务进度", expanded=task.get("status") in {"running", "waiting_confirmation"}):
        st.caption(f"状态：{task.get('status', 'unknown')} · 下一步：{task.get('next_action') or '已完成'}")
        for step in steps:
            label = STEP_LABELS.get(step.get("tool_name"), step.get("step_name") or "处理任务")
            retry = int(step.get("retry_count") or 0)
            if retry:
                label = f"正在重试… {label}（{retry}）"
            icon = {"success": "✅", "failed": "❌", "running": "⏳", "waiting_confirmation": "🟠"}.get(step.get("status"), "○")
            st.write(f"{icon} {label}")


def render_batch(batch: dict[str, Any] | None) -> None:
    if not batch:
        return
    summary = batch.get("summary") or {}
    total = int(summary.get("total") or batch.get("total") or 0)
    completed = sum(int(summary.get(key) or 0) for key in ("success", "failed", "skipped"))
    st.markdown("#### 批量任务进度")
    st.progress(completed / total if total else 0.0, text=f"{completed} / {total}")
    columns = st.columns(4)
    columns[0].metric("总数", total)
    columns[1].metric("成功", summary.get("success", 0))
    columns[2].metric("失败", summary.get("failed", 0))
    columns[3].metric("跳过", summary.get("skipped", 0))
    if batch.get("failure_details"):
        with st.expander("失败明细"):
            st.dataframe(batch["failure_details"], use_container_width=True, hide_index=True)

