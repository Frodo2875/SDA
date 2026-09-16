"""The same explicit, two-phase document controls in every chat mode."""

from typing import Any

import streamlit as st

from frontend import api_client, controller


def _completed(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant" or message.get("error") or message.get("pending_action"):
        return False
    if message.get("research_task_id"):
        task = message.get("research") or {}
        return task.get("status") == "COMPLETED" and (task.get("result") or {}).get("status") == "completed"
    return message.get("response_status") == "completed"


def render_document_actions() -> None:
    with st.expander("文档操作"):
        st.caption("先预览，再确认。取消不会修改文件。")
        operation = st.selectbox("操作", ["保存回答", "删除文件", "撤销最近修改", "恢复历史版本"], key="document-operation")
        files = st.session_state.get("files") or []
        if st.button("刷新文件", key="document-refresh"):
            controller.refresh_files()
            st.rerun()
        try:
            if operation == "保存回答":
                messages = st.session_state.messages
                choices = [index for index, message in enumerate(messages) if _completed(message)]
                if not choices:
                    st.caption("回答完成后，可以在这里编辑并保存。")
                    return
                index = st.selectbox("选择回答", choices, index=len(choices) - 1,
                                     format_func=lambda i: messages[i]["content"][:60], key="document-answer")
                message = messages[index]
                content = st.text_area("待保存内容", value=message["content"], height=200,
                                       key=f"document-content-{st.session_state.session_id}-{index}")
                targets = {None: "新建文档", **{item["file_id"]: item["file_name"] for item in files
                            if item.get("writable") and item.get("file_type") == "word" and item.get("lifecycle_status") == "ready"}}
                file_id = st.selectbox("保存位置", list(targets), format_func=targets.get, key="document-target")
                format = st.selectbox("文件格式", ["docx", "md"], key="document-format") if file_id is None else "docx"
                if file_id:
                    st.caption("内容追加到文档末尾。")
                if st.button("预览保存", key="document-preview", disabled=not content.strip()):
                    result = api_client.preview_document_draft(session_id=st.session_state.session_id,
                        source_answer=message["content"], content=content,
                        research_task_id=message.get("research_task_id"), file_id=file_id, format=format)
                    controller.add_version_action(result)
            else:
                eligible = {item["file_id"]: item["file_name"] for item in files if item.get("file_id") and (
                    item.get("deletable") and item.get("source_type") == "upload" if operation == "删除文件"
                    else item.get("writable") and item.get("file_type") == "word")}
                if not eligible:
                    st.caption("暂无可操作文件。")
                    return
                file_id = st.selectbox("目标文件", list(eligible), format_func=eligible.get, key="document-manage-target")
                version_id = None
                if operation == "恢复历史版本":
                    versions = api_client.list_versions(file_id)
                    options = {item["version_id"]: f"版本 {item['version_number']} · {item.get('created_at', '')}" for item in versions}
                    if not options:
                        st.caption("暂无历史版本。")
                        return
                    version_id = st.selectbox("恢复到", list(options), format_func=options.get, key="document-version")
                if st.button("预览操作", key="document-manage-preview"):
                    session = st.session_state.session_id
                    result = (api_client.prepare_delete(file_id, session) if operation == "删除文件" else
                              api_client.prepare_undo(file_id, session) if operation == "撤销最近修改" else
                              api_client.prepare_rollback(file_id, version_id, session))
                    controller.add_version_action(result)
        except RuntimeError as exc:
            st.error(str(exc))
