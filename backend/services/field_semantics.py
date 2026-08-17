"""Conservative deterministic field-name semantic mapping."""

import re
from typing import Any


HIGH_CONFIDENCE_ALIASES = {
    "student_id": {"学号", "学生学号", "studentid"},
    "name": {"姓名", "学生姓名", "name"},
    "college": {"学院", "所属院系"},
    "phone": {"联系电话", "移动电话", "手机号", "手机"},
    "email": {"邮箱", "email"},
}

SENSITIVE_CANONICAL_NAMES = {"student_id", "name", "phone", "email"}
UNCERTAIN_NAMES = {"编号", "代码", "类型", "备注"}


def map_field_semantics(source_name: str) -> dict[str, Any]:
    """Map only explicit aliases; ambiguous generic fields remain unknown."""
    normalized = _normalize(source_name)
    if normalized in {_normalize(value) for value in UNCERTAIN_NAMES}:
        return _unknown_mapping(source_name)

    for canonical_name, aliases in HIGH_CONFIDENCE_ALIASES.items():
        if normalized in {_normalize(alias) for alias in aliases}:
            return {
                "source_name": source_name,
                "canonical_name": canonical_name,
                "semantic_type": canonical_name,
                "mapping_confidence": 0.99,
                "mapping_source": "deterministic_rule",
                "sensitive": canonical_name in SENSITIVE_CANONICAL_NAMES,
            }

    auxiliary = _auxiliary_mapping(source_name, normalized)
    return auxiliary if auxiliary is not None else _unknown_mapping(source_name)


def _auxiliary_mapping(
    source_name: str,
    normalized: str,
) -> dict[str, Any] | None:
    auxiliary_rules = (
        ({"身份证", "身份证号", "idcard", "nationalid"}, "id_card", True),
        ({"地址", "家庭地址", "住址", "address"}, "address", True),
        ({"护照", "护照号", "passport"}, "passport", True),
        ({"银行卡", "银行账号", "bankaccount"}, "bank_account", True),
    )
    for aliases, canonical_name, sensitive in auxiliary_rules:
        if normalized in {_normalize(alias) for alias in aliases}:
            return {
                "source_name": source_name,
                "canonical_name": canonical_name,
                "semantic_type": canonical_name,
                "mapping_confidence": 0.96,
                "mapping_source": "deterministic_rule",
                "sensitive": sensitive,
            }
    if any(token in normalized for token in ("日期", "时间", "date", "time")):
        return {
            "source_name": source_name,
            "canonical_name": "date",
            "semantic_type": "date",
            "mapping_confidence": 0.82,
            "mapping_source": "deterministic_rule",
            "sensitive": False,
        }
    if any(token in normalized for token in ("成绩", "分数", "score", "平均分")):
        return {
            "source_name": source_name,
            "canonical_name": "score",
            "semantic_type": "score",
            "mapping_confidence": 0.84,
            "mapping_source": "deterministic_rule",
            "sensitive": False,
        }
    return None


def _unknown_mapping(source_name: str) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "canonical_name": None,
        "semantic_type": "unknown",
        "mapping_confidence": 0.0,
        "mapping_source": "unmapped",
        "sensitive": False,
    }


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-（）()]+", "", str(value)).lower()
