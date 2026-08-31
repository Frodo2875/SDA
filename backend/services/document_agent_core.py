"""General document capability façade over the stable V1-V4 implementations.

This module is an architectural boundary, not a second execution system. Every
method delegates to the existing parser, Tool, Runtime, Safety, Evidence, and
Version services so legacy Tool names and handlers remain unchanged.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from backend.runtime import async_task_runtime, safety_policy
from backend.services import (
    confirmation,
    evaluation_service,
    evidence_locator,
    file_versioning,
    visual_table,
    visual_understanding,
)
from backend.tools import (
    data_quality_tools,
    document_tools,
    file_tools,
    schema_tools,
    table_tools,
)


GENERAL_CORE_TOOL_NAMES = frozenset({
    "list_files",
    "inspect_excel",
    "get_table_schema",
    "query_table",
    "aggregate_table",
    "validate_document",
    "find_duplicate_records",
    "retrieve_document",
})

# Auditable ownership map for capabilities that remain in their stable modules.
# It prevents architecture documentation from implying that these systems were
# copied into this façade.
CORE_CAPABILITY_MODULES = MappingProxyType({
    "file_schema_parser": (
        "backend.tools.file_tools",
        "backend.tools.schema_tools",
        "backend.services.document_index",
    ),
    "ocr_vision": (
        "backend.services.ocr_service",
        "backend.services.visual_understanding",
        "backend.services.visual_table",
    ),
    "table_query_aggregate": (
        "backend.tools.table_tools",
    ),
    "retrieval_rerank": (
        "backend.services.document_index",
        "backend.services.hybrid_retrieval",
    ),
    "evidence": (
        "backend.evidence",
        "backend.services.evidence_locator",
    ),
    "workflow_async": (
        "backend.runtime.task_runner",
        "backend.runtime.async_task_runtime",
    ),
    "safety_policy": (
        "backend.runtime.safety_policy",
    ),
    "diff_version_rollback": (
        "backend.services.file_versioning",
        "backend.services.confirmation",
    ),
    "trace_evaluation": (
        "backend.services.trace_service",
        "backend.services.evaluation_service",
    ),
    "validation": (
        "backend.tools.data_quality_tools",
    ),
})


@dataclass(frozen=True)
class DocumentAgentCore:
    """Thin domain-neutral interface to existing document capabilities."""

    @property
    def tool_names(self) -> frozenset[str]:
        return GENERAL_CORE_TOOL_NAMES

    @property
    def capability_modules(self) -> MappingProxyType:
        return CORE_CAPABILITY_MODULES

    def list_files(self) -> dict[str, Any]:
        return file_tools.list_files()

    def inspect_excel(self, file_id: str) -> dict[str, Any]:
        return schema_tools.inspect_excel(file_id)

    def get_table_schema(
        self, file_id: str, sheet_name: str | None = None
    ) -> dict[str, Any]:
        return schema_tools.get_table_schema(file_id, sheet_name)

    def query_table(
        self,
        file_id: str,
        sheet: str,
        filters: list[dict[str, Any]],
        select: list[str],
        order_by: dict[str, Any] | list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return table_tools.query_table(
            file_id, sheet, filters, select, order_by=order_by, limit=limit
        )

    def aggregate_table(
        self,
        file_id: str,
        sheet: str,
        operation: str,
        field: str | None = None,
        group_by: str | list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return table_tools.aggregate_table(
            file_id,
            sheet,
            operation,
            field=field,
            group_by=group_by,
            filters=filters,
        )

    def retrieve_document(
        self, scope: dict[str, Any], query: str, top_k: int = 5
    ) -> dict[str, Any]:
        return document_tools.retrieve_document(scope, query, top_k)

    def run_controlled_retrieval(
        self,
        task: str,
        *,
        domain: str = "general",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Invoke the bounded V4.9 control layer over this Core's RAG 2.0."""
        from backend.services.agentic_retrieval import run_controlled_retrieval

        return run_controlled_retrieval(task, domain=domain, **kwargs)

    def validate_document(self, file_id: str) -> dict[str, Any]:
        return data_quality_tools.validate_document(file_id)

    def find_duplicate_records(
        self, file_id: str, keys: list[str]
    ) -> dict[str, Any]:
        return data_quality_tools.find_duplicate_records(file_id, keys)

    def locate_evidence(
        self, evidence_id: str, *, session_id: str | None = None
    ) -> dict[str, Any]:
        return evidence_locator.locate_evidence(evidence_id, session_id=session_id)

    def extract_visual_blocks(self, file_id: str) -> dict[str, Any]:
        return visual_understanding.extract_visual_blocks(file_id)

    def classify_document(self, file_id: str) -> dict[str, Any]:
        return visual_understanding.classify_document(file_id)

    def extract_key_fields(
        self,
        file_id: str,
        *,
        domain: str = "general",
        schema_hint: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return visual_understanding.extract_key_fields(
            file_id, domain=domain, schema_hint=schema_hint
        )

    def extract_visual_tables(self, file_id: str) -> dict[str, Any]:
        return visual_table.extract_visual_tables(file_id)

    def enqueue_async_task(self, request: dict[str, Any]) -> dict[str, Any]:
        return async_task_runtime.enqueue_async_task(request)

    def assess_tool_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        read_only: bool,
        requires_confirmation: bool,
        confirmation_granted: bool = False,
    ) -> safety_policy.SafetyAssessment:
        return safety_policy.assess_tool_execution(
            tool_name=tool_name,
            arguments=arguments,
            read_only=read_only,
            requires_confirmation=requires_confirmation,
            confirmation_granted=confirmation_granted,
        )

    def preview_word_diff(
        self, file_id: str, operation: dict[str, Any]
    ) -> dict[str, Any]:
        return file_versioning.preview_word_diff(file_id, operation)

    def list_versions(self, file_id: str) -> dict[str, Any]:
        return file_versioning.list_versions(file_id)

    def prepare_rollback(
        self, file_id: str, version_id: str, *, session_id: str = "direct"
    ) -> dict[str, Any]:
        return confirmation.rollback_file(file_id, version_id, session_id=session_id)

    def evaluate_task(self, task_id: str) -> dict[str, Any]:
        return evaluation_service.evaluate_task(task_id)

    def evaluate_session(self, session_id: str, limit: int = 500) -> dict[str, Any]:
        return evaluation_service.evaluate_session(session_id, limit)


DOCUMENT_AGENT_CORE = DocumentAgentCore()


__all__ = [
    "CORE_CAPABILITY_MODULES",
    "DOCUMENT_AGENT_CORE",
    "GENERAL_CORE_TOOL_NAMES",
    "DocumentAgentCore",
]
