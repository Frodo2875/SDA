"""Case 5: both report formats require approval and create versions."""

from pathlib import Path
from typing import Any

from docx import Document
import pytest

from backend import agent, database
from backend.evidence import UNIFIED_EVIDENCE_FACTORY
from backend.schemas import ResearchTaskRequest
from backend.services.confirmation import confirm_action
from backend.services.evidence_report import (
    approved_report_path,
    generate_report,
    preview_report_export,
)
from backend.services.research_task import create_research_task, run_research_task
from backend.tools import excel_utils


def test_markdown_docx_approval_evidence_warning_and_version(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    evidence = [UNIFIED_EVIDENCE_FACTORY.local(
        evidence_id="report-local", source_type="unstructured",
        file_id="b" * 32, file_name="student_info.md", chunk_id="chunk-1",
        value_summary="计算机专业有2名学生",
    ).model_dump()]

    async def fixed_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "answer": "计算机专业有2名学生。就业建议需要进一步核实。",
            "status": "completed", "unified_evidence": evidence,
        }

    monkeypatch.setattr(agent, "run_agent", fixed_result)
    task = create_research_task(ResearchTaskRequest(
        session_id="v6-final-report", query="生成带证据报告",
    ))
    run_research_task(task["id"])
    report = generate_report(task["id"])["data"]
    assert report["conclusions"][0]["evidence_refs"] == ["report-local"]
    assert any(item["code"] == "EVIDENCE_MISSING" for item in report["warnings"])

    for output_format in ("md", "docx"):
        pending = preview_report_export(task["id"], report["report_id"], output_format)
        assert pending["ok"] is True
        action = pending["data"]
        target = tmp_path / "uploads" / action["target_file"]
        assert not target.exists()
        assert database.list_file_versions(action["file_id"]) == []

        assert confirm_action(action["action_id"])["ok"] is True
        path = approved_report_path(task["id"], report["report_id"], output_format)
        text = (
            path.read_text(encoding="utf-8")
            if output_format == "md"
            else "\n".join(item.text for item in Document(path).paragraphs)
        )
        assert "report-local" in text
        assert "EVIDENCE\\_MISSING" in text
        assert len(database.list_file_versions(action["file_id"])) == 2
