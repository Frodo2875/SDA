"""V4.1 Visual Document Router and formal image Document coverage."""

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from backend import database
from backend.main import app
from backend.runtime.async_task_runtime import enqueue_async_task, run_async_task
from backend.services import document_index, input_router
from backend.services.file_lifecycle import get_file_lifecycle
from backend.services.file_upload import save_uploaded_file
from backend.services.input_router import route_document_input
from backend.services.task_view import get_task_view
from backend.tools import excel_utils
from frontend import api_client
from frontend.components.file_panel import (
    file_card_fields,
    file_detail_sections,
    file_operation_capabilities,
    filter_files_by_lifecycle,
)


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest.fixture
def image_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _image_bytes(image_format: str, size: tuple[int, int] = (24, 16)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(30, 80, 160)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("file_name", "image_format", "declared_mime", "expected_mime"),
    [
        ("材料.jpg", "JPEG", "image/jpeg", "image/jpeg"),
        ("材料.jpeg", "JPEG", "image/jpeg", "image/jpeg"),
        ("材料.png", "PNG", "image/png", "image/png"),
    ],
)
async def test_jpg_jpeg_png_upload_as_formal_visual_documents(
    client: httpx.AsyncClient,
    image_data_dir: Path,
    file_name: str,
    image_format: str,
    declared_mime: str,
    expected_mime: str,
) -> None:
    response = await client.post(
        "/api/files/upload",
        files={"file": (file_name, _image_bytes(image_format), declared_mime)},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["file_type"] == "image"
    assert payload["input_route"] == "VISUAL"
    assert payload["mime_type"] == expected_mime
    record = database.get_file_record(file_name)
    assert record["file_type"] == "image"
    assert record["lifecycle_status"] == "ready"
    assert record["parse_status"] == "parsed"
    assert record["index_status"] == "indexed"
    assert record["queryable"] == 1
    chunks = database.get_document_chunks(record["file_id"])
    assert len(chunks) == 1
    assert chunks[0]["chunk_text"] == ""
    assert chunks[0]["metadata"]["input_route"] == "VISUAL"
    assert chunks[0]["metadata"]["text_extracted"] is False


async def test_invalid_mismatched_and_corrupt_images_are_rejected(
    client: httpx.AsyncClient, image_data_dir: Path
) -> None:
    mismatched = await client.post(
        "/api/files/upload",
        files={"file": ("伪装.jpg", _image_bytes("PNG"), "image/jpeg")},
    )
    corrupt = await client.post(
        "/api/files/upload",
        files={"file": ("损坏.png", b"\x89PNG\r\n\x1a\ntruncated", "image/png")},
    )

    assert mismatched.status_code == 400
    assert mismatched.json()["error_code"] == "INVALID_FILE_CONTENT"
    assert corrupt.status_code == 400
    assert corrupt.json()["error_code"] == "INVALID_FILE_CONTENT"
    assert database.get_file_record("伪装.jpg") is None
    assert database.get_file_record("损坏.png") is None


async def test_unsupported_image_mime_is_explainably_rejected(
    client: httpx.AsyncClient, image_data_dir: Path
) -> None:
    response = await client.post(
        "/api/files/upload",
        files={"file": ("错误MIME.png", _image_bytes("PNG"), "text/plain")},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "UNSUPPORTED_MIME_TYPE"
    assert body["data"]["route"] == "SAFE_REJECT"
    assert database.get_file_record("错误MIME.png") is None


async def test_image_lifecycle_uses_visual_layout_index_and_queryable_states(
    client: httpx.AsyncClient, image_data_dir: Path
) -> None:
    uploaded = await client.post(
        "/api/files/upload",
        files={"file": ("状态.png", _image_bytes("PNG"), "image/png")},
    )
    file_id = uploaded.json()["data"]["file_id"]
    traces = database.get_session_trace_records(f"file-lifecycle:{file_id}", 100)
    statuses = {
        json.loads(trace["result_summary"])["status"]
        for trace in traces
        if trace["event_type"] == "file_lifecycle_transition"
    }

    assert {
        "DETECTING",
        "VISUAL_PROCESSING",
        "LAYOUT_PROCESSING",
        "INDEXING",
        "QUERYABLE",
    } <= statuses
    assert get_file_lifecycle(file_id)["data"]["status"] == "QUERYABLE"


async def test_image_processing_failure_persists_error_summary_after_registration(
    client: httpx.AsyncClient,
    image_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        document_index,
        "route_document_input",
        lambda path: {
            "ok": False,
            "data": None,
            "error_code": "IMAGE_DETECTION_ERROR",
            "message": "模拟视觉检测失败",
        },
    )
    response = await client.post(
        "/api/files/upload",
        files={"file": ("处理失败.jpg", _image_bytes("JPEG"), "image/jpeg")},
    )

    assert response.status_code == 500
    record = database.get_file_record("处理失败.jpg")
    assert record is not None
    assert record["lifecycle_status"] == "failed"
    error = get_file_lifecycle(record["file_id"])["data"]["error"]
    assert error["error_code"] == "IMAGE_DETECTION_ERROR"
    assert error["failure_stage"] == "detect"
    assert error["message"] == "模拟视觉检测失败"


async def test_batch_image_upload_keeps_per_file_partial_failure(
    image_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = [
        SimpleNamespace(
            name="批量一.jpg", type="image/jpeg", getvalue=lambda: _image_bytes("JPEG")
        ),
        SimpleNamespace(
            name="批量损坏.png", type="image/png", getvalue=lambda: b"broken"
        ),
        SimpleNamespace(
            name="批量二.png", type="image/png", getvalue=lambda: _image_bytes("PNG")
        ),
    ]

    def upload_one(uploaded: SimpleNamespace) -> dict:
        result = save_uploaded_file(
            uploaded.name,
            uploaded.getvalue(),
            declared_mime_type=uploaded.type,
        )
        if not result["ok"]:
            raise RuntimeError(result["message"])
        return result

    monkeypatch.setattr(api_client, "upload_file", upload_one)
    outcomes = api_client.upload_files(uploads)

    assert [item["status"] for item in outcomes] == ["success", "failed", "success"]
    assert database.get_file_record("批量一.jpg") is not None
    assert database.get_file_record("批量损坏.png") is None
    assert database.get_file_record("批量二.png") is not None


async def test_image_reprocess_replaces_candidate_and_failure_preserves_old_index(
    client: httpx.AsyncClient, image_data_dir: Path
) -> None:
    uploaded = await client.post(
        "/api/files/upload",
        files={"file": ("重处理.png", _image_bytes("PNG", (20, 10)), "image/png")},
    )
    file_id = uploaded.json()["data"]["file_id"]
    path = image_data_dir / "uploads" / "重处理.png"
    old_chunks = database.get_document_chunks(file_id)

    path.write_bytes(_image_bytes("PNG", (40, 30)))
    success_response = await client.post(f"/api/files/{file_id}/reprocess")
    replaced = database.get_document_chunks(file_id)
    assert success_response.status_code == 200
    assert replaced != old_chunks
    assert replaced[0]["metadata"]["width"] == 40
    assert replaced[0]["metadata"]["height"] == 30

    path.write_bytes(b"corrupt image")
    before_failure = database.get_document_chunks(file_id)
    failed_response = await client.post(f"/api/files/{file_id}/reprocess")
    detail = (await client.get(f"/api/files/{file_id}")).json()["data"]

    assert failed_response.status_code >= 400
    assert database.get_document_chunks(file_id) == before_failure
    assert get_file_lifecycle(file_id)["data"]["status"] == "QUERYABLE"
    assert detail["error_summary"]["failure_stage"] == "detect"
    assert detail["error_summary"]["error_code"] == "INVALID_FILE_CONTENT"


async def test_workspace_lists_and_previews_image_document(
    client: httpx.AsyncClient, image_data_dir: Path
) -> None:
    uploaded = await client.post(
        "/api/files/upload",
        files={"file": ("工作区.jpg", _image_bytes("JPEG", (32, 18)), "image/jpeg")},
    )
    file_id = uploaded.json()["data"]["file_id"]
    listed = await client.get("/api/files", params={"file_type": "image"})
    preview = await client.get(f"/api/files/{file_id}/preview")

    assert [item["file_name"] for item in listed.json()["data"]] == ["工作区.jpg"]
    item = listed.json()["data"][0]
    assert item["input_route"] == "VISUAL"
    assert item["image_format"] == "JPEG"
    preview_data = preview.json()["data"]
    assert preview_data["preview_type"] == "image"
    assert preview_data["mime_type"] == "image/jpeg"
    assert preview_data["width"] == 32
    with Image.open(BytesIO(base64.b64decode(preview_data["content_base64"]))) as image:
        assert image.format == "JPEG"


async def test_task_center_exposes_image_processing_document(
    image_data_dir: Path,
) -> None:
    uploaded = save_uploaded_file(
        "任务图片.png", _image_bytes("PNG"), declared_mime_type="image/png"
    )
    file_id = uploaded["data"]["file_id"]
    queued = enqueue_async_task(
        {
            "session_id": "v4-image-task",
            "task_type": "reindex",
            "payload": {"file_id": file_id},
        }
    )
    task_id = queued["data"]["task"]["task_id"]
    queued_view = get_task_view(task_id)["task"]

    assert queued_view["display_status"] == "queued"
    assert queued_view["document"] == {
        "file_id": file_id,
        "file_name": "任务图片.png",
        "file_type": "image",
    }
    completed = await run_async_task(task_id)
    assert completed["data"]["task"]["display_status"] == "success"


def test_router_preserves_excel_word_pdf_routes_and_safely_rejects_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel = tmp_path / "表格.xlsx"
    word = tmp_path / "材料.docx"
    pdf = tmp_path / "材料.pdf"
    unknown = tmp_path / "材料.bin"
    for path in (excel, word, pdf, unknown):
        path.write_bytes(b"fixture")

    assert route_document_input(excel)["data"]["route"] == "STRUCTURED"
    assert route_document_input(word)["data"]["route"] == "TEXT"
    monkeypatch.setattr(
        input_router.ocr_service,
        "detect_pdf_type",
        lambda path: {
            "ok": True,
            "data": {
                "pdf_type": "text",
                "page_count": 1,
                "text_pages": [1],
                "needs_ocr_pages": [],
            },
            "error_code": None,
            "message": "ok",
        },
    )
    assert route_document_input(pdf)["data"]["route"] == "TEXT"
    monkeypatch.setattr(
        input_router.ocr_service,
        "detect_pdf_type",
        lambda path: {
            "ok": True,
            "data": {
                "pdf_type": "scanned",
                "page_count": 1,
                "text_pages": [],
                "needs_ocr_pages": [1],
            },
            "error_code": None,
            "message": "ok",
        },
    )
    assert route_document_input(pdf)["data"]["route"] == "VISUAL"
    rejected = route_document_input(unknown)
    assert rejected["ok"] is False
    assert rejected["error_code"] == "UNSUPPORTED_FILE_TYPE"
    assert rejected["data"]["route"] == "SAFE_REJECT"


def test_frontend_minimally_recognizes_image_documents_and_visual_status() -> None:
    item = {
        "file_id": "a" * 32,
        "file_name": "证明.png",
        "file_type": "image",
        "size": 2048,
        "created_time": "2026-08-28T12:00:00+08:00",
        "lifecycle_status": "processing",
        "canonical_status": "VISUAL_PROCESSING",
        "index_status": "not_required",
        "queryable": False,
        "source_type": "upload",
        "deletable": True,
    }

    assert file_card_fields(item)["file_type"] == "图片"
    assert file_card_fields(item)["lifecycle_status"] == "视觉处理中"
    assert filter_files_by_lifecycle([item], "VISUAL_PROCESSING") == [item]
    capabilities = file_operation_capabilities(item)
    assert capabilities["preview"] is True
    assert capabilities["reprocess"] is True
    assert capabilities["reindex"] is True
    assert file_detail_sections(
        {
            **item,
            "image_format": "PNG",
            "mime_type": "image/png",
            "width": 800,
            "height": 600,
            "input_route": "VISUAL",
            "visual_status": "routed",
        }
    )["图片"] == {
        "格式": "PNG",
        "MIME": "image/png",
        "尺寸": "800 × 600",
        "输入路由": "VISUAL",
        "视觉状态": "routed",
    }
