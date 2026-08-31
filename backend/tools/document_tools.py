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


__all__ = [
    "classify_document",
    "extract_key_fields",
    "extract_visual_blocks",
    "index_document",
    "parse_pdf",
    "retrieve_document",
]
