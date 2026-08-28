"""FastAPI application entry point."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, FastAPI, File, Path, Query, UploadFile
from fastapi.responses import JSONResponse

from backend import database
from backend.agent import run_agent
from backend.llm_client import LLMAPIError, LLMConfigurationError
from backend.runtime.task_runner import resume_task as resume_workflow_task
from backend.runtime.async_task_runtime import (
    cancel_async_task,
    enqueue_async_task,
    resume_async_task,
    retry_async_task,
    run_async_task,
)
from backend.services.confirmation import (
    cancel_action,
    confirm_action,
    create_pending_delete_action,
    create_pending_undo_action,
    rollback_file,
)
from backend.services.file_versioning import list_versions, preview_word_diff
from backend.services.file_upload import save_uploaded_file
from backend.services.document_index import reindex_document, reprocess_document
from backend.services.batch_service import BatchArguments, get_batch, run_batch
from backend.services.file_view import get_file_preview, get_file_view, list_file_views
from backend.services.evidence_locator import locate_evidence
from backend.services.task_view import get_task_view, list_task_views
from backend.runtime.context_manager import get_context
from backend.schemas import (
    ActionResponse,
    AsyncTaskCreateRequest,
    ChatRequest,
    ChatResponse,
    CompareStudentsRequest,
    DeleteFileRequest,
    HealthResponse,
    ToolResponse,
    VersionActionRequest,
    WordDiffOperationRequest,
)
from backend.tools.analysis_tools import compare_students
from backend.tools.file_tools import list_files
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)


app = FastAPI(
    title="Student Document Agent",
    description="学生材料智能文档助手开发测试 API",
    version="0.1.0",
)


CLIENT_ERROR_CODES = {
    "DUPLICATE_STUDENT_IDS",
    "INVALID_QUERY",
    "INVALID_STUDENT_ID",
    "INVALID_STUDENT_IDS",
    "INVALID_DIFF_OPERATION",
    "INVALID_TARGET_FILE",
    "UNSUPPORTED_FILE_TYPE",
    "INVALID_EVIDENCE_ID",
}
NOT_FOUND_ERROR_CODES = {
    "FILE_NOT_FOUND", "STUDENT_NOT_FOUND", "VERSION_NOT_FOUND",
    "VERSION_FILE_MISSING",
    "BATCH_NOT_FOUND",
    "EVIDENCE_LOCATION_NOT_FOUND", "EVIDENCE_SOURCE_UNAVAILABLE",
    "EVIDENCE_PREVIEW_UNAVAILABLE",
}


def _tool_http_status(result: dict[str, Any]) -> int:
    """Map a failed tool result to an HTTP status without changing its body."""
    if result.get("ok"):
        return 200
    error_code = result.get("error_code")
    if error_code in NOT_FOUND_ERROR_CODES:
        return 404
    if error_code in CLIENT_ERROR_CODES:
        return 400
    return 500


def _call_tool(
    tool: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any
) -> dict[str, Any] | JSONResponse:
    """Call one existing tool and prevent internal exceptions from reaching clients."""
    try:
        result = tool(*args, **kwargs)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )

    status_code = _tool_http_status(result)
    if status_code != 200:
        return JSONResponse(status_code=status_code, content=result)
    return result


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Return the current service health status."""
    return HealthResponse(
        status="ok",
        project="Student Document Agent",
        version="0.1.0",
    )


@app.get("/api/files", response_model=ToolResponse, tags=["files"])
async def api_list_files(
    search: str | None = Query(default=None, max_length=255),
    file_type: Literal["excel", "word", "pdf", "image"] | None = Query(default=None),
    lifecycle_status: Literal[
        "uploaded", "detecting", "parsing", "visual_processing", "ocr_processing",
        "layout_processing", "indexing", "reprocessing", "reindexing", "queryable",
        "processing", "ready", "failed", "deleted", "cleanup_failed"
    ] | None = Query(default=None),
    sort_by: Literal["created_time", "size"] | None = Query(default=None),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
) -> dict[str, Any] | JSONResponse:
    """List compatible Tool fields plus V2 presentation metadata."""
    return _call_tool(
        list_file_views,
        search=search,
        file_type=file_type,
        lifecycle_status=lifecycle_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.get("/api/files/{file_id}", response_model=ToolResponse, tags=["files"])
async def api_file_workspace_details(
    file_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-fA-F]{32}$",
            description="稳定文件标识",
        ),
    ],
) -> dict[str, Any] | JSONResponse:
    """Read one file's workspace, lifecycle, parse and index metadata."""
    return _call_tool(get_file_view, file_id)


@app.get("/api/files/{file_id}/details", response_model=ToolResponse, tags=["files"])
async def api_file_details(file_id: str) -> dict[str, Any] | JSONResponse:
    """Read lifecycle, Schema summary and writable/version presentation metadata."""
    return _call_tool(get_file_view, file_id)


@app.get(
    "/api/files/{file_id}/preview", response_model=ToolResponse, tags=["files"]
)
async def api_file_preview(
    file_id: Annotated[
        str,
        Path(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$"),
    ],
) -> dict[str, Any] | JSONResponse:
    """Return a bounded read-only preview; never expose a client file path."""
    return _call_tool(get_file_preview, file_id)


@app.get(
    "/api/evidence/{evidence_id}/locate",
    response_model=ToolResponse,
    tags=["evidence"],
)
async def api_locate_evidence(
    evidence_id: Annotated[
        str,
        Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
    session_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict[str, Any] | JSONResponse:
    """Resolve one answer Evidence ID to a bounded, read-only source preview."""
    return _call_tool(locate_evidence, evidence_id, session_id=session_id)


@app.post(
    "/api/files/{file_id}/reprocess", response_model=ToolResponse, tags=["files"]
)
async def api_reprocess_document(
    file_id: Annotated[
        str,
        Path(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$"),
    ],
) -> dict[str, Any] | JSONResponse:
    """Call the V3.13 candidate parse and atomic activation pipeline."""
    return _call_tool(reprocess_document, file_id)


@app.post(
    "/api/files/{file_id}/reindex", response_model=ToolResponse, tags=["files"]
)
async def api_reindex_document(
    file_id: Annotated[
        str,
        Path(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$"),
    ],
) -> dict[str, Any] | JSONResponse:
    """Call the V3.13 candidate index and atomic active switch."""
    return _call_tool(reindex_document, file_id)


@app.post("/api/files/upload", response_model=ToolResponse, tags=["files"])
async def api_upload_file(file: UploadFile = File(...)) -> dict[str, Any] | JSONResponse:
    """Validate and store one supported document without overwriting."""
    try:
        content = await file.read()
        result = save_uploaded_file(
            file.filename or "",
            content,
            declared_mime_type=file.content_type,
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )
    finally:
        await file.close()

    if result["ok"]:
        return result
    status_code = {
        "INVALID_FILE_NAME": 400,
        "UNSUPPORTED_FILE_TYPE": 400,
        "INVALID_FILE_CONTENT": 400,
        "UNSUPPORTED_MIME_TYPE": 400,
        "UNSUPPORTED_IMAGE_FORMAT": 400,
        "IMAGE_SIZE_INVALID": 400,
        "OCR_NOT_SUPPORTED": 400,
        "EMPTY_UPLOAD": 400,
        "FILE_ALREADY_EXISTS": 409,
        "PDF_DEPENDENCY_MISSING": 503,
    }.get(result.get("error_code"), 500)
    return JSONResponse(status_code=status_code, content=result)


@app.post(
    "/api/files/{file_id}/delete",
    response_model=ActionResponse,
    tags=["files"],
)
async def api_prepare_file_delete(
    file_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-fA-F]{32}$",
            description="稳定文件标识",
        ),
    ],
    request: DeleteFileRequest,
) -> dict[str, Any] | JSONResponse:
    """Create a pending upload deletion; this endpoint never deletes directly."""
    try:
        result = create_pending_delete_action(
            session_id=request.session_id,
            file_id=file_id,
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )
    if result["ok"]:
        return result
    status_code = {
        "INVALID_FILE_ID": 400,
        "FILE_NOT_FOUND": 404,
        "FILE_DELETE_FORBIDDEN": 403,
        "FILE_ALREADY_DELETED": 409,
    }.get(result.get("error_code"), 500)
    return JSONResponse(status_code=status_code, content=result)


@app.get("/api/students/search", response_model=ToolResponse, tags=["students"])
async def api_search_student(
    q: str = Query(min_length=1, description="学生姓名或学号", examples=["S001"]),
) -> dict[str, Any] | JSONResponse:
    """Search a student through the existing identity tool."""
    return _call_tool(search_student, q)


@app.get("/api/students/{student_id}", response_model=ToolResponse, tags=["students"])
async def api_get_student(student_id: str) -> dict[str, Any] | JSONResponse:
    """Get basic student information through the existing student tool."""
    return _call_tool(get_student_info, student_id)


@app.get(
    "/api/students/{student_id}/scores",
    response_model=ToolResponse,
    tags=["students"],
)
async def api_get_student_scores(student_id: str) -> dict[str, Any] | JSONResponse:
    """Get student scores through the existing score tool."""
    return _call_tool(get_student_scores, student_id)


@app.get(
    "/api/students/{student_id}/research",
    response_model=ToolResponse,
    tags=["students"],
)
async def api_get_student_research(student_id: str) -> dict[str, Any] | JSONResponse:
    """Get student research records through the existing research tool."""
    return _call_tool(get_student_research, student_id)


@app.post("/api/students/compare", response_model=ToolResponse, tags=["students"])
async def api_compare_students(request: CompareStudentsRequest) -> dict[str, Any] | JSONResponse:
    """Compare students through the existing deterministic comparison tool."""
    return _call_tool(compare_students, request.student_ids)


@app.post("/api/chat", response_model=ChatResponse, tags=["agent"])
async def api_chat(request: ChatRequest) -> dict[str, Any] | JSONResponse:
    """Run one stateless natural-language request through the LLM tool loop."""
    try:
        return await run_agent(request.message, session_id=request.session_id)
    except LLMConfigurationError as exc:
        return JSONResponse(
            status_code=503,
            content={"answer": str(exc), "tool_calls": [], "status": "configuration_error"},
        )
    except LLMAPIError as exc:
        return JSONResponse(
            status_code=502,
            content={"answer": str(exc), "tool_calls": [], "status": "llm_api_error"},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"answer": "服务器内部错误", "tool_calls": [], "status": "error"},
        )


def _action_response(result: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Map confirmation-service results without exposing internal exceptions."""
    if result["ok"]:
        return result
    status_code = {
        "ACTION_NOT_FOUND": 404,
        "ACTION_NOT_PENDING": 409,
        "FILE_NOT_FOUND": 404,
        "VERSION_NOT_FOUND": 404,
        "NO_UNDO_VERSION": 409,
        "FILE_WRITE_FORBIDDEN": 403,
        "INVALID_DIFF_OPERATION": 400,
        "INVALID_TARGET_FILE": 400,
    }.get(result.get("error_code"), 500)
    return JSONResponse(status_code=status_code, content=result)


@app.post(
    "/api/actions/{action_id}/confirm",
    response_model=ActionResponse,
    tags=["actions"],
)
async def api_confirm_action(action_id: str) -> dict[str, Any] | JSONResponse:
    """Execute the exact frozen content of one pending action once."""
    try:
        return _action_response(confirm_action(action_id))
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )


@app.post(
    "/api/actions/{action_id}/cancel",
    response_model=ActionResponse,
    tags=["actions"],
)
async def api_cancel_action(action_id: str) -> dict[str, Any] | JSONResponse:
    """Cancel one pending action without modifying its target file."""
    try:
        return _action_response(cancel_action(action_id))
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )


@app.post("/api/files/{file_id}/diff", response_model=ToolResponse, tags=["versions"])
async def api_preview_word_diff(
    file_id: str, request: WordDiffOperationRequest
) -> dict[str, Any] | JSONResponse:
    """Preview one validated Word change without modifying the file."""
    return _call_tool(preview_word_diff, file_id, request.model_dump())


@app.get("/api/files/{file_id}/versions", response_model=ToolResponse, tags=["versions"])
async def api_list_file_versions(file_id: str) -> dict[str, Any] | JSONResponse:
    """List immutable versions for one stable file ID."""
    return _call_tool(list_versions, file_id)


@app.post(
    "/api/files/{file_id}/undo", response_model=ActionResponse, tags=["versions"]
)
async def api_prepare_file_undo(
    file_id: str, request: VersionActionRequest
) -> dict[str, Any] | JSONResponse:
    """Create a pending undo action; this endpoint never modifies the file."""
    try:
        return _action_response(
            create_pending_undo_action(session_id=request.session_id, file_id=file_id)
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False, "data": None, "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )


@app.post(
    "/api/files/{file_id}/rollback/{version_id}",
    response_model=ActionResponse,
    tags=["versions"],
)
async def api_prepare_file_rollback(
    file_id: str, version_id: str, request: VersionActionRequest
) -> dict[str, Any] | JSONResponse:
    """Create a pending rollback action; confirmation remains mandatory."""
    try:
        return _action_response(
            rollback_file(file_id, version_id, session_id=request.session_id)
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False, "data": None, "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )


@app.post("/api/batches", response_model=ToolResponse, tags=["batches"])
async def api_run_batch(request: BatchArguments) -> dict[str, Any]:
    """Run one validated Batch with persisted per-item progress."""
    return await run_batch(request)


@app.get("/api/batches/{batch_id}", response_model=ToolResponse, tags=["batches"])
async def api_get_batch(batch_id: str) -> dict[str, Any] | JSONResponse:
    """Return Batch counts, item outcomes, and partial-failure details."""
    return _call_tool(get_batch, batch_id)


@app.get("/api/tasks/{task_id}", response_model=ToolResponse, tags=["tasks"])
async def api_get_task(task_id: str) -> dict[str, Any] | JSONResponse:
    """Return observable Task/Step state without model reasoning content."""
    view = get_task_view(task_id)
    if view is None:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "data": None,
                "error_code": "TASK_NOT_FOUND",
                "message": "未找到指定任务",
            },
        )
    return {
        "ok": True,
        "data": view,
        "error_code": None,
        "message": "任务状态读取成功",
    }


@app.get("/api/sessions/{session_id}/tasks", response_model=ToolResponse, tags=["tasks"])
async def api_list_session_tasks(
    session_id: str, limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    """List persisted async and Workflow tasks for the current Task Center."""
    return {
        "ok": True,
        "data": list_task_views(session_id, limit),
        "error_code": None,
        "message": "Task Center 列表读取成功",
    }


@app.post("/api/tasks", response_model=ToolResponse, tags=["tasks"])
async def api_create_async_task(
    request: AsyncTaskCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any] | JSONResponse:
    """Queue one validated long task and execute it with the lightweight worker."""
    result = enqueue_async_task(request)
    if not result["ok"]:
        return JSONResponse(status_code=400, content=result)
    task_id = result["data"]["task"]["task_id"]
    background_tasks.add_task(run_async_task, task_id)
    return result


@app.post("/api/tasks/{task_id}/cancel", response_model=ToolResponse, tags=["tasks"])
async def api_cancel_async_task(task_id: str) -> dict[str, Any] | JSONResponse:
    result = cancel_async_task(task_id)
    if result["ok"]:
        return result
    return JSONResponse(
        status_code=404 if result.get("error_code") == "TASK_NOT_FOUND" else 409,
        content=result,
    )


@app.post("/api/tasks/{task_id}/retry", response_model=ToolResponse, tags=["tasks"])
async def api_retry_async_task(
    task_id: str, background_tasks: BackgroundTasks
) -> dict[str, Any] | JSONResponse:
    result = retry_async_task(task_id)
    if not result["ok"]:
        return JSONResponse(
            status_code=404 if result.get("error_code") == "TASK_NOT_FOUND" else 409,
            content=result,
        )
    background_tasks.add_task(run_async_task, task_id)
    return result


@app.get("/api/tasks/{task_id}/traces", response_model=ToolResponse, tags=["tasks"])
async def api_get_task_traces(task_id: str) -> dict[str, Any] | JSONResponse:
    """Return only observable, redacted Tool traces for one Task."""
    task = database.get_task_record(task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "data": None, "error_code": "TASK_NOT_FOUND", "message": "未找到指定任务"},
        )
    return {
        "ok": True,
        "data": database.get_task_trace_records(task_id),
        "error_code": None,
        "message": "Trace 读取成功",
    }


@app.get("/api/sessions/{session_id}/context", response_model=ToolResponse, tags=["sessions"])
async def api_get_session_context(session_id: str) -> dict[str, Any]:
    """Return bounded structured Context without chat history or hidden reasoning."""
    return {
        "ok": True,
        "data": get_context(session_id),
        "error_code": None,
        "message": "会话上下文读取成功",
    }


@app.get("/api/sessions/{session_id}/traces", response_model=ToolResponse, tags=["sessions"])
async def api_get_session_traces(
    session_id: str, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, Any]:
    """Return recent redacted Trace events, including lightweight tasks."""
    return {
        "ok": True,
        "data": database.get_session_trace_records(session_id, limit),
        "error_code": None,
        "message": "会话 Trace 读取成功",
    }


@app.post("/api/tasks/{task_id}/resume", response_model=ToolResponse, tags=["tasks"])
async def api_resume_task(
    task_id: str, background_tasks: BackgroundTasks
) -> dict[str, Any] | JSONResponse:
    """Resume a terminal HITL checkpoint without replaying successful reads."""
    try:
        task = database.get_task_record(task_id)
        is_async = task is not None and task.get("task_status") is not None
        result = resume_async_task(task_id) if is_async else resume_workflow_task(task_id)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "data": None,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
            },
        )
    if not result["ok"]:
        return JSONResponse(
            status_code=404 if result.get("error_code") == "TASK_NOT_FOUND" else 409,
            content={**result, "message": "任务无法恢复"},
        )
    if is_async:
        background_tasks.add_task(run_async_task, task_id)
    return {**result, "message": "任务恢复状态已更新"}
