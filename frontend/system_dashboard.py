"""Read-only status snapshots from existing public APIs; no configuration access."""

from datetime import datetime, timezone
import re
from typing import Any

import streamlit as st

from frontend import api_client
from frontend.presentation import label as status_label


def collect_snapshot(sessions: set[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"backend": "Unavailable", "version": "未提供", "errors": [],
                                "counts": dict.fromkeys(["文件", "知识库", "研究任务", "报告", "Evidence"]),
                                "processing": None, "failed": None, "indexed": None,
                                "time": datetime.now(timezone.utc).isoformat()}
    try:
        health = api_client.request("GET", "/health", timeout=5)
        snapshot["backend"] = "Online" if health.get("status") == "ok" else "Unavailable"
        version = health.get("version")
        if isinstance(version, str) and re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", version):
            snapshot["version"] = version
    except RuntimeError:
        snapshot["errors"].append("健康检查失败，请确认后端服务可用。")
    try:
        files = api_client.list_files()
        snapshot["counts"]["文件"] = len(files)
        statuses = [str(item.get("canonical_status") or item.get("lifecycle_status") or "").lower() for item in files]
        snapshot["failed"] = sum(status in {"failed", "cleanup_failed"} for status in statuses)
        snapshot["processing"] = sum(status in {"processing", "detecting", "parsing", "indexing", "reprocessing", "reindexing", "visual_processing", "ocr_processing", "layout_processing"} for status in statuses)
        snapshot["indexed"] = sum(item.get("index_status") == "indexed" for item in files)
    except RuntimeError:
        snapshot["errors"].append("文件统计暂不可用。")
    try:
        snapshot["counts"]["知识库"] = len(api_client.list_knowledge_bases())
    except RuntimeError:
        snapshot["errors"].append("知识库统计暂不可用。")
    try:
        ids = {task["task_id"] for session in sessions for view in api_client.list_tasks(session)
               for task in [view.get("task") or view] if task.get("task_type") in {"research", "async_research"}}
        reports: set[tuple[str, str]] = set()
        evidence: set[str] = set()
        for identifier in ids:
            result = api_client.get_research_task(identifier).get("result") or {}
            reports.update((identifier, report_id) for report_id in result.get("reports", {}))
            evidence.update(item["evidence_id"] for item in (result.get("unified_evidence") or result.get("evidence") or []) if item.get("evidence_id"))
        snapshot["counts"].update({"研究任务": len(ids), "报告": len(reports), "Evidence": len(evidence)})
    except RuntimeError:
        snapshot["errors"].append("研究任务及结果统计不完整，暂不显示数量。")
    return snapshot


def render_status() -> None:
    st.markdown("### 系统状态")
    if st.button("刷新系统状态与统计", key="system-refresh"):
        sessions = {st.session_state.session_id, *st.session_state.get("chat_history", {}).keys()}
        st.session_state.system_snapshot = collect_snapshot(sessions)
    snapshot = st.session_state.get("system_snapshot") or {}
    columns = st.columns(4)
    for column, label, status in zip(columns,
                                    ["服务连接", "文档处理", "联网搜索", "智能模型"],
                                    [status_label(snapshot.get("backend", "未检查")), "暂未检测", "暂未检测", "暂未检测"]):
        with column, st.container(border=True):
            st.markdown(f"**{label}**")
            st.text(status)
    with st.expander("系统详情"):
        st.caption(f"服务版本：{snapshot.get('version', '未提供')}")
        st.caption("搜索服务：Tavily。模型、文档搜索及处理服务暂不提供单独的连接检测。")
    for error in snapshot.get("errors", []):
        st.warning(error)
    if snapshot:
        st.caption(f"更新时间（UTC）：{snapshot['time']}")
        st.caption(f"处理中：{display(snapshot.get('processing'))} · 失败：{display(snapshot.get('failed'))} · 可查询：{display(snapshot.get('indexed'))}")
    render_counts(snapshot)


def display(value: int | None) -> str:
    return "—" if value is None else str(value)


def render_counts(snapshot: dict[str, Any]) -> None:
    st.markdown("### 资源统计")
    columns = st.columns(5)
    for column, label in zip(columns, ["文件", "知识库", "研究任务", "报告", "Evidence"]):
        with column, st.container(border=True):
            st.text(f"{'引用资料' if label == 'Evidence' else label}：{display(snapshot.get('counts', {}).get(label))}")
    with st.expander("统计范围"):
        st.caption("文件和知识库按当前可用列表统计；任务、报告和引用资料仅包含本次浏览器会话中找到的记录，每段聊天最多最近 100 个任务。")
