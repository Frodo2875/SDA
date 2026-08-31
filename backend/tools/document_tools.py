"""Public Tool-facing interfaces for local unstructured document retrieval."""

from backend.services.document_index import (
    index_document,
    parse_pdf,
    retrieve_document,
)
from backend.services.visual_understanding import (
    classify_document,
    extract_key_fields,
    extract_visual_blocks,
)
from backend.services.visual_table import calculate_visual_table, extract_visual_tables


__all__ = [
    "classify_document",
    "calculate_visual_table",
    "extract_key_fields",
    "extract_visual_blocks",
    "extract_visual_tables",
    "index_document",
    "parse_pdf",
    "retrieve_document",
]
