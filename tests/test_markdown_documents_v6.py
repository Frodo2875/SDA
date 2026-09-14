"""Markdown uses V5 lifecycle, index, embedding, retrieval and Evidence contracts."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend import database
from backend.evidence import UnifiedEvidence, UnifiedEvidenceFactory
from backend.main import app
from backend.runtime.async_task_runtime import enqueue_async_task, run_async_task
from backend.services import embedding_service, markdown_parser
from backend.services.document_index import reindex_document, reprocess_document
from backend.services.evidence_locator import locate_evidence
from backend.services.file_upload import save_uploaded_file
from backend.services.input_router import route_document_input
from backend.services.markdown_parser import parse_markdown
from backend.services.multiformat_parser import MultiFormatParseError, ParsedDocument
from backend.table_structure import TableStructure
from backend.tools import excel_utils
from backend.tools.document_tools import retrieve_document
from backend.tools.file_tools import list_files


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def parse(tmp_path: Path, text: str) -> ParsedDocument:
    path = tmp_path / "example.md"
    path.write_text(text, encoding="utf-8")
    return parse_markdown(path, file_id="a" * 32, file_name=path.name)


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".MD"])
def test_markdown_type_upload_lifecycle_and_discovery(data_dir: Path, suffix: str) -> None:
    name = "材料" + suffix
    result = save_uploaded_file(name, "# 项目说明\n\n安装星河软件。".encode(), "text/markdown")
    assert result["ok"] is True
    assert result["data"]["file_type"] == "markdown"
    assert result["data"]["input_route"] == "TEXT"
    record = database.get_file_record(name)
    assert (record["parse_status"], record["index_status"], record["queryable"]) == ("parsed", "indexed", 1)
    assert record["lifecycle_status"] == "ready"
    assert list_files()["data"][0]["file_type"] == "markdown"
    routed = route_document_input(data_dir / "uploads" / name)
    assert routed["data"]["mime_type"] == "text/markdown"
    chunks = database.get_document_chunks(record["file_id"])
    assert all(chunk["metadata"]["document_format"] == "markdown" for chunk in chunks)


def test_heading_paths_follow_levels_and_keep_source_lines(tmp_path: Path) -> None:
    document = parse(tmp_path, "# 项目说明\n\n## 安装方法\n安装星河\n继续说明\n### Linux\n细节\n## 卸载\n移除\n# 附录\n结束")
    metadata = [document.metadata_by_block[block.block_id] for block in document.blocks]
    assert document.blocks[0].block_type == "title"
    assert metadata[1]["heading_path"] == ["项目说明", "安装方法"]
    assert metadata[2]["heading_path"] == ["项目说明", "安装方法"]
    assert (metadata[2]["line_number"], metadata[2]["line_end"]) == (4, 5)
    assert document.blocks[2].content == "安装星河\n继续说明"
    assert metadata[3]["heading_path"] == ["项目说明", "安装方法", "Linux"]
    assert metadata[5]["heading_path"] == ["项目说明", "卸载"]
    assert metadata[-1]["heading_path"] == ["附录"]
    assert metadata[1]["block_type"] == "markdown_section"


@pytest.mark.parametrize("text", ["- 一\n- 二\n  - 子项", "1. 一\n2. 二", "1) 一\n2) 二", "+ 一\n* 二"])
def test_lists_preserve_markers_and_indentation(tmp_path: Path, text: str) -> None:
    document = parse(tmp_path, text)
    assert len(document.blocks) == 1
    block = document.blocks[0]
    assert block.content == text
    assert block.block_type == "paragraph"
    assert document.metadata_by_block[block.block_id]["block_type"] == "markdown_list"


def test_table_uses_existing_block_and_table_structure(tmp_path: Path) -> None:
    document = parse(tmp_path, "|姓名|专业|\n|-|-|\n|张三|计算机|\n|李四|数\\|学|")
    block = document.blocks[0]
    assert block.block_type == "table"
    metadata = document.metadata_by_block[block.block_id]
    table = TableStructure.model_validate(metadata["table"])
    assert (table.row_count, table.column_count) == (3, 2)
    assert [cell.cell_text for cell in table.cells] == ["姓名", "专业", "张三", "计算机", "李四", "数|学"]
    assert (metadata["line_number"], metadata["line_end"]) == (1, 4)


def test_irregular_table_degrades_without_inventing_cells(tmp_path: Path) -> None:
    document = parse(tmp_path, "|a|b|\n|-|-|\n|only one|")
    block = document.blocks[0]
    assert block.status == "degraded"
    assert block.warnings == ["IRREGULAR_MARKDOWN_TABLE"]
    assert "table" not in document.metadata_by_block[block.block_id]
    assert "only one" in block.content


@pytest.mark.parametrize("marker", ["```", "~~~~"])
def test_fenced_code_preserves_language_content_and_ignores_syntax(tmp_path: Path, marker: str) -> None:
    body = '# not a heading\nprint("hello")\n[not a link](https://example.com)'
    document = parse(tmp_path, f"# 安装\n{marker}python\n{body}\n{marker}\n结束")
    block = document.blocks[1]
    metadata = document.metadata_by_block[block.block_id]
    assert metadata["language"] == "python"
    assert metadata["content"] == block.content == body
    assert metadata["heading_path"] == ["安装"]
    assert "links" not in metadata
    assert (metadata["line_number"], metadata["line_end"]) == (2, 6)


def test_unclosed_fence_preserves_text_with_warning(tmp_path: Path) -> None:
    document = parse(tmp_path, "```sh\necho hello")
    assert document.blocks[0].content == "echo hello"
    assert document.blocks[0].warnings == ["UNCLOSED_MARKDOWN_FENCE"]


def test_links_are_source_data_and_preserve_text_and_url(tmp_path: Path) -> None:
    document = parse(tmp_path, '[GitHub](https://github.com) 和 [文档](<https://example.com/docs> "标题")')
    metadata = document.metadata_by_block[document.blocks[0].block_id]
    assert metadata["links"] == [
        {"text": "GitHub", "url": "https://github.com"},
        {"text": "文档", "url": "https://example.com/docs"},
    ]
    assert "[GitHub](https://github.com)" in document.content


@pytest.mark.parametrize("suffix", [".md", ".markdown"])
def test_retrieval_returns_canonical_evidence_and_heading_line_location(data_dir: Path, suffix: str) -> None:
    name = "example" + suffix
    result = save_uploaded_file(name, "# 项目说明\n\n## 安装方法\n运行星河安装程序。\n确认安装完成。".encode())
    assert result["ok"]
    file_id = result["data"]["file_id"]
    retrieved = retrieve_document({"file_id": file_id}, "星河安装程序")
    assert retrieved["ok"]
    evidence = next(item for item in retrieved["unified_evidence"] if "运行星河" in item["content"])
    canonical = UnifiedEvidence.model_validate(evidence)
    assert canonical.evidence_version == "4.0"
    assert canonical.file_name == name
    assert canonical.source_type == "LOCAL"
    assert canonical.line_number == 4
    assert canonical.metadata["heading_path"] == ["项目说明", "安装方法"]
    assert canonical.metadata["line_end"] == 5
    assert canonical.metadata["legacy_evidence_version"] == "3.0"
    assert canonical.can_trigger_tool is False
    assert canonical.can_approve is False
    located = locate_evidence(canonical.evidence_id)
    assert located["ok"]
    assert located["data"]["location_type"] == "markdown"
    assert located["data"]["heading_path"] == ["项目说明", "安装方法"]
    assert (located["data"]["line_number"], located["data"]["line_end"]) == (4, 5)


def test_long_block_citation_locates_exact_split_chunk(data_dir: Path) -> None:
    result = save_uploaded_file("long.md", ("# 标题\n" + "开头材料" * 300 + "\n定位独特结尾信号").encode())
    file_id = result["data"]["file_id"]
    chunks = database.get_document_chunks(file_id)
    assert len(chunks) > 2
    retrieved = retrieve_document({"file_id": file_id}, "定位独特结尾信号")
    evidence = next(item for item in retrieved["unified_evidence"] if "结尾信号" in item["content"])
    location = locate_evidence(evidence["evidence_id"])["data"]
    assert location["chunk_id"] == evidence["chunk_id"]
    assert "结尾信号" in location["text"]
    assert location["line_number"] == 2  # Block range, not invented exact substring lines.


def test_reindex_stable_ids_and_reprocess_changed_source(data_dir: Path) -> None:
    result = save_uploaded_file("update.md", b"# Title\nold unique source")
    file_id = result["data"]["file_id"]
    before = database.get_document_chunks(file_id)
    assert reindex_document(file_id)["ok"]
    assert [c["chunk_id"] for c in before] == [c["chunk_id"] for c in database.get_document_chunks(file_id)]
    old = retrieve_document({"file_id": file_id}, "old unique source")["unified_evidence"][0]
    (data_dir / "uploads" / "update.md").write_text("# Changed\n新的升级配置", encoding="utf-8")
    assert reprocess_document(file_id)["ok"]
    new = retrieve_document({"file_id": file_id}, "升级配置")
    assert any("升级配置" in item["content"] for item in new["unified_evidence"])
    assert locate_evidence(old["evidence_id"])["ok"] is False


def test_failed_reprocess_preserves_active_index(data_dir: Path) -> None:
    uploaded = save_uploaded_file("keep.md", b"# Title\nretained source")
    file_id = uploaded["data"]["file_id"]
    before = database.get_document_chunks(file_id)
    (data_dir / "uploads" / "keep.md").write_bytes(b"\x00\xff")
    assert reprocess_document(file_id)["ok"] is False
    assert database.get_document_chunks(file_id) == before


def test_markdown_uses_existing_async_index_handler(data_dir: Path) -> None:
    uploaded = save_uploaded_file("async.md", b"# Async\nsource")
    queued = enqueue_async_task({"session_id": "md", "task_type": "reindex", "payload": {"file_id": uploaded["data"]["file_id"]}})
    assert queued["ok"]
    result = asyncio.run(run_async_task(queued["data"]["task"]["task_id"]))
    assert result["ok"]
    assert result["data"]["task"]["task_status"] == "success"


@pytest.mark.parametrize("content", [b"", b"  \n", b"\x00\x01\xff", b"```\n```"])
def test_invalid_or_empty_markdown_not_registered(data_dir: Path, content: bytes) -> None:
    result = save_uploaded_file("invalid.md", content)
    assert result["ok"] is False
    assert database.get_file_record("invalid.md") is None


def test_mime_mismatch_and_duplicate_upload_rejected(data_dir: Path) -> None:
    assert not save_uploaded_file("same.md", b"# Title", "image/png")["ok"]
    assert save_uploaded_file("same.md", b"# Title", "text/plain")["ok"]
    assert save_uploaded_file("same.md", b"# Changed")["error_code"] == "FILE_ALREADY_EXISTS"
    assert (data_dir / "uploads" / "same.md").read_bytes() == b"# Title"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_markdown_reuses_text_decoding(data_dir: Path, encoding: str) -> None:
    result = save_uploaded_file("中文.md", "# 中文\n安装说明".encode(encoding))
    assert result["ok"]


@pytest.mark.parametrize("limit,content", [("MAX_LINES", "a\nb\nc"), ("MAX_BLOCKS", "# a\n# b\n# c"), ("MAX_TABLE_CELLS", "|a|b|\n|-|-|\n|c|d|")])
def test_parser_resource_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str, content: str) -> None:
    monkeypatch.setattr(markdown_parser, limit, 2)
    with pytest.raises(MultiFormatParseError):
        parse(tmp_path, content)


def test_untrusted_markdown_never_executes_or_fetches(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Markdown parsing must not perform HTTP requests")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    source = '# 资料\n<script>alert(1)</script>\n[内部](http://127.0.0.1)\n![图片](https://example.com/a.png)\n```python\nraise RuntimeError("do not execute")\n```'
    result = save_uploaded_file("unsafe.md", source.encode())
    assert result["ok"]
    retrieved = retrieve_document({"file_id": result["data"]["file_id"]}, "资料")
    assert retrieved["unified_evidence"]
    assert all(item["can_trigger_tool"] is False and item["can_approve"] is False for item in retrieved["unified_evidence"])


def test_upload_and_file_filter_http_api(data_dir: Path) -> None:
    async def request() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/files/upload", files={"file": ("api.markdown", b"# API\nsource", "text/markdown")})
            assert response.status_code == 200
            assert response.json()["ok"]
            listed = await client.get("/api/files", params={"file_type": "markdown"})
            assert listed.status_code == 200
            assert [item["file_name"] for item in listed.json()["data"]] == ["api.markdown"]
    asyncio.run(request())


def test_retrieval_uses_existing_embedding_and_factory(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    embedded: list[str] = []
    created: list[UnifiedEvidence] = []
    original_embed = embedding_service.LocalHashEmbeddingProvider.embed_documents
    original_factory = UnifiedEvidenceFactory.local

    def embed(self: embedding_service.LocalHashEmbeddingProvider, texts: list[str]) -> list[list[float]]:
        embedded.extend(texts)
        return original_embed(self, texts)

    def local(self: UnifiedEvidenceFactory, **values: Any) -> UnifiedEvidence:
        result = original_factory(self, **values)
        created.append(result)
        return result

    monkeypatch.setattr(embedding_service.LocalHashEmbeddingProvider, "embed_documents", embed)
    monkeypatch.setattr(UnifiedEvidenceFactory, "local", local)
    uploaded = save_uploaded_file("factory.md", b"# Setup\ninstall starlight")
    result = retrieve_document({"file_id": uploaded["data"]["file_id"]}, "install starlight")
    assert "install starlight" in embedded
    assert created
    assert {item["evidence_id"] for item in result["unified_evidence"]} == {item.evidence_id for item in created}
    # Format metadata supplements the factory result, never recreates provenance.
    assert all(item.trust_level == "untrusted_document_data" for item in created)


def test_empty_code_among_real_content_does_not_create_empty_evidence(data_dir: Path) -> None:
    uploaded = save_uploaded_file("empty-code.md", b"# Guide\n```python\n```\ninstall starlight")
    assert uploaded["ok"]
    result = retrieve_document({"file_id": uploaded["data"]["file_id"]}, "install")
    assert result["unified_evidence"]
    assert all(item["content"].strip() for item in result["unified_evidence"])


def test_markdown_table_content_is_retrievable(data_dir: Path) -> None:
    uploaded = save_uploaded_file("table.md", "# 人员\n|姓名|专业|\n|-|-|\n|张三|计算机|".encode())
    result = retrieve_document({"file_id": uploaded["data"]["file_id"]}, "张三")
    table = next(item for item in result["unified_evidence"] if "张三" in item["content"])
    assert table["metadata"]["block_type"] == "markdown_table"
    assert table["metadata"]["heading_path"] == ["人员"]
    assert (table["metadata"]["line_number"], table["metadata"]["line_end"]) == (2, 4)


def test_crlf_blank_lines_and_repeated_headings_have_stable_distinct_blocks(tmp_path: Path) -> None:
    first = parse(tmp_path, "# 同名\r\n\r\n正文\r\n# 同名\r\n正文")
    second = parse(tmp_path, "# 同名\r\n\r\n正文\r\n# 同名\r\n正文")
    ids = [block.block_id for block in first.blocks]
    assert len(set(ids)) == 4
    assert ids == [block.block_id for block in second.blocks]
    assert first.metadata_by_block[ids[-1]]["line_number"] == 5


def test_missing_markdown_file_has_explicit_parse_error(tmp_path: Path) -> None:
    with pytest.raises(MultiFormatParseError, match="无法读取"):
        parse_markdown(tmp_path / "missing.md", file_id="a" * 32, file_name="missing.md")


def test_unmatched_link_brackets_are_preserved_as_text(tmp_path: Path) -> None:
    source = "[" * 10_000 + "\n[valid](https://example.com)"
    document = parse(tmp_path, source)
    assert document.content == source
    metadata = document.metadata_by_block[document.blocks[0].block_id]
    assert metadata["links"] == [{"text": "valid", "url": "https://example.com"}]
