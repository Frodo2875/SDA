"""Bounded deterministic parsers for V5 local document formats."""

from __future__ import annotations

import csv
import json
import re
import struct
import zipfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from backend.document_blocks import DocumentBlock, make_document_block


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARACTERS = 5_000_000
MAX_CSV_ROWS = 100_000
MAX_CSV_COLUMNS = 500
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 200_000
MAX_PPTX_ENTRIES = 5_000
MAX_PPTX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
PPTX_MAGIC = b"PK\x03\x04"
PPT_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_SLIDE_PATTERN = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_TEXT_CHARS_ATOM = 4000
_TEXT_BYTES_ATOM = 4008
_SLIDE_CONTAINER = 1006


class MultiFormatParseError(ValueError):
    """One local file cannot be safely decoded or parsed."""


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    file_name: str
    content: str
    blocks: list[DocumentBlock]
    metadata_by_block: dict[str, dict[str, Any]]
    metadata: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        serialized_blocks = [block.model_dump() for block in self.blocks]
        return {
            "document_id": self.document_id,
            "file_name": self.file_name,
            "content": self.content,
            "block": serialized_blocks,
            "blocks": serialized_blocks,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CSVTable:
    encoding: str
    delimiter: str
    headers: list[str]
    rows: list[list[Any]]


def parse_local_document(path: Path, *, file_id: str, file_name: str) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        from backend.services.markdown_parser import parse_markdown

        return parse_markdown(path, file_id=file_id, file_name=file_name)
    if suffix == ".txt":
        return parse_txt(path, file_id=file_id, file_name=file_name)
    if suffix == ".json":
        return parse_json(path, file_id=file_id, file_name=file_name)
    if suffix in {".ppt", ".pptx"}:
        return parse_presentation(path, file_id=file_id, file_name=file_name)
    raise MultiFormatParseError(f"不支持的本地文档格式：{suffix or 'unknown'}")


def parse_txt(path: Path, *, file_id: str, file_name: str) -> ParsedDocument:
    text, encoding = decode_text_file(path)
    lines = text.splitlines()
    blocks: list[DocumentBlock] = []
    metadata_by_block: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(lines, start=1):
        clean = line.strip()
        if not clean:
            continue
        block = make_document_block(
            file_id=file_id,
            sequence=len(blocks),
            block_type="paragraph",
            content=clean,
            source_parser="v5-text-parser",
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {"line_number": line_number, "encoding": encoding}
    if not blocks:
        raise MultiFormatParseError("TXT 文档中没有可索引文本")
    return ParsedDocument(
        document_id=file_id,
        file_name=file_name,
        content="\n".join(block.content for block in blocks),
        blocks=blocks,
        metadata_by_block=metadata_by_block,
        metadata={"document_format": "txt", "encoding": encoding, "line_count": len(lines)},
    )


def parse_json(path: Path, *, file_id: str, file_name: str) -> ParsedDocument:
    text, encoding = decode_text_file(path)
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise MultiFormatParseError("JSON 文件无法正常解析") from exc
    flattened: list[tuple[str, Any]] = []
    _flatten_json(value, "$", flattened, depth=0, counter=[0])
    if not flattened:
        flattened.append(("$", value))
    blocks: list[DocumentBlock] = []
    metadata_by_block: dict[str, dict[str, Any]] = {}
    for json_path, item in flattened:
        rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
        block = make_document_block(
            file_id=file_id,
            sequence=len(blocks),
            block_type="paragraph",
            content=f"{json_path}: {rendered}",
            source_parser="v5-json-parser",
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {"json_path": json_path, "encoding": encoding}
    return ParsedDocument(
        document_id=file_id,
        file_name=file_name,
        content="\n".join(block.content for block in blocks),
        blocks=blocks,
        metadata_by_block=metadata_by_block,
        metadata={
            "document_format": "json",
            "encoding": encoding,
            "root_type": "array" if isinstance(value, list) else "object" if isinstance(value, dict) else "scalar",
            "node_count": len(flattened),
        },
    )


def parse_csv_table(path: Path) -> CSVTable:
    text, encoding = decode_text_file(path)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    try:
        raw_rows = list(csv.reader(StringIO(text), dialect=dialect))
    except csv.Error as exc:
        raise MultiFormatParseError("CSV 文件无法正常解析") from exc
    if not raw_rows:
        raise MultiFormatParseError("CSV 文件为空")
    if len(raw_rows) - 1 > MAX_CSV_ROWS:
        raise MultiFormatParseError(f"CSV 数据行超过安全上限 {MAX_CSV_ROWS}")
    width = max(len(row) for row in raw_rows)
    if width < 1 or width > MAX_CSV_COLUMNS:
        raise MultiFormatParseError(f"CSV 字段数必须在 1 到 {MAX_CSV_COLUMNS} 之间")
    headers = [str(value).strip() for value in raw_rows[0]]
    if any(not value for value in headers) or len(headers) != len(set(headers)):
        raise MultiFormatParseError("CSV 表头必须非空且唯一")
    rows = []
    for raw in raw_rows[1:]:
        padded = list(raw[: len(headers)]) + [""] * max(0, len(headers) - len(raw))
        if any(value.strip() for value in padded):
            rows.append([_csv_scalar(value) for value in padded])
    return CSVTable(
        encoding=encoding,
        delimiter=str(dialect.delimiter),
        headers=headers,
        rows=rows,
    )


def validate_local_format(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        parse_local_document(path, file_id="validation", file_name=path.name)
        return
    if suffix == ".txt":
        text, _ = decode_text_file(path)
        if not text.strip():
            raise MultiFormatParseError("TXT 文件为空")
        return
    if suffix == ".json":
        parse_json(path, file_id="validation", file_name=path.name)
        return
    if suffix == ".csv":
        parse_csv_table(path)
        return
    if suffix in {".ppt", ".pptx"}:
        parse_presentation(path, file_id="validation", file_name=path.name)
        return
    raise MultiFormatParseError("不支持的文件类型")


def parse_presentation(path: Path, *, file_id: str, file_name: str) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        raw_blocks = _pptx_blocks(path)
        parser = "v5-pptx-xml-parser"
    elif suffix == ".ppt":
        raw_blocks = _ppt_binary_blocks(path)
        parser = "v5-ppt-binary-parser"
    else:
        raise MultiFormatParseError("演示文稿扩展名无效")
    if not raw_blocks:
        raise MultiFormatParseError("演示文稿中没有可索引文本")
    blocks: list[DocumentBlock] = []
    metadata_by_block: dict[str, dict[str, Any]] = {}
    for raw in raw_blocks:
        block = make_document_block(
            file_id=file_id,
            sequence=len(blocks),
            block_type=raw["block_type"],
            content=raw["content"],
            page_no=raw["slide_number"],
            source_parser=parser,
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {
            key: value for key, value in raw.items() if key not in {"content", "block_type"}
        }
    slide_count = max(item["slide_number"] for item in raw_blocks)
    return ParsedDocument(
        document_id=file_id,
        file_name=file_name,
        content="\n".join(block.content for block in blocks),
        blocks=blocks,
        metadata_by_block=metadata_by_block,
        metadata={"document_format": suffix.lstrip("."), "slide_count": slide_count},
    )


def decode_text_file(path: Path) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MultiFormatParseError("文本文件无法读取") from exc
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise MultiFormatParseError(f"文本文件大小必须在 1 到 {MAX_SOURCE_BYTES} 字节之间")
    encodings = ["utf-8-sig"]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    encodings.extend(["gb18030", "big5"])
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if len(text) > MAX_TEXT_CHARACTERS or _unsafe_text(text):
            continue
        return text, encoding
    raise MultiFormatParseError("文本编码无法可靠识别；支持 UTF-8、UTF-16 BOM、GB18030/GBK 和 Big5")


def _unsafe_text(text: str) -> bool:
    if "\x00" in text:
        return True
    if not text:
        return False
    controls = sum(ord(char) < 32 and char not in "\n\r\t" for char in text)
    return controls / len(text) > 0.01


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非法 JSON 常量：{value}")


def _flatten_json(
    value: Any,
    path: str,
    output: list[tuple[str, Any]],
    *,
    depth: int,
    counter: list[int],
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise MultiFormatParseError(f"JSON 嵌套深度超过安全上限 {MAX_JSON_DEPTH}")
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES:
        raise MultiFormatParseError(f"JSON 节点超过安全上限 {MAX_JSON_NODES}")
    if isinstance(value, dict):
        if not value:
            output.append((path, {}))
        for key, item in value.items():
            child = f"{path}.{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)) else f"{path}[{json.dumps(str(key), ensure_ascii=False)}]"
            _flatten_json(item, child, output, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        if not value:
            output.append((path, []))
        for index, item in enumerate(value):
            _flatten_json(item, f"{path}[{index}]", output, depth=depth + 1, counter=counter)
    else:
        output.append((path, value))


def _csv_scalar(value: str) -> Any:
    clean = value.strip()
    if not clean:
        return None
    if clean.casefold() in {"true", "false"}:
        return clean.casefold() == "true"
    if re.fullmatch(r"[-+]?\d+", clean):
        try:
            return int(clean)
        except ValueError:
            return clean
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[Ee][-+]?\d+)?", clean):
        try:
            return float(clean)
        except ValueError:
            return clean
    return clean


def _pptx_blocks(path: Path) -> list[dict[str, Any]]:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES or not path.read_bytes()[:4] == PPTX_MAGIC:
            raise MultiFormatParseError("PPTX 文件签名无效或超过大小上限")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PPTX_ENTRIES:
                raise MultiFormatParseError("PPTX 包含过多文件条目")
            if sum(info.file_size for info in infos) > MAX_PPTX_UNCOMPRESSED_BYTES:
                raise MultiFormatParseError("PPTX 解压后大小超过安全上限")
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                raise MultiFormatParseError("文件不是有效的 PPTX 演示文稿")
            slides = sorted(
                ((int(match.group(1)), name) for name in names if (match := _SLIDE_PATTERN.match(name))),
                key=lambda item: item[0],
            )
            output: list[dict[str, Any]] = []
            for slide_number, name in slides:
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except ElementTree.ParseError as exc:
                    raise MultiFormatParseError(f"PPTX 第 {slide_number} 页 XML 损坏") from exc
                for shape in root.findall(f".//{{{_P_NS}}}sp"):
                    texts = [node.text or "" for node in shape.findall(f".//{{{_A_NS}}}t")]
                    content = " ".join(item.strip() for item in texts if item.strip())
                    if content:
                        output.append({"slide_number": slide_number, "block_type": "paragraph", "content": content})
                for table_no, table in enumerate(root.findall(f".//{{{_A_NS}}}tbl"), start=1):
                    rows = []
                    for row_no, row in enumerate(table.findall(f"{{{_A_NS}}}tr"), start=1):
                        cells = []
                        for column_no, cell in enumerate(row.findall(f"{{{_A_NS}}}tc"), start=1):
                            cell_text = " ".join(
                                (node.text or "").strip()
                                for node in cell.findall(f".//{{{_A_NS}}}t")
                                if (node.text or "").strip()
                            )
                            cells.append(cell_text)
                            if cell_text:
                                output.append({
                                    "slide_number": slide_number,
                                    "block_type": "cell",
                                    "content": cell_text,
                                    "table_no": table_no,
                                    "row_number": row_no,
                                    "column_number": column_no,
                                })
                        rows.append(cells)
                    if rows:
                        output.append({
                            "slide_number": slide_number,
                            "block_type": "table",
                            "content": "\n".join(" | ".join(row) for row in rows),
                            "table_no": table_no,
                        })
            return output
    except (OSError, zipfile.BadZipFile) as exc:
        raise MultiFormatParseError("PPTX 文件无法正常解析") from exc


def _ppt_binary_blocks(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MultiFormatParseError("PPT 文件无法读取") from exc
    if len(raw) > MAX_SOURCE_BYTES or not _valid_cfb_header(raw):
        raise MultiFormatParseError("文件不是有效的旧版 PPT")
    output: list[dict[str, Any]] = []
    slide_number = 0
    offset = 0
    # Conservative atom scan: record headers are retained inside the OLE stream;
    # unknown records are ignored and only bounded text atoms are decoded.
    while offset + 8 <= len(raw):
        _, record_type, length = struct.unpack_from("<HHI", raw, offset)
        if record_type == _SLIDE_CONTAINER and length <= len(raw) - offset - 8:
            slide_number += 1
        if record_type in {_TEXT_CHARS_ATOM, _TEXT_BYTES_ATOM} and 0 < length <= 1_000_000 and offset + 8 + length <= len(raw):
            payload = raw[offset + 8 : offset + 8 + length]
            try:
                text = payload.decode("utf-16-le" if record_type == _TEXT_CHARS_ATOM else "cp1252")
            except UnicodeDecodeError:
                text = ""
            text = " ".join(text.replace("\x00", " ").split())
            if text and not _unsafe_text(text):
                output.append({
                    "slide_number": max(1, slide_number),
                    "block_type": "paragraph",
                    "content": text,
                })
            offset += 8 + length
        else:
            offset += 1
    return output


def _valid_cfb_header(raw: bytes) -> bool:
    if len(raw) < 512 or not raw.startswith(PPT_MAGIC):
        return False
    major_version = struct.unpack_from("<H", raw, 26)[0]
    byte_order = struct.unpack_from("<H", raw, 28)[0]
    sector_shift = struct.unpack_from("<H", raw, 30)[0]
    return (
        byte_order == 0xFFFE
        and (major_version, sector_shift) in {(3, 9), (4, 12)}
    )


__all__ = [
    "CSVTable",
    "MultiFormatParseError",
    "ParsedDocument",
    "decode_text_file",
    "parse_csv_table",
    "parse_json",
    "parse_local_document",
    "parse_presentation",
    "parse_txt",
    "validate_local_format",
]
