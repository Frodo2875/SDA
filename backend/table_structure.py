"""Validated basic two-dimensional table structure for document layout."""

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_id: str = Field(min_length=1, max_length=128)
    table_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    page_no: int | None = Field(default=None, ge=1)
    image_no: int | None = Field(default=None, ge=1)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    cell_text: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] = "printed"
    source_region_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_parser: str | None = Field(default=None, min_length=1, max_length=128)
    source_model: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["reliable", "low_confidence", "failed"] = "reliable"
    safe_for_calculation: bool = True


class TableRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1, max_length=128)
    table_id: str = Field(min_length=1, max_length=128)
    row_index: int = Field(ge=0)
    cell_ids: list[str]
    page_no: int | None = Field(default=None, ge=1)
    image_no: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


class TableColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_id: str = Field(min_length=1, max_length=128)
    table_id: str = Field(min_length=1, max_length=128)
    column_index: int = Field(ge=0)
    cell_ids: list[str]
    page_no: int | None = Field(default=None, ge=1)
    image_no: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


class TableStructure(BaseModel):
    """Simple grid or an explicit degraded table retaining its source text."""

    model_config = ConfigDict(extra="forbid")

    table_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    page_no: int | None = Field(default=None, ge=1)
    image_no: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] = "printed"
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    rows: list[TableRow] = Field(default_factory=list)
    columns: list[TableColumn] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    raw_text: str
    status: Literal["normal", "degraded"] = "normal"
    warnings: list[str] = Field(default_factory=list)
    source_parser: str
    source_model: str | None = Field(default=None, min_length=1, max_length=128)
    source_region_ids: list[str] = Field(default_factory=list)
    fallback: dict[str, Any] | None = None


def build_simple_table(
    *,
    table_id: str,
    file_id: str,
    rows: list[list[str]],
    page_no: int | None,
    source_parser: str,
    cell_geometry: dict[tuple[int, int], dict[str, Any]] | None = None,
    image_no: int | None = None,
    bbox: list[float] | None = None,
    confidence: float | None = None,
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] = "printed",
    source_model: str | None = None,
    source_region_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> TableStructure:
    """Build a real rectangular grid; reject irregular input instead of guessing."""
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("简单表格必须是非空规则二维网格")
    geometry = cell_geometry or {}
    cells = []
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            values = geometry.get((row_index, column_index)) or {}
            cells.append(
                TableCell(
                    cell_id=_stable_id(table_id, "cell", row_index, column_index),
                    table_id=table_id,
                    file_id=file_id,
                    page_no=page_no,
                    image_no=image_no,
                    row_index=row_index,
                    column_index=column_index,
                    cell_text=str(text),
                    bbox=values.get("bbox"),
                    confidence=values.get("confidence"),
                    recognition_type=values.get("recognition_type", recognition_type),
                    source_region_id=values.get("source_region_id"),
                    source_parser=values.get("source_parser", source_parser),
                    source_model=values.get("source_model", source_model),
                    status=values.get("status", "reliable"),
                    safe_for_calculation=bool(values.get("safe_for_calculation", True)),
                )
            )
    row_models = [
        TableRow(
            row_id=_stable_id(table_id, "row", row_index),
            table_id=table_id,
            row_index=row_index,
            cell_ids=[cell.cell_id for cell in cells if cell.row_index == row_index],
            page_no=page_no,
            image_no=image_no,
            bbox=_union_bbox([cell.bbox for cell in cells if cell.row_index == row_index]),
        )
        for row_index in range(len(rows))
    ]
    column_models = [
        TableColumn(
            column_id=_stable_id(table_id, "column", column_index),
            table_id=table_id,
            column_index=column_index,
            cell_ids=[cell.cell_id for cell in cells if cell.column_index == column_index],
            page_no=page_no,
            image_no=image_no,
            bbox=_union_bbox([cell.bbox for cell in cells if cell.column_index == column_index]),
        )
        for column_index in range(len(rows[0]))
    ]
    return TableStructure(
        table_id=table_id,
        file_id=file_id,
        page_no=page_no,
        image_no=image_no,
        bbox=bbox or _union_bbox([cell.bbox for cell in cells]),
        confidence=confidence,
        recognition_type=recognition_type,
        row_count=len(rows),
        column_count=len(rows[0]),
        rows=row_models,
        columns=column_models,
        cells=cells,
        raw_text="\n".join("\t".join(row) for row in rows),
        source_parser=source_parser,
        source_model=source_model,
        source_region_ids=list(source_region_ids or []),
        warnings=list(warnings or []),
    )


def degraded_table(
    *, table_id: str, file_id: str, raw_text: str, source_parser: str,
    warning: str, page_no: int | None = None, image_no: int | None = None,
    bbox: list[float] | None = None, confidence: float | None = None,
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] = "unknown",
    source_model: str | None = None, source_region_ids: list[str] | None = None,
    fallback: dict[str, Any] | None = None,
) -> TableStructure:
    return TableStructure(
        table_id=table_id, file_id=file_id, page_no=page_no, image_no=image_no,
        bbox=bbox, confidence=confidence, recognition_type=recognition_type,
        row_count=0, column_count=0, raw_text=raw_text,
        status="degraded", warnings=[warning], source_parser=source_parser,
        source_model=source_model, source_region_ids=list(source_region_ids or []),
        fallback=fallback,
    )


def _union_bbox(boxes: list[list[float] | None]) -> list[float] | None:
    valid = [box for box in boxes if box is not None]
    if not valid:
        return None
    return [
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    ]


def _stable_id(table_id: str, kind: str, *indexes: int) -> str:
    payload = ":".join([table_id, kind, *(str(index) for index in indexes)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
