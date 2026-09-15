"""Persistent knowledge organization using the existing document controls."""

from typing import Any

import streamlit as st

from frontend import api_client, controller
from frontend.chat_workspace import new_conversation
from frontend.components.file_panel import (
    FILE_TYPE_FILTERS, SUPPORTED_UPLOAD_EXTENSIONS, filter_files_by_lifecycle,
    format_file_size, render_file_panel,
)


def file_status(item: dict[str, Any]) -> str:
    lifecycle = str(item.get("canonical_status") or item.get("lifecycle_status") or "").lower()
    if lifecycle in {"failed", "cleanup_failed"} or str(item.get("index_status")).lower() == "failed":
        return "Failed"
    if lifecycle == "uploaded":
        return "Uploaded"
    if lifecycle in {"processing", "detecting", "parsing", "indexing", "reprocessing", "reindexing",
                     "visual_processing", "ocr_processing", "layout_processing"}:
        return "Processing"
    if item.get("queryable") or str(item.get("index_status")).lower() == "indexed":
        return "Indexed"
    return "Processing"


def filtered_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = filter_files_by_lifecycle(files, st.session_state.workspace_lifecycle)
    search = st.session_state.workspace_search.casefold()
    file_type = FILE_TYPE_FILTERS[st.session_state.workspace_file_type]
    selected = [item for item in selected if search in item.get("file_name", "").casefold()
                and (not file_type or item.get("file_type") == file_type)]
    order = st.session_state.workspace_sort
    field = "size" if "大小" in order else "created_time"
    return sorted(selected, key=lambda item: item.get(field) or (0 if field == "size" else ""),
                  reverse=order in {"文件大小（大到小）", "登记时间（新到旧）"})


def upload_files(identifier: str, files: list[Any]) -> None:
    outcomes = []
    with st.spinner("正在通过 Document Pipeline 上传文件…"):
        for file in files:
            try:
                api_client.upload_knowledge_file(identifier, file)
                outcomes.append({"file_name": file.name, "status": "success", "message": "已上传并加入知识库"})
            except RuntimeError as exc:
                outcomes.append({"file_name": file.name, "status": "failed", "message": str(exc)})
    st.session_state[f"knowledge-upload-{identifier}"] = outcomes
    st.session_state.uploader_version += 1
    controller.refresh_files()
    st.rerun()


def render() -> None:
    st.title("知识库")
    if notice := st.session_state.pop("knowledge_notice", None):
        st.success(notice)
    identifier = st.session_state.get("selected_knowledge_base_id")
    if identifier:
        if st.button("返回知识库列表", key="knowledge-back"):
            st.session_state.selected_knowledge_base_id = None
            st.rerun()
        try:
            record = api_client.get_knowledge_base(identifier)
            files = api_client.list_knowledge_files(identifier)
        except RuntimeError as exc:
            st.error(str(exc))
            return
        render_detail(record, files)
        return
    with st.form("create-knowledge"):
        st.subheader("新建知识库")
        name = st.text_input("名称", max_chars=100, key="knowledge-name")
        description = st.text_area("描述", max_chars=2000, key="knowledge-description")
        if st.form_submit_button("创建", type="primary"):
            if not name.strip():
                st.error("请输入知识库名称")
            else:
                try:
                    api_client.create_knowledge_base(name.strip(), description.strip())
                    st.session_state.knowledge_notice = "知识库创建成功"
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
    try:
        records = api_client.list_knowledge_bases()
        st.session_state.knowledge_count = len(records)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.subheader("我的知识库")
    if not records:
        st.info("暂无知识库，请先创建。")
    for record in records:
        with st.container(border=True):
            st.subheader(record["name"])
            st.text(record["description"])
            st.caption(f"文件：{record['file_count']} · 状态：{record['status']}")
            st.caption(f"创建：{record['created_at']} · 更新：{record['updated_at']}")
            if st.button("进入", key=f"knowledge-enter-{record['knowledge_base_id']}"):
                st.session_state.selected_knowledge_base_id = record["knowledge_base_id"]
                st.rerun()


def render_detail(record: dict[str, Any], files: list[dict[str, Any]]) -> None:
    identifier = record["knowledge_base_id"]
    st.subheader(record["name"])
    st.text(record["description"])
    st.metric("文件数量", record["file_count"])
    st.caption(f"创建：{record['created_at']} · 更新：{record['updated_at']} · 状态：{record['status']}")
    if st.button("新建聊天", key="knowledge-chat"):
        new_conversation(identifier, record["name"])
        st.session_state.active_page = "chat"
        st.rerun()
    st.caption("聊天入口携带知识库上下文；本阶段尚未限定检索范围。")
    with st.expander("加入已有文件"):
        try:
            members = {item["file_id"] for item in files}
            available = {item["file_id"]: item for item in api_client.list_files() if item["file_id"] not in members}
            if available:
                selected = st.selectbox("已有文件", list(available), format_func=lambda key: available[key]["file_name"])
                if st.button("加入知识库", key="knowledge-attach"):
                    api_client.attach_knowledge_file(identifier, selected)
                    st.session_state.knowledge_notice = "文件已加入知识库"
                    st.rerun()
            else:
                st.caption("没有可加入的已有文件。")
        except RuntimeError as exc:
            st.error(str(exc))
    st.caption("文件可被多个知识库引用。删除文件需原有审批确认，并影响所有引用该文件的知识库。")
    if files:
        st.dataframe([{"文件名": item["file_name"], "类型": item.get("file_type", ""),
                       "大小": format_file_size(item.get("size")), "状态": file_status(item),
                       "更新时间": item.get("updated_at") or item.get("created_time") or "—"}
                      for item in files], use_container_width=True)
    st.caption("上传支持原有格式及 Markdown（.md / .markdown）。")
    render_file_panel(
        files=filtered_files(files), session_id=st.session_state.session_id,
        uploader_version=st.session_state.uploader_version,
        upload_notice=st.session_state.get(f"knowledge-upload-{identifier}"), files_error=None,
        on_upload=lambda uploaded: upload_files(identifier, uploaded),
        on_refresh=lambda: (controller.refresh_files(), st.rerun()),
        on_version_action=controller.add_version_action,
        upload_extensions=[*SUPPORTED_UPLOAD_EXTENSIONS, "md", "markdown"],
        uploader_key=f"knowledge-uploader-{identifier}-{st.session_state.uploader_version}",
    )
