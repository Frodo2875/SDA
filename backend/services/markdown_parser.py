"""Bounded Markdown input adapter; never render, execute, or fetch source data."""

from pathlib import Path
import re
from typing import Any

from backend.document_blocks import BlockType, DocumentBlock, make_document_block
from backend.services.multiformat_parser import (
    MultiFormatParseError, ParsedDocument, decode_text_file,
)
from backend.table_structure import build_simple_table


PARSER_NAME = "v6-markdown-parser"
MAX_BLOCKS = 20_000
MAX_LINES = 100_000
MAX_TABLE_CELLS = 20_000
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])[ \t]+")
_LINK = re.compile(r"(?<!!)\[([^\[\]\n]+)\]\((<[^>\n]+>|[^\s()]+)(?:[ \t]+\"[^\"\n]*\")?\)")


def _cells(line: str) -> list[str]:
    """Split basic pipe tables while retaining escaped literal pipes."""
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    cells = _cells(lines[index + 1])
    return bool(cells) and all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def _starts_block(lines: list[str], index: int) -> bool:
    return bool(_HEADING.match(lines[index]) or _FENCE.match(lines[index])
                or _LIST.match(lines[index]) or _table_start(lines, index))


def parse_markdown(path: Path, *, file_id: str, file_name: str) -> ParsedDocument:
    """Parse an explicit Markdown subset into the existing Block/metadata contract."""
    text, encoding = decode_text_file(path)
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise MultiFormatParseError("Markdown 行数超过限制")
    blocks: list[DocumentBlock] = []
    metadata_by_block: dict[str, dict[str, Any]] = {}
    headings: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if len(blocks) >= MAX_BLOCKS:
            raise MultiFormatParseError("Markdown Block 数量超过限制")
        start = index
        kind: BlockType = "paragraph"
        semantic = "markdown_paragraph"
        extra: dict[str, Any] = {}
        warnings: list[str] = []
        heading = _HEADING.match(lines[index])
        fence = _FENCE.match(lines[index])
        if fence:
            marker, info = fence.groups()
            language = info.strip().split(maxsplit=1)[0] if info.strip() else ""
            index += 1
            body_start = index
            closer = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            while index < len(lines) and not closer.fullmatch(lines[index]):
                index += 1
            content = "\n".join(lines[body_start:index])
            if index < len(lines):
                index += 1
            else:
                warnings.append("UNCLOSED_MARKDOWN_FENCE")
            semantic = "markdown_code"
            extra = {"language": language, "content": content}
        elif heading:
            level = len(heading.group(1))
            content = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2) or "").strip()
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, content))
            extra["heading_level"] = level
            kind, semantic = "title", "markdown_section"
            index += 1
        elif _table_start(lines, index):
            rows = [_cells(lines[index])]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(_cells(lines[index]))
                index += 1
            if sum(len(row) for row in rows) > MAX_TABLE_CELLS:
                raise MultiFormatParseError("Markdown 表格单元格数量超过限制")
            content = "\n".join(lines[start:index])
            kind, semantic = "table", "markdown_table"
            if any(len(row) != len(rows[0]) for row in rows) or len(_cells(lines[start + 1])) != len(rows[0]):
                warnings.append("IRREGULAR_MARKDOWN_TABLE")
            else:
                table = build_simple_table(
                    table_id=f"{file_id}:markdown:{start + 1}", file_id=file_id,
                    rows=rows, page_no=None, source_parser=PARSER_NAME,
                )
                extra["table"] = table.model_dump()
        else:
            is_list = bool(_LIST.match(lines[index]))
            semantic = "markdown_list" if is_list else semantic
            index += 1
            while index < len(lines) and lines[index].strip():
                if is_list and _LIST.match(lines[index]):
                    index += 1
                    continue
                if _starts_block(lines, index):
                    break
                index += 1
            content = "\n".join(lines[start:index])
        if semantic != "markdown_code":
            extra["links"] = [
                {"text": match.group(1), "url": match.group(2).strip("<>")}
                for match in _LINK.finditer(content)
            ]
        block = make_document_block(
            file_id=file_id, sequence=len(blocks), block_type=kind,
            content=content, source_parser=PARSER_NAME,
            status="degraded" if warnings else "normal", warnings=warnings,
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {
            "document_format": "markdown", "encoding": encoding,
            "heading_path": [title for _, title in headings],
            "line_number": start + 1, "line_end": index,
            "block_type": semantic, **extra,
        }
    if not any(block.content.strip() for block in blocks):
        raise MultiFormatParseError("Markdown 文档中没有可索引文本")
    return ParsedDocument(
        document_id=file_id, file_name=file_name,
        content="\n".join(block.content for block in blocks), blocks=blocks,
        metadata_by_block=metadata_by_block,
        metadata={"document_format": "markdown", "encoding": encoding, "line_count": len(lines)},
    )
