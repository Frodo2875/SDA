"""Thin knowledge management API around the existing file pipeline."""

import sqlite3
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import database
from backend.repositories.knowledge_repository import KnowledgeRepository
from backend.services.file_view import get_file_view, list_file_views

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge"])
repository = KnowledgeRepository(database._connect)


class KnowledgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class KnowledgeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def failure(code: str, message: str, status: int = 404, data: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "data": data, "error_code": code, "message": message})


def success(data: Any, message: str) -> dict[str, Any]:
    return {"ok": True, "data": data, "error_code": None, "message": message}


@router.get("")
async def list_knowledge() -> dict[str, Any]:
    return success(repository.list(), "知识库列表已读取")


@router.post("", response_model=None)
async def create_knowledge(request: KnowledgeCreate) -> dict[str, Any] | JSONResponse:
    try:
        identifier = repository.create(request.name, request.description, database.utc_now())
    except sqlite3.IntegrityError:
        return failure("KNOWLEDGE_NAME_EXISTS", "同名知识库已存在", 409)
    return success(repository.get(identifier), "知识库创建成功")


@router.get("/{knowledge_base_id}", response_model=None)
async def get_knowledge(knowledge_base_id: str) -> dict[str, Any] | JSONResponse:
    record = repository.get(knowledge_base_id)
    return success(record, "知识库已读取") if record else failure("KNOWLEDGE_NOT_FOUND", "知识库不存在")


@router.get("/{knowledge_base_id}/files", response_model=None)
async def list_knowledge_files(knowledge_base_id: str) -> dict[str, Any] | JSONResponse:
    if repository.get(knowledge_base_id) is None:
        return failure("KNOWLEDGE_NOT_FOUND", "知识库不存在")
    result = list_file_views()
    if not result["ok"]:
        return JSONResponse(status_code=503, content=result)
    ids = repository.file_ids(knowledge_base_id)
    return success([item for item in result["data"] if item["file_id"] in ids], "知识库文件已读取")


@router.post("/{knowledge_base_id}/files", response_model=None)
async def attach_knowledge_file(knowledge_base_id: str, request: KnowledgeFile) -> dict[str, Any] | JSONResponse:
    if repository.get(knowledge_base_id) is None:
        return failure("KNOWLEDGE_NOT_FOUND", "知识库不存在")
    result = get_file_view(request.file_id)
    if not result["ok"]:
        return failure("FILE_NOT_FOUND", "文件不存在或当前不可用")
    try:
        repository.attach(knowledge_base_id, request.file_id, database.utc_now())
    except (ValueError, sqlite3.IntegrityError):
        return failure("KNOWLEDGE_FILE_UNAVAILABLE", "知识库或文件已不可用", 409)
    return success(result["data"], "文件已加入知识库")


@router.post("/{knowledge_base_id}/upload", response_model=None)
async def upload_knowledge_file(knowledge_base_id: str, file: UploadFile = File(...)) -> dict[str, Any] | JSONResponse:
    if repository.get(knowledge_base_id) is None:
        await file.close()
        return failure("KNOWLEDGE_NOT_FOUND", "知识库不存在")
    from backend.main import api_upload_file

    result = await api_upload_file(file)
    if isinstance(result, JSONResponse) or not result.get("ok"):
        return result
    file_id = result["data"]["file_id"]
    try:
        repository.attach(knowledge_base_id, file_id, database.utc_now())
    except (ValueError, sqlite3.Error):
        return failure("KNOWLEDGE_ATTACH_FAILED", "文件已上传，但加入知识库失败；可从已有文件重新加入", 409, result["data"])
    return result
