"""Research scheduling and recovery use real SQLite, Async Runtime and Agent tools."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from pathlib import Path
import threading
from typing import Any

from fastapi import BackgroundTasks
import httpx
import pytest

from backend import agent, database, main
from backend.runtime.async_task_runtime import (
    cancel_async_task, resume_async_task, retry_async_task, run_async_task,
)
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.schemas import ChatRequest, ResearchTaskRequest
from backend.services.research_task import create_research_task, get_research_task, run_research_task
from backend.services.task_complexity_router import route_task_complexity
from backend.tools import excel_utils


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def local_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)


def create(query: str = "汇总项目文档", session: str = "research-test") -> dict[str, Any]:
    return create_research_task(ResearchTaskRequest(session_id=session, query=query))


class ScriptClient:
    def __init__(self, *, fail_once: bool = False, two_tools: bool = False) -> None:
        self.fail_once = fail_once
        self.failed = False
        self.two_tools = two_tools
        self.calls = 0

    async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        if messages[-1]["role"] == "user":
            calls = [{"id": "read-1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]
            if self.two_tools:
                calls.append({"id": "read-2", "type": "function", "function": {"name": "list_files", "arguments": "{}"}})
            return {"content": None, "tool_calls": calls}
        if self.fail_once and not self.failed:
            self.failed = True
            raise RuntimeError("Authorization: Bearer SECRET_TEST_KEY")
        return {"content": "资料已汇总。", "tool_calls": []}


@pytest.mark.parametrize("query", ["你好", "查询S001的学生信息", "今日天气", "读取这份文档", "查看https://example.com"])
def test_simple_complexity_rules(query: str) -> None:
    assert route_task_complexity(query)["async_required"] is False


@pytest.mark.parametrize("query", ["分析100份学生材料，结合网页总结规律并生成报告", "结合本地和网页资料", "生成研究报告", "分析比较总结全部材料", "generate a report"])
def test_complexity_rules(query: str) -> None:
    route = route_task_complexity(query)
    assert route["async_required"]
    assert route["reason_codes"]


@pytest.mark.anyio
async def test_simple_chat_still_calls_synchronous_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    async def fake(message: str, **kwargs: Any) -> dict[str, Any]:
        seen.append(message)
        return {"answer": "同步结果", "tool_calls": [], "status": "completed"}
    monkeypatch.setattr(main, "run_agent", fake)
    background = BackgroundTasks()
    response = await main.api_chat(ChatRequest(session_id="s", message="你好"), background)
    assert response["answer"] == "同步结果"
    assert seen == ["你好"]
    assert not background.tasks


@pytest.mark.anyio
async def test_complex_chat_returns_id_before_executing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pytest.fail("Complex chat must not execute the Agent inline")
    monkeypatch.setattr(main, "run_agent", forbidden)
    background = BackgroundTasks()
    response = await main.api_chat(ChatRequest(session_id="s", message="分析100份材料并生成报告"), background)
    assert response["status"] == "research_task_created"
    task = get_research_task(response["task_id"])
    assert task["status"] == "CREATED"
    assert task["result"] is None
    assert len(background.tasks) == 1
    assert inspect.iscoroutinefunction(background.tasks[0].func)
    assert background.tasks[0].func is main.dispatch_research_task


def test_concurrent_submissions_deduplicate_atomically() -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create(), range(16)))
    assert len({task["id"] for task in results}) == 1
    assert len(database.fetch_all("tasks")) == 1
    assert len(database.get_task_step_records(results[0]["id"])) == 1


def test_dedup_is_session_query_and_window_scoped() -> None:
    first = create()
    assert create()["id"] == first["id"]
    assert create(session="other")["id"] != first["id"]
    assert create(query="另一项查询")["id"] != first["id"]
    with database._connect() as connection:
        connection.execute("UPDATE tasks SET created_at = ? WHERE task_id = ?", ("2000-01-01T00:00:00+00:00", first["id"]))
    assert create()["id"] != first["id"]


def test_real_agent_completes_and_persists_result(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScriptClient()
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    task = create()
    run_research_task(task["id"])
    view = get_research_task(task["id"])
    assert view["status"] == "COMPLETED"
    assert view["progress"] == {"stage": "COMPLETED", "percent": 100}
    assert view["result"]["answer"] == "资料已汇总。"
    assert view["created_at"] <= view["updated_at"]
    assert view["error"] is None
    assert database.get_task_record(task["id"])["task_type"] == "async_research"
    run_research_task(task["id"])
    assert client.calls == 2
    stages = [json.loads(row["metrics_json"])["stage"] for row in database.get_task_trace_records(task["id"]) if row["event_type"] == "research_stage"]
    assert "PLANNING" in stages and "WAITING_TOOL" in stages and "EVIDENCE_CHECK" in stages


def test_failed_agent_saves_safe_error_and_retry_resumes_without_replaying_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScriptClient(fail_once=True)
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    calls: list[int] = []
    def tool() -> dict[str, Any]:
        calls.append(1)
        return {"ok": True, "data": [], "message": "已读取", "error_code": None}
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_files", tool)
    task = create()
    run_research_task(task["id"])
    failed = get_research_task(task["id"])
    assert failed["status"] == "FAILED"
    assert failed["error"]["error_code"] == "ASYNC_TASK_EXECUTION_ERROR"
    assert "SECRET_TEST_KEY" not in json.dumps(database.fetch_all("tasks"))
    assert "SECRET_TEST_KEY" not in json.dumps(database.get_task_trace_records(task["id"]))
    assert failed["result"] is None
    assert retry_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "COMPLETED"
    assert calls == [1]
    assert get_research_task(task["id"])["retry_count"] == 1
    core = database.get_task_record(task["id"])["checkpoint_data"]["async_task"]["checkpoint"]["agent"]["core"]
    assert core["llm_attempts"] == 3


def test_retry_limit_remains_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failure(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("secret")
    monkeypatch.setattr(agent, "run_agent", failure)
    task = create()
    for attempt in range(3):
        run_research_task(task["id"])
        retried = retry_async_task(task["id"])
        assert retried["ok"] is (attempt < 2)
    assert retried["error_code"] == "TASK_RETRY_LIMIT"


def test_queued_cancellation_never_executes_and_resume_reuses_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScriptClient()
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    task = create()
    assert cancel_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "CANCELLED"
    assert client.calls == 0
    assert resume_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "COMPLETED"


def test_cancel_between_tools_saves_finished_tool_and_resumes_pending_call(monkeypatch: pytest.MonkeyPatch) -> None:
    task = create()
    client = ScriptClient(two_tools=True)
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    calls: list[int] = []
    def tool() -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            cancel_async_task(task["id"])
        return {"ok": True, "data": [], "message": "完成", "error_code": None}
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_files", tool)
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "CANCELLED"
    assert len(calls) == 1
    assert resume_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "COMPLETED"
    assert len(calls) == 2
    assert client.calls == 2


@pytest.mark.anyio
async def test_http_poll_and_cancel_remain_responsive_during_blocking_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    client = ScriptClient()
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    def blocking() -> dict[str, Any]:
        entered.set()
        assert release.wait(5), "test did not release blocking tool"
        return {"ok": True, "data": [], "message": "完成", "error_code": None}
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "list_files", blocking)
    task = create()
    pool = ThreadPoolExecutor(max_workers=1)
    worker = pool.submit(run_research_task, task["id"])
    try:
        for _ in range(300):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            polled = await asyncio.wait_for(api.get(f"/research-task/{task['id']}"), 1)
            assert polled.json()["status"] == "WAITING_TOOL"
            assert polled.json()["progress"]["percent"] is None
            cancelled = await api.post(f"/research-task/{task['id']}/cancel")
            assert cancelled.json()["cancel_requested"]
    finally:
        release.set()
        while not worker.done():
            await asyncio.sleep(0.01)
        worker.result()
        pool.shutdown(wait=True)
    assert get_research_task(task["id"])["status"] == "CANCELLED"


def test_workflow_identity_and_steps_are_reused_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    route = agent.route_domain("查询S001的学生信息", trace=False)
    route["effective_domain"] = "student"
    monkeypatch.setattr(agent, "route_domain", lambda *args, **kwargs: route)
    plan = TaskPlan("existing_workflow", (PlannedStep(1, "文件读取", "QUERY", "list_files"),))
    monkeypatch.setattr(agent, "create_plan", lambda _: plan)
    client = ScriptClient(fail_once=True)
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    task = create()
    run_research_task(task["id"])
    checkpoint = database.get_task_record(task["id"])["checkpoint_data"]["async_task"]["checkpoint"]
    child = checkpoint["agent"]["execution"]["task_id"]
    assert child and child != task["id"]
    assert database.get_task_step_records(child)[0]["status"] == "success"
    assert resume_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "COMPLETED"
    assert len(database.fetch_all("tasks")) == 2
    assert database.get_task_record(child)["checkpoint_data"]["parent_async_task_id"] == task["id"]


@pytest.mark.anyio
async def test_http_create_result_validation_and_missing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LLMClient", ScriptClient)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
        response = await api.post("/research-task", json={"session_id": "http", "query": "汇总文件"})
        assert response.status_code == 202
        assert response.json()["status"] == "CREATED"
        task_id = response.json()["task_id"]
        polled = await api.get(f"/api/research-task/{task_id}")
        assert polled.json()["status"] == "COMPLETED"
        duplicate = await api.post("/research-task", json={"session_id": "http", "query": "汇总文件"})
        assert duplicate.json()["task_id"] == task_id
        assert (await api.post(f"/research-task/{task_id}/cancel")).status_code == 409
        assert (await api.get("/research-task/missing")).status_code == 404
        assert (await api.post("/research-task/missing/retry")).status_code == 404
        assert (await api.post("/research-task", json={"session_id": "s", "query": "  "})).status_code == 422


def test_agent_error_status_is_not_mislabeled_as_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "max_tool_rounds_exceeded", "answer": "SECRET_TEST_KEY"}
    monkeypatch.setattr(agent, "run_agent", failed)
    task = create()
    run_research_task(task["id"])
    result = get_research_task(task["id"])
    assert result["status"] == "FAILED"
    assert result["error"]["error_code"] == "RESEARCH_BUDGET_EXHAUSTED"
    assert "SECRET_TEST_KEY" not in json.dumps(result)


@pytest.mark.parametrize("tool_name,stage,query", [
    ("retrieve_document", "LOCAL_RETRIEVAL", "汇总文档"),
    ("retrieve_web", "WEB_RETRIEVAL", "检索网页信息"),
])
def test_retrieval_stages_are_real_tool_boundaries(monkeypatch: pytest.MonkeyPatch, tool_name: str, stage: str, query: str) -> None:
    task = create(query=query)
    class Client:
        async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            if messages[-1]["role"] == "user":
                arguments = {"query": "项目"} if tool_name == "retrieve_web" else {"query": "项目", "scope": {}}
                return {"tool_calls": [{"id": "retrieve", "type": "function", "function": {
                    "name": tool_name, "arguments": json.dumps(arguments),
                }}]}
            return {"content": "没有足够依据。"}
    def tool(**kwargs: Any) -> dict[str, Any]:
        view = get_research_task(task["id"])
        assert view["status"] == "WAITING_TOOL"
        assert view["progress"]["stage"] == stage
        return {"ok": True, "data": {"status": "not_found"}, "message": "未找到资料", "error_code": None}
    monkeypatch.setattr(agent, "LLMClient", Client)
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, tool_name, tool)
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "COMPLETED"
    stages = [json.loads(row["metrics_json"]).get("stage") for row in database.get_task_trace_records(task["id"])]
    assert stage in stages


def test_resume_does_not_reset_model_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScriptClient()
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    task = create()
    database.TASK_REPOSITORY.update_research(task["id"], agent={"core": {"llm_attempts": 100}})
    run_research_task(task["id"])
    assert get_research_task(task["id"])["error"]["error_code"] == "RESEARCH_BUDGET_EXHAUSTED"
    assert resume_async_task(task["id"])["ok"]
    run_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "FAILED"
    assert client.calls == 0


def test_concurrent_workers_claim_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    async def fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"status": "completed", "answer": "一次执行"}
    monkeypatch.setattr(agent, "run_agent", fake)
    task = create()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: run_research_task(task["id"]), range(4)))
    assert calls == [1]
    assert get_research_task(task["id"])["status"] == "COMPLETED"


@pytest.mark.anyio
async def test_retry_and_resume_http_apis_schedule_existing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScriptClient(fail_once=True)
    monkeypatch.setattr(agent, "LLMClient", lambda: client)
    task = create()
    run_research_task_result = await main.api_create_research_task(
        ResearchTaskRequest(session_id="deferred", query="文档汇总"), BackgroundTasks(),
    )
    assert run_research_task_result["status"] == "CREATED"
    # Use the same BackgroundTasks bridge as production, not a nested event loop.
    await main.dispatch_research_task(task["id"])
    assert get_research_task(task["id"])["status"] == "FAILED"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
        retried = await api.post(f"/research-task/{task['id']}/retry")
        assert retried.status_code == 200
        assert retried.json()["id"] == task["id"]
        assert get_research_task(task["id"])["status"] == "COMPLETED"
        deferred_id = run_research_task_result["id"]
        assert (await api.post(f"/research-task/{deferred_id}/cancel")).status_code == 200
        resumed = await api.post(f"/research-task/{deferred_id}/resume")
        assert resumed.status_code == 200
        assert get_research_task(deferred_id)["status"] == "COMPLETED"


def test_concurrent_retry_transitions_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("failure")
    monkeypatch.setattr(agent, "run_agent", failed)
    task = create()
    run_research_task(task["id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: retry_async_task(task["id"]), range(8)))
    assert sum(item["ok"] for item in results) == 1
    assert get_research_task(task["id"])["retry_count"] == 1


@pytest.mark.parametrize("approve", [True, False])
def test_research_keeps_existing_approval_authoritative(monkeypatch: pytest.MonkeyPatch, approve: bool) -> None:
    from io import BytesIO
    from docx import Document
    from backend.services.file_upload import save_uploaded_file
    from backend.services.confirmation import create_pending_action, confirm_action, cancel_action

    stream = BytesIO()
    document = Document()
    document.add_paragraph("现有报告模板")
    document.save(stream)
    assert save_uploaded_file("report.docx", stream.getvalue())["ok"]
    calls: list[int] = []
    async def prepare(message: str, *, session_id: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        pending = create_pending_action(session_id=session_id, target_file="report.docx", student_id="", student_name="", content="报告内容")
        assert pending["ok"]
        return {"status": "confirmation_required", "answer": "等待确认", "pending_action": pending["data"], "task_id": pending["data"]["task_id"]}
    monkeypatch.setattr(agent, "run_agent", prepare)
    task = create()
    run_research_task(task["id"])
    view = get_research_task(task["id"])
    assert view["status"] == "WAITING_CONFIRMATION"
    action_id = view["result"]["pending_action"]["action_id"]
    result = confirm_action(action_id) if approve else cancel_action(action_id)
    assert result["ok"]
    assert get_research_task(task["id"])["status"] == ("COMPLETED" if approve else "CANCELLED")
    if not approve:
        assert resume_async_task(task["id"])["ok"]
        run_research_task(task["id"])
        assert get_research_task(task["id"])["error"]["error_code"] == "RESEARCH_ACTION_TERMINATED"
    assert calls == [1]
