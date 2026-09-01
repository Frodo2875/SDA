"""V5.0 multi-format documents use the existing lifecycle, retrieval and Evidence."""

from io import BytesIO
from pathlib import Path
import struct
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend import database
from backend.services.evidence_locator import locate_evidence
from backend.services.file_upload import save_uploaded_file
from backend.services.file_view import get_file_preview
from backend.services.input_router import route_document_input
from backend.tools import excel_utils
from backend.tools.schema_tools import get_table_schema
from backend.tools.table_tools import aggregate_table, query_table
from backend.tools.document_tools import retrieve_document


@pytest.fixture
def multiformat_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _pptx_bytes() -> bytes:
    output = BytesIO()
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
</Types>"""
    presentation = """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>"""

    def slide(text: str, table: bool = False) -> str:
        table_xml = """
<p:graphicFrame><a:graphic><a:graphicData><a:tbl>
  <a:tr h="1"><a:tc><a:txBody><a:p><a:r><a:t>项目</a:t></a:r></a:p></a:txBody></a:tc>
  <a:tc><a:txBody><a:p><a:r><a:t>预算</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
  <a:tr h="1"><a:tc><a:txBody><a:p><a:r><a:t>星河</a:t></a:r></a:p></a:txBody></a:tc>
  <a:tc><a:txBody><a:p><a:r><a:t>120</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
</a:tbl></a:graphicData></a:graphic></p:graphicFrame>""" if table else ""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>{table_xml}</p:spTree></p:cSld>
</p:sld>"""

    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide("第一张：项目介绍"))
        archive.writestr("ppt/slides/slide2.xml", slide("第二张：预算结论", table=True))
    return output.getvalue()


def _ppt_bytes() -> bytes:
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 26, 3)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)

    def record(record_type: int, payload: bytes = b"") -> bytes:
        return struct.pack("<HHI", 0, record_type, len(payload)) + payload

    return bytes(header) + b"".join([
        record(1006),
        record(4000, "第一页旧版演示".encode("utf-16-le")),
        record(1006),
        record(4000, "第二页旧版结论".encode("utf-16-le")),
    ])


def test_txt_utf8_and_gb18030_enter_existing_index_and_line_evidence(
    multiformat_data_dir: Path,
) -> None:
    utf8 = save_uploaded_file("通知.txt", "第一行\n奖学金材料在第二行\n".encode())
    gb = save_uploaded_file("中文编码.txt", "常见中文编码内容".encode("gb18030"))

    assert utf8["ok"] is True
    assert gb["ok"] is True
    record = database.get_file_record("通知.txt")
    assert record["file_type"] == "txt"
    assert (record["lifecycle_status"], record["parse_status"], record["index_status"], record["queryable"]) == (
        "ready", "parsed", "indexed", 1
    )
    chunks = database.get_document_chunks(record["file_id"])
    assert chunks[1]["metadata"]["line_number"] == 2
    retrieved = retrieve_document({"file_id": record["file_id"]}, "奖学金材料")
    evidence = retrieved["evidence"][0]
    assert evidence["locator_type"] == "txt"
    assert evidence["line_number"] == 2
    located = locate_evidence(evidence["evidence_id"])
    assert located["data"]["location_type"] == "txt"
    assert located["data"]["line_number"] == 2


def test_txt_invalid_encoding_is_rejected_without_registration(
    multiformat_data_dir: Path,
) -> None:
    result = save_uploaded_file("异常.txt", b"\x00\x01\x02\x03\x00\xff")

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_FILE_CONTENT"
    assert database.get_file_record("异常.txt") is None


def test_csv_header_query_sort_aggregate_and_row_column_evidence(
    multiformat_data_dir: Path,
) -> None:
    content = "项目,部门,预算\n星河,研发,120\n晨曦,行政,80\n远航,研发,200\n".encode()
    uploaded = save_uploaded_file("项目预算.csv", content, "text/csv")

    assert uploaded["ok"] is True
    file_id = uploaded["data"]["file_id"]
    schema = get_table_schema(file_id, "CSV")
    assert [field["source_name"] for field in schema["data"]["schemas"][0]["fields"]] == [
        "项目", "部门", "预算"
    ]
    queried = query_table(
        file_id,
        "CSV",
        [{"field": "部门", "op": "=", "value": "研发"}],
        ["项目", "预算"],
        order_by={"field": "预算", "direction": "desc"},
    )
    assert queried["data"]["rows"] == [
        {"项目": "远航", "预算": 200},
        {"项目": "星河", "预算": 120},
    ]
    evidence = queried["evidence_chain"][0]
    assert evidence["locator_type"] == "csv"
    assert evidence["row_number"] == 4
    assert evidence["column_name"] == "项目"
    located = locate_evidence(evidence["evidence_id"])
    assert located["data"]["location_type"] == "csv"
    assert located["data"]["target_value"] == "远航"
    aggregated = aggregate_table(
        file_id,
        "CSV",
        "sum",
        field="预算",
        filters=[{"field": "部门", "op": "=", "value": "研发"}],
    )
    assert aggregated["data"]["results"] == [
        {"operation": "sum", "field": "预算", "value": 320}
    ]


def test_json_object_array_and_nested_path_are_preserved_in_evidence(
    multiformat_data_dir: Path,
) -> None:
    content = b'{"users":[{"profile":{"name":"Alice","score":91}},{"profile":{"name":"Bob","score":88}}]}'
    uploaded = save_uploaded_file("用户.json", content, "application/json")

    assert uploaded["ok"] is True
    file_id = uploaded["data"]["file_id"]
    chunks = database.get_document_chunks(file_id)
    paths = {chunk["metadata"]["json_path"] for chunk in chunks}
    assert "$.users[0].profile.name" in paths
    assert "$.users[1].profile.score" in paths
    retrieved = retrieve_document({"file_id": file_id}, "Alice")
    evidence = retrieved["evidence"][0]
    assert evidence["locator_type"] == "json"
    assert evidence["json_path"] == "$.users[0].profile.name"
    located = locate_evidence(evidence["evidence_id"])
    assert located["data"]["json_path"] == "$.users[0].profile.name"


def test_pptx_multislide_text_and_basic_table_use_slide_evidence(
    multiformat_data_dir: Path,
) -> None:
    uploaded = save_uploaded_file(
        "项目汇报.pptx",
        _pptx_bytes(),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    assert uploaded["ok"] is True
    assert uploaded["data"]["file_type"] == "presentation"
    assert uploaded["data"]["input_route"] == "TEXT"
    file_id = uploaded["data"]["file_id"]
    chunks = database.get_document_chunks(file_id)
    assert {chunk["metadata"]["slide_number"] for chunk in chunks} == {1, 2}
    assert any(chunk["metadata"].get("table_no") == 1 for chunk in chunks)
    retrieved = retrieve_document({"file_id": file_id, "slide": 2}, "预算结论")
    evidence = retrieved["evidence"][0]
    assert evidence["locator_type"] == "presentation"
    assert evidence["slide_number"] == 2
    located = locate_evidence(evidence["evidence_id"])
    assert located["data"]["location_type"] == "presentation"
    assert located["data"]["slide_number"] == 2
    preview = get_file_preview(file_id)
    assert preview["ok"] is True
    assert preview["data"]["preview_type"] == "text"


def test_legacy_ppt_text_atoms_keep_slide_numbers(
    multiformat_data_dir: Path,
) -> None:
    uploaded = save_uploaded_file("旧版汇报.ppt", _ppt_bytes(), "application/vnd.ms-powerpoint")

    assert uploaded["ok"] is True
    file_id = uploaded["data"]["file_id"]
    chunks = database.get_document_chunks(file_id)
    assert [(item["metadata"]["slide_number"], item["chunk_text"]) for item in chunks] == [
        (1, "第一页旧版演示"),
        (2, "第二页旧版结论"),
    ]


def test_router_accepts_all_v5_formats_and_rejects_corrupt_presentation(
    multiformat_data_dir: Path,
) -> None:
    samples = {
        "a.txt": b"plain text",
        "a.json": b'{"ok":true}',
        "a.csv": b"name,value\na,1\n",
        "a.pptx": _pptx_bytes(),
    }
    routes = {}
    for name, content in samples.items():
        path = multiformat_data_dir / name
        path.write_bytes(content)
        routes[name] = route_document_input(path)["data"]["route"]

    assert routes == {"a.txt": "TEXT", "a.json": "TEXT", "a.csv": "STRUCTURED", "a.pptx": "TEXT"}
    corrupt = multiformat_data_dir / "bad.ppt"
    corrupt.write_bytes(b"not a presentation")
    rejected = route_document_input(corrupt)
    assert rejected["ok"] is False
    assert rejected["error_code"] == "INVALID_FILE_CONTENT"
