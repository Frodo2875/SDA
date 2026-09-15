"""Report endpoints consume saved research results; approval uses existing APIs."""

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.services.evidence_report import (
    approved_report_path, generate_report, get_report, preview_report_export,
)

router = APIRouter(prefix="/api/research-task", tags=["reports"])


class ReportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="证据研究报告", min_length=1, max_length=200)


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["md", "docx"]


def response(result: dict[str, Any]) -> JSONResponse:
    status = 200 if result["ok"] else 404 if result["error_code"] in {"REPORT_NOT_FOUND", "REPORT_TASK_NOT_FOUND"} else 409
    return JSONResponse(status_code=status, content=result)


@router.post("/{task_id}/reports")
async def create(task_id: str, request: ReportDraftRequest) -> JSONResponse:
    return response(generate_report(task_id, request.title))


@router.get("/{task_id}/reports/{report_id}")
async def read(task_id: str, report_id: str) -> JSONResponse:
    return response(get_report(task_id, report_id))


@router.post("/{task_id}/reports/{report_id}/preview")
async def preview(task_id: str, report_id: str, request: ReportExportRequest) -> JSONResponse:
    return response(preview_report_export(task_id, report_id, request.format))


@router.get("/{task_id}/reports/{report_id}/download", response_model=None)
async def download(task_id: str, report_id: str, format: Literal["md", "docx"]) -> Response | JSONResponse:
    try:
        path = approved_report_path(task_id, report_id, format)
    except ValueError:
        return JSONResponse(status_code=409, content={"ok": False, "error_code": "REPORT_NOT_EXPORTED", "message": "报告尚未通过审批写入或文件已不可用"})
    return Response(path.read_bytes(), headers={"Content-Disposition": f'attachment; filename="{path.name}"'}, media_type="text/markdown; charset=utf-8" if format == "md" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
