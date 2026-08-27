"""API integration tests proving that FastAPI exposes the existing tools."""

import httpx
import pytest

from backend import database
from backend import agent
from backend.llm_client import LLMConfigurationError
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


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "project": "Student Document Agent",
        "version": "0.1.0",
    }


async def test_files(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/files")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert {
        "学生基本信息.xlsx",
        "学生成绩.xlsx",
        "科研成果.xlsx",
        "综合评价.docx",
    } <= {item["file_name"] for item in body["data"]}
    item = next(entry for entry in body["data"] if entry["file_name"] == "学生基本信息.xlsx")
    assert {
        "source_type", "lifecycle_status", "parse_status", "queryable",
        "index_status", "uploaded_at", "schema_summary",
    } <= set(item)


async def test_file_detail_and_redacted_trace_endpoints(client: httpx.AsyncClient) -> None:
    record = database.get_file_record("学生基本信息.xlsx")
    details = await client.get(f"/api/files/{record['file_id']}/details")
    context = await client.get("/api/sessions/api-v2/context")
    traces = await client.get("/api/sessions/api-v2/traces")

    assert details.status_code == 200
    assert details.json()["data"]["file_id"] == record["file_id"]
    assert context.status_code == 200
    assert context.json()["data"]["session_id"] == "api-v2"
    assert traces.status_code == 200
    assert traces.json()["data"] == []


async def test_evidence_location_failure_endpoint_records_trace(
    client: httpx.AsyncClient,
) -> None:
    evidence_id = "f" * 24
    response = await client.get(
        f"/api/evidence/{evidence_id}/locate",
        params={"session_id": "api-evidence"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "来源存在，但当前无法打开对应预览位置"
    traces = await client.get("/api/sessions/api-evidence/traces")
    assert traces.status_code == 200
    assert traces.json()["data"][0]["tool_name"] == "locate_evidence"


async def test_session_task_center_endpoint_returns_unified_contract(
    client: httpx.AsyncClient,
) -> None:
    record = database.get_file_record("综合评价.docx")
    from backend.runtime.async_task_runtime import enqueue_async_task

    queued = enqueue_async_task(
        {
            "session_id": "api-task-center",
            "task_type": "index",
            "payload": {"file_id": record["file_id"]},
        }
    )
    task_id = queued["data"]["task"]["task_id"]

    response = await client.get("/api/sessions/api-task-center/tasks")

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["task"]["task_id"] == task_id
    assert item["task"]["display_status"] == "queued"
    assert {
        "task_summary", "current_node", "progress_detail", "started_at",
        "duration_ms", "success_count", "failed_count", "skipped_count",
        "error_summary", "can_cancel", "can_retry", "can_resume",
    } <= set(item["task"])


async def test_search_student(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/students/search", params={"q": "张三"})

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ambiguous"


async def test_get_student(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/students/S001")

    assert response.status_code == 200
    assert response.json()["data"]["student_id"] == "S001"


async def test_get_student_scores(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/students/S001/scores")

    assert response.status_code == 200
    assert response.json()["data"]["average_score"] == 90.67


async def test_get_student_research_and_missing_record(client: httpx.AsyncClient) -> None:
    found_response = await client.get("/api/students/S001/research")
    missing_response = await client.get("/api/students/S011/research")

    assert found_response.status_code == 200
    assert found_response.json()["data"]["research"] == {
        "论文数": 1,
        "专利数": 0,
        "竞赛数": 2,
    }
    assert missing_response.status_code == 200
    assert missing_response.json()["data"]["status"] == "no_record"


async def test_compare_students(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/students/compare",
        json={"student_ids": ["S001", "S002"]},
    )

    assert response.status_code == 200
    difference = response.json()["data"]["differences"][0]["values"]
    assert difference == {
        "average_score": 5.0,
        "rank": -2,
        "paper_count": 1,
        "patent_count": -1,
        "competition_count": 1,
    }


async def test_unknown_student_maps_to_404_without_traceback(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/students/S999")

    assert response.status_code == 404
    assert response.json()["error_code"] == "STUDENT_NOT_FOUND"
    assert "Traceback" not in response.text


async def test_invalid_compare_request_is_rejected_by_pydantic(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/students/compare", json={"student_ids": ["S001"]})

    assert response.status_code == 422


async def test_openapi_contains_all_requested_routes(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {
        "/health",
        "/api/files",
        "/api/students/search",
        "/api/students/{student_id}",
        "/api/students/{student_id}/scores",
        "/api/students/{student_id}/research",
        "/api/students/compare",
        "/api/chat",
        "/api/files/{file_id}/details",
        "/api/tasks/{task_id}/traces",
        "/api/sessions/{session_id}/context",
        "/api/sessions/{session_id}/traces",
    } <= set(paths)


async def test_chat_reports_missing_llm_configuration_without_traceback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_configuration_client():
        raise LLMConfigurationError(
            "缺少 LLM 配置：LLM_API_KEY, LLM_BASE_URL, LLM_MODEL"
        )

    monkeypatch.setattr(agent, "LLMClient", missing_configuration_client)

    response = await client.post(
        "/api/chat",
        json={"session_id": "test-session", "message": "查询S001的学生信息"},
    )

    assert response.status_code == 503
    assert response.json()["status"] == "configuration_error"
    assert response.json()["tool_calls"] == []
    assert "Traceback" not in response.text
