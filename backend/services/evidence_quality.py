"""Read-only, deterministic Evidence checks before a draft is delivered.

PASS means the listed structural/association checks found no warning. It does
not establish that a source or a claim is true. No retrieval or LLM is invoked.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from urllib.parse import urlsplit

from backend.evidence import UnifiedEvidence


MAX_EVIDENCE = 1000
MAX_CLAIMS = 200
MAX_DRAFT_CHARS = 100_000
_CITATION = re.compile(r"\[(?:evidence:)?([A-Za-z0-9_-]{1,128})\]")
_SENTENCE = re.compile(r"[。！？!?；;\n]+|(?<=\.)\s+")
_MESSAGES = {
    "EVIDENCE_MISSING": "部分结论没有关联到可用 Evidence，请核对引用。",
    "SOURCE_INCOMPLETE": "来源信息缺失或格式无效。",
    "LOCATION_MISSING": "本地 Evidence 缺少定位信息。",
    "FRESHNESS_MISSING": "时间敏感问题的 Evidence 缺少 retrieved_at。",
    "FRESHNESS_INVALID": "Evidence 的 retrieved_at 不是可识别的日期时间。",
    "EVIDENCE_CONFLICT": "同一字段存在不同显式值或已有冲突标记，不能自动判断哪个正确。",
    "EVIDENCE_INVALID": "Evidence 标识、内容或版本无效，不能用于覆盖检查。",
    "ANSWER_DRAFT_MISSING": "缺少可检查的回答草稿。",
    "QUALITY_INPUT_LIMIT": "输入超过检查上限，未检查的部分不能视为通过。",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normal(value: str) -> str:
    return re.sub(r"\s+", "", value).strip("。.!！?？;；").casefold()


def _timestamp(value: Any) -> bool:
    try:
        datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _has_locator(item: dict[str, Any], metadata: dict[str, Any]) -> bool:
    if any(_text(item.get(key)) for key in ("block_id", "chunk_id", "json_path")):
        return True
    if any(_positive(item.get(key)) for key in ("page_no", "line_number", "slide_number", "image_no", "row_number")):
        return True
    if _positive(metadata.get("line_number")):
        return True
    heading = metadata.get("heading_path")
    if isinstance(heading, list) and heading and all(_text(part) for part in heading):
        return True
    if _text(item.get("table")) and _text(item.get("cell")):
        return True
    if all(_text(item.get(key)) for key in ("sheet", "field", "record_key")):
        return True
    bbox = item.get("bbox")
    return bool(_text(item.get("region_id")) and isinstance(bbox, list) and len(bbox) == 4
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in bbox))


def _value_key(value: Any) -> str | None:
    """Compare explicit scalar values only; never extract facts from prose."""
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    if isinstance(value, bool):
        return f"boolean:{value}"
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
        if number.is_finite():
            return f"number:{number.normalize()}"
    except InvalidOperation:
        pass
    return f"text:{text}"


def evaluate_evidence_quality(
    answer_draft: str,
    evidence: list[UnifiedEvidence | dict[str, Any]],
    *,
    query: str = "",
    claim_evidence_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Check provenance and associations, tolerating incomplete input as warnings.

    Optional mapping keys are exact claim text. Existing inline [evidence:ID]
    or [ID] citations take precedence over exact-excerpt fallback. A mapping
    establishes association only, not semantic entailment or correctness.
    """
    warnings: dict[str, dict[str, Any]] = {}

    def warn(code: str, *, evidence_ids: list[str] | None = None,
             claim_id: str | None = None, fields: list[str] | None = None) -> None:
        warning = warnings.setdefault(code, {
            "code": code, "message": _MESSAGES[code],
            "evidence_ids": [], "claim_ids": [], "fields": [],
        })
        for key, values in (("evidence_ids", evidence_ids or []),
                            ("claim_ids", [claim_id] if claim_id else []), ("fields", fields or [])):
            warning[key] = list(dict.fromkeys([*warning[key], *values]))

    if len(evidence) > MAX_EVIDENCE or len(answer_draft) > MAX_DRAFT_CHARS:
        warn("QUALITY_INPUT_LIMIT")
    time_sensitive = bool(re.search(r"当前|最新|今日|今天|实时|目前|\b(?:current|latest|today|up.to.date)\b", query, re.I))
    usable: dict[str, dict[str, Any]] = {}
    comparable: dict[tuple[str, ...], list[tuple[str, str, str]]] = defaultdict(list)
    seen: set[str] = set()
    for raw in evidence[:MAX_EVIDENCE]:
        item = raw.model_dump() if isinstance(raw, UnifiedEvidence) else raw
        if not isinstance(item, dict):
            warn("EVIDENCE_INVALID")
            continue
        evidence_id = _text(item.get("evidence_id"))
        ids = [evidence_id] if evidence_id else []
        origin = _text(item.get("source_type"))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        valid = True
        if not evidence_id or evidence_id in seen or item.get("evidence_version") != "4.0" or not _text(item.get("content")):
            warn("EVIDENCE_INVALID", evidence_ids=ids)
            valid = False
        seen.add(evidence_id)
        missing: list[str] = []
        if origin == "LOCAL":
            missing = [key for key in ("file_id", "file_name") if not _text(item.get(key))]
            if not _has_locator(item, metadata):
                warn("LOCATION_MISSING", evidence_ids=ids)
                valid = False
        elif origin in {"WEB", "URL"}:
            missing = [key for key in ("url", "domain", "retrieved_at") if not _text(item.get(key))]
            try:
                url = urlsplit(_text(item.get("url")))
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    missing.append("url")
                elif url.hostname.casefold().rstrip(".") != _text(item.get("domain")).casefold().rstrip("."):
                    missing.append("domain")
            except ValueError:
                missing.append("url")
        else:
            missing.append("source_type")
        if missing:
            warn("SOURCE_INCOMPLETE", evidence_ids=ids, fields=missing)
            valid = False
        retrieved_at = item.get("retrieved_at")
        if time_sensitive and not _text(retrieved_at):
            warn("FRESHNESS_MISSING", evidence_ids=ids, fields=["retrieved_at"])
        elif _text(retrieved_at) and not _timestamp(retrieved_at):
            warn("FRESHNESS_INVALID", evidence_ids=ids, fields=["retrieved_at"])
        if item.get("evidence_status") == "conflict" or item.get("conflict_sources"):
            warn("EVIDENCE_CONFLICT", evidence_ids=ids)
        field = _text(item.get("field")) or _text(metadata.get("field"))
        record = _text(item.get("record_key")) or _text(metadata.get("record_key"))
        value = _value_key(metadata.get("value", item.get("value_summary")))
        if field and value is not None and origin in {"LOCAL", "WEB", "URL"}:
            # Different entity/time/unit scopes are not comparable. If neither
            # side declares an entity, this is only an unscoped field warning.
            key = (field, record, *(_value_key(metadata.get(name)) or "" for name in ("year", "period", "unit")))
            comparable[key].append((str(origin), value, evidence_id))
        if valid:
            usable[evidence_id] = item

    for key, entries in comparable.items():
        local = [(value, eid) for origin, value, eid in entries if origin == "LOCAL"]
        web = [(value, eid) for origin, value, eid in entries if origin in {"WEB", "URL"}]
        local_values = {value for value, _ in local}
        web_values = {value for value, _ in web}
        conflicting = [eid for value, eid in local if web_values - {value}]
        conflicting.extend(eid for value, eid in web if local_values - {value})
        if conflicting:
            warn("EVIDENCE_CONFLICT", evidence_ids=conflicting, fields=[key[0]])

    clauses = [part.strip() for part in _SENTENCE.split(answer_draft[:MAX_DRAFT_CHARS]) if part.strip()]
    clauses = [part for part in clauses if not re.fullmatch(r"#{1,6}\s+[^\n]+|[|\s:\-]+", part)]
    if len(clauses) > MAX_CLAIMS:
        warn("QUALITY_INPUT_LIMIT")
    if not clauses:
        warn("ANSWER_DRAFT_MISSING")
    content = {eid: _normal(item["content"][:50_000]) for eid, item in usable.items()}
    associations: list[dict[str, Any]] = []
    for index, clause in enumerate(clauses[:MAX_CLAIMS], 1):
        claim_id = f"claim-{index}"
        explicit = (claim_evidence_map or {}).get(clause)
        references = explicit if explicit is not None else _CITATION.findall(clause)
        plain = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", _CITATION.sub("", clause))
        normalized = _normal(plain)
        method = "explicit_mapping" if explicit is not None else "inline_citation" if references else "exact_excerpt"
        linked = list(dict.fromkeys(eid for eid in references if eid in usable)) if references else [
            eid for eid, text in content.items() if normalized and normalized in text
        ]
        if explicit == []:
            linked = []
        if not linked or any(eid not in usable for eid in references):
            warn("EVIDENCE_MISSING", claim_id=claim_id)
        associations.append({"claim_id": claim_id, "evidence_ids": linked, "method": method})
    return {
        "rule_version": "6.2", "passed": not warnings,
        "status": "WARNING" if warnings else "PASS", "warnings": list(warnings.values()),
        "checked_evidence_count": min(len(evidence), MAX_EVIDENCE),
        "coverage": {"checked_claim_count": len(associations), "claim_evidence": associations},
    }


def attach_evidence_quality(result: dict[str, Any], *, query: str) -> dict[str, Any]:
    """Add delivery metadata without rewriting the answer or mutating Evidence."""
    pending = result.get("pending_action")
    draft = _text(pending.get("content")) if isinstance(pending, dict) else ""
    return {**result, "evidence_quality": evaluate_evidence_quality(
        draft or _text(result.get("answer")), result.get("unified_evidence") or [], query=query,
    )}
