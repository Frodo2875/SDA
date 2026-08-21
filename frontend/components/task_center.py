"""Session-scoped OCR, indexing, and Workflow task presentation."""

from collections.abc import Callable
from typing import Any

import streamlit as st


TASK_CATEGORIES = ("OCR任务", "索引任务", "Workflow任务")
STATUS_LABELS = {
    "created": "pending",
    "pending": "pending",
    "running": "running",
    "success": "success",
    "failed": "failed",
    "waiting_confirmation": "waiting_confirmation",
    "cancelled": "cancelled",
}
STATUS_ICONS = {
    "pending": "○",
    "running": "⏳",
    "success": "✅",
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
    return "Workflow任务"


def task_status(task_data: dict[str, Any]) -> str:
    task = task_data.get("task") or task_data
    raw = str(task.get("task_status") or task.get("status") or "pending").casefold()
    return STATUS_LABELS.get(raw, raw)


def task_center_groups(
    tasks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups = {category: [] for category in TASK_CATEGORIES}
    for task in tasks:
        groups[task_category(task)].append(task)
    return groups


def render_task_center(
    tasks: list[dict[str, Any]], *, on_refresh: Callable[[], None]
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
                progress = int(task.get("progress") or (100 if status == "success" else 0))
                st.markdown(
                    f"{STATUS_ICONS.get(status, '○')} **{task.get('task_type') or category}**"
                )
                st.caption(
                    f"{status} · {task.get('message') or task.get('next_action') or '—'}"
                )
                if status in {"pending", "running"} or progress:
                    st.progress(min(100, max(0, progress)) / 100, text=f"{progress}%")
                st.caption(f"Task ID：{task.get('task_id', '—')}")
