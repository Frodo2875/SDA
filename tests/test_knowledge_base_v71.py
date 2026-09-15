"""Knowledge management persists relationships without copying the document pipeline."""

import asyncio
from pathlib import Path

import httpx
import pytest

from backend import database
from backend.knowledge_api import repository
from backend.main import app
from backend.repositories.knowledge_repository import KnowledgeRepository
from backend.tools import excel_utils


def call(method: str, path: str, **kwargs: object) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def create(name: str = "学生资料库") -> dict:
    response = call("POST", "/api/knowledge-bases", json={"name": name, "description": "用于学生分析"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def test_create_list_detail_persist_across_repository_instances() -> None:
    record = create()
    assert record["status"] == "Empty" and record["file_count"] == 0
    assert record["created_at"] == record["updated_at"]
    assert KnowledgeRepository(database._connect).get(record["knowledge_base_id"]) == record
    assert call("GET", "/api/knowledge-bases").json()["data"] == [record]
    assert call("GET", f"/api/knowledge-bases/{record['knowledge_base_id']}").json()["data"] == record


@pytest.mark.parametrize("body", [{"name": " "}, {"name": "a" * 101}, {"name": "库", "description": "a" * 2001}, {"name": "库", "owner": "other"}])
def test_create_validation(body: dict) -> None:
    assert call("POST", "/api/knowledge-bases", json=body).status_code == 422
    assert repository.list() == []


def test_duplicate_name_conflict_is_explicit() -> None:
    create()
    response = call("POST", "/api/knowledge-bases", json={"name": " 学生资料库 "})
    assert response.status_code == 409
    assert response.json()["error_code"] == "KNOWLEDGE_NAME_EXISTS"
    assert len(repository.list()) == 1


@pytest.mark.parametrize("suffix", ["", "/files"])
def test_unknown_knowledge_returns_404(suffix: str) -> None:
    assert call("GET", f"/api/knowledge-bases/missing{suffix}").status_code == 404


def test_markdown_upload_uses_canonical_index_and_membership(data_dir: Path) -> None:
    first, second = create(), create("其他库")
    identifier = first["knowledge_base_id"]
    response = call("POST", f"/api/knowledge-bases/{identifier}/upload",
                    files={"file": ("学生.md", "# 学生\n\n计算机专业就业研究".encode(), "text/markdown")})
    assert response.status_code == 200, response.text
    file_id = response.json()["data"]["file_id"]
    record = database.get_file_record_by_id(file_id)
    assert record["file_type"] == "markdown" and record["queryable"]
    assert database.get_document_chunks(file_id)
    files = call("GET", f"/api/knowledge-bases/{identifier}/files").json()["data"]
    assert [item["file_id"] for item in files] == [file_id]
    assert call("GET", f"/api/knowledge-bases/{second['knowledge_base_id']}/files").json()["data"] == []
    assert repository.get(identifier)["status"] == "Ready"
    before = database.get_document_chunks(file_id)
    for _ in range(2):
        assert call("POST", f"/api/knowledge-bases/{second['knowledge_base_id']}/files", json={"file_id": file_id}).status_code == 200
    assert repository.get(second["knowledge_base_id"])["file_count"] == 1
    assert database.get_document_chunks(file_id) == before
    assert len(call("GET", "/api/files").json()["data"]) == 1
    assert (data_dir / "uploads" / "学生.md").exists()


def test_upload_validation_and_missing_library_do_not_create_files(data_dir: Path) -> None:
    identifier = create()["knowledge_base_id"]
    for target, name, content, expected in [(identifier, "bad.exe", b"bad", 400),
                                            (identifier, "empty.txt", b"", 400),
                                            ("missing", "good.txt", b"text", 404)]:
        response = call("POST", f"/api/knowledge-bases/{target}/upload", files={"file": (name, content)})
        assert response.status_code == expected
    assert repository.file_ids(identifier) == set()
    assert not (data_dir / "uploads" / "good.txt").exists()


def test_missing_file_cannot_be_attached() -> None:
    identifier = create()["knowledge_base_id"]
    assert call("POST", f"/api/knowledge-bases/{identifier}/files", json={"file_id": "a" * 32}).status_code == 404
    assert repository.get(identifier)["file_count"] == 0


def test_uploaded_file_can_be_recovered_after_link_failure(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = create()["knowledge_base_id"]
    original = repository.attach
    def fail(*args: object) -> None:
        raise ValueError("unavailable")
    monkeypatch.setattr(repository, "attach", fail)
    response = call("POST", f"/api/knowledge-bases/{identifier}/upload", files={"file": ("recover.txt", b"saved content")})
    assert response.status_code == 409
    assert response.json()["error_code"] == "KNOWLEDGE_ATTACH_FAILED"
    file_id = response.json()["data"]["file_id"]
    assert database.get_file_record_by_id(file_id)
    monkeypatch.setattr(repository, "attach", original)
    assert call("POST", f"/api/knowledge-bases/{identifier}/files", json={"file_id": file_id}).status_code == 200
    assert repository.get(identifier)["file_count"] == 1


def test_lifecycle_counts_and_existing_delete_approval(data_dir: Path) -> None:
    identifier = create()["knowledge_base_id"]
    response = call("POST", f"/api/knowledge-bases/{identifier}/upload", files={"file": ("remove.txt", b"test material")})
    file_id = response.json()["data"]["file_id"]
    with database._connect() as connection:
        connection.execute("UPDATE files SET lifecycle_status = 'reindexing' WHERE file_id = ?", (file_id,))
    assert repository.get(identifier)["status"] == "Indexing"
    with database._connect() as connection:
        connection.execute("UPDATE files SET lifecycle_status = 'failed' WHERE file_id = ?", (file_id,))
    assert repository.get(identifier)["status"] == "Failed"
    with database._connect() as connection:
        connection.execute("UPDATE files SET lifecycle_status = 'ready' WHERE file_id = ?", (file_id,))
    pending = call("POST", f"/api/files/{file_id}/delete", json={"session_id": "kb-delete"})
    assert pending.status_code == 200, pending.text
    assert repository.get(identifier)["file_count"] == 1
    action = pending.json()["data"]["action_id"]
    confirmed = call("POST", f"/api/actions/{action}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert repository.get(identifier)["file_count"] == 0
    assert repository.get(identifier)["status"] == "Empty"
    assert call("GET", f"/api/knowledge-bases/{identifier}/files").json()["data"] == []
    assert call("POST", f"/api/knowledge-bases/{identifier}/files", json={"file_id": file_id}).status_code == 404
