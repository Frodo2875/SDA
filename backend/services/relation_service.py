"""Deterministic cross-document entity resolution over discovered Excel schemas."""

from collections import defaultdict
from typing import Any

from backend import database
from backend.services.field_semantics import map_field_semantics
from backend.tools.excel_utils import read_excel_rows
from backend.tools.student_tools import (
    RESEARCH_FIELDS,
    RESEARCH_FILE,
    SCORE_FIELDS,
    SCORE_FILE,
    STUDENT_FIELDS,
    STUDENT_FILE,
)
from backend.tools.table_tools import load_table_data


PRIMARY_KEY = "student_id"
EXPLICIT_UNIQUE_KEYS = ("id_card", "passport", "email")
COMPOSITE_KEYS = ("name", "college", "grade", "class")


def discover_relation_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load queryable, reliably parsed Excel sheets and describe rejected sources."""
    sources: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for record in database.fetch_all("files"):
        if record["file_type"] != "excel" or not bool(record["queryable"]):
            continue
        for schema in database.get_table_schema_records(record["file_id"]):
            if schema["detection_status"] != "detected":
                continue
            loaded = load_table_data(record["file_id"], schema["sheet_name"])
            if not loaded["ok"]:
                issues.append(
                    {
                        "file_id": record["file_id"],
                        "file_name": record["file_name"],
                        "sheet": schema["sheet_name"],
                        "reason": loaded["error_code"],
                    }
                )
                continue
            context = loaded["data"]
            semantic_fields, ambiguous_semantics = _semantic_fields(context["fields"])
            sources.append(
                {
                    "file_id": record["file_id"],
                    "file_name": record["file_name"],
                    "sheet": schema["sheet_name"],
                    "schema": schema,
                    "fields": context["fields"],
                    "semantic_fields": semantic_fields,
                    "ambiguous_semantics": ambiguous_semantics,
                    "rows": context["rows"],
                }
            )
    _append_v1_compatibility_sources(sources, issues)
    return sources, issues


def resolve_entity(entity_id: str) -> dict[str, Any]:
    """Resolve one entity without allowing callers or an LLM to invent join keys."""
    normalized_id = _normalized(entity_id)
    sources, load_issues = discover_relation_sources()
    matches: list[dict[str, Any]] = []
    relation_issues: list[dict[str, Any]] = []

    for source in sources:
        if PRIMARY_KEY in source["ambiguous_semantics"]:
            relation_issues.append(_issue(source, "ambiguous", "存在多个学号语义字段"))
            continue
        id_field = source["semantic_fields"].get(PRIMARY_KEY)
        if id_field is None:
            continue
        for row in source["rows"]:
            if _normalized(row.get(id_field["source_name"])) == normalized_id:
                matches.append(_record(source, row, "student_id"))

    # If the supplied identifier is not a student ID, only explicitly unique fields
    # may establish an anchor. Generic columns such as 编号/代码 are never considered.
    if not matches:
        for source in sources:
            for key in EXPLICIT_UNIQUE_KEYS:
                field = source["semantic_fields"].get(key)
                if field is None or not _is_reliably_unique(field, source["schema"]):
                    continue
                for row in source["rows"]:
                    if _normalized(row.get(field["source_name"])) == normalized_id:
                        matches.append(_record(source, row, key))

    if not matches:
        return {
            "status": "not_found",
            "entity_id": entity_id,
            "records": [],
            "relation_issues": relation_issues,
            "load_issues": load_issues,
        }

    anchor_values = _consensus_values(matches)
    matched_sources = {_source_key(item) for item in matches}
    for source in sources:
        if _source_key(source) in matched_sources:
            continue
        candidate_rows, method = _auxiliary_candidates(source, anchor_values)
        if len(candidate_rows) == 1:
            matches.append(_record(source, candidate_rows[0], method))
        elif len(candidate_rows) > 1:
            relation_issues.append(
                _issue(source, "ambiguous", f"{method} 匹配到多条记录")
            )
        elif _has_partial_composite(source, anchor_values):
            relation_issues.append(
                _issue(source, "ambiguous", "辅助身份字段不完整，无法可靠关联")
            )

    status = "ambiguous" if any(
        issue["status"] == "ambiguous" for issue in relation_issues
    ) else "found"
    return {
        "status": status,
        "entity_id": entity_id,
        "records": matches,
        "relation_issues": relation_issues,
        "load_issues": load_issues,
    }


def canonical_values(record: dict[str, Any]) -> dict[str, Any]:
    """Return non-null canonical values from one resolved source row."""
    values: dict[str, Any] = {}
    for canonical_name, field in record["semantic_fields"].items():
        value = record["row"].get(field["source_name"])
        if not _is_null(value):
            values[canonical_name] = value
    return values


def source_reference(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": record["file_id"],
        "file_name": record["file_name"],
        "sheet": record["sheet"],
        "row_number": record["row"].get("__row_number__"),
        "relation_method": record["relation_method"],
    }


def _semantic_fields(
    fields: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields.values():
        canonical = field.get("canonical_name")
        if canonical and float(field.get("mapping_confidence") or 0) >= 0.8:
            candidates[canonical].append(field)
    unique = {name: values[0] for name, values in candidates.items() if len(values) == 1}
    ambiguous = {name for name, values in candidates.items() if len(values) > 1}
    return unique, ambiguous


def _record(source: dict[str, Any], row: dict[str, Any], method: str) -> dict[str, Any]:
    return {
        "file_id": source["file_id"],
        "file_name": source["file_name"],
        "sheet": source["sheet"],
        "semantic_fields": source["semantic_fields"],
        "fields": source["fields"],
        "row": row,
        "relation_method": method,
    }


def _consensus_values(records: list[dict[str, Any]]) -> dict[str, Any]:
    gathered: dict[str, set[Any]] = defaultdict(set)
    originals: dict[tuple[str, str], Any] = {}
    for record in records:
        for key, value in canonical_values(record).items():
            normalized = _normalized(value)
            if normalized:
                gathered[key].add(normalized)
                originals[(key, normalized)] = value
    return {
        key: originals[(key, next(iter(values)))]
        for key, values in gathered.items()
        if len(values) == 1
    }


def _auxiliary_candidates(
    source: dict[str, Any], anchor_values: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    for key in EXPLICIT_UNIQUE_KEYS:
        field = source["semantic_fields"].get(key)
        if key not in anchor_values or field is None:
            continue
        if not _is_reliably_unique(field, source["schema"]):
            continue
        matches = [
            row
            for row in source["rows"]
            if _normalized(row.get(field["source_name"]))
            == _normalized(anchor_values[key])
        ]
        if matches:
            return matches, key

    if all(key in anchor_values for key in COMPOSITE_KEYS) and all(
        key in source["semantic_fields"] for key in COMPOSITE_KEYS
    ):
        matches = []
        for row in source["rows"]:
            if all(
                _normalized(row.get(source["semantic_fields"][key]["source_name"]))
                == _normalized(anchor_values[key])
                for key in COMPOSITE_KEYS
            ):
                matches.append(row)
        return matches, "name_college_grade_class"
    return [], "none"


def _has_partial_composite(source: dict[str, Any], anchors: dict[str, Any]) -> bool:
    available = set(source["semantic_fields"]).intersection(COMPOSITE_KEYS)
    return bool(available) and available != set(COMPOSITE_KEYS) and "name" in anchors


def _is_reliably_unique(field: dict[str, Any], schema: dict[str, Any]) -> bool:
    return (
        int(field.get("null_count") or 0) == 0
        and int(field.get("unique_count") or 0) == int(schema.get("row_count") or 0)
        and int(schema.get("row_count") or 0) > 0
    )


def _issue(source: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "file_id": source["file_id"],
        "file_name": source["file_name"],
        "sheet": source["sheet"],
        "reason": reason,
    }


def _append_v1_compatibility_sources(
    sources: list[dict[str, Any]], issues: list[dict[str, Any]]
) -> None:
    """Expose fixed V1 tables that intentionally require no Schema discovery."""
    already_loaded = {item["file_id"] for item in sources}
    adapters = (
        (STUDENT_FILE, STUDENT_FIELDS),
        (SCORE_FILE, SCORE_FIELDS),
        (RESEARCH_FILE, RESEARCH_FIELDS),
    )
    for file_name, required_fields in adapters:
        record = database.get_file_record(file_name)
        if record is None or record["file_id"] in already_loaded or not bool(record["queryable"]):
            continue
        result = read_excel_rows(file_name, required_fields)
        if not result["ok"]:
            issues.append(
                {
                    "file_id": record["file_id"],
                    "file_name": file_name,
                    "sheet": "active",
                    "reason": result["error_code"],
                }
            )
            continue
        field_records: dict[str, dict[str, Any]] = {}
        for index, source_name in enumerate(required_fields, start=1):
            semantic = map_field_semantics(source_name)
            # These aliases are scoped to the established V1 compatibility schema.
            canonical = semantic["canonical_name"]
            if source_name == "专业":
                canonical = "major"
            field_records[source_name] = {
                "source_name": source_name,
                "source_index": index,
                "canonical_name": canonical,
                "mapping_confidence": semantic["mapping_confidence"] if canonical else 0.0,
                "unique_count": len(
                    {str(row.get(source_name)).strip() for row in result["data"] if not _is_null(row.get(source_name))}
                ),
                "null_count": sum(_is_null(row.get(source_name)) for row in result["data"]),
            }
            if source_name == "专业":
                field_records[source_name]["mapping_confidence"] = 1.0
        semantic_fields, ambiguous = _semantic_fields(field_records)
        rows = []
        for row_number, row in enumerate(result["data"], start=2):
            rows.append({**row, "__row_number__": row_number})
        sources.append(
            {
                "file_id": record["file_id"],
                "file_name": file_name,
                "sheet": "active",
                "schema": {"row_count": len(rows)},
                "fields": field_records,
                "semantic_fields": semantic_fields,
                "ambiguous_semantics": ambiguous,
                "rows": rows,
            }
        )


def _source_key(value: dict[str, Any]) -> tuple[str, str]:
    return value["file_id"], value["sheet"]


def _normalized(value: Any) -> str:
    return "" if _is_null(value) else str(value).strip().casefold()


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
