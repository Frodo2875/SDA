"""V3.8 lightweight SQLite asynchronous task runtime tests."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend import database
from backend.runtime.async_task_runtime import (
    AsyncTaskContext,
    cancel_async_task,
    enqueue_async_task,
    get_async_task,
    resume_async_task,
    retry_async_task,
    run_async_task,
)


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def word_file_id(tmp_path: Path) -> str:
    database.register_file(
        file_name="异步测试.docx",
        file_type="word",
        file_path="data/uploads/异步测试.docx",
        lifecycle_status="uploaded",
        parse_status="pending",
        queryable=False,
    )
    return database.get_file_record("异步测试.docx")["file_id"]


def _enqueue(file_id: str, session_id: str = "async-v3") -> dict[str, Any]:
    result = enqueue_async_task(
        {
            "session_id": session_id,
            "task_type": "index",
            "payload": {"file_id": file_id},
        }
    )
    assert result["ok"] is True
    return result["data"]


async def test_create_task_persists_queue_fields_and_step(word_file_id: str) -> None:
    data = _enqueue(word_file_id)
    task = data["task"]

    assert task["status"] == "pending"
    assert task["task_status"] == "created"
    assert task["progress"] == 0
    assert task["message"] == "任务已进入队列"
    assert data["steps"][0]["status"] == "created"
    with sqlite3.connect(database.DB_PATH) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }
    assert {"task_status", "progress", "message"} <= columns


async def test_query_progress_returns_persisted_worker_update(word_file_id: str) -> None:
    task_id = _enqueue(word_file_id)["task"]["task_id"]
    observed: dict[str, Any] = {}

    def worker(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_progress(
            45,
            "已完成文档预处理",
            checkpoint={"phase": "parsed", "file_id": payload["file_id"]},
        )
        observed.update(get_async_task(context.task_id)["data"]["task"])
        return {"ok": True, "data": {}, "error_code": None, "message": "索引完成"}

    result = await run_async_task(task_id, executor=worker)

    assert observed["task_status"] == "running"
    assert observed["progress"] == 45
    assert observed["message"] == "已完成文档预处理"
    assert result["ok"] is True
    assert result["data"]["task"]["task_status"] == "success"
    assert result["data"]["task"]["progress"] == 100


async def test_cancelled_queued_task_is_never_executed(word_file_id: str) -> None:
    task_id = _enqueue(word_file_id)["task"]["task_id"]
    calls = 0

    def worker(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"ok": True, "data": {}, "error_code": None, "message": "完成"}

    cancelled = cancel_async_task(task_id)
    result = await run_async_task(task_id, executor=worker)

    assert cancelled["ok"] is True
    assert cancelled["data"]["task"]["task_status"] == "cancelled"
    assert result["ok"] is True
    assert result["data"]["task"]["task_status"] == "cancelled"
    assert calls == 0


async def test_resume_preserves_checkpoint_and_continues_after_cancel(
    word_file_id: str,
) -> None:
    task_id = _enqueue(word_file_id)["task"]["task_id"]

    def interrupted(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_progress(40, "第一页完成", checkpoint={"next_page": 2})
        assert cancel_async_task(context.task_id)["ok"] is True
        context.raise_if_cancelled()
        raise AssertionError("取消后不应继续执行")

    first = await run_async_task(task_id, executor=interrupted)
    resumed = resume_async_task(task_id)
    seen_checkpoint: dict[str, Any] = {}

    def continued(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        seen_checkpoint.update(context.checkpoint)
        assert context.progress == 40
        context.update_progress(80, "第二页完成", checkpoint={"next_page": 3})
        return {"ok": True, "data": {}, "error_code": None, "message": "恢复完成"}

    second = await run_async_task(task_id, executor=continued)

    assert first["data"]["task"]["task_status"] == "cancelled"
    assert resumed["ok"] is True
    assert resumed["data"]["task"]["progress"] == 40
    assert seen_checkpoint == {"next_page": 2}
    assert second["data"]["task"]["task_status"] == "success"
    assert second["data"]["task"]["progress"] == 100


async def test_retry_failed_task_restarts_with_bounded_retry_count(
    word_file_id: str,
) -> None:
    task_id = _enqueue(word_file_id)["task"]["task_id"]

    def failed(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_progress(25, "处理失败", checkpoint={"phase": "broken"})
        return {
            "ok": False,
            "data": None,
            "error_code": "WORD_PARSE_ERROR",
            "message": "解析失败",
        }

    assert (await run_async_task(task_id, executor=failed))["ok"] is False
    retried = retry_async_task(task_id)

    assert retried["ok"] is True
    assert retried["data"]["task"]["progress"] == 0
    metadata = retried["data"]["task"]["checkpoint_data"]["async_task"]
    assert metadata["checkpoint"] == {}
    assert metadata["retry_count"] == 1
    assert retried["data"]["steps"][0]["retry_count"] == 1
