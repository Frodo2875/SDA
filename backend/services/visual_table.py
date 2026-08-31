"""V4.5 visual table parsing over the existing OCR and V3 table schemas."""

import hashlib
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from backend import database
from backend.evidence import build_evidence, make_evidence_id
from backend.services import ocr_service
from backend.services.visual_understanding import VisualBlock
from backend.table_structure import TableStructure, build_simple_table, degraded_table
from backend.tools.excel_utils import failure, success


CALCULATION_OPERATIONS = {"sum", "avg", "min", "max", "count"}
NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def extract_visual_tables(
    file_id: str,
    *,
    regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build only complete, explicit grids; otherwise return a safe table fallback."""
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    source = list(
        database.get_document_ocr_regions(record["file_id"])
        if regions is None else regions
    )
    normalized: list[dict[str, Any]] = []
    for index, region in enumerate(source, start=1):
        try:
            item = ocr_service.normalize_region_record(
                file_id=record["file_id"],
                page_no=int(region.get("page_no") or region.get("image_no") or 1),
                region_index=index,
                region=region,
                image_input=record["file_type"] == "image",
            )
        except (TypeError, ValueError):
            continue
        if item.get("visual_block_type") == "table" or item.get("table_id_hint"):
            normalized.append(item)
    groups: dict[str, list[dict[str, Any]]] = {}
    for region in normalized:
        key = str(
            region.get("table_id_hint")
            or f"page:{region.get('page_no') or 1}:visual-table"
        )
        groups.setdefault(key, []).append(region)
    try:
        items = [
            _build_visual_table(record, group_key, group)
            for group_key, group in groups.items()
        ]
    except ValueError as exc:
        return failure("VISUAL_TABLE_CONFIG_INVALID", str(exc))
    degraded = [item for item in items if item["table"]["status"] == "degraded"]
    low_cells = [
        cell
        for item in items for cell in item["table"]["cells"]
        if cell["status"] != "reliable"
    ]
    return success(
        {
            "file_id": record["file_id"],
            "status": "partial_success" if degraded or low_cells else "success",
            "tables": items,
            "table_count": len(items),
            "degraded_table_count": len(degraded),
            "low_confidence_cell_count": len(low_cells),
            "source_ocr_preserved": True,
        },
        "视觉表格解析完成",
    )


def calculate_visual_table(
    file_id: str,
    table_id: str,
    *,
    operation: Literal["sum", "avg", "min", "max", "count"],
    cell_ids: list[str],
) -> dict[str, Any]:
    """Run exact Python/Decimal calculation only over explicitly reliable cells."""
    if operation not in CALCULATION_OPERATIONS:
        return failure("VISUAL_TABLE_OPERATION_INVALID", "不支持的视觉表格计算操作")
    requested = [str(cell_id or "").strip() for cell_id in cell_ids]
    if not requested or any(not item for item in requested) or len(set(requested)) != len(requested):
        return failure("VISUAL_TABLE_CELL_INVALID", "cell_ids 必须非空且唯一")
    extracted = extract_visual_tables(file_id)
    if not extracted["ok"]:
        return extracted
    item = next(
        (candidate for candidate in extracted["data"]["tables"]
         if candidate["table"]["table_id"] == table_id),
        None,
    )
    if item is None:
        return failure("VISUAL_TABLE_NOT_FOUND", "未找到指定视觉表格")
    table = TableStructure.model_validate(item["table"])
    if table.status != "normal":
        return failure(
            "VISUAL_TABLE_NOT_RELIABLE",
            "表格结构未可靠解析，禁止执行精确计算",
            {"table_id": table.table_id, "warnings": table.warnings},
        )
    by_id = {cell.cell_id: cell for cell in table.cells}
    if any(cell_id not in by_id for cell_id in requested):
        return failure("VISUAL_TABLE_CELL_NOT_FOUND", "指定 Cell 不属于当前表格")
    cells = [by_id[cell_id] for cell_id in requested]
    if any(
        cell.status != "reliable" or not cell.safe_for_calculation
        for cell in cells
    ):
        return failure(
            "VISUAL_TABLE_CELL_NOT_RELIABLE",
            "存在低置信度 Cell，禁止执行精确计算",
            {"cell_ids": [cell.cell_id for cell in cells if not cell.safe_for_calculation]},
        )
    if operation == "count":
        value: int | float = len(cells)
    else:
        numbers: list[Decimal] = []
        for cell in cells:
            cleaned = cell.cell_text.strip().replace(",", "")
            if not NUMBER_PATTERN.fullmatch(cleaned):
                return failure(
                    "VISUAL_TABLE_NON_NUMERIC_CELL",
                    "指定 Cell 包含非数值文本，禁止猜测或隐式转换",
                    {"cell_id": cell.cell_id},
                )
            try:
                numbers.append(Decimal(cleaned))
            except InvalidOperation:
                return failure("VISUAL_TABLE_NON_NUMERIC_CELL", "Cell 数值格式无效")
        calculated = {
            "sum": lambda: sum(numbers, Decimal("0")),
            "avg": lambda: sum(numbers, Decimal("0")) / Decimal(len(numbers)),
            "min": lambda: min(numbers),
            "max": lambda: max(numbers),
        }[operation]()
        value = int(calculated) if calculated == calculated.to_integral_value() else float(calculated)
    record = database.get_file_record_by_id(table.file_id)
    if record is None:
        return failure("FILE_NOT_FOUND", "视觉表格来源文件已不可用")
    evidence = []
    for cell in cells:
        item = build_evidence(
            evidence_id=make_evidence_id(
                file_id=table.file_id, table_id=table.table_id,
                cell_id=cell.cell_id, operation=operation,
            ),
            source_type="unstructured",
            locator_type="visual_table",
            file_id=table.file_id,
            file_name=record["file_name"],
            page_no=cell.page_no,
            image_no=cell.image_no,
            chunk_id=None,
            block_id=table.table_id,
            region_id=cell.source_region_id,
            table=table.table_id,
            cell=cell.cell_id,
            row_index=cell.row_index,
            column_index=cell.column_index,
            bbox=cell.bbox,
            confidence=cell.confidence,
            recognition_type=cell.recognition_type,
            source_parser=cell.source_parser,
            source_model=cell.source_model,
            field=None,
            record_key=None,
            value_summary=cell.cell_text,
            text_excerpt=cell.cell_text,
            review_required=False,
            trust_level="untrusted_document_data",
            instruction_authority="none",
            approval_authority="none",
            can_trigger_tool=False,
            can_change_tool_risk=False,
            can_approve=False,
            detected_untrusted_patterns=list(cell.detected_untrusted_patterns),
        )
        # Retain the V4.5 response aliases while the persisted locator uses the
        # canonical Evidence table/cell fields.
        item.update({
            "cell_id": cell.cell_id,
            "source_region_id": cell.source_region_id,
        })
        evidence.append(item)
    return success(
        {
            "status": "calculated",
            "execution_engine": "python_decimal",
            "table_id": table.table_id,
            "operation": operation,
            "cell_ids": requested,
            "value": value,
            "evidence": evidence,
            "evidence_chain": evidence,
        },
        "视觉表格精确计算完成",
    )


def _build_visual_table(
    record: dict[str, Any], group_key: str, regions: list[dict[str, Any]]
) -> dict[str, Any]:
    page_no = int(regions[0].get("page_no") or 1)
    image_no = regions[0].get("image_no")
    table_id = _table_id(record["file_id"], group_key, page_no)
    source_region_ids = [str(region["region_id"]) for region in regions]
    raw_text = "\n".join(str(region.get("text") or "") for region in regions)
    bbox = _table_bbox(regions)
    confidences = [
        float(region["confidence"])
        for region in regions if region.get("confidence") is not None
    ]
    confidence = min(confidences) if confidences else None
    types = {str(region.get("recognition_type") or "unknown") for region in regions}
    recognition_type = next(iter(types)) if len(types) == 1 else "mixed"
    source_model = "+".join(dict.fromkeys(
        str(region.get("source_model") or "unknown") for region in regions
    ))
    source_parser = "visual-table-adapter"
    warning = _structure_warning(regions)
    if warning is None:
        coordinates = {
            (int(region["row_index"]), int(region["column_index"])): region
            for region in regions
        }
        max_row = max(row for row, _ in coordinates)
        max_column = max(column for _, column in coordinates)
        expected = {
            (row, column)
            for row in range(max_row + 1) for column in range(max_column + 1)
        }
        if set(coordinates) != expected or len(coordinates) != len(regions):
            warning = "IRREGULAR_OR_INCOMPLETE_GRID_UNSUPPORTED"
    threshold = _cell_confidence_threshold()
    if warning is None:
        rows: list[list[str]] = []
        geometry: dict[tuple[int, int], dict[str, Any]] = {}
        low_confidence = False
        for row_index in range(max_row + 1):
            row: list[str] = []
            for column_index in range(max_column + 1):
                region = coordinates[(row_index, column_index)]
                row.append(str(region.get("text") or ""))
                cell_reliable = (
                    region.get("status") == "success"
                    and region.get("bbox") is not None
                    and region.get("confidence") is not None
                    and float(region["confidence"]) >= threshold
                    and not region.get("review_required")
                )
                low_confidence = low_confidence or not cell_reliable
                geometry[(row_index, column_index)] = {
                    "bbox": region.get("bbox"),
                    "confidence": region.get("confidence"),
                    "recognition_type": region.get("recognition_type") or "unknown",
                    "source_region_id": region["region_id"],
                    "source_parser": region.get("source_parser") or source_parser,
                    "source_model": region.get("source_model"),
                    "status": "reliable" if cell_reliable else "low_confidence",
                    "safe_for_calculation": cell_reliable,
                }
            rows.append(row)
        structure = build_simple_table(
            table_id=table_id,
            file_id=record["file_id"],
            rows=rows,
            page_no=page_no,
            image_no=image_no,
            source_parser=source_parser,
            source_model=source_model,
            cell_geometry=geometry,
            bbox=bbox,
            confidence=confidence,
            recognition_type=recognition_type,
            source_region_ids=source_region_ids,
            warnings=["LOW_CONFIDENCE_CELLS_REQUIRE_REVIEW"] if low_confidence else [],
        )
        table_block = _table_block(
            record["file_id"], table_id, raw_text, page_no, image_no, bbox,
            confidence, recognition_type, source_model, source_region_ids,
            degraded=low_confidence,
            warnings=structure.warnings,
        )
        return {"table": structure.model_dump(), "table_block": table_block, "fallback": None}
    fallback = {
        "reason": warning,
        "ocr_text": raw_text,
        "original_image_region": {
            "file_id": record["file_id"],
            "page_no": page_no,
            "image_no": image_no,
            "bbox": bbox,
            "source_region_ids": source_region_ids,
        },
    }
    structure = degraded_table(
        table_id=table_id,
        file_id=record["file_id"],
        raw_text=raw_text,
        source_parser=source_parser,
        source_model=source_model,
        warning=warning,
        page_no=page_no,
        image_no=image_no,
        bbox=bbox,
        confidence=confidence,
        recognition_type=recognition_type,
        source_region_ids=source_region_ids,
        fallback=fallback,
    )
    return {
        "table": structure.model_dump(),
        "table_block": _table_block(
            record["file_id"], table_id, raw_text, page_no, image_no, bbox,
            confidence, recognition_type, source_model, source_region_ids,
            degraded=True, warnings=[warning],
        ),
        "fallback": fallback,
    }


def _structure_warning(regions: list[dict[str, Any]]) -> str | None:
    hints = {region.get("table_structure_hint") for region in regions}
    if "merged_cells" in hints or any(
        int(region.get("row_span") or 1) != 1
        or int(region.get("column_span") or 1) != 1
        for region in regions
    ):
        return "MERGED_CELLS_UNSUPPORTED"
    if "borderless_hand_drawn" in hints:
        return "BORDERLESS_HAND_DRAWN_TABLE_UNSUPPORTED"
    if any(
        region.get("row_index") is None
        or region.get("column_index") is None
        or region.get("bbox") is None
        for region in regions
    ):
        return "TABLE_STRUCTURE_OR_CELL_GEOMETRY_UNAVAILABLE"
    coordinates = [
        (int(region["row_index"]), int(region["column_index"])) for region in regions
    ]
    if len(coordinates) != len(set(coordinates)):
        return "DUPLICATE_CELL_COORDINATES_UNSUPPORTED"
    return None


def _table_block(
    file_id: str,
    table_id: str,
    text: str,
    page_no: int,
    image_no: int | None,
    bbox: list[float] | None,
    confidence: float | None,
    recognition_type: str,
    source_model: str,
    source_region_ids: list[str],
    *,
    degraded: bool,
    warnings: list[str],
) -> dict[str, Any]:
    return VisualBlock(
        block_id=table_id,
        file_id=file_id,
        page_no=page_no,
        image_no=image_no,
        block_type="table",
        content=text,
        bbox=bbox,
        confidence=confidence,
        recognition_type=recognition_type,
        source_parser="visual-table-adapter",
        source_model=source_model,
        source_region_ids=source_region_ids,
        review_required=degraded,
        safe_for_high_impact=not degraded,
        safe_for_identity_match=not degraded,
        status="degraded" if degraded else "normal",
        warnings=warnings,
    ).visual_dump()


def _table_bbox(regions: list[dict[str, Any]]) -> list[float] | None:
    explicit = next((region.get("table_bbox") for region in regions if region.get("table_bbox")), None)
    if explicit is not None:
        return list(explicit)
    boxes = [region["bbox"] for region in regions if region.get("bbox") is not None]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    ]


def _cell_confidence_threshold() -> float:
    try:
        threshold = float(os.getenv("VISUAL_TABLE_CELL_CONFIDENCE_THRESHOLD", "0.85"))
    except ValueError as exc:
        raise ValueError("VISUAL_TABLE_CELL_CONFIDENCE_THRESHOLD 配置无效") from exc
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("VISUAL_TABLE_CELL_CONFIDENCE_THRESHOLD 必须在 0 到 1 之间")
    return threshold


def _table_id(file_id: str, group_key: str, page_no: int) -> str:
    return hashlib.sha256(
        f"{file_id}:visual-table:{page_no}:{group_key}".encode("utf-8")
    ).hexdigest()[:32]
