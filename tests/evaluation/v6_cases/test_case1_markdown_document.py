"""Case 1: Markdown enters the canonical document and Evidence pipeline."""

from pathlib import Path

from backend.evidence import UnifiedEvidence
from backend.services.evidence_locator import locate_evidence
from backend.services.file_upload import save_uploaded_file
from backend.tools import excel_utils
from backend.tools.document_tools import retrieve_document


def test_markdown_heading_retrieval_and_location(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    uploaded = save_uploaded_file(
        "student_info.md",
        "# 学生资料\n\n## 专业信息\n张三的专业是计算机科学。\n".encode(),
    )
    assert uploaded["ok"] is True

    retrieved = retrieve_document(
        {"file_id": uploaded["data"]["file_id"]}, "张三 专业 计算机科学"
    )
    evidence = next(
        item for item in retrieved["unified_evidence"]
        if "计算机科学" in item["content"]
    )
    canonical = UnifiedEvidence.model_validate(evidence)
    assert canonical.evidence_version == "4.0"
    assert canonical.file_name == "student_info.md"
    assert canonical.metadata["heading_path"] == ["学生资料", "专业信息"]
    assert canonical.line_number == 4

    located = locate_evidence(canonical.evidence_id)
    assert located["ok"] is True
    assert located["data"]["heading_path"] == ["学生资料", "专业信息"]
    assert located["data"]["line_number"] == 4
