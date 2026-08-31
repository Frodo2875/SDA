"""V4.6 Evidence 3.0 compatibility and visual localization tests."""

from pathlib import Path
from typing import Any

from PIL import Image

from backend import database
from backend.agent import _collect_evidence
from backend.evidence import (
    build_evidence,
    deserialize_evidence,
    make_evidence_id,
    serialize_evidence,
)
from backend.services import ocr_service
from backend.services.evidence_locator import locate_evidence
from backend.services.visual_table import calculate_visual_table, extract_visual_tables
from backend.services.visual_understanding import extract_key_fields
from backend.tools import excel_utils


def _register_visual(
    data_dir: Path,
    name: str,
    regions: list[dict[str, Any]],
    *,
    file_type: str = "image",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(exist_ok=True)
    path = upload_dir / name
    if file_type == "image":
        Image.new("RGB", (320, 180), "white").save(path)
    else:
        path.write_bytes(b"%PDF-1.4\n% visual evidence fixture")
    database.register_file(
        file_name=name,
        file_type=file_type,
        file_path=f"data/uploads/{name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    record = database.get_file_record(name)
    normalized = [
        ocr_service.normalize_region_record(
            file_id=record["file_id"],
            page_no=int(region.get("page_no") or 1),
            region_index=index,
            region=region,
            image_input=file_type == "image",
        )
        for index, region in enumerate(regions, start=1)
    ]
    database.replace_document_ocr_pages(record["file_id"], [{
        "page_no": 1,
        "text": "\n".join(item["text"] for item in normalized),
        "bbox": [0.0, 0.0, 320.0, 180.0],
        "confidence": min(
            (item["confidence"] for item in normalized if item.get("confidence") is not None),
            default=None,
        ),
        "status": "success",
        "error": None,
        "source_type": "ocr",
        "blocks": normalized,
        "updated_at": database.utc_now(),
    }])
    return record, normalized


def _raw_region(
    text: str,
    bbox: list[float],
    *,
    confidence: float = 0.96,
    recognition_type: str = "printed",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "text": text,
        "bbox": bbox,
        "confidence": confidence,
        "recognition_type": recognition_type,
        "source_parser": "fixture-ocr",
        "source_model": "fixture-model",
        **extra,
    }


def test_evidence_3_serialization_accepts_legacy_evidence_2_payload() -> None:
    legacy = {
        "evidence_id": "legacy-evidence",
        "task_id": None,
        "source_type": "unstructured",
        "file_id": "legacy-file",
        "file_name": "legacy.pdf",
        "sheet": None,
        "page_no": 2,
        "chunk_id": "legacy-chunk",
        "block_id": "legacy-block",
        "field": None,
        "record_key": None,
        "value_summary": "原有 Evidence 2.0",
        "score": 0.91,
        "text_excerpt": "原有 Evidence 2.0",
    }

    loaded = deserialize_evidence(legacy)
    serialized = serialize_evidence(loaded)

    assert loaded.evidence_version == "2.0"
    assert serialized["evidence_id"] == legacy["evidence_id"]
    assert serialized["chunk_id"] == legacy["chunk_id"]
    assert serialized["block_id"] == legacy["block_id"]
    assert serialized["score"] == 0.91


def test_image_region_evidence_opens_preview_and_highlights_bbox(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    record, regions = _register_visual(
        tmp_path, "printed.png", [_raw_region("奖学金申请", [12, 18, 180, 52])]
    )
    region = regions[0]
    evidence = build_evidence(
        evidence_id=make_evidence_id(file_id=record["file_id"], region=region["region_id"]),
        source_type="unstructured",
        locator_type="image",
        file_id=record["file_id"],
        file_name=record["file_name"],
        page_no=1,
        image_no=1,
        region_id=region["region_id"],
        bbox=region["bbox"],
        confidence=region["confidence"],
        recognition_type="printed",
        source_parser=region["source_parser"],
        source_model=region["source_model"],
        value_summary=region["text"],
        text_excerpt=region["text"],
    )

    located = locate_evidence(evidence["evidence_id"], session_id="v4-image")

    assert located["ok"] is True
    assert located["data"]["location_type"] == "image"
    assert located["data"]["region_id"] == region["region_id"]
    assert located["data"]["highlight"] == {"bbox": [12.0, 18.0, 180.0, 52.0]}
    assert located["data"]["preview"]["content_base64"]


def test_visual_pdf_region_localizes_without_requiring_a_text_chunk(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    record, regions = _register_visual(
        tmp_path,
        "scan.pdf",
        [_raw_region("扫描页原文", [20, 30, 210, 70])],
        file_type="pdf",
    )
    region = regions[0]
    evidence = build_evidence(
        evidence_id=make_evidence_id(file_id=record["file_id"], region=region["region_id"]),
        source_type="unstructured",
        locator_type="visual_pdf",
        file_id=record["file_id"],
        file_name=record["file_name"],
        page_no=1,
        region_id=region["region_id"],
        bbox=region["bbox"],
        confidence=region["confidence"],
        recognition_type="printed",
        value_summary=region["text"],
    )

    located = locate_evidence(evidence["evidence_id"], session_id="v4-pdf-region")

    assert located["ok"] is True
    assert located["data"]["location_type"] == "pdf"
    assert located["data"]["page_no"] == 1
    assert located["data"]["region_id"] == region["region_id"]
    assert located["data"]["highlight"] == {"bbox": region["bbox"]}


def test_kie_field_persists_actual_region_and_bbox_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    record, regions = _register_visual(
        tmp_path, "form.png", [
            _raw_region("学生登记表", [30, 8, 200, 30]),
            _raw_region("姓名：张三", [30, 40, 200, 72]),
        ]
    )

    result = extract_key_fields(record["file_id"], domain="student")
    evidence = result["data"]["fields"][0]["evidence"]
    persisted = database.get_evidence_location(evidence["evidence_id"])

    assert persisted is not None
    assert persisted["region_id"] == regions[1]["region_id"]
    assert persisted["bbox"] == [30.0, 40.0, 200.0, 72.0]
    assert persisted["field"] == "name"
    assert locate_evidence(evidence["evidence_id"])["ok"] is True


def test_handwriting_conflict_keeps_all_sources_and_requires_review(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    candidates = [
        {"text": "张三", "confidence": 0.61, "recognition_type": "handwritten", "source_model": "hw"},
        {"text": "张川", "confidence": 0.66, "recognition_type": "printed", "source_model": "ocr"},
    ]
    record, regions = _register_visual(
        tmp_path,
        "conflict.png",
        [_raw_region(
            "", [10, 10, 120, 48], confidence=0.61,
            recognition_type="handwritten", conflict_sources=candidates,
            review_required=True,
        )],
    )
    region = regions[0]
    evidence = build_evidence(
        evidence_id=make_evidence_id(file_id=record["file_id"], conflict=region["region_id"]),
        source_type="unstructured",
        locator_type="handwriting",
        file_id=record["file_id"],
        file_name=record["file_name"],
        page_no=1,
        image_no=1,
        region_id=region["region_id"],
        bbox=region["bbox"],
        confidence=region["confidence"],
        recognition_type="handwritten",
        conflict_sources=region["conflict_sources"],
        value_summary="识别结果冲突，需核对",
        text_excerpt="识别结果冲突，需核对",
    )

    located = locate_evidence(evidence["evidence_id"], session_id="v4-conflict")

    assert evidence["evidence_status"] == "conflict"
    assert evidence["review_required"] is True
    assert evidence["handwriting_confidence"] == 0.61
    assert {item["text"] for item in evidence["conflict_sources"]} == {"张三", "张川"}
    assert located["data"]["text"] == "识别结果冲突，需核对"
    assert located["data"]["conflict_sources"] == evidence["conflict_sources"]


def test_visual_table_calculation_evidence_highlights_exact_cell(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    raw = [
        _raw_region(
            text, [10 + column * 80, 10 + row * 40, 80 + column * 80, 42 + row * 40],
            visual_block_type="table", table_id_hint="scores",
            row_index=row, column_index=column,
        )
        for row, values in enumerate((["姓名", "成绩"], ["张三", "96"]))
        for column, text in enumerate(values)
    ]
    record, _ = _register_visual(tmp_path, "table.png", raw)
    extracted = extract_visual_tables(record["file_id"])
    table = extracted["data"]["tables"][0]["table"]
    score_cell = next(cell for cell in table["cells"] if cell["cell_text"] == "96")

    calculated = calculate_visual_table(
        record["file_id"], table["table_id"], operation="sum",
        cell_ids=[score_cell["cell_id"]],
    )
    evidence = calculated["data"]["evidence_chain"][0]
    located = locate_evidence(evidence["evidence_id"], session_id="v4-cell")

    assert calculated["data"]["value"] == 96
    assert evidence["table"] == table["table_id"]
    assert evidence["cell"] == score_cell["cell_id"]
    assert located["ok"] is True
    assert located["data"]["cell_id"] == score_cell["cell_id"]
    assert located["data"]["highlight"] == {"bbox": score_cell["bbox"]}


def test_memory_and_history_are_never_exposed_as_fact_evidence() -> None:
    actual = {"evidence_id": "actual", "source_type": "unstructured"}
    memory = {"evidence_id": "memory", "source_type": "memory"}
    history = {"evidence_id": "history", "source_kind": "history"}

    assert _collect_evidence([{
        "name": "retrieve_document",
        "result": {"evidence": [memory, actual, history]},
    }]) == [actual]
