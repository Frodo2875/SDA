"""Read and present existing research tasks without introducing a runtime."""

import json
from typing import Any

import streamlit as st

from frontend import api_client

FILTERS = {"全部": None, "运行中": {"CREATED", "RUNNING", "WAITING_TOOL"},
           "已完成": {"COMPLETED"}, "失败": {"FAILED", "PARTIAL_SUCCESS"}}


def is_research(view: dict[str, Any]) -> bool:
    task = view.get("task") or view
    return task.get("task_type") in {"research", "async_research"}


def refresh_list() -> tuple[list[dict[str, Any]], list[str]]:
    sessions = {st.session_state.session_id, *st.session_state.get("chat_history", {}).keys()}
    identifiers = set(st.session_state.get("research_catalog", {}))
    errors = []
    for session in sorted(sessions):
        try:
            for view in api_client.list_tasks(session):
                task = view.get("task") or view
                if task.get("task_id"):
                    st.session_state.known_tasks[task["task_id"]] = view
                if is_research(view):
                    identifiers.add(task["task_id"])
        except RuntimeError as exc:
            errors.append(f"会话 {session} 的任务列表读取失败：{exc}")
    for messages in [st.session_state.messages, *[item.get("messages", []) for item in st.session_state.get("chat_history", {}).values()]]:
        identifiers.update(item["research_task_id"] for item in messages if item.get("research_task_id"))
    identifiers.update(key for key, view in st.session_state.known_tasks.items() if is_research(view))
    records = st.session_state.setdefault("research_catalog", {})
    for identifier in sorted(identifiers):
        try:
            records[identifier] = {**api_client.get_research_task(identifier), "stale": False}
        except RuntimeError as exc:
            if identifier in records:
                records[identifier] = {**records[identifier], "stale": True}
            errors.append(f"任务 {identifier} 状态读取失败：{exc}")
    return sorted(records.values(), key=lambda item: item.get("created_at") or "", reverse=True), errors


def filter_tasks(tasks: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    statuses = FILTERS[label]
    return [task for task in tasks if statuses is None or task.get("status") in statuses]


def evidence_counts(task: dict[str, Any]) -> tuple[int | None, int | None]:
    result = task.get("result")
    if not isinstance(result, dict):
        return None, None
    evidence = result.get("unified_evidence") or result.get("evidence") or []
    return (sum(item.get("source_type") in {"LOCAL", "structured", "unstructured"} for item in evidence),
            sum(item.get("source_type") in {"WEB", "URL"} for item in evidence))


def timeline(task: dict[str, Any], traces: list[dict[str, Any]]) -> list[str]:
    rows = [f"• CREATED · {task.get('created_at') or '时间未提供'}"]
    for trace in sorted(traces, key=lambda item: item.get("created_at") or ""):
        if trace.get("event_type") != "research_stage":
            continue
        try:
            metrics = json.loads(trace.get("metrics_json") or "{}")
        except (ValueError, TypeError):
            continue
        stage = metrics.get("stage") if isinstance(metrics, dict) else None
        if stage:
            rows.append(f"• {stage} · {trace.get('created_at') or '时间未提供'}")
    status = task.get("status", "UNKNOWN")
    stage = (task.get("progress") or {}).get("stage", status)
    rows.append(f"{'✓' if status == 'COMPLETED' else '→'} {stage} · 当前状态 {status}")
    return rows


def apply_operation(identifier: str, operation: str) -> None:
    try:
        task = api_client.research_task_action(identifier, operation)
        st.session_state.setdefault("research_catalog", {})[identifier] = task
        st.session_state.research_operation_notice = (True, "操作请求已提交，以下展示后端最新状态。")
    except RuntimeError as exc:
        st.session_state.research_operation_notice = (False, str(exc))
