"""Case 3: deterministic freshness and conflict warnings."""

from backend import database
from backend.evidence import UNIFIED_EVIDENCE_FACTORY
from backend.services.evidence_quality import evaluate_evidence_quality


def warning_codes(result: dict) -> set[str]:
    return {item["code"] for item in result["warnings"]}


def test_time_sensitive_query_without_retrieved_at_warns() -> None:
    local = UNIFIED_EVIDENCE_FACTORY.local(
        evidence_id="fresh-local", source_type="structured",
        file_id="a" * 32, file_name="student_info.md",
        table="students", cell="B2", value_summary="当前人数为2人",
    ).model_dump()
    quality = evaluate_evidence_quality(
        "当前人数为2人", [local], query="当前专业人数是多少",
    )
    assert "FRESHNESS_MISSING" in warning_codes(quality)
    assert quality["status"] == "WARNING"


def test_explicit_local_web_conflict_warns_without_winner() -> None:
    local = UNIFIED_EVIDENCE_FACTORY.local(
        evidence_id="conflict-local", source_type="structured",
        file_id="a" * 32, file_name="student_info.md", table="students",
        cell="B2", field="就业率", record_key="计算机", value_summary="90",
    ).model_dump()
    web = UNIFIED_EVIDENCE_FACTORY.web(
        {"url": "https://example.com/employment", "snippet": "就业率为85%"},
        source_type="WEB", retrieved_at=database.utc_now(),
    ).model_dump()
    web["metadata"].update(field="就业率", record_key="计算机", value=85)
    quality = evaluate_evidence_quality("就业率为90", [local, web])
    assert "EVIDENCE_CONFLICT" in warning_codes(quality)
    assert "winner" not in quality
