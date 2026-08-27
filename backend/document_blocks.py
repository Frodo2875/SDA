"""Canonical V3.5 document blocks and backward-compatible chunk conversion."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


BlockType = Literal[
    "title", "paragraph", "table", "cell", "image", "header", "footer"
]


class DocumentBlock(BaseModel):
    """One validated layout unit produced from an actual document source."""

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    page_no: int | None = Field(default=None, ge=1)
    block_type: BlockType
    content: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_parser: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["normal", "degraded"] = "normal"
    warnings: list[str] = Field(default_factory=list)

    @property
    def document_id(self) -> str:
        """Compatibility alias for consumers that call the file a document."""
        return self.file_id

    @property
    def text(self) -> str:
        return self.content


def make_document_block(
    *,
    file_id: str,
    sequence: int,
    block_type: BlockType,
    content: str,
    page_no: int | None = None,
    bbox: list[float] | None = None,
    confidence: float | None = None,
    parent_id: str | None = None,
    source_parser: str | None = None,
    status: Literal["normal", "degraded"] = "normal",
    warnings: list[str] | None = None,
    block_id: str | None = None,
) -> DocumentBlock:
    """Create a stable block ID from source identity and normalized block data."""
    payload = json.dumps(
        {
            "file_id": file_id,
            "sequence": int(sequence),
            "page_no": page_no,
            "block_type": block_type,
            "content": str(content),
            "bbox": bbox,
            "parent_id": parent_id,
            "source_parser": source_parser,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    stable_block_id = block_id or hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return DocumentBlock(
        block_id=stable_block_id,
        file_id=file_id,
        page_no=page_no,
        block_type=block_type,
        content=str(content),
        bbox=bbox,
        confidence=confidence,
        parent_id=parent_id,
        source_parser=source_parser,
        status=status,
        warnings=list(warnings or []),
    )


def block_to_chunks(
    block: DocumentBlock,
    *,
    start_index: int = 0,
    source_type: str = "document",
    metadata: dict[str, Any] | None = None,
    chunk_size: int = 900,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """Generate legacy document_chunks rows while retaining Block provenance."""
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size 和 overlap 参数无效")
    step = chunk_size - overlap
    parts = (
        [block.content[start : start + chunk_size] for start in range(0, len(block.content), step)]
        if block.content
        else [""]
    )
    chunks = []
    for offset, text in enumerate(parts):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = hashlib.sha256(
            f"{block.block_id}:{offset}:{text_hash}".encode("utf-8")
        ).hexdigest()[:32]
        block_metadata = {
            **(metadata or {}),
            "source_type": source_type,
            "block_id": block.block_id,
            "block_type": block.block_type,
            "block": block.model_dump(),
        }
        if block.parent_id is not None:
            block_metadata["parent_id"] = block.parent_id
        if block.source_parser is not None:
            block_metadata["source_parser"] = block.source_parser
        if block.status != "normal":
            block_metadata["layout_status"] = block.status
        if block.warnings:
            block_metadata["layout_warnings"] = list(block.warnings)
        if block.bbox is not None:
            block_metadata["bbox"] = block.bbox
        if block.confidence is not None:
            block_metadata["confidence"] = block.confidence
        if block.page_no is not None:
            block_metadata["page_no"] = block.page_no
        chunks.append(
            {
                "chunk_id": chunk_id,
                "file_id": block.file_id,
                "page_no": block.page_no,
                "chunk_index": start_index + offset,
                "chunk_text": text,
                "text_hash": text_hash,
                "metadata": block_metadata,
            }
        )
    return chunks


def blocks_to_chunks(
    blocks: list[DocumentBlock],
    *,
    source_type: str,
    metadata_by_block: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert ordered blocks to contiguous legacy chunk indexes."""
    chunks: list[dict[str, Any]] = []
    for block in blocks:
        chunks.extend(
            block_to_chunks(
                block,
                start_index=len(chunks),
                source_type=source_type,
                metadata=(metadata_by_block or {}).get(block.block_id),
            )
        )
    return chunks


def blocks_from_chunks(chunks: list[dict[str, Any]]) -> list[DocumentBlock]:
    """Read V3.5 Blocks from chunk metadata and safely ignore legacy chunks."""
    blocks: list[DocumentBlock] = []
    seen: set[str] = set()
    for chunk in chunks:
        payload = (chunk.get("metadata") or {}).get("block")
        if not isinstance(payload, dict):
            continue
        try:
            block = DocumentBlock.model_validate(payload)
        except ValueError:
            continue
        if block.block_id in seen:
            continue
        seen.add(block.block_id)
        blocks.append(block)
    return blocks
