"""Student-domain adapter over stable student Tools and the general Core port."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.services import confirmation
from backend.services.document_agent_core import DOCUMENT_AGENT_CORE, DocumentAgentCore
from backend.tools import analysis_tools, data_quality_tools, hybrid_tools, student_tools


STUDENT_DOMAIN_TOOL_NAMES = frozenset({
    "search_student",
    "get_student_info",
    "get_student_scores",
    "get_student_research",
    "get_top_three_students",
    "compare_students",
    "find_cross_file_conflicts",
    "validate_student_data",
    "evaluate_scholarship_eligibility",
})


class DocumentCorePort(Protocol):
    """Only the domain-neutral operations the student adapter may consume."""

    def query_table(
        self,
        file_id: str,
        sheet: str,
        filters: list[dict[str, Any]],
        select: list[str],
        order_by: dict[str, Any] | list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...

    def aggregate_table(
        self,
        file_id: str,
        sheet: str,
        operation: str,
        field: str | None = None,
        group_by: str | list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

    def retrieve_document(
        self, scope: dict[str, Any], query: str, top_k: int = 5
    ) -> dict[str, Any]: ...

    def run_controlled_retrieval(
        self, task: str, *, domain: str = "general", **kwargs: Any
    ) -> dict[str, Any]: ...

    def run_cross_source_retrieval(
        self, task: str, **kwargs: Any
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StudentDomainAdapter:
    """Compatibility adapter; it never reimplements student business rules."""

    core: DocumentCorePort = field(default_factory=lambda: DOCUMENT_AGENT_CORE)

    @property
    def tool_names(self) -> frozenset[str]:
        return STUDENT_DOMAIN_TOOL_NAMES

    def search_student(self, name_or_id: str) -> dict[str, Any]:
        return student_tools.search_student(name_or_id)

    def resolve_student_identity(self, name_or_id: str) -> dict[str, Any]:
        return student_tools.search_student(name_or_id)

    def get_student_info(self, student_id: str) -> dict[str, Any]:
        return student_tools.get_student_info(student_id)

    def get_student_scores(self, student_id: str) -> dict[str, Any]:
        return student_tools.get_student_scores(student_id)

    def get_student_research(self, student_id: str) -> dict[str, Any]:
        return student_tools.get_student_research(student_id)

    def get_top_three_students(self) -> dict[str, Any]:
        return analysis_tools.get_top_three_students()

    def compare_students(self, student_ids: list[str]) -> dict[str, Any]:
        return analysis_tools.compare_students(student_ids)

    def validate_student_data(self, student_id: str) -> dict[str, Any]:
        return data_quality_tools.validate_student_data(student_id)

    def find_student_cross_file_conflicts(
        self, student_id: str
    ) -> dict[str, Any]:
        # The legacy Tool is named generically, but its current resolver uses
        # student identity semantics. Keep it on the adapter side until a
        # future general relation contract is explicitly introduced.
        return data_quality_tools.find_cross_file_conflicts(student_id)

    def evaluate_scholarship_eligibility(
        self,
        student_id: str,
        award_name: str = "一等奖学金",
        *,
        evidence_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return hybrid_tools.evaluate_scholarship_eligibility(
            student_id,
            award_name,
            evidence_context=evidence_context,
        )

    def query_domain_table(
        self,
        file_id: str,
        sheet: str,
        filters: list[dict[str, Any]],
        select: list[str],
        order_by: dict[str, Any] | list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Use the general Core rather than a student-only table engine."""
        return self.core.query_table(
            file_id,
            sheet,
            filters,
            select,
            order_by=order_by,
            limit=limit,
        )

    def retrieve_domain_document(
        self, scope: dict[str, Any], query: str, top_k: int = 5
    ) -> dict[str, Any]:
        return self.core.retrieve_document(scope, query, top_k)

    def run_controlled_retrieval(
        self, task: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Use the same Core control layer while retaining student domain context."""
        return self.core.run_controlled_retrieval(
            task, domain="student", **kwargs
        )

    def run_cross_source_retrieval(
        self, task: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Use the same bounded cross-source coordinator as the general Core."""
        return self.core.run_cross_source_retrieval(task, **kwargs)

    def prepare_report_write(
        self,
        *,
        session_id: str,
        target_file: str,
        student_id: str,
        student_name: str,
        content: str,
        task_id: str | None = None,
        evidence_context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate the existing student report HITL/version workflow unchanged."""
        return confirmation.create_pending_action(
            session_id=session_id,
            target_file=target_file,
            student_id=student_id,
            student_name=student_name,
            content=content,
            task_id=task_id,
            evidence_context=evidence_context,
        )


STUDENT_DOMAIN_ADAPTER = StudentDomainAdapter(core=DOCUMENT_AGENT_CORE)


__all__ = [
    "DocumentCorePort",
    "STUDENT_DOMAIN_ADAPTER",
    "STUDENT_DOMAIN_TOOL_NAMES",
    "StudentDomainAdapter",
]
