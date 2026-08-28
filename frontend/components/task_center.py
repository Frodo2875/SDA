"""Session-scoped OCR, indexing, and Workflow task presentation."""

from collections.abc import Callable
from typing import Any

import streamlit as st


TASK_CATEGORIES = ("OCR任务", "索引任务", "Batch任务", "Workflow任务")
STATUS_LABELS = {
    "created": "pending",
    "queued": "queued",
    "pending": "pending",
    "running": "running",
    "paused": "paused",
    "success": "success",
    "partial_success": "partial_success",
    "failed": "failed",
    "waiting_confirmation": "waiting_confirmation",
    "cancelled": "cancelled",
}
STATUS_ICONS = {
    "pending": "○",
    "queued": "○",
    "running": "⏳",
    "paused": "⏸️",
    "success": "✅",
    "partial_success": "⚠️",
    "failed": "❌",
    "waiting_confirmation": "🟠",
    "cancelled": "⊘",
}


def task_category(task_data: dict[str, Any]) -> str:
    task = task_data.get("task") or task_data
    task_type = str(task.get("task_type") or "").casefold()
    if "ocr" in task_type:
        return "OCR任务"
    if "index" in task_type:
        return "索引任务"
    if "layout" in task_type:
        return "索引任务"
    if "batch" in task_type:
        return "Batch任务"
    return "Workflow任务"


def task_status(task_data: dict[str, Any]) -> str:
    task = task_data.get("task") or task_data
    raw = str(
        task.get("display_status") or task.get("task_status")
        or task.get("status") or "pending"
    ).casefold()
    return STATUS_LABELS.get(raw, raw)


def task_center_groups(
    tasks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups = {category: [] for category in TASK_CATEGORIES}
    for task in tasks:
        groups[task_category(task)].append(task)
    return groups


def render_task_center(
    tasks: list[dict[str, Any]], *, on_refresh: Callable[[], None],
    on_cancel: Callable[[str], None] | None = None,
    on_retry: Callable[[str], None] | None = None,
    on_resume: Callable[[str], None] | None = None,
) -> None:
    heading, refresh = st.columns([3, 1])
    heading.subheader("Task Center")
    if refresh.button("刷新", key="refresh-task-center", use_container_width=True):
        on_refresh()
    st.caption("当前浏览器会话中的 OCR、索引与 Workflow 任务")
    for category, items in task_center_groups(tasks).items():
        with st.expander(f"{category}（{len(items)}）", expanded=bool(items)):
            if not items:
                st.caption("暂无任务")
                continue
            for task_data in reversed(items):
                task = task_data.get("task") or task_data
                status = task_status(task_data)
                st.markdown(
                    f"{STATUS_ICONS.get(status, '○')} **{task.get('task_summary') or task.get('task_type') or category}**"
                )
                st.caption(
                    f"{status} · {task.get('message') or task.get('next_action') or '—'}"
                )
                document = task.get("document") or {}
                if document:
                    st.caption(
                        f"文档：{document.get('file_name', '—')} · "
                        f"类型：{document.get('file_type', '—')}"
                    )
                progress_detail = task.get("progress_detail") or {}
                progress_text = format_progress(progress_detail)
                if progress_detail.get("percent") is not None:
                    percent = max(0, min(100, int(progress_detail["percent"])))
                    st.progress(percent / 100, text=progress_text)
                elif progress_text:
                    st.caption(f"当前阶段：{progress_text}")
                st.caption(f"Task ID：{task.get('task_id', '—')}")
                current = task.get("current_node") or {}
                st.caption(
                    f"当前 Step/Node：{current.get('name') or task.get('next_action') or '—'}"
                )
                st.caption(
                    f"开始时间：{task.get('started_at') or '尚未开始'} · "
                    f"耗时：{format_duration(task.get('duration_ms'))}"
                )
                st.caption(
                    f"成功：{int(task.get('success_count') or 0)} · "
                    f"失败：{int(task.get('failed_count') or 0)} · "
                    f"跳过：{int(task.get('skipped_count') or 0)}"
                )
                error = task.get("error_summary") or {}
                if error:
                    st.error(
                        f"{error.get('error_code') or 'TASK_FAILED'}："
                        f"{error.get('message') or '任务失败'}"
                    )
                if task.get("cancel_requested"):
                    st.warning("取消请求已登记，任务将在安全点停止。")
                controls = st.columns(4)
                task_id = str(task.get("task_id") or "")
                if controls[0].button(
                    "取消", key=f"task-cancel-{task_id}",
                    disabled=not task.get("can_cancel") or on_cancel is None,
                ):
                    on_cancel(task_id)
                if controls[1].button(
                    "重试", key=f"task-retry-{task_id}",
                    disabled=not task.get("can_retry") or on_retry is None,
                ):
                    on_retry(task_id)
                if controls[2].button(
                    "恢复", key=f"task-resume-{task_id}",
                    disabled=not task.get("can_resume") or on_resume is None,
                ):
                    on_resume(task_id)
                detail_key = f"task-detail-open-{task_id}"
                if controls[3].button("查看详情", key=f"task-detail-{task_id}"):
                    st.session_state[detail_key] = not st.session_state.get(detail_key, False)
                if st.session_state.get(detail_key, False):
                    _render_task_detail(task_data)


def format_progress(detail: dict[str, Any]) -> str:
    if detail.get("unit") == "pages":
        return f"{int(detail.get('processed_pages') or 0)} / {int(detail.get('total_pages') or 0)} 页"
    if detail.get("unit") == "items":
        return f"{int(detail.get('processed_items') or 0)} / {int(detail.get('total_items') or 0)} 项"
    if detail.get("unit") == "nodes":
        return f"{int(detail.get('completed_nodes') or 0)} / {int(detail.get('total_nodes') or 0)} Node"
    return str(detail.get("stage") or "")


def format_duration(duration_ms: Any) -> str:
    if duration_ms is None:
        return "—"
    seconds = max(0, int(duration_ms)) / 1000
    return f"{seconds:.1f}s"


def _render_task_detail(task_data: dict[str, Any]) -> None:
    steps = task_data.get("steps") or []
    if steps:
        st.dataframe(
            [
                {
                    "step/node": step.get("step_name"),
                    "status": step.get("status"),
                    "retry": step.get("retry_count", 0),
                    "error": step.get("failed_reason"),
                }
                for step in steps
            ],
            use_container_width=True,
            hide_index=True,
        )
    batch = task_data.get("batch") or {}
    if batch.get("failure_details"):
        st.dataframe(batch["failure_details"], use_container_width=True, hide_index=True)
