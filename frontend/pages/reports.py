"""Report presentation using saved backend reports and approved file exports."""

import re
from typing import Any

import streamlit as st
from frontend.presentation import label as status_label

from frontend import api_client, research_center
from frontend.components.confirmation_card import render_confirmation
from frontend.components.evidence_panel import render_evidence_preview


def valid_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))


def open_report(task_id: str, report_id: str) -> None:
    st.session_state.selected_report = (task_id, report_id)
    st.rerun()


def render() -> None:
    st.title("报告中心")
    if selected := st.session_state.get("selected_report"):
        if st.button("返回报告列表", key="report-back"):
            st.session_state.selected_report = None
            st.rerun()
        render_detail(*selected)
        return
    st.caption("显示本次浏览器会话中找到的报告。")
    with st.form("report-open"):
        task_id = st.text_input("任务编号", max_chars=128).strip()
        report_id = st.text_input("报告编号", max_chars=128).strip()
        if st.form_submit_button("打开已有报告"):
            if valid_id(task_id) and valid_id(report_id):
                open_report(task_id, report_id)
            else:
                st.error("请输入有效的任务 ID 和报告 ID。")
    tasks, errors = research_center.refresh_list()
    for error in errors:
        st.warning(error)
    with st.expander("创建报告"):
        completed = {task["task_id"]: task for task in tasks if task.get("status") == "COMPLETED" and not task.get("stale")}
        if completed:
            source = st.selectbox("来源任务", list(completed), format_func=lambda key: completed[key].get("query") or key)
            title = st.text_input("报告名称", value="证据研究报告", max_chars=200)
            if st.button("创建报告草稿", key="report-new"):
                try:
                    if not title.strip():
                        raise RuntimeError("请输入报告名称。")
                    report = api_client.create_report(source, title.strip())
                    open_report(source, report["report_id"])
                except RuntimeError as exc:
                    st.error(str(exc))
        else:
            st.caption("暂无已完成的研究任务可用于创建报告。")
    references = {(task["task_id"], report_id) for task in tasks
                  for report_id in ((task.get("result") or {}).get("reports") or {})}
    for session in [dict(messages=st.session_state.messages), *st.session_state.get("chat_history", {}).values()]:
        for message in session.get("messages", []):
            report = message.get("report")
            if report and message.get("research_task_id"):
                references.add((message["research_task_id"], report["report_id"]))
    if not references:
        st.info("暂无已发现的报告。")
    for task_id, report_id in sorted(references):
        try:
            report = api_client.get_report(task_id, report_id)
        except RuntimeError as exc:
            st.warning(f"报告 {report_id}：{exc}")
            continue
        with st.container(border=True):
            st.subheader(report["title"])
            st.caption(f"来源任务：{task_id} · 创建：{report['created_at']} · 状态：{status_label(report['status'])}")
            if st.button("查看 / 下载 / 版本", key=f"report-open-{task_id}-{report_id}"):
                open_report(task_id, report_id)


def decide(key: str, decision: str) -> None:
    action = st.session_state[key]
    try:
        result = api_client.decide_action(action["action_id"], decision)
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "操作失败")
        st.session_state[key] = {**action, "status": "executed" if decision == "confirm" else "cancelled"}
        st.session_state[f"{key}-notice"] = (True, "操作已确认执行。" if decision == "confirm" else "操作已取消。")
    except RuntimeError as exc:
        st.session_state[f"{key}-notice"] = (False, str(exc))
    st.rerun()


def render_detail(task_id: str, report_id: str) -> None:
    st.button("刷新报告", key="report-refresh")
    try:
        report = api_client.get_report(task_id, report_id)
        task = api_client.get_research_task(task_id)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.subheader(report["title"])
    st.caption(f"来源任务：{task_id} · 创建：{report['created_at']} · 状态：{status_label(report['status'])}")
    st.caption("预览为报告原稿，下载文件以当前导出版本为准。")
    st.markdown("## 任务说明")
    st.markdown(report.get("task_description") or "未提供")
    st.markdown("## 摘要")
    st.markdown(report["summary"])
    st.markdown("## 核心结论")
    for conclusion in report.get("conclusions", []):
        st.markdown(conclusion["text"])
        st.caption("引用编号：" + ", ".join(conclusion.get("evidence_refs") or []))
    for section in report.get("sections", []):
        st.subheader(section["title"])
        st.markdown(section["content"])
    for warning in report.get("warnings", []):
        st.warning(str(warning.get("message") or warning))
    location_key = f"report-location-{task_id}-{report_id}"
    def locate(evidence: dict[str, Any]) -> None:
        try:
            st.session_state[location_key] = api_client.locate_evidence(evidence["evidence_id"], task["session_id"])
            st.session_state.pop(f"{location_key}-error", None)
        except RuntimeError as exc:
            st.session_state.pop(location_key, None)
            st.session_state[f"{location_key}-error"] = str(exc)
        st.rerun()
    render_evidence_preview(report.get("evidence_refs") or [], on_locate=locate,
                            location=st.session_state.get(location_key), location_error=st.session_state.get(f"{location_key}-error"))
    action_key = f"report-action-{task_id}-{report_id}"
    for format, label in [("md", "Markdown"), ("docx", "Word")]:
        with st.expander(f"{label} 下载 / 版本", expanded=True):
            export = report.get("exports", {}).get(format) or {}
            if export.get("status") == "executed":
                if st.button(f"加载{label}下载", key=f"report-load-{format}"):
                    try:
                        content = api_client.download_report(task_id, report_id, format)
                        st.download_button(f"下载{label}", content, file_name=f"report-{report_id}.{format}",
                                           mime="text/markdown" if format == "md" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                           on_click="ignore", key=f"report-download-{format}")
                        if format == "md":
                            st.markdown(content.decode("utf-8"))
                    except (RuntimeError, UnicodeDecodeError) as exc:
                        st.error(str(exc))
            else:
                st.caption("尚未批准导出，暂不可下载。")
            if st.button(f"预览并申请{label}导出", key=f"report-preview-{format}"):
                try:
                    response = api_client.preview_report(task_id, report_id, format)
                    if not response.get("ok"):
                        raise RuntimeError(response.get("message") or "导出预览失败")
                    st.session_state[action_key] = response["data"]
                    st.session_state.pop(f"{action_key}-notice", None)
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
            if export.get("file_id"):
                render_versions(export["file_id"], format, task["session_id"], action_key)
    if notice := st.session_state.get(f"{action_key}-notice"):
        (st.success if notice[0] else st.error)(notice[1])
    if action := st.session_state.get(action_key):
        render_confirmation(action, key_prefix=action_key, on_decision=lambda decision: decide(action_key, decision))


def render_versions(file_id: str, format: str, session_id: str, action_key: str) -> None:
    st.markdown("#### 版本历史")
    try:
        versions = api_client.list_versions(file_id)
    except RuntimeError as exc:
        st.warning(str(exc))
        return
    if not versions:
        st.caption("暂无版本记录。")
    if format == "md":
        st.caption("仅 Word 支持恢复历史版本。")
    for version in reversed(versions):
        st.caption(f"v{version.get('version_number')} · {version.get('created_at')} · {status_label(version.get('status', '未提供'))}")
        if format == "docx" and st.button("恢复此版本", key=f"report-rollback-{version['version_id']}", disabled=version.get("status") != "available"):
            try:
                response = api_client.prepare_rollback(file_id, version["version_id"], session_id)
                if not response.get("ok"):
                    raise RuntimeError(response.get("message") or "恢复预览失败")
                st.session_state[action_key] = response["data"]
                st.session_state.pop(f"{action_key}-notice", None)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
