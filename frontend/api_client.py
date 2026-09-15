"""HTTP-only client for the FastAPI backend."""

import os
from typing import Any

import httpx


BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 60.0
CHAT_TIMEOUT = 120.0


def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    timeout = kwargs.pop("timeout", TIMEOUT)
    try:
        response = httpx.request(method, f"{BASE_URL}{path}", timeout=timeout, **kwargs)
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "后端处理超时；服务可能仍在运行，请查看 FastAPI 日志后再决定是否重试。"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError("无法连接后端服务，请确认 FastAPI 已在 8000 端口启动。") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("后端通信失败，请检查 FastAPI 日志和网络状态。") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("后端返回了无法识别的响应。") from exc
    if response.is_error:
        raise RuntimeError(str(payload.get("message") or payload.get("answer") or "请求处理失败"))
    return payload


def list_files(
    *,
    search: str | None = None,
    file_type: str | None = None,
    lifecycle_status: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    params = {
        key: value
        for key, value in {
            "search": search,
            "file_type": file_type,
            "lifecycle_status": lifecycle_status,
            "sort_by": sort_by,
            "sort_order": sort_order if sort_by else None,
        }.items()
        if value not in {None, ""}
    }
    result = request("GET", "/api/files", params=params)
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "文件列表读取失败")
    return result.get("data") or []


def get_file_detail(file_id: str) -> dict[str, Any]:
    result = request("GET", f"/api/files/{file_id}")
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "文件详情读取失败")
    return result.get("data") or {}


def get_file_preview(file_id: str) -> dict[str, Any]:
    result = request("GET", f"/api/files/{file_id}/preview")
    return result.get("data") or {}


def locate_evidence(evidence_id: str, session_id: str) -> dict[str, Any]:
    result = request(
        "GET",
        f"/api/evidence/{evidence_id}/locate",
        params={"session_id": session_id},
    )
    return result.get("data") or {}


def reprocess_file(file_id: str) -> dict[str, Any]:
    return request("POST", f"/api/files/{file_id}/reprocess")


def reindex_file(file_id: str) -> dict[str, Any]:
    return request("POST", f"/api/files/{file_id}/reindex")


def prepare_delete(file_id: str, session_id: str) -> dict[str, Any]:
    return request(
        "POST",
        f"/api/files/{file_id}/delete",
        json={"session_id": session_id},
    )


def upload_file(uploaded_file: Any) -> dict[str, Any]:
    return request(
        "POST", "/api/files/upload",
        files={"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type or "application/octet-stream")},
    )


def upload_files(uploaded_files: list[Any]) -> list[dict[str, Any]]:
    """Upload selected files independently and preserve each visible outcome."""
    outcomes = []
    for uploaded_file in uploaded_files:
        try:
            result = upload_file(uploaded_file)
            outcomes.append(
                {
                    "file_name": uploaded_file.name,
                    "status": "success",
                    "message": result.get("message") or "上传成功",
                    "data": result.get("data") or {},
                }
            )
        except RuntimeError as exc:
            outcomes.append(
                {
                    "file_name": uploaded_file.name,
                    "status": "failed",
                    "message": str(exc),
                    "data": {},
                }
            )
    return outcomes


def chat(session_id: str, message: str) -> dict[str, Any]:
    return request(
        "POST",
        "/api/chat",
        json={"session_id": session_id, "message": message},
        timeout=CHAT_TIMEOUT,
    )


def decide_action(action_id: str, decision: str) -> dict[str, Any]:
    return request("POST", f"/api/actions/{action_id}/{decision}")


def get_task(task_id: str) -> dict[str, Any]:
    return request("GET", f"/api/tasks/{task_id}").get("data") or {}


def list_tasks(session_id: str) -> list[dict[str, Any]]:
    return request(
        "GET", f"/api/sessions/{session_id}/tasks", params={"limit": 100}
    ).get("data") or []


def cancel_task(task_id: str) -> dict[str, Any]:
    return request("POST", f"/api/tasks/{task_id}/cancel")


def retry_task(task_id: str) -> dict[str, Any]:
    return request("POST", f"/api/tasks/{task_id}/retry")


def resume_task(task_id: str) -> dict[str, Any]:
    return request("POST", f"/api/tasks/{task_id}/resume")


def get_traces(*, task_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
    path = f"/api/tasks/{task_id}/traces" if task_id else f"/api/sessions/{session_id}/traces"
    return request("GET", path).get("data") or []


def list_versions(file_id: str) -> list[dict[str, Any]]:
    return request("GET", f"/api/files/{file_id}/versions").get("data") or []


def prepare_undo(file_id: str, session_id: str) -> dict[str, Any]:
    return request("POST", f"/api/files/{file_id}/undo", json={"session_id": session_id})


def prepare_rollback(file_id: str, version_id: str, session_id: str) -> dict[str, Any]:
    return request(
        "POST", f"/api/files/{file_id}/rollback/{version_id}",
        json={"session_id": session_id},
    )


def get_batch(batch_id: str) -> dict[str, Any]:
    return request("GET", f"/api/batches/{batch_id}").get("data") or {}


def knowledge_request(method: str, suffix: str = "", **kwargs: Any) -> Any:
    """Keep management API failures visible instead of returning empty success."""
    result = request(method, f"/api/knowledge-bases{suffix}", **kwargs)
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "知识库操作失败")
    return result["data"]


def list_knowledge_bases() -> list[dict[str, Any]]:
    return knowledge_request("GET")


def create_knowledge_base(name: str, description: str) -> dict[str, Any]:
    return knowledge_request("POST", json={"name": name, "description": description})


def get_knowledge_base(identifier: str) -> dict[str, Any]:
    return knowledge_request("GET", f"/{identifier}")


def list_knowledge_files(identifier: str) -> list[dict[str, Any]]:
    return knowledge_request("GET", f"/{identifier}/files")


def attach_knowledge_file(identifier: str, file_id: str) -> dict[str, Any]:
    return knowledge_request("POST", f"/{identifier}/files", json={"file_id": file_id})


def upload_knowledge_file(identifier: str, uploaded_file: Any) -> dict[str, Any]:
    return knowledge_request("POST", f"/{identifier}/upload", files={
        "file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type or "application/octet-stream")
    })


def create_research_task(session_id: str, query: str) -> dict[str, Any]:
    return request("POST", "/api/research-task", json={"session_id": session_id, "query": query})


def get_research_task(task_id: str) -> dict[str, Any]:
    return request("GET", f"/api/research-task/{task_id}")


def create_report(task_id: str, title: str) -> dict[str, Any]:
    result = request("POST", f"/api/research-task/{task_id}/reports", json={"title": title})
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "报告生成失败")
    return result["data"]


def preview_report(task_id: str, report_id: str, format: str) -> dict[str, Any]:
    return request("POST", f"/api/research-task/{task_id}/reports/{report_id}/preview", json={"format": format})
