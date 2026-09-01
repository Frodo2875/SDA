"""V5.2 Evidence 4.0 unified local, Web Search and Direct URL tests."""

from typing import Any

import pytest

from backend.agent import _collect_evidence
from backend.evidence import (
    UnifiedEvidence,
    deserialize_unified_evidence,
    serialize_unified_evidence,
    upgrade_local_evidence,
)
from backend.services.web_models import WebDocument
from backend.services.web_retrieval import WebRetrievalService
from frontend.components.evidence_panel import evidence_location


class FixedProvider:
    name = "v52-fixed"

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    def search(self, query, scope):
        return list(self.results)


class FixedFetcher:
    def __init__(self, document: WebDocument) -> None:
        self.document = document
        self.urls: list[str] = []

    def fetch(self, url: str) -> WebDocument:
        self.urls.append(url)
        return self.document


@pytest.mark.parametrize(
    "legacy, expected",
    [
        (
            {
                "evidence_id": "pdf-local",
                "evidence_version": "3.0",
                "source_type": "unstructured",
                "locator_type": "text_pdf",
                "file_id": "pdf-file",
                "file_name": "policy.pdf",
                "page_no": 2,
                "block_id": "pdf-block",
                "value_summary": "PDF 原文",
                "text_excerpt": "PDF 原文",
            },
            {"page_no": 2, "block_id": "pdf-block"},
        ),
        (
            {
                "evidence_id": "excel-local",
                "evidence_version": "3.0",
                "source_type": "structured",
                "locator_type": "excel",
                "file_id": "excel-file",
                "file_name": "scores.xlsx",
                "sheet": "Sheet1",
                "table": "Sheet1",
                "cell": "B2",
                "field": "成绩",
                "record_key": "row:2",
                "value_summary": "95",
            },
            {"table": "Sheet1", "cell": "B2"},
        ),
        (
            {
                "evidence_id": "image-local",
                "evidence_version": "3.0",
                "source_type": "unstructured",
                "locator_type": "image",
                "file_id": "image-file",
                "file_name": "form.png",
                "page_no": 1,
                "image_no": 1,
                "region_id": "region-1",
                "bbox": [1.0, 2.0, 40.0, 20.0],
                "confidence": 0.91,
                "value_summary": "图片 OCR 原文",
            },
            {"region_id": "region-1", "bbox": [1.0, 2.0, 40.0, 20.0]},
        ),
    ],
)
def test_local_pdf_excel_and_image_evidence_upgrade_without_losing_locators(
    legacy: dict[str, Any], expected: dict[str, Any]
) -> None:
    evidence = upgrade_local_evidence(legacy).model_dump(mode="json", exclude_none=True)

    assert evidence["evidence_version"] == "4.0"
    assert evidence["source_type"] == "LOCAL"
    assert evidence["legacy_source_type"] == legacy["source_type"]
    assert evidence["content"] == legacy.get("text_excerpt", legacy["value_summary"])
    assert evidence["authority"] == "unknown"
    assert evidence["freshness"] == "not_applicable"
    assert all(evidence[key] == value for key, value in expected.items())


def test_web_search_returns_real_untrusted_evidence_with_explicit_metadata() -> None:
    service = WebRetrievalService(search_provider=FixedProvider([{
        "title": "Policy Update",
        "url": "https://example.gov/policy",
        "snippet": "The published policy text.",
        "source": "fixed",
        "metadata": {
            "publisher": "Policy Office",
            "published_at": "2026-08-20",
            "authority": "official",
        },
    }]))

    result = service.retrieve(query="policy", top_k=1)
    evidence = result["data"]["evidence_chain"][0]

    assert UnifiedEvidence.model_validate(evidence).source_type == "WEB"
    assert evidence["url"] == "https://example.gov/policy"
    assert evidence["title"] == "Policy Update"
    assert evidence["domain"] == "example.gov"
    assert evidence["publisher"] == "Policy Office"
    assert evidence["published_at"] == "2026-08-20"
    assert evidence["retrieved_at"]
    assert evidence["authority"] == "official"
    assert evidence["freshness"] == "published_at_available"
    assert evidence["trust_level"] == "untrusted_web_data"
    assert evidence["can_trigger_tool"] is False


def test_direct_url_returns_url_evidence_instead_of_raw_document() -> None:
    url = "https://example.com/direct"
    document = WebDocument(
        document_id="direct-doc",
        url=url,
        title="Direct Page",
        content="Fetched page body.",
        canonical_url=url,
        domain="example.com",
        publisher=None,
        published_at=None,
        metadata={"content_type": "text/html"},
    )
    fetcher = FixedFetcher(document)
    result = WebRetrievalService(url_fetcher=fetcher).retrieve(url=url)
    evidence = result["data"]["evidence_chain"][0]

    assert "document" not in result["data"]
    assert evidence["source_type"] == "URL"
    assert evidence["url"] == url
    assert evidence["title"] == "Direct Page"
    assert evidence["content"] == "Fetched page body."
    assert evidence["domain"] == "example.com"
    assert evidence["retrieved_at"]
    assert "published_at" not in evidence
    assert evidence["freshness"] == "retrieval_time_only"
    assert fetcher.urls == [url]


def test_mixed_local_and_web_evidence_share_evidence_4_schema() -> None:
    local = {
        "evidence_id": "mixed-local",
        "evidence_version": "3.0",
        "source_type": "unstructured",
        "file_id": "local-file",
        "file_name": "paper.pdf",
        "page_no": 1,
        "chunk_id": "chunk-1",
        "value_summary": "Local source text.",
    }
    web_result = WebRetrievalService(search_provider=FixedProvider([{
        "title": "Web Source",
        "url": "https://example.org/source",
        "snippet": "Web source text.",
        "source": "fixed",
        "metadata": {},
    }])).retrieve(query="source")
    mixed = _collect_evidence([
        {"name": "retrieve_document", "result": {"evidence": [local]}},
        {"name": "retrieve_web", "result": web_result},
    ])

    assert {item["source_type"] for item in mixed} == {"LOCAL", "WEB"}
    assert all(item["evidence_version"] == "4.0" for item in mixed)
    assert all({
        "evidence_id", "source_type", "content", "authority", "freshness", "metadata"
    } <= set(item) for item in mixed)
    assert all(UnifiedEvidence.model_validate(item) for item in mixed)


def test_missing_authority_and_published_time_remain_unknown_and_empty() -> None:
    result = WebRetrievalService(search_provider=FixedProvider([{
        "title": "Unclassified Source",
        "url": "https://agency.gov.example/item",
        "snippet": "Source content without publication metadata.",
        "source": "fixed",
        "metadata": {"authority": "definitely_true"},
    }])).retrieve(query="item")
    evidence = result["data"]["evidence_chain"][0]

    assert evidence["authority"] == "unknown"
    assert "published_at" not in evidence
    assert evidence["retrieved_at"]
    assert evidence["freshness"] == "retrieval_time_only"
    assert "Publisher" not in " · ".join(evidence_location(evidence))
    location = evidence_location(evidence)
    assert "Domain：agency.gov.example" in location
    assert any(item.startswith("Retrieved：") for item in location)

    serialized = serialize_unified_evidence(evidence)
    restored = deserialize_unified_evidence(serialized)
    assert restored.authority == "unknown"
    assert restored.published_at is None
