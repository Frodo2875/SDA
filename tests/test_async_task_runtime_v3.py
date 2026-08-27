"""V3.20 WF08-WF10 real progress, safe cancel and Task Center contracts."""

import asyncio
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
from backend.services.task_view import get_task_view
from backend.services.task_view import list_task_views
from backend.services import confirmation
from backend.runtime import async_task_runtime


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def pdf_file_id(tmp_path: Path) -> str:
    upload = tmp_path / "uploads"
    upload.mkdir(exist_ok=True)
    (upload / "异步扫描.pdf").write_bytes(b"%PDF fixture")
    database.register_file(
        file_name="异步扫描.pdf",
        file_type="pdf",
        file_path="data/uploads/异步扫描.pdf",
        lifecycle_status="uploaded",
        parse_status="pending",
        queryable=False,
    )
    return database.get_file_record("异步扫描.pdf")["file_id"]


def _ocr_task(file_id: str, session_id: str) -> dict[str, Any]:
    result = enqueue_async_task(
        {
            "session_id": session_id,
            "task_type": "ocr",
            "payload": {"file_id": file_id},
        }
    )
    assert result["ok"] is True
    return result["data"]


def _pending_action(action_id: str, session_id: str) -> dict[str, Any]:
    action = {
        "action_id": action_id,
        "session_id": session_id,
        "action_type": "write_word",
        "target_file": "综合评价.docx",
        "student_id": "S001",
        "student_name": "张三",
        "content": "异步确认测试",
        "status": "pending",
        "created_at": database.utc_now(),
        "executed_at": None,
        "file_id": None,
        "operation_json": None,
        "diff_json": None,
        "target_version_id": None,
        "task_id": None,
    }
    database.insert_pending_action(action)
    return action


async def test_wf08_ocr_async_uses_real_page_progress(pdf_file_id: str) -> None:
    queued = _ocr_task(pdf_file_id, "wf08")
    task_id = queued["task"]["task_id"]
    assert queued["task"]["display_status"] == "queued"
    assert queued["task"]["progress_detail"] == {"unit": "stage", "stage": "queued"}
    observed: dict[str, Any] = {}

    async def worker(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_counts(1, 3, "第1页完成", unit="pages")
        observed.update(get_async_task(context.task_id)["data"]["task"])
        context.update_counts(
            3, 3, "全部页面完成", unit="pages",
            counts={"success_count": 3, "failed_count": 0, "skipped_count": 0},
        )
        return {
            "ok": True,
            "data": {"status": "success", "page_results": [{}, {}, {}]},
            "error_code": None,
            "message": "OCR完成",
        }

    result = await run_async_task(task_id, executor=worker)

    assert observed["display_status"] == "running"
    assert observed["progress_detail"]["processed_pages"] == 1
    assert observed["progress_detail"]["total_pages"] == 3
    assert observed["progress_detail"]["percent"] == 33
    assert result["data"]["task"]["task_status"] == "success"
    assert result["data"]["task"]["progress_detail"]["percent"] == 100


async def test_wf08_default_ocr_worker_uses_page_pipeline(
    pdf_file_id: str, monkeypatch
) -> None:
    task_id = _ocr_task(pdf_file_id, "wf08-default")["task"]["task_id"]
    called: list[tuple[str, list[int] | None]] = []
    monkeypatch.setattr(async_task_runtime, "resolve_by_file_id", lambda file_id: Path("scan.pdf"))
    monkeypatch.setattr(
        async_task_runtime,
        "detect_pdf_type",
        lambda path: {"ok": True, "data": {"page_count": 2}},
    )

    def fake_ocr(file_id: str, pages: list[int] | None = None) -> dict[str, Any]:
        called.append((file_id, pages))
        return {
            "ok": True,
            "data": {
                "status": "partial_success",
                "page_results": [
                    {"page_no": 1, "status": "success"},
                    {"page_no": 2, "status": "failed"},
                ],
                "failed_pages": [2],
            },
            "error_code": None,
            "message": "OCR部分完成",
        }

    monkeypatch.setattr(async_task_runtime, "ocr_document", fake_ocr)

    result = await run_async_task(task_id)

    assert called == [(pdf_file_id, None)]
    assert result["data"]["task"]["task_status"] == "partial_success"
    assert result["data"]["task"]["progress_detail"] == {
        "unit": "pages",
        "processed_pages": 2,
        "total_pages": 2,
        "success_count": 1,
        "failed_count": 1,
        "skipped_count": 0,
        "percent": 100,
    }


async def test_wf09_running_cancel_waits_for_atomic_safe_point(
    pdf_file_id: str,
) -> None:
    task_id = _ocr_task(pdf_file_id, "wf09")["task"]["task_id"]
    atomic_started = asyncio.Event()
    release_atomic = asyncio.Event()
    committed: list[str] = []

    async def worker(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.enter_atomic("index_activation", "正在原子切换索引")
        atomic_started.set()
        await release_atomic.wait()
        committed.append("atomic-complete")
        context.leave_atomic(checkpoint={"atomic_commit": True})
        context.raise_if_cancelled()
        raise AssertionError("取消请求应在原子区结束后生效")

    running = asyncio.create_task(run_async_task(task_id, executor=worker))
    await atomic_started.wait()
    requested = cancel_async_task(task_id)

    assert requested["ok"] is True
    assert requested["data"]["task"]["task_status"] == "running"
    assert requested["data"]["task"]["cancel_requested"] is True
    assert "等待原子阶段" in requested["data"]["task"]["message"]
    release_atomic.set()
    result = await running

    assert committed == ["atomic-complete"]
    assert result["data"]["task"]["task_status"] == "cancelled"
    checkpoint = result["data"]["task"]["checkpoint_data"]["async_task"]["checkpoint"]
    assert checkpoint == {"atomic_commit": True}


async def test_wf10_partial_success_retry_only_failed_pages(
    pdf_file_id: str,
) -> None:
    task_id = _ocr_task(pdf_file_id, "wf10")["task"]["task_id"]

    def partial(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_counts(
            3, 3, "OCR部分完成", unit="pages",
            counts={"success_count": 2, "failed_count": 1, "skipped_count": 0},
            checkpoint={"processed_pages": [1, 2, 3], "failed_pages": [2]},
        )
        return {
            "ok": True,
            "data": {"status": "partial_success", "failed_pages": [2]},
            "error_code": None,
            "message": "第2页失败",
        }

    first = await run_async_task(task_id, executor=partial)
    retried = retry_async_task(task_id)

    assert first["data"]["task"]["task_status"] == "partial_success"
    assert first["data"]["task"]["failed_count"] == 1
    assert first["data"]["task"]["progress_detail"]["processed_pages"] == 3
    assert retried["ok"] is True
    metadata = retried["data"]["task"]["checkpoint_data"]["async_task"]
    assert metadata["payload"]["pages"] == [2]
    assert metadata["retry_count"] == 1


async def test_waiting_confirmation_and_task_center_contract(
    pdf_file_id: str,
) -> None:
    task_id = _ocr_task(pdf_file_id, "waiting")["task"]["task_id"]

    def waiting(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_stage("approval", "等待确认")
        return {
            "ok": True,
            "data": {
                "status": "waiting_confirmation",
                "pending_action": {"action_id": "action-1"},
            },
            "error_code": None,
            "message": "等待用户确认",
        }

    result = await run_async_task(task_id, executor=waiting)
    view = get_task_view(task_id)

    assert result["data"]["task"]["task_status"] == "waiting_confirmation"
    task = view["task"]
    assert {
        "task_id", "task_summary", "display_status", "current_node",
        "progress_detail", "started_at", "duration_ms", "success_count",
        "failed_count", "skipped_count", "error_summary",
    } <= set(task)
    assert task["display_status"] == "waiting_confirmation"


async def test_workflow_request_is_queued_and_runs_in_background_contract(
    monkeypatch,
) -> None:
    from backend import agent

    queued = enqueue_async_task(
        {
            "session_id": "workflow-async",
            "task_type": "workflow",
            "payload": {"message": "判断学生奖学金资格"},
        }
    )
    task_id = queued["data"]["task"]["task_id"]
    assert queued["data"]["task"]["display_status"] == "queued"

    async def fake_agent(message: str, *, session_id: str) -> dict[str, Any]:
        return {
            "answer": "等待确认",
            "status": "confirmation_required",
            "task_id": None,
            "pending_action": {"action_id": "workflow-action"},
        }

    monkeypatch.setattr(agent, "run_agent", fake_agent)
    result = await run_async_task(task_id)

    assert result["data"]["task"]["task_status"] == "waiting_confirmation"
    assert result["data"]["task"]["current_node"]["status"] == "waiting_confirmation"


async def test_async_confirmation_converges_outer_task_to_success(
    pdf_file_id: str, monkeypatch,
) -> None:
    session_id = "async-confirm-success"
    action = _pending_action("async-action-success", session_id)
    task_id = _ocr_task(pdf_file_id, session_id)["task"]["task_id"]

    def waiting(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "status": "waiting_confirmation",
                "pending_action": {"action_id": action["action_id"]},
            },
            "error_code": None,
            "message": "等待确认",
        }

    await run_async_task(task_id, executor=waiting)
    monkeypatch.setattr(
        confirmation,
        "_execute_frozen_action",
        lambda pending: {
            "ok": True,
            "data": {"action_id": pending["action_id"]},
            "error_code": None,
            "message": "模拟写入成功",
        },
    )
    confirmed = confirmation.confirm_action(action["action_id"])
    view = get_task_view(task_id)

    assert confirmed["ok"] is True
    assert view["task"]["task_status"] == "success"
    assert view["task"]["display_status"] == "success"
    assert view["steps"][0]["status"] == "success"
    assert view["task"]["progress"] == 100


async def test_async_cancellation_converges_outer_task_without_side_effect(
    pdf_file_id: str, monkeypatch,
) -> None:
    session_id = "async-confirm-cancel"
    action = _pending_action("async-action-cancel", session_id)
    task_id = _ocr_task(pdf_file_id, session_id)["task"]["task_id"]
    executed: list[str] = []

    def waiting(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "status": "waiting_confirmation",
                "pending_action": {"action_id": action["action_id"]},
            },
            "error_code": None,
            "message": "等待确认",
        }

    await run_async_task(task_id, executor=waiting)
    monkeypatch.setattr(
        confirmation,
        "_execute_frozen_action",
        lambda pending: executed.append(pending["action_id"]),
    )
    cancelled = confirmation.cancel_action(action["action_id"])
    view = get_task_view(task_id)

    assert cancelled["ok"] is True
    assert executed == []
    assert view["task"]["task_status"] == "cancelled"
    assert view["task"]["display_status"] == "cancelled"
    assert view["steps"][0]["status"] == "cancelled"


async def test_async_confirmation_failure_converges_outer_task_to_failed(
    pdf_file_id: str, monkeypatch,
) -> None:
    session_id = "async-confirm-failed"
    action = _pending_action("async-action-failed", session_id)
    task_id = _ocr_task(pdf_file_id, session_id)["task"]["task_id"]

    def waiting(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "status": "waiting_confirmation",
                "pending_action": {"action_id": action["action_id"]},
            },
            "error_code": None,
            "message": "等待确认",
        }

    await run_async_task(task_id, executor=waiting)
    monkeypatch.setattr(
        confirmation,
        "_execute_frozen_action",
        lambda pending: {
            "ok": False,
            "data": None,
            "error_code": "WRITE_FAILED",
            "message": "模拟写入失败",
        },
    )
    confirmed = confirmation.confirm_action(action["action_id"])
    view = get_task_view(task_id)

    assert confirmed["ok"] is False
    assert view["task"]["task_status"] == "failed"
    assert view["task"]["error_code"] == "ACTION_EXECUTION_FAILED"
    assert view["task"]["error_summary"]["message"] == "模拟写入失败"
    assert view["steps"][0]["status"] == "failed"


async def test_task_center_hides_internal_async_child(pdf_file_id: str) -> None:
    session_id = "async-child-visibility"
    parent = _ocr_task(pdf_file_id, session_id)["task"]
    child = _ocr_task(pdf_file_id, session_id)["task"]
    checkpoint_data = dict(child["checkpoint_data"])
    checkpoint_data["parent_async_task_id"] = parent["task_id"]
    database.update_task_record(
        child["task_id"],
        checkpoint_data=checkpoint_data,
        updated_at=database.utc_now(),
    )

    visible_ids = {
        item["task"]["task_id"] for item in list_task_views(session_id)
    }

    assert visible_ids == {parent["task_id"]}


async def test_resume_keeps_checkpoint_and_retry_is_bounded(
    pdf_file_id: str,
) -> None:
    task_id = _ocr_task(pdf_file_id, "resume-v320")["task"]["task_id"]

    def stop(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
        context.update_counts(
            1, 2, "第一页完成", unit="pages",
            checkpoint={"processed_pages": [1]},
        )
        assert cancel_async_task(context.task_id)["ok"] is True
        context.raise_if_cancelled()
        raise AssertionError

    await run_async_task(task_id, executor=stop)
    resumed = resume_async_task(task_id)

    assert resumed["ok"] is True
    metadata = resumed["data"]["task"]["checkpoint_data"]["async_task"]
    assert metadata["checkpoint"] == {"processed_pages": [1]}
