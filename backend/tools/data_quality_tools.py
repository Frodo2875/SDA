"""Deterministic cross-file conflict and data-quality inspection tools."""

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from backend import database
from backend.services.relation_service import (
    canonical_values,
    discover_relation_sources,
    resolve_entity,
    source_reference,
)
from backend.tool_models import (
    DuplicateRecordsArguments,
    EntityIdArguments,
    FileIdArguments,
    StudentIdArguments,
)
from backend.tools.excel_utils import failure, success
from backend.tools.table_tools import load_table_data


HIGH_MISSING_RATIO = 0.5
PHONE_PATTERN = re.compile(r"^(?:\+?\d[\d\s-]{5,19})$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def find_cross_file_conflicts(entity_id: str) -> dict[str, Any]:
    """Report cross-source disagreements without choosing an authoritative value."""
    try:
        arguments = EntityIdArguments.model_validate({"entity_id": entity_id})
    except ValidationError as exc:
        return failure("INVALID_ENTITY_ID", _validation_message(exc))

    resolution = resolve_entity(arguments.entity_id)
    if resolution["status"] == "not_found":
        return _result(
            status="not_found",
            data={"entity_id": arguments.entity_id, "conflicts": [], "sources": []},
            message="未查询到可可靠关联的实体记录",
        )
    if resolution["status"] == "ambiguous":
        return _result(
            status="ambiguous",
            data={
                "entity_id": arguments.entity_id,
                "conflicts": [],
                "sources": [source_reference(item) for item in resolution["records"]],
                "relation_issues": resolution["relation_issues"],
            },
            message="存在无法唯一确定的跨文档关联",
        )

    records = resolution["records"]
    conflicts = _collect_conflicts(arguments.entity_id, records)
    distinct_files = {record["file_id"] for record in records}
    if len(distinct_files) == 1:
        only_record = records[0]
        conflicts.append(
            _conflict(
                "single_source_only",
                "warning",
                arguments.entity_id,
                ["仅在一个文件/Sheet 中匹配"],
                [source_reference(only_record)],
                "确认其他材料是否缺少该学生记录；当前不能据此断言其他记录不存在。",
            )
        )
    status = "conflict" if conflicts else "found"
    return _result(
        status=status,
        data={
            "entity_id": arguments.entity_id,
            "conflicts": conflicts,
            "sources": [source_reference(item) for item in records],
        },
        message="发现跨文档冲突" if conflicts else "未发现跨文档冲突",
    )


def validate_document(file_id: str) -> dict[str, Any]:
    """Validate every discovered Sheet of one Excel without mutating the file."""
    try:
        arguments = FileIdArguments.model_validate({"file_id": file_id})
    except ValidationError as exc:
        return failure("INVALID_FILE_ID", _validation_message(exc))
    record = database.get_file_record_by_id(arguments.file_id)
    if record is None:
        return _result("not_found", {"file_id": arguments.file_id, "issues": []}, "文件不存在")
    if record["file_type"] != "excel":
        return _result(
            "invalid_data",
            {"file_id": arguments.file_id, "issues": []},
            "数据质量校验当前仅支持已解析 Excel",
            error_code="UNSUPPORTED_FILE_TYPE",
        )
    schemas = database.get_table_schema_records(arguments.file_id)
    if not schemas:
        return _result(
            "invalid_data",
            {"file_id": arguments.file_id, "issues": []},
            "文件没有可靠 Schema",
            error_code="SCHEMA_NOT_FOUND",
        )

    issues: list[dict[str, Any]] = []
    checked_sheets = 0
    total_rows = 0
    for schema in schemas:
        loaded = load_table_data(arguments.file_id, schema["sheet_name"])
        if not loaded["ok"]:
            issues.append(_quality_issue("table_read_error", "error", schema["sheet_name"], loaded["message"]))
            continue
        context = loaded["data"]
        checked_sheets += 1
        total_rows += len(context["rows"])
        issues.extend(_validate_sheet(context))

    if total_rows == 0:
        status = "no_record"
    elif any(item["severity"] == "error" for item in issues):
        status = "invalid_data"
    else:
        status = "found"
    return _result(
        status,
        {
            "file_id": arguments.file_id,
            "file_name": record["file_name"],
            "checked_sheets": checked_sheets,
            "row_count": total_rows,
            "issues": issues,
        },
        "文档数据质量校验完成",
    )


def find_duplicate_records(file_id: str, keys: list[str]) -> dict[str, Any]:
    """Find duplicate key tuples using only real fields from persisted Schema."""
    try:
        arguments = DuplicateRecordsArguments.model_validate(
            {"file_id": file_id, "keys": keys}
        )
    except ValidationError as exc:
        return failure("INVALID_DUPLICATE_ARGUMENTS", _validation_message(exc))
    record = database.get_file_record_by_id(arguments.file_id)
    if record is None:
        return _result("not_found", {"duplicates": []}, "文件不存在")
    schemas = database.get_table_schema_records(arguments.file_id)
    if not schemas:
        return _result(
            "invalid_data", {"duplicates": []}, "文件没有可靠 Schema", "SCHEMA_NOT_FOUND"
        )

    duplicates: list[dict[str, Any]] = []
    compatible_sheet_found = False
    unavailable: dict[str, list[str]] = {}
    for schema in schemas:
        available = {field["source_name"] for field in schema["fields"]}
        missing = [key for key in arguments.keys if key not in available]
        if missing:
            unavailable[schema["sheet_name"]] = missing
            continue
        compatible_sheet_found = True
        loaded = load_table_data(arguments.file_id, schema["sheet_name"])
        if not loaded["ok"]:
            return loaded
        groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
        for row in loaded["data"]["rows"]:
            key = tuple(_hashable(row.get(field)) for field in arguments.keys)
            groups[key].append(row["__row_number__"])
        for values, row_numbers in groups.items():
            if len(row_numbers) > 1:
                duplicates.append(
                    {
                        "sheet": schema["sheet_name"],
                        "keys": dict(zip(arguments.keys, values, strict=True)),
                        "count": len(row_numbers),
                        "row_numbers": row_numbers,
                    }
                )
    if not compatible_sheet_found:
        return _result(
            "invalid_data",
            {"duplicates": [], "missing_fields": unavailable},
            "指定 keys 不存在于任何同一 Sheet",
            "FIELD_NOT_FOUND",
        )
    status = "conflict" if duplicates else "no_record"
    return _result(
        status,
        {
            "file_id": arguments.file_id,
            "file_name": record["file_name"],
            "keys": arguments.keys,
            "duplicates": duplicates,
        },
        "发现重复记录" if duplicates else "未查询到重复记录",
    )


def validate_student_data(student_id: str) -> dict[str, Any]:
    """Validate one student across reliably linked structured documents."""
    try:
        arguments = StudentIdArguments.model_validate({"student_id": student_id})
    except ValidationError as exc:
        return failure("INVALID_STUDENT_ID", _validation_message(exc))
    resolution = resolve_entity(arguments.student_id)
    if resolution["status"] == "not_found":
        return _result(
            "not_found", {"student_id": arguments.student_id, "issues": []}, "未查询到学生记录"
        )
    if resolution["status"] == "ambiguous":
        return _result(
            "ambiguous",
            {
                "student_id": arguments.student_id,
                "issues": [
                    _quality_issue("unreliable_relation", "error", item["file_name"], item["reason"])
                    for item in resolution["relation_issues"]
                ],
            },
            "学生跨文件关联不唯一",
        )

    conflicts = _collect_conflicts(arguments.student_id, resolution["records"])
    issues = [
        _quality_issue(item["conflict_type"], item["severity"], "跨文件", item["suggested_action"])
        for item in conflicts
    ]
    counts = Counter(item["file_id"] for item in resolution["records"])
    if len(set(counts.values())) > 1:
        issues.append(
            _quality_issue(
                "cross_file_count_mismatch",
                "warning",
                "跨文件",
                "同一学生在关联来源中的记录数量不一致，请核对重复或缺失记录。",
            )
        )
    for record in resolution["records"]:
        values = canonical_values(record)
        for key in ("phone", "email"):
            if key in values and not _valid_contact(key, values[key]):
                issues.append(
                    _quality_issue(
                        "invalid_contact_format",
                        "error",
                        record["file_name"],
                        f"{key} 格式异常（Sheet: {record['sheet']}）。",
                    )
                )
    status = "conflict" if conflicts else (
        "invalid_data" if any(item["severity"] == "error" for item in issues) else "found"
    )
    return _result(
        status,
        {
            "student_id": arguments.student_id,
            "issues": issues,
            "sources": [source_reference(item) for item in resolution["records"]],
        },
        "学生跨文档数据校验完成",
    )


def _collect_conflicts(entity_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    by_field: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        reference = source_reference(record)
        for field, value in canonical_values(record).items():
            normalized = _normalized(value)
            by_field[field].setdefault(normalized, {"value": value, "sources": []})["sources"].append(reference)

    dedicated = {"name": "same_id_different_name", "college": "same_id_different_college"}
    for field, values in by_field.items():
        if field == "student_id" or len(values) <= 1:
            continue
        conflict_type = dedicated.get(field, "same_field_different_value")
        severity = "error" if field in {"name", "college"} else "warning"
        conflicts.append(
            _conflict(
                conflict_type,
                severity,
                entity_id,
                [{"field": field, "value": item["value"]} for item in values.values()],
                [source for item in values.values() for source in item["sources"]],
                f"核对各来源中的 {field}；系统不会自动选择任一值作为事实。",
            )
        )

    # Preserve exact same-name fields which are not semantically mapped. This is
    # deliberately stricter than fuzzy label matching: similarly named columns are
    # not joined and the LLM cannot introduce a comparison key.
    raw_fields: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    raw_source_count: Counter[str] = Counter()
    for record in records:
        reference = source_reference(record)
        for source_name, field in record["fields"].items():
            if field.get("canonical_name") or source_name.startswith("__"):
                continue
            value = record["row"].get(source_name)
            if _is_null(value):
                continue
            raw_source_count[source_name] += 1
            normalized = _normalized(value)
            raw_fields[source_name].setdefault(
                normalized, {"value": value, "sources": []}
            )["sources"].append(reference)
    for source_name, values in raw_fields.items():
        if raw_source_count[source_name] < 2 or len(values) <= 1:
            continue
        conflicts.append(
            _conflict(
                "same_field_different_value",
                "warning",
                entity_id,
                [{"field": source_name, "value": item["value"]} for item in values.values()],
                [source for item in values.values() for source in item["sources"]],
                f"核对同名字段 {source_name} 的来源；系统不会自动选择任一值。",
            )
        )

    dated_records = [item for item in records if "date" in canonical_values(item)]
    dated_snapshots = {
        tuple(
            sorted(
                (key, _normalized(value))
                for key, value in canonical_values(item).items()
                if key != "date"
            )
        )
        for item in dated_records
    }
    if len(dated_records) > 1 and len(dated_snapshots) > 1:
        conflicts.append(
            _conflict(
                "time_version_conflict",
                "warning",
                entity_id,
                [canonical_values(item).get("date") for item in dated_records],
                [source_reference(item) for item in dated_records],
                "按明确时间和业务版本规则人工确认，不自动选择最新值。",
            )
        )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[_source_key(record)].append(record)
    for same_source in groups.values():
        if len(same_source) <= 1:
            continue
        references = [source_reference(item) for item in same_source]
        conflicts.append(
            _conflict(
                "duplicate_record", "error", entity_id, [len(same_source)], references,
                "检查同一来源中的重复实体记录并人工确认保留策略。",
            )
        )
    return conflicts


def _validate_sheet(context: dict[str, Any]) -> list[dict[str, Any]]:
    schema = context["schema"]
    fields = list(context["fields"].values())
    rows = context["rows"]
    sheet = schema["sheet_name"]
    issues: list[dict[str, Any]] = []
    canonical = {field.get("canonical_name") for field in fields}
    is_student_table = bool(canonical.intersection({"name", "college", "grade", "class", "student_id"}))
    if is_student_table and "student_id" not in canonical:
        issues.append(_quality_issue("missing_required_field", "error", sheet, "学生类表格缺少可靠学号字段。"))
    for field in fields:
        name = field["source_name"]
        null_count = int(field.get("null_count") or 0)
        ratio = float(field.get("null_ratio") or 0)
        if null_count:
            issues.append(_quality_issue("null_value", "warning", sheet, f"字段 {name} 有 {null_count} 个空值。"))
        if ratio >= HIGH_MISSING_RATIO and rows:
            issues.append(_quality_issue("high_missing_ratio", "error", sheet, f"字段 {name} 空值比例为 {ratio:.1%}。"))
        if field.get("inferred_type") in {"mixed", "other"}:
            issues.append(_quality_issue("type_anomaly", "warning", sheet, f"字段 {name} 类型不一致或无法识别。"))
        semantic = field.get("canonical_name")
        values = [row.get(name) for row in rows if not _is_null(row.get(name))]
        if semantic == "score":
            for value in values:
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
                    issues.append(_quality_issue("numeric_range_anomaly", "error", sheet, f"字段 {name} 存在非数值或超出 0~100 的成绩。"))
                    break
        if semantic in {"phone", "email"} and any(not _valid_contact(semantic, value) for value in values):
            issues.append(_quality_issue("invalid_contact_format", "error", sheet, f"字段 {name} 存在格式异常。"))

    for semantic, issue_type in (("student_id", "duplicate_student_id"), ("name", "duplicate_name")):
        field = next((item for item in fields if item.get("canonical_name") == semantic), None)
        if field is None:
            continue
        counts = Counter(_normalized(row.get(field["source_name"])) for row in rows if not _is_null(row.get(field["source_name"])))
        duplicated = [value for value, count in counts.items() if count > 1]
        if duplicated:
            severity = "error" if semantic == "student_id" else "warning"
            issues.append(_quality_issue(issue_type, severity, sheet, f"发现 {len(duplicated)} 组重复{field['source_name']}。"))
    return issues


def _conflict(conflict_type: str, severity: str, entity: str, values: list[Any], sources: list[dict[str, Any]], suggested_action: str) -> dict[str, Any]:
    return {
        "conflict_type": conflict_type,
        "severity": severity,
        "entity": entity,
        "values": values,
        "sources": sources,
        "suggested_action": suggested_action,
    }


def _quality_issue(issue_type: str, severity: str, source: str, message: str) -> dict[str, Any]:
    return {"issue_type": issue_type, "severity": severity, "source": source, "message": message}


def _result(status: str, data: dict[str, Any], message: str, error_code: str | None = None) -> dict[str, Any]:
    payload = {"status": status, **data}
    return failure(error_code, message, payload) if error_code else success(payload, message)


def _source_key(record: dict[str, Any]) -> tuple[str, str]:
    return record["file_id"], record["sheet"]


def _normalized(value: Any) -> str:
    return "" if _is_null(value) else str(value).strip().casefold()


def _hashable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _valid_contact(kind: str, value: Any) -> bool:
    text = str(value).strip()
    return bool((PHONE_PATTERN if kind == "phone" else EMAIL_PATTERN).fullmatch(text))


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first["loc"])
    return f"参数 {location} 无效：{first['msg']}"
