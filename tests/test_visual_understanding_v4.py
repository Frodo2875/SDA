"""V4.4 deterministic visual document understanding and basic KIE tests."""

from pathlib import Path
from typing import Any

import pytest

from backend import database
from backend.document_blocks import block_to_chunks, make_document_block
from backend.services import ocr_service, visual_understanding
from backend.services.document_index import retrieve_document
from backend.services.visual_understanding import (
    classify_document,
    extract_key_fields,
    extract_visual_blocks,
)
from backend.tools import excel_utils


@pytest.fixture()
def visual_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _region(
    text: str,
    index: int,
    *,
    block_type: str | None = None,
    confidence: float = 0.96,
    recognition_type: str = "printed",
    status: str = "success",
) -> dict[str, Any]:
    return {
        "region_id": f"region-{index}",
        "page_no": 1,
        "image_no": 1,
        "text": text,
        "bbox": [10.0, float(index * 30), 240.0, float(index * 30 + 22)],
        "confidence": confidence,
        "recognition_type": recognition_type,
        "source_model": "visual-layout-fixed-v1",
        "source_parser": "visual-layout-adapter",
        "status": status,
        "visual_block_type": block_type,
    }


def _document(
    visual_data_dir: Path,
    name: str,
    regions: list[dict[str, Any]],
    *,
    queryable: bool = False,
) -> str:
    path = visual_data_dir / "uploads" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"visual fixture")
    database.register_file(
        file_name=name,
        file_type="image",
        file_path=f"data/uploads/{name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=queryable,
        index_status="indexed" if queryable else "not_required",
    )
    record = database.get_file_record(name)
    normalized = [
        ocr_service.normalize_region_record(
            file_id=record["file_id"], page_no=1, region_index=index,
            region=region, image_input=True,
        )
        for index, region in enumerate(regions, start=1)
    ]
    database.replace_document_ocr_pages(record["file_id"], [{
        "page_no": 1,
        "text": "\n".join(item["text"] for item in normalized if item["text"]),
        "bbox": [10.0, 30.0, 240.0, float(max(1, len(normalized)) * 30 + 22)],
        "confidence": min(
            (item["confidence"] for item in normalized if item["confidence"] is not None),
            default=None,
        ),
        "status": "success",
        "error": None,
        "source_type": "ocr",
        "blocks": normalized,
        "updated_at": database.utc_now(),
    }])
    return record["file_id"]


@pytest.mark.parametrize(
    ("name", "title", "expected_type"),
    [
        ("证书.png", "优秀学生荣誉证书", "certificate"),
        ("表单.png", "学生信息登记表", "form"),
        ("通知.png", "关于开学安排的通知", "notice"),
    ],
)
def test_certificate_form_and_notice_classification(
    visual_data_dir: Path, name: str, title: str, expected_type: str
) -> None:
    file_id = _document(visual_data_dir, name, [
        _region(title, 1, block_type="title"),
        _region("正文内容", 2, block_type="paragraph"),
    ])

    result = classify_document(file_id)

    assert result["ok"] is True
    assert result["data"]["document_type"] == expected_type
    assert result["data"]["status"] == "success"
    assert result["data"]["general_query_allowed"] is True
    assert result["data"]["evidence_block_ids"]
    assert result["data"]["evidence_region_ids"] == ["region-1"]


def test_visual_blocks_cover_required_types_and_parent_provenance(
    visual_data_dir: Path,
) -> None:
    regions = [
        _region("材料标题", 1, block_type="title"),
        _region("正文段落", 2, block_type="paragraph"),
        _region("列一 | 列二 | 列三", 3, block_type="table"),
        _region("", 4, block_type="image"),
        _region("签名：张三", 5, block_type="signature", recognition_type="handwritten"),
        _region("单位公章", 6, block_type="stamp"),
        _region("", 7, block_type="unknown"),
    ]
    file_id = _document(visual_data_dir, "全部Block.png", regions)
    result = extract_visual_blocks(file_id)
    blocks = result["data"]["blocks"]

    assert result["ok"] is True
    assert {block["block_type"] for block in blocks} == {
        "title", "paragraph", "table", "image", "signature", "stamp", "unknown"
    }
    required = {
        "file_id", "document_id", "page_no", "image_no", "block_id", "block_type",
        "text", "bbox", "confidence", "recognition_type", "source_parser",
        "source_model", "parent_id",
    }
    assert all(required <= set(block) for block in blocks)
    title_id = blocks[0]["block_id"]
    assert blocks[0]["parent_id"] is None
    assert all(block["parent_id"] == title_id for block in blocks[1:])
    assert [block["source_region_ids"] for block in blocks] == [
        [f"region-{index}"] for index in range(1, 8)
    ]


def test_basic_kie_field_keeps_bbox_and_source_region_without_mutating_ocr(
    visual_data_dir: Path,
) -> None:
    file_id = _document(visual_data_dir, "申请表.png", [
        _region("申请表", 1, block_type="title"),
        _region("申请事项：困难补助", 2, block_type="paragraph"),
    ])
    before = database.get_document_ocr_pages(file_id)
    result = extract_key_fields(file_id)
    field = result["data"]["fields"][0]

    assert result["ok"] is True
    assert result["data"]["schema"] == "general_field_value"
    assert result["data"]["source_ocr_preserved"] is True
    assert field["field_name"] == "申请事项"
    assert field["value"] == "困难补助"
    assert field["bbox"] == [10.0, 60.0, 240.0, 82.0]
    assert field["source_region_id"] == "region-2"
    assert field["evidence"]["region_id"] == "region-2"
    assert field["evidence"]["block_id"] == field["block_id"]
    assert field["is_confirmed_fact"] is False
    assert database.get_document_ocr_pages(file_id) == before


def test_low_confidence_kie_stays_review_candidate(
    visual_data_dir: Path,
) -> None:
    file_id = _document(visual_data_dir, "低置信字段.png", [
        _region("金额：8800元", 1, block_type="paragraph", confidence=0.61,
                recognition_type="handwritten"),
    ])

    result = extract_key_fields(file_id)
    field = result["data"]["fields"][0]

    assert result["data"]["status"] == "partial_success"
    assert field["confidence"] == 0.61
    assert field["status"] == "review_required"
    assert field["is_confirmed_fact"] is False
    assert field["safe_for_high_impact"] is False


def test_unknown_document_remains_available_for_general_query(
    visual_data_dir: Path,
) -> None:
    file_id = _document(
        visual_data_dir,
        "未知材料.png",
        [_region("蓝色材料内容 关键字", 1, block_type="paragraph")],
        queryable=True,
    )
    block = make_document_block(
        file_id=file_id, sequence=0, block_type="paragraph",
        content="蓝色材料内容 关键字", page_no=1,
        source_parser="visual-layout-adapter",
    )
    database.replace_document_chunks(file_id, block_to_chunks(block, source_type="ocr"))

    classification = classify_document(file_id)
    retrieved = retrieve_document({"file_id": file_id}, "关键字", 3)

    assert classification["data"] == {
        **classification["data"],
        "document_type": "unknown",
        "status": "failed",
        "general_query_allowed": True,
    }
    assert retrieved["ok"] is True
    assert retrieved["data"]["status"] == "found"


def test_classification_exception_falls_back_to_unknown(
    visual_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_id = _document(
        visual_data_dir, "分类失败.png", [_region("普通内容", 1, block_type="paragraph")]
    )
    monkeypatch.setattr(
        visual_understanding,
        "_classify_from_blocks",
        lambda file_id, blocks: (_ for _ in ()).throw(RuntimeError("backend failed")),
    )

    result = classify_document(file_id)

    assert result["ok"] is True
    assert result["data"]["document_type"] == "unknown"
    assert result["data"]["status"] == "failed"
    assert result["data"]["reason"] == "VISUAL_CLASSIFICATION_FAILED"
    assert result["data"]["general_query_allowed"] is True


def test_student_and_general_kie_schema_are_strictly_separated(
    visual_data_dir: Path,
) -> None:
    file_id = _document(visual_data_dir, "领域隔离.png", [
        _region("学号：S2026001", 1, block_type="paragraph"),
        _region("成绩：96", 2, block_type="paragraph"),
        _region("院系：计算机学院", 3, block_type="paragraph"),
    ])

    general = extract_key_fields(file_id, domain="general")
    student = extract_key_fields(
        file_id, domain="student", schema_hint={"院系": "department"}
    )
    forbidden = extract_key_fields(
        file_id, domain="general", schema_hint={"学号": "student_id"}
    )

    assert general["data"]["schema"] == "general_field_value"
    assert [field["field_name"] for field in general["data"]["fields"]] == [
        "学号", "成绩", "院系"
    ]
    assert student["data"]["schema"] == "student_field_value"
    assert [field["field_name"] for field in student["data"]["fields"]] == [
        "student_id", "score", "department"
    ]
    assert forbidden["ok"] is False
    assert forbidden["error_code"] == "VISUAL_KIE_ARGUMENT_INVALID"
