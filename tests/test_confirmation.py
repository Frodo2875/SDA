"""Integration tests for two-phase Human-in-the-loop Word writes."""

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from docx import Document

from backend import agent, main
from backend.services import confirmation
from backend.tools import excel_utils
from backend.main import app


pytestmark = pytest.mark.anyio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_WORD = PROJECT_ROOT / "data" / "综合评价.docx"
FROZEN_CONTENT = "S001 综合评价：该生学习认真，成绩优秀，积极参与科研与竞赛。"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client


@pytest.fixture
def isolated_word_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route all data-file resolution to a complete isolated data set."""
    for source in (PROJECT_ROOT / "data").iterdir():
        if source.is_file() and source.suffix.lower() in {".xlsx", ".docx"}:
            shutil.copy2(source, tmp_path / source.name)
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    yield tmp_path


class WriteRequestClient:
    """Generate all required read Tool calls and then a frozen evaluation."""

    def __init__(self) -> None:
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        self.count += 1
        tool_names = {tool["function"]["name"] for tool in tools}
        assert "write_word" not in tool_names
        if self.count == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    self._call("info", "get_student_info", {"student_id": "S001"}),
                    self._call("scores", "get_student_scores", {"student_id": "S001"}),
                    self._call(
                        "research", "get_student_research", {"student_id": "S001"}
                    ),
                ],
            }
        assert messages[-1]["role"] == "tool"
        return {"role": "assistant", "content": FROZEN_CONTENT}

    @staticmethod
    def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }


def _paragraphs(path: Path) -> list[str]:
    return [paragraph.text for paragraph in Document(path).paragraphs if paragraph.text]


async def _request_pending_action(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> dict[str, Any]:
    monkeypatch.setattr(agent, "LLMClient", WriteRequestClient)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "把S001评价写进综合评价.docx",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmation_required"
    assert body["pending_action"]["status"] == "pending"
    assert body["pending_action"]["content"] == FROZEN_CONTENT
    assert body["pending_action"]["student_id"] == "S001"
    assert body["pending_action"]["student_name"] == "张三"
    assert body["pending_action"]["target_file"] == "综合评价.docx"
    assert [call["name"] for call in body["tool_calls"]] == [
        "get_student_info",
        "get_student_scores",
        "get_student_research",
    ]
    return body["pending_action"]


async def test_write_request_creates_pending_action_without_changing_file(
    client: httpx.AsyncClient,
    isolated_word_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = isolated_word_dir / "综合评价.docx"
    before = target.read_bytes()
    paragraphs_before = _paragraphs(target)

    action = await _request_pending_action(client, monkeypatch, "pending-test")

    assert action["action_type"] == "write_word"
    assert target.read_bytes() == before
    assert _paragraphs(target) == paragraphs_before


async def test_cancel_keeps_file_unchanged(
    client: httpx.AsyncClient,
    isolated_word_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = isolated_word_dir / "综合评价.docx"
    before = target.read_bytes()
    action = await _request_pending_action(client, monkeypatch, "cancel-test")

    response = await client.post(f"/api/actions/{action['action_id']}/cancel")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"
    assert target.read_bytes() == before
    confirm_response = await client.post(
        f"/api/actions/{action['action_id']}/confirm"
    )
    assert confirm_response.status_code == 409
    assert target.read_bytes() == before


async def test_confirm_writes_frozen_content_once_and_repeat_is_idempotent(
    client: httpx.AsyncClient,
    isolated_word_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = isolated_word_dir / "综合评价.docx"
    action = await _request_pending_action(client, monkeypatch, "confirm-test")

    first_response = await client.post(
        f"/api/actions/{action['action_id']}/confirm",
        json={"content": "攻击者试图替换的内容"},
    )

    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["data"]["pending_action"]["status"] == "executed"
    assert first_body["data"]["pending_action"]["content"] == FROZEN_CONTENT
    paragraphs_after_first = _paragraphs(target)
    bytes_after_first = target.read_bytes()
    assert paragraphs_after_first.count(FROZEN_CONTENT) == 1
    assert all("攻击者试图替换" not in text for text in paragraphs_after_first)

    second_response = await client.post(
        f"/api/actions/{action['action_id']}/confirm"
    )

    assert second_response.status_code == 409
    assert second_response.json()["error_code"] == "ACTION_NOT_PENDING"
    assert target.read_bytes() == bytes_after_first
    assert _paragraphs(target).count(FROZEN_CONTENT) == 1


async def test_new_action_after_cancel_can_be_confirmed(
    client: httpx.AsyncClient,
    isolated_word_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = await _request_pending_action(client, monkeypatch, "first-action")
    cancel_response = await client.post(
        f"/api/actions/{cancelled['action_id']}/cancel"
    )
    assert cancel_response.status_code == 200

    new_action = await _request_pending_action(client, monkeypatch, "second-action")
    assert new_action["action_id"] != cancelled["action_id"]
    confirm_response = await client.post(
        f"/api/actions/{new_action['action_id']}/confirm"
    )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["data"]["pending_action"]["status"] == "executed"
    assert _paragraphs(isolated_word_dir / "综合评价.docx").count(FROZEN_CONTENT) == 1


async def test_unknown_action_returns_not_found(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/actions/not-found/confirm")

    assert response.status_code == 404
    assert response.json()["error_code"] == "ACTION_NOT_FOUND"


async def test_openapi_exposes_confirmation_routes(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/actions/{action_id}/confirm" in paths
    assert "/api/actions/{action_id}/cancel" in paths


def test_failed_write_sets_action_status_failed(
    isolated_word_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    action_result = confirmation.create_pending_action(
        session_id="failed-write",
        target_file="综合评价.docx",
        student_id="S001",
        student_name="张三",
        content=FROZEN_CONTENT,
    )

    monkeypatch.setattr(
        confirmation,
        "write_word",
        lambda file_name, content: {
            "ok": False,
            "data": None,
            "error_code": "WORD_WRITE_ERROR",
            "message": "模拟写入失败",
        },
    )
    result = confirmation.confirm_action(action_result["data"]["action_id"])

    assert result["ok"] is False
    assert result["error_code"] == "ACTION_EXECUTION_FAILED"
    assert result["data"]["pending_action"]["status"] == "failed"


def test_confirm_reserves_action_before_write(
    isolated_word_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    action_result = confirmation.create_pending_action(
        session_id="reserve-write",
        target_file="综合评价.docx",
        student_id="S001",
        student_name="张三",
        content=FROZEN_CONTENT,
    )
    action_id = action_result["data"]["action_id"]
    nested_result = None

    def inspect_during_write(file_name, content):
        nonlocal nested_result
        nested_result = confirmation.confirm_action(action_id)
        return {
            "ok": True,
            "data": {"file_name": file_name, "mode": "append"},
            "error_code": None,
            "message": "模拟写入成功",
        }

    monkeypatch.setattr(confirmation, "write_word", inspect_during_write)
    result = confirmation.confirm_action(action_id)

    assert result["ok"] is True
    assert result["data"]["pending_action"]["status"] == "executed"
    assert nested_result["ok"] is False
    assert nested_result["error_code"] == "ACTION_NOT_PENDING"
    assert nested_result["data"]["status"] == "confirmed"
