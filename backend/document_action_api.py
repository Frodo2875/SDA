"""User-initiated document drafts; execution stays in the existing approval API."""

import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import database
from backend.services.confirmation import create_pending_report_action
from backend.services.research_task import get_research_task
from backend.services.visual_safety import assess_visual_high_impact_write

router = APIRouter(prefix="/api/document-actions", tags=["documents"])


class DocumentDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(min_length=1, max_length=128)
    source_answer: str = Field(min_length=1, max_length=100_000)
    content: str = Field(min_length=1, max_length=100_000)
    research_task_id: str | None = Field(default=None, max_length=128)
    file_id: str | None = Field(default=None, min_length=1, max_length=128)
    format: Literal["docx", "md"] = "docx"


def _source_evidence(request: DocumentDraftRequest) -> list[dict[str, Any]]:
    """Resolve provenance on the server; clients cannot remove visual warnings."""
    if request.research_task_id:
        task = get_research_task(request.research_task_id)
        if not task or task["session_id"] != request.session_id or task["status"] != "COMPLETED":
            raise ValueError("请先等待当前会话的研究完成。")
        result = task.get("result") or {}
        if result.get("status") != "completed" or result.get("answer") != request.source_answer:
            raise ValueError("研究结果已变化，请刷新后重新预览。")
        return result.get("unified_evidence") or result.get("evidence") or []
    with database._connect() as connection:
        row = connection.execute(
            "SELECT used_tools FROM chat_messages WHERE session_id = ? AND role = 'assistant' "
            "AND status = 'completed' AND content = ? ORDER BY id DESC LIMIT 1",
            (request.session_id, request.source_answer),
        ).fetchone()
    if row is None:
        raise ValueError("未找到当前会话的已完成回答，请重新提问后再保存。")
    from backend.agent import _collect_evidence, _collect_unified_evidence
    calls = json.loads(row["used_tools"])
    return [*_collect_evidence(calls), *_collect_unified_evidence(calls)]


@router.post("/preview")
async def preview(request: DocumentDraftRequest) -> JSONResponse:
    try:
        evidence = _source_evidence(request)
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error_code": "DRAFT_SOURCE_UNAVAILABLE", "message": str(exc)})
    if assess_visual_high_impact_write(evidence)["decision"] != "confirm":
        return JSONResponse(status_code=409, content={"ok": False, "error_code": "VISUAL_FIELD_REVIEW_REQUIRED", "message": "资料中有待核对的识别内容，请先核实。"})
    file_id = request.file_id
    if file_id is None:
        # Use the existing report file adapter and its constrained storage path.
        # Registration reserves a destination; no bytes or versions are written.
        name = f"report-{uuid4().hex}.{request.format}"
        database.register_file(file_name=name, file_type="word" if request.format == "docx" else "markdown",
                               file_path=f"data/uploads/{name}", writable=True, lifecycle_status="ready",
                               parse_status="not_required", queryable=False)
        file_id = database.get_file_record(name)["file_id"]
    result = create_pending_report_action(session_id=request.session_id, file_id=file_id,
                                          content=request.content, evidence_context=evidence)
    return JSONResponse(status_code=200 if result["ok"] else 409, content=result)
