"""Bounded retry policy for observable Python Tool failures."""

from typing import Any


MAX_RETRIES = 2

RETRYABLE_ERROR_CODES = {
    "TEMPORARY_IO_ERROR",
    "FILE_LIST_ERROR",
    "TABLE_READ_ERROR",
    "SCHEMA_READ_ERROR",
    "RETRIEVAL_ERROR",
    "FILE_PARSE_ERROR",
    "PDF_PARSE_ERROR",
    "WORD_PARSE_ERROR",
}

NON_RETRYABLE_ERROR_CODES = {
    "FILE_NOT_FOUND",
    "SHEET_NOT_FOUND",
    "FIELD_NOT_FOUND",
    "PERMISSION_DENIED",
    "FILE_DELETE_FORBIDDEN",
    "CONFLICT",
    "AMBIGUOUS",
    "INVALID_DATA",
    "TOOL_SEQUENCE_ERROR",
    "INVALID_TOOL_ARGUMENTS",
}


def is_retryable_result(
    tool_name: str,
    result: dict[str, Any],
    *,
    retryable: bool,
    read_only: bool,
    requires_confirmation: bool,
) -> bool:
    """Never retry side effects; retry only explicit transient/read-only outcomes."""
    if not retryable or not read_only or requires_confirmation:
        return False
    error_code = result.get("error_code")
    if error_code in NON_RETRYABLE_ERROR_CODES:
        return False
    if error_code in RETRYABLE_ERROR_CODES:
        return True
    return (
        tool_name == "retrieve_document"
        and result.get("ok") is True
        and isinstance(result.get("data"), dict)
        and result["data"].get("status") == "not_found"
    )
