"""V4.5 visual and handwritten table processing tests."""

from pathlib import Path
from typing import Any

import pytest

from backend import database
from backend.document_blocks import block_to_chunks, make_document_block
from backend.services import ocr_service
from backend.services.document_index import retrieve_document
from backend.services.visual_table import calculate_visual_table, extract_visual_tables
from backend.tools import excel_utils


@pytest.fixture()
def visual_table_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _cell(
    text: str,
    row: int,
    column: int,
    *,
    confidence: float = 0.96,
    recognition_type: str = "printed",
    table_hint: str = "table-1",
    structure_hint: str = "grid",
    row_span: int = 1,
    column_span: int = 1,
) -> dict[str, Any]:
    x1 = 10.0 + column * 100.0
    y1 = 20.0 + row * 35.0
    return {
        "region_id": f"{table_hint}-r{row}c{column}",
        "page_no": 1,
        "image_no": 1,
        "text": text,
        "bbox": [x1, y1, x1 + 95.0, y1 + 30.0],
        "confidence": confidence,
        "recognition_type": recognition_type,
        "source_model": "fixed-visual-table-v1",
        "source_parser": "fixed-visual-table-adapter",
        "status": "success",
        "visual_block_type": "table",
        "table_id_hint": table_hint,
        "row_index": row,
        "column_index": column,
        "row_span": row_span,
        "column_span": column_span,
        "table_bbox": [8.0, 18.0, 208.0, 88.0],
        "table_structure_hint": structure_hint,
    }


def _document(
    data_dir: Path,
    name: str,
    regions: list[dict[str, Any]],
    *,
    queryable: bool = False,
) -> str:
    path = data_dir / "uploads" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"visual table fixture")
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
        "text": "\n".join(item["text"] for item in normalized),
        "bbox": [8.0, 18.0, 208.0, 88.0],
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


def _two_by_two(**overrides: Any) -> list[dict[str, Any]]:
    values = [["姓名", "成绩"], ["张三", "96"]]
    return [
        _cell(values[row][column], row, column, **overrides)
        for row in range(2) for column in range(2)
    ]


def test_simple_printed_table_image_reuses_table_row_column_cell_schema(
    visual_table_data_dir: Path,
) -> None:
    file_id = _document(visual_table_data_dir, "打印表格.png", _two_by_two())
    result = extract_visual_tables(file_id)
    item = result["data"]["tables"][0]
    table = item["table"]

    assert result["ok"] is True
    assert result["data"]["status"] == "success"
    assert table["status"] == "normal"
    assert table["row_count"] == table["column_count"] == 2
    assert len(table["rows"]) == len(table["columns"]) == 2
    assert len(table["cells"]) == 4
    assert [cell["cell_text"] for cell in table["cells"]] == ["姓名", "成绩", "张三", "96"]
    assert item["table_block"]["block_type"] == "table"
    assert item["fallback"] is None


def test_simple_handwritten_table_preserves_recognition_type(
    visual_table_data_dir: Path,
) -> None:
    file_id = _document(
        visual_table_data_dir,
        "手写表格.png",
        _two_by_two(confidence=0.93, recognition_type="handwritten"),
    )
    table = extract_visual_tables(file_id)["data"]["tables"][0]["table"]

    assert table["status"] == "normal"
    assert table["recognition_type"] == "handwritten"
    assert all(cell["recognition_type"] == "handwritten" for cell in table["cells"])
    assert all(cell["status"] == "reliable" for cell in table["cells"])


def test_reliable_cells_keep_page_image_bbox_text_and_confidence(
    visual_table_data_dir: Path,
) -> None:
    file_id = _document(visual_table_data_dir, "Cell定位.png", _two_by_two())
    table = extract_visual_tables(file_id)["data"]["tables"][0]["table"]
    score = next(cell for cell in table["cells"] if cell["cell_text"] == "96")

    assert table["page_no"] == table["image_no"] == 1
    assert table["bbox"] == [8.0, 18.0, 208.0, 88.0]
    assert score["page_no"] == score["image_no"] == 1
    assert score["bbox"] == [110.0, 55.0, 205.0, 85.0]
    assert score["confidence"] == 0.96
    assert score["source_region_id"] == "table-1-r1c1"


def test_low_confidence_handwritten_cell_is_kept_but_not_calculable(
    visual_table_data_dir: Path,
) -> None:
    regions = _two_by_two(confidence=0.93, recognition_type="handwritten")
    regions[-1]["confidence"] = 0.70
    file_id = _document(visual_table_data_dir, "低置信手写Cell.png", regions)
    result = extract_visual_tables(file_id)
    table = result["data"]["tables"][0]["table"]
    low = next(cell for cell in table["cells"] if cell["cell_text"] == "96")

    assert result["data"]["status"] == "partial_success"
    assert table["status"] == "normal"
    assert low["status"] == "low_confidence"
    assert low["safe_for_calculation"] is False
    rejected = calculate_visual_table(
        file_id, table["table_id"], operation="sum", cell_ids=[low["cell_id"]]
    )
    assert rejected["ok"] is False
    assert rejected["error_code"] == "VISUAL_TABLE_CELL_NOT_RELIABLE"


def test_reliable_numeric_cells_are_calculated_only_by_python_decimal(
    visual_table_data_dir: Path,
) -> None:
    regions = [
        _cell("项目", 0, 0), _cell("金额", 0, 1),
        _cell("甲", 1, 0), _cell("10.5", 1, 1),
        _cell("乙", 2, 0), _cell("20.5", 2, 1),
    ]
    file_id = _document(visual_table_data_dir, "数值表.png", regions)
    table = extract_visual_tables(file_id)["data"]["tables"][0]["table"]
    numeric_ids = [
        cell["cell_id"] for cell in table["cells"]
        if cell["row_index"] in {1, 2} and cell["column_index"] == 1
    ]

    calculated = calculate_visual_table(
        file_id, table["table_id"], operation="sum", cell_ids=numeric_ids
    )

    assert calculated["ok"] is True
    assert calculated["data"]["value"] == 31
    assert calculated["data"]["execution_engine"] == "python_decimal"
    assert [item["cell_id"] for item in calculated["data"]["evidence"]] == numeric_ids
    header_id = next(
        cell["cell_id"] for cell in table["cells"] if cell["cell_text"] == "金额"
    )
    rejected = calculate_visual_table(
        file_id, table["table_id"], operation="sum", cell_ids=[header_id]
    )
    assert rejected["ok"] is False
    assert rejected["error_code"] == "VISUAL_TABLE_NON_NUMERIC_CELL"


def test_merged_cell_structure_degrades_without_fabricating_cells(
    visual_table_data_dir: Path,
) -> None:
    regions = _two_by_two()
    regions[0]["row_span"] = 2
    regions[0]["table_structure_hint"] = "merged_cells"
    file_id = _document(visual_table_data_dir, "合并单元格.png", regions)
    item = extract_visual_tables(file_id)["data"]["tables"][0]

    assert item["table"]["status"] == "degraded"
    assert item["table"]["warnings"] == ["MERGED_CELLS_UNSUPPORTED"]
    assert item["table"]["rows"] == item["table"]["columns"] == item["table"]["cells"] == []
    assert "张三" in item["fallback"]["ocr_text"]
    assert item["fallback"]["original_image_region"]["bbox"] == [8.0, 18.0, 208.0, 88.0]


def test_hand_drawn_borderless_table_degrades_safely(
    visual_table_data_dir: Path,
) -> None:
    file_id = _document(
        visual_table_data_dir,
        "无边框手绘表格.png",
        _two_by_two(
            confidence=0.91,
            recognition_type="handwritten",
            structure_hint="borderless_hand_drawn",
        ),
    )
    item = extract_visual_tables(file_id)["data"]["tables"][0]

    assert item["table"]["status"] == "degraded"
    assert item["fallback"]["reason"] == "BORDERLESS_HAND_DRAWN_TABLE_UNSUPPORTED"
    assert item["table_block"]["status"] == "degraded"
    assert item["table"]["cells"] == []


def test_incomplete_grid_safe_fallback_keeps_document_queryable(
    visual_table_data_dir: Path,
) -> None:
    regions = _two_by_two()
    regions.pop(2)
    file_id = _document(
        visual_table_data_dir, "结构失败仍可查询.png", regions, queryable=True
    )
    raw_text = "姓名 成绩 张三 96"
    block = make_document_block(
        file_id=file_id, sequence=0, block_type="table", content=raw_text,
        page_no=1, bbox=[8.0, 18.0, 208.0, 88.0],
        source_parser="visual-ocr",
    )
    database.replace_document_chunks(file_id, block_to_chunks(block, source_type="ocr"))

    parsed = extract_visual_tables(file_id)
    item = parsed["data"]["tables"][0]
    retrieved = retrieve_document({"file_id": file_id}, "张三", 3)

    assert item["table"]["status"] == "degraded"
    assert item["table"]["cells"] == []
    assert item["fallback"]["reason"] == "IRREGULAR_OR_INCOMPLETE_GRID_UNSUPPORTED"
    assert database.get_file_record_by_id(file_id)["queryable"] == 1
    assert retrieved["data"]["status"] == "found"
