"""Pydantic request and response models for the development API."""

from typing import Any

from pydantic import BaseModel, Field


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


class AgentToolCall(BaseModel):
    """One tool invocation executed during an Agent run."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    """Final Agent response and its auditable tool calls."""

    answer: str
    tool_calls: list[AgentToolCall]
    status: str
