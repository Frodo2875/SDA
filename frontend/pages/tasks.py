"""Independent Research Task Center with a polling presentation fragment."""

import re

import streamlit as st
from frontend import api_client, research_center as center, controller as actions
from frontend.components.task_center import render_task_center
from frontend.components.evidence_panel import render_evidence_preview
from frontend.components.trace_panel import render_trace


def render() -> None:
    st.title("研究任务")
    st.subheader("研究任务中心")
    automatic = st.toggle("自动刷新（每 5 秒）", value=True, key="research-auto-refresh")
    st.caption("列表覆盖当前浏览器已知会话，每个会话最多读取最近 100 个任务；不是全局任务列表。")

    @st.fragment(run_every=5 if automatic else None)
    def live() -> None:
        render_center()

    live()
    with st.expander("原有 OCR / 索引 / Workflow 任务"):
        render_task_center(
            [view for view in st.session_state.known_tasks.values() if not center.is_research(view)],
            on_refresh=lambda: (actions.refresh_tasks(), st.rerun()),
            on_cancel=lambda task_id: actions.handle_task_operation(task_id, "cancel"),
            on_retry=lambda task_id: actions.handle_task_operation(task_id, "retry"),
            on_resume=lambda task_id: actions.handle_task_operation(task_id, "resume"),
        )


def render_center() -> None:
    if notice := st.session_state.pop("research_operation_notice", None):
        (st.success if notice[0] else st.error)(notice[1])
    identifier = st.session_state.get("research_selected_id")
    if identifier:
        if st.button("返回任务列表", key="research-back"):
            st.session_state.research_selected_id = None
            st.rerun()
        render_detail(identifier)
        return
    with st.form("research-open"):
        task_id = st.text_input("按 Task ID 打开已有研究任务", max_chars=128)
        if st.form_submit_button("打开任务"):
            if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id.strip()):
                st.session_state.research_selected_id = task_id.strip()
                st.rerun()
            else:
                st.error("请输入有效的 Task ID（字母、数字、下划线或连字符）。")
    st.button("刷新研究任务列表", key="research-center-refresh")
    tasks, errors = center.refresh_list()
    for error in errors:
        st.warning(error)
    selected = st.radio("任务状态", list(center.FILTERS), horizontal=True, key="research-filter")
    visible = center.filter_tasks(tasks, selected)
    if not visible:
        st.info("当前范围内没有符合筛选条件的研究任务。" if not errors else "任务列表暂不完整，请稍后刷新。")
    for task in visible:
        with st.container(border=True):
            st.text(task.get("query") or task["task_id"])
            st.caption(f"状态：{task['status']} · 当前阶段：{(task.get('progress') or {}).get('stage', '未提供')}")
            st.caption(f"创建：{task.get('created_at') or '未提供'} · 更新：{task.get('updated_at') or '未提供'}")
            if task.get("stale"):
                st.warning("当前为上次读取的状态，本次刷新失败。")
            if st.button("查看任务详情", key=f"research-open-{task['task_id']}"):
                st.session_state.research_selected_id = task["task_id"]
                st.rerun()


def render_detail(identifier: str) -> None:
    st.button("刷新任务详情", key="research-detail-refresh")
    try:
        task = api_client.get_research_task(identifier)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.session_state.setdefault("research_catalog", {})[identifier] = task
    st.subheader(task.get("query") or identifier)
    st.caption(f"Task ID：{identifier}")
    st.write(f"状态：{task['status']} · 当前阶段：{(task.get('progress') or {}).get('stage', '未提供')}")
    st.caption(f"创建：{task.get('created_at') or '未提供'} · 更新：{task.get('updated_at') or '未提供'}")
    if task.get("error"):
        st.error(task["error"].get("error_message") or "任务执行失败")
    if task.get("cancel_requested"):
        st.warning("取消请求已登记，等待任务在安全点停止。")
    try:
        view = api_client.get_task(identifier)
        capabilities = view.get("task") or {}
    except RuntimeError as exc:
        capabilities = {}
        st.warning(f"操作权限暂不可用：{exc}")
    columns = st.columns(3)
    for column, operation, label in zip(columns, ("cancel", "retry", "resume"), ("取消任务", "重试任务", "恢复任务")):
        if column.button(label, key=f"research-{operation}", disabled=not capabilities.get(f"can_{operation}", False)):
            center.apply_operation(identifier, operation)
            st.rerun()
    try:
        traces = api_client.get_traces(task_id=identifier)
    except RuntimeError as exc:
        traces = []
        st.warning(f"执行记录暂不可用：{exc}")
    st.markdown("#### 执行阶段")
    st.caption("展示实际记录的阶段和当前状态；Trace 为最近记录，未出现的阶段不代表已经执行。")
    for row in center.timeline(task, traces):
        st.text(row)
    local, web = center.evidence_counts(task)
    columns = st.columns(2)
    columns[0].metric("Local Evidence", local if local is not None else "—")
    columns[1].metric("Web Evidence", web if web is not None else "—")
    if local is None:
        st.caption("任务尚未返回结果，Evidence 数量暂不可用。")
    if st.checkbox("查看执行日志", key=f"research-trace-{identifier}"):
        if traces:
            render_trace(traces)
        else:
            st.info("暂无可显示的 Trace。")
    if st.checkbox("查看研究结果", key=f"research-result-{identifier}"):
        result = task.get("result")
        if not isinstance(result, dict):
            st.info("研究结果尚不可用。")
        else:
            st.markdown(result.get("answer") or "结果未包含正文。")
            for warning in [*((result.get("evidence_quality") or {}).get("warnings") or []), *(result.get("web_search_warnings") or [])]:
                st.warning(str(warning.get("message") or warning) if isinstance(warning, dict) else str(warning))
            def locate(evidence: dict) -> None:
                try:
                    st.session_state[f"research-location-{identifier}"] = api_client.locate_evidence(evidence["evidence_id"], task["session_id"])
                    st.session_state.pop(f"research-location-error-{identifier}", None)
                except RuntimeError as exc:
                    st.session_state.pop(f"research-location-{identifier}", None)
                    st.session_state[f"research-location-error-{identifier}"] = str(exc)
                st.rerun()
            render_evidence_preview(result.get("unified_evidence") or result.get("evidence") or [],
                                    on_locate=locate, location=st.session_state.get(f"research-location-{identifier}"),
                                    location_error=st.session_state.get(f"research-location-error-{identifier}"))
