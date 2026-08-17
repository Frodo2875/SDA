"""V2.2 file lifecycle and Human-in-the-loop deletion tests."""

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

from backend import database
from backend.main import app
from backend.services.file_lifecycle import process_uploaded_file
from backend.services import file_upload
from backend.services.file_upload import save_uploaded_file
from backend.tools import excel_utils
from backend.tools.excel_utils import failure
from backend.tools.file_tools import get_file_info, list_files


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client


@pytest.fixture
def lifecycle_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _xlsx_bytes() -> bytes:
    output = BytesIO()
    workbook = Workbook()
    workbook.active.append(["字段"])
    workbook.active.append(["值"])
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _register_uploaded_file(data_dir: Path, file_name: str) -> tuple[Path, dict]:
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / file_name
    path.write_bytes(_xlsx_bytes())
    database.register_file(
        file_name=file_name,
        file_type="excel",
        file_path=f"data/uploads/{file_name}",
        lifecycle_status="uploaded",
        parse_status="pending",
        queryable=False,
    )
    record = database.get_file_record(file_name)
    assert record is not None
    return path, record


def test_upload_transitions_uploaded_processing_ready(
    lifecycle_data_dir: Path,
) -> None:
    path, initial = _register_uploaded_file(lifecycle_data_dir, "状态成功.xlsx")
    assert initial["lifecycle_status"] == "uploaded"
    assert initial["parse_status"] == "pending"
    assert initial["queryable"] == 0

    def inspect_processing(parsed_path: Path, suffix: str):
        assert parsed_path == path
        assert suffix == ".xlsx"
        processing = database.get_file_record_by_id(initial["file_id"])
        assert processing["lifecycle_status"] == "processing"
        assert processing["parse_status"] == "processing"
        assert processing["queryable"] == 0
        return None

    result = process_uploaded_file(initial["file_id"], path, inspect_processing)

    assert result["ok"] is True
    ready = database.get_file_record_by_id(initial["file_id"])
    assert ready["lifecycle_status"] == "ready"
    assert ready["parse_status"] == "parsed"
    assert ready["queryable"] == 1
    assert ready["index_status"] == "not_required"


def test_upload_service_registers_uploaded_before_starting_parser(
    lifecycle_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def inspect_initial_state(file_id: str, path: Path, parser):
        record = database.get_file_record_by_id(file_id)
        observed.update(record)
        return {
            "ok": True,
            "data": {"file_id": file_id},
            "error_code": None,
            "message": "模拟解析完成",
        }

    monkeypatch.setattr(file_upload, "process_uploaded_file", inspect_initial_state)
    result = save_uploaded_file("先登记再解析.xlsx", _xlsx_bytes())

    assert result["ok"] is True
    assert observed["lifecycle_status"] == "uploaded"
    assert observed["parse_status"] == "pending"
    assert observed["queryable"] == 0


def test_parse_failure_is_persisted_and_not_queryable(
    lifecycle_data_dir: Path,
) -> None:
    path, initial = _register_uploaded_file(lifecycle_data_dir, "状态失败.xlsx")

    result = process_uploaded_file(
        initial["file_id"],
        path,
        lambda parsed_path, suffix: failure("FILE_PARSE_ERROR", "模拟解析失败"),
    )

    assert result["ok"] is False
    failed = database.get_file_record_by_id(initial["file_id"])
    assert failed["lifecycle_status"] == "failed"
    assert failed["parse_status"] == "failed"
    assert failed["queryable"] == 0


async def test_system_file_cannot_enter_delete_flow(
    client: httpx.AsyncClient,
) -> None:
    record = database.get_file_record("学生基本信息.xlsx")

    response = await client.post(
        f"/api/files/{record['file_id']}/delete",
        json={"session_id": "system-delete"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "FILE_DELETE_FORBIDDEN"


async def test_delete_file_id_is_validated_by_api_schema(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/files/not-a-file-id/delete",
        json={"session_id": "invalid-file-id"},
    )

    assert response.status_code == 422


async def test_upload_delete_requires_confirmation_and_cancel_keeps_file(
    client: httpx.AsyncClient,
    lifecycle_data_dir: Path,
) -> None:
    upload = save_uploaded_file("取消删除.xlsx", _xlsx_bytes())
    target = lifecycle_data_dir / "uploads" / "取消删除.xlsx"
    before = target.read_bytes()

    prepared = await client.post(
        f"/api/files/{upload['data']['file_id']}/delete",
        json={"session_id": "cancel-delete"},
    )

    assert prepared.status_code == 200
    action = prepared.json()["data"]
    assert action["action_type"] == "delete_file"
    assert action["status"] == "pending"
    assert target.read_bytes() == before

    cancelled = await client.post(f"/api/actions/{action['action_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert target.read_bytes() == before
    assert database.get_file_record_by_id(upload["data"]["file_id"])[
        "lifecycle_status"
    ] == "ready"


async def test_confirmed_upload_delete_keeps_database_and_disk_in_sync(
    client: httpx.AsyncClient,
    lifecycle_data_dir: Path,
) -> None:
    upload = save_uploaded_file("确认删除.xlsx", _xlsx_bytes())
    file_id = upload["data"]["file_id"]
    target = lifecycle_data_dir / "uploads" / "确认删除.xlsx"
    prepared = await client.post(
        f"/api/files/{file_id}/delete",
        json={"session_id": "confirm-delete"},
    )

    confirmed = await client.post(
        f"/api/actions/{prepared.json()['data']['action_id']}/confirm"
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["pending_action"]["status"] == "executed"
    assert not target.exists()
    deleted = database.get_file_record_by_id(file_id)
    assert deleted["lifecycle_status"] == "deleted"
    assert deleted["queryable"] == 0
    assert "确认删除.xlsx" not in {
        item["file_name"] for item in list_files()["data"]
    }


async def test_physical_delete_failure_restores_file_and_marks_cleanup_failed(
    client: httpx.AsyncClient,
    lifecycle_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = save_uploaded_file("删除失败.xlsx", _xlsx_bytes())
    file_id = upload["data"]["file_id"]
    target = lifecycle_data_dir / "uploads" / "删除失败.xlsx"
    prepared = await client.post(
        f"/api/files/{file_id}/delete",
        json={"session_id": "failed-delete"},
    )
    original_unlink = Path.unlink

    def fail_staging_unlink(path: Path, *args, **kwargs):
        if path.name.startswith(".delete-"):
            raise OSError("simulated physical failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staging_unlink)
    confirmed = await client.post(
        f"/api/actions/{prepared.json()['data']['action_id']}/confirm"
    )

    assert confirmed.status_code == 500
    assert confirmed.json()["error_code"] == "ACTION_EXECUTION_FAILED"
    assert target.is_file()
    failed = database.get_file_record_by_id(file_id)
    assert failed["lifecycle_status"] == "cleanup_failed"
    assert failed["queryable"] == 0


def test_lifecycle_and_file_metadata_survive_restart(
    lifecycle_data_dir: Path,
) -> None:
    path, record = _register_uploaded_file(lifecycle_data_dir, "重启状态.xlsx")
    database.update_file_state(
        file_id=record["file_id"],
        lifecycle_status="processing",
        parse_status="processing",
        queryable=False,
        expected_lifecycle="uploaded",
    )

    database.initialize_database()

    restarted = database.get_file_record_by_id(record["file_id"])
    assert restarted["lifecycle_status"] == "processing"
    assert restarted["parse_status"] == "processing"
    assert restarted["queryable"] == 0
    info = get_file_info(path.name)
    assert info["ok"] is True
    assert info["data"]["source_type"] == "upload"
    assert info["data"]["lifecycle_status"] == "processing"
    assert info["data"]["parse_status"] == "processing"
    assert info["data"]["queryable"] is False
    assert info["data"]["index_status"] == "not_required"


def test_list_files_exposes_lifecycle_without_removing_v1_fields(
    lifecycle_data_dir: Path,
) -> None:
    upload = save_uploaded_file("列表状态.xlsx", _xlsx_bytes())

    item = next(
        value
        for value in list_files()["data"]
        if value["file_name"] == "列表状态.xlsx"
    )
    assert {
        "file_name",
        "file_type",
        "path",
        "size",
        "exists",
        "file_id",
        "source_type",
        "lifecycle_status",
        "parse_status",
        "queryable",
        "index_status",
    } <= item.keys()
    assert item["file_id"] == upload["data"]["file_id"]
    assert item["lifecycle_status"] == "ready"
    assert item["queryable"] is True
