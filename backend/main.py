"""FastAPI application entry point."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from backend.agent import run_agent
from backend.llm_client import LLMAPIError, LLMConfigurationError
from backend.services.confirmation import cancel_action, confirm_action
from backend.schemas import (
    ActionResponse,
    ChatRequest,
    ChatResponse,
    CompareStudentsRequest,
    HealthResponse,
    ToolResponse,
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
}
NOT_FOUND_ERROR_CODES = {"FILE_NOT_FOUND", "STUDENT_NOT_FOUND"}


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


def _call_tool(tool: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any] | JSONResponse:
    """Call one existing tool and prevent internal exceptions from reaching clients."""
    try:
        result = tool(*args)
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
async def api_list_files() -> dict[str, Any] | JSONResponse:
    """List supported files through the existing file tool."""
    return _call_tool(list_files)


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
        return await run_agent(request.message)
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
