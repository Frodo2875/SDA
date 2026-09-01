"""Strict Web retrieval records; external fields never carry instruction authority."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WebSearchScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    top_k: int = Field(default=5, ge=1, le=10)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    region: str | None = Field(default=None, min_length=2, max_length=16)


class UntrustedWebRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust_level: Literal["untrusted_web_data"] = "untrusted_web_data"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"
    can_trigger_tool: Literal[False] = False
    can_change_tool_risk: Literal[False] = False
    can_approve: Literal[False] = False


class WebSearchResult(UntrustedWebRecord):
    title: str | None = Field(default=None, max_length=500)
    url: str = Field(min_length=1, max_length=4096)
    snippet: str | None = Field(default=None, max_length=4000)
    source: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebDocument(UntrustedWebRecord):
    document_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=500)
    content: str = Field(max_length=2_000_000)
    canonical_url: str = Field(min_length=1, max_length=4096)
    domain: str = Field(min_length=1, max_length=253)
    publisher: str | None = Field(default=None, max_length=500)
    published_at: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["WebDocument", "WebSearchResult", "WebSearchScope"]
