"""V3.2 Document Workspace API and deterministic view tests."""

import httpx
import pytest

from backend import database
from backend.main import app


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client


async def test_workspace_lists_file_cards_with_required_metadata(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/files")

    assert response.status_code == 200
    items = response.json()["data"]
    assert items
    assert {
        "file_id",
        "file_name",
        "file_type",
        "size",
        "created_time",
        "status",
        "lifecycle_status",
        "parse_status",
        "index_status",
    } <= set(items[0])


async def test_workspace_searches_by_partial_file_name(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/files", params={"search": "学生成绩"})

    assert response.status_code == 200
    names = [item["file_name"] for item in response.json()["data"]]
    assert names == ["学生成绩.xlsx"]


async def test_workspace_filters_and_sorts_in_python(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/files",
        params={
            "file_type": "excel",
            "lifecycle_status": "ready",
            "sort_by": "size",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    items = response.json()["data"]
    assert items
    assert all(item["file_type"] == "excel" for item in items)
    assert all(item["lifecycle_status"] == "ready" for item in items)
    assert [item["size"] for item in items] == sorted(item["size"] for item in items)


async def test_workspace_reads_file_details_by_stable_id(
    client: httpx.AsyncClient,
) -> None:
    record = database.get_file_record("综合评价.docx")

    response = await client.get(f"/api/files/{record['file_id']}")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["file_id"] == record["file_id"]
    assert detail["file_name"] == "综合评价.docx"
    assert detail["lifecycle_status"] == "ready"
    assert detail["parse_status"] == "not_required"
    assert detail["index_status"] == "not_required"

