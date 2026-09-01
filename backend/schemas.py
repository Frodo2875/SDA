"""Pydantic request and response models for the development API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Service health response."""

    status: str
    project: str
    version: str


class ToolResponse(BaseModel):
    """Response envelope shared by the existing Python tools."""

    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str


class CompareStudentsRequest(BaseModel):
    """Student IDs submitted for deterministic comparison."""

    student_ids: list[str] = Field(
        min_length=2,
        description="需要比较的学生学号，至少提供 2 个",
        examples=[["S001", "S002"]],
    )


class ChatRequest(BaseModel):
    """One stateless user message submitted to the Agent."""

    session_id: str = Field(min_length=1, description="客户端会话标识")
    message: str = Field(min_length=1, description="用户自然语言消息")


class DeleteFileRequest(BaseModel):
    """Explicit session context for preparing a file deletion."""

    session_id: str = Field(min_length=1, description="请求删除的客户端会话标识")


class VersionActionRequest(BaseModel):
    """Session context for a confirmed undo or rollback."""

    session_id: str = Field(min_length=1, description="版本操作的客户端会话标识")


class AsyncTaskCreateRequest(BaseModel):
    """Validated request for one allow-listed long-running Python task."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128)
    task_type: Literal["ocr", "layout", "index", "reindex", "batch", "workflow"]
    payload: dict[str, Any] = Field(default_factory=dict)


class WordDiffOperationRequest(BaseModel):
    """Validated public request for a side-effect-free Word Diff preview."""

    operation_type: Literal["append", "undo", "rollback"]
    content: str | None = Field(default=None, max_length=100_000)
    version_id: str | None = Field(default=None, max_length=128)


class AgentToolCall(BaseModel):
    """One tool invocation executed during an Agent run."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    retry_count: int = 0


class PendingAction(BaseModel):
    """Frozen write proposal awaiting an explicit user decision."""

    action_id: str
    approval_id: str
    session_id: str
    action_type: Literal["write_word", "delete_file", "undo_word", "rollback_word"]
    target_file: str
    student_id: str
    student_name: str
    content: str
    created_at: datetime
    status: Literal["pending", "confirmed", "cancelled", "executed", "failed"]
    executed_at: datetime | None = None
    file_id: str | None = None
    operation: dict[str, Any] | None = None
    diff_preview: dict[str, Any] | None = None
    target_version_id: str | None = None
    task_id: str | None = None


class ChatResponse(BaseModel):
    """Final Agent response and its auditable tool calls."""

    answer: str
    tool_calls: list[AgentToolCall]
    status: str
    pending_action: PendingAction | None = None
    task_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    unified_evidence: list[dict[str, Any]] = Field(default_factory=list)


class ActionResponse(BaseModel):
    """Confirmation or cancellation result."""

    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str
