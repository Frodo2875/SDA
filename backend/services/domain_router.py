"""Schema-validated domain routing with deterministic safe fallback.

The deterministic classifier is a weighted signal model over message structure,
schema context, file types, and requested Tool families. It is intentionally not
a single keyword if/else chain. An optional classifier may assist, but its output
must pass the same strict schema before it can affect Tool access.
"""

import json
import re
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.services.document_agent_core import GENERAL_CORE_TOOL_NAMES
from backend.services.student_domain_adapter import STUDENT_DOMAIN_TOOL_NAMES
from backend.services.trace_service import record_trace


DomainName = Literal["student", "general", "unknown"]
TaskType = Literal["query", "compare", "rule_check", "summarize", "workflow", "write"]
ReasonCode = str


class DomainRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: DomainName
    task_type: TaskType
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    effective_domain: Literal["student", "general"]

    @field_validator("effective_domain")
    @classmethod
    def validate_effective_domain(cls, value: str, info: Any) -> str:
        domain = info.data.get("domain")
        if domain == "student" and value != "student":
            raise ValueError("student route 必须使用 student effective domain")
        if domain in {"general", "unknown"} and value != "general":
            raise ValueError("general/unknown route 必须安全降级到 general")
        return value


class DomainRouterContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_fields: list[str] = Field(default_factory=list, max_length=200)
    file_types: list[str] = Field(default_factory=list, max_length=20)
    requested_tools: list[str] = Field(default_factory=list, max_length=50)


class DomainClassifier(Protocol):
    def classify(
        self, message: str, context: dict[str, Any]
    ) -> dict[str, Any] | str: ...


STUDENT_SCHEMA_FIELDS = frozenset({
    "student_id", "学号", "学生学号", "姓名", "学生姓名", "成绩", "平均成绩",
    "平均分", "专业排名", "论文数", "专利数", "竞赛数",
})
STUDENT_TERMS = {
    "学生": 2, "学号": 4, "成绩": 2, "专业排名": 3, "科研": 2,
    "奖学金": 4, "综合评价": 3, "综合分析": 2, "姓名": 2,
    "班级": 1, "年级": 1,
}
GENERAL_TERMS = {
    "项目": 2, "预算": 3, "合同": 2, "发票": 2, "库存": 2,
    "供应商": 2, "管理办法": 2, "审批": 2, "报告": 1, "文件": 1,
}
TASK_SIGNALS: dict[TaskType, tuple[str, ...]] = {
    "write": ("写入", "写进", "追加", "修改", "保存到"),
    "compare": ("比较", "对比", "差异", "高于", "低于"),
    "rule_check": ("判断", "是否符合", "是否需要", "条件", "资格", "审批"),
    "summarize": ("总结", "摘要", "概括", "归纳"),
    "workflow": ("然后", "并根据", "再根据", "依次", "批量"),
    "query": ("查询", "查找", "找出", "列出", "多少", "哪些"),
}


def route_domain(
    message: str,
    *,
    context: dict[str, Any] | None = None,
    classifier: DomainClassifier | Callable[[str, dict[str, Any]], Any] | None = None,
    session_id: str = "domain-router",
    trace: bool = True,
) -> dict[str, Any]:
    """Return a strict route and always trace domain + reason_code."""
    clean_message = str(message or "").strip()
    try:
        validated_context = DomainRouterContext.model_validate(context or {})
    except ValidationError:
        route = _fallback_route(clean_message, "INVALID_ROUTER_CONTEXT_GENERAL_FALLBACK")
        if trace:
            record_domain_route_trace(
                route, session_id, classifier_used=classifier is not None
            )
        return route.model_dump()

    if classifier is not None:
        try:
            raw = (
                classifier.classify(clean_message, validated_context.model_dump())
                if hasattr(classifier, "classify")
                else classifier(clean_message, validated_context.model_dump())
            )
            payload = json.loads(raw) if isinstance(raw, str) else raw
            route = DomainRoute.model_validate(payload)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            route = _fallback_route(clean_message, "INVALID_ROUTER_SCHEMA_GENERAL_FALLBACK")
        except Exception:
            route = _fallback_route(clean_message, "ROUTER_FAILURE_GENERAL_FALLBACK")
    else:
        route = _deterministic_route(clean_message, validated_context)
    if trace:
        record_domain_route_trace(
            route, session_id, classifier_used=classifier is not None
        )
    return route.model_dump()


def allowed_tool_names(route: dict[str, Any] | DomainRoute) -> frozenset[str]:
    model = route if isinstance(route, DomainRoute) else DomainRoute.model_validate(route)
    if model.effective_domain == "student":
        return GENERAL_CORE_TOOL_NAMES | STUDENT_DOMAIN_TOOL_NAMES
    return GENERAL_CORE_TOOL_NAMES


def _deterministic_route(
    message: str, context: DomainRouterContext
) -> DomainRoute:
    student_score = sum(weight for term, weight in STUDENT_TERMS.items() if term in message)
    general_score = sum(weight for term, weight in GENERAL_TERMS.items() if term in message)
    if re.search(r"(?<![A-Za-z0-9])S\d{2,}(?![A-Za-z0-9])", message, re.I):
        student_score += 6
    person_request = bool(re.search(
        r"(?:综合分析|查询一个不存在的|查找学生?)\s*[\u4e00-\u9fff]{2,4}[？?]?$"
        r"|查询\s*[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹彭鲁韦马方任袁柳唐薛雷贺倪汤罗郝安顾孟黄萧姚邵汪毛戴宋庞熊舒项董梁杜贾江郭梅林钟徐高夏蔡田樊胡霍万卢莫石崔龚程陆叶黎乔谭申刘邓曾][\u4e00-\u9fff]{1,2}[？?]?$",
        message,
    ))
    if person_request:
        student_score += 4
    schema_fields = {str(field).strip() for field in context.schema_fields}
    student_score += 5 * len(schema_fields & STUDENT_SCHEMA_FIELDS)
    generic_schema_count = len([
        field for field in schema_fields
        if field and field not in STUDENT_SCHEMA_FIELDS
    ])
    general_score += min(5, generic_schema_count)
    requested = set(context.requested_tools)
    student_score += 5 * len(requested & STUDENT_DOMAIN_TOOL_NAMES)
    general_score += 3 * len(requested & GENERAL_CORE_TOOL_NAMES)
    if context.file_types:
        general_score += 1

    task_type = _task_type(message)
    if student_score == 0 and general_score == 0:
        return DomainRoute(
            domain="unknown", task_type=task_type,
            reason_code="NO_DOMAIN_SIGNAL_GENERAL_FALLBACK",
            effective_domain="general",
        )
    if student_score and general_score and abs(student_score - general_score) <= 2:
        return DomainRoute(
            domain="unknown", task_type=task_type,
            reason_code="AMBIGUOUS_DOMAIN_SIGNALS_GENERAL_FALLBACK",
            effective_domain="general",
        )
    if student_score > general_score:
        reason = (
            "STUDENT_STRUCTURAL_ID_SIGNAL"
            if re.search(r"(?<![A-Za-z0-9])S\d{2,}(?![A-Za-z0-9])", message, re.I)
            else "STUDENT_SCHEMA_OR_TASK_SIGNAL"
        )
        return DomainRoute(
            domain="student", task_type=task_type,
            reason_code=reason, effective_domain="student",
        )
    return DomainRoute(
        domain="general", task_type=task_type,
        reason_code="GENERAL_SCHEMA_OR_TASK_SIGNAL", effective_domain="general",
    )


def _task_type(message: str) -> TaskType:
    scores = {
        task_type: sum(term in message for term in terms)
        for task_type, terms in TASK_SIGNALS.items()
    }
    if scores["write"]:
        return "write"
    # A compound request is a workflow only when it has an explicit sequencing
    # signal and at least one additional operation signal.
    if scores["workflow"] and sum(bool(value) for key, value in scores.items() if key != "workflow") >= 1:
        return "workflow"
    priority: tuple[TaskType, ...] = (
        "rule_check", "compare", "summarize", "query"
    )
    return max(priority, key=lambda item: (scores[item], -priority.index(item))) if any(scores.values()) else "query"


def _fallback_route(message: str, reason_code: ReasonCode) -> DomainRoute:
    return DomainRoute(
        domain="unknown",
        task_type=_task_type(message),
        reason_code=reason_code,
        effective_domain="general",
    )


def record_domain_route_trace(
    route: DomainRoute | dict[str, Any],
    session_id: str,
    *,
    classifier_used: bool = False,
) -> None:
    model = route if isinstance(route, DomainRoute) else DomainRoute.model_validate(route)
    try:
        record_trace(
            session_id=str(session_id or "domain-router")[:128],
            event_type="domain_route",
            tool_name="domain_router",
            arguments={"classifier_used": classifier_used},
            result={
                "ok": True,
                "status": model.domain,
                "message": model.reason_code,
            },
            result_status="success",
            metrics={
                "domain": model.domain,
                "effective_domain": model.effective_domain,
                "task_type": model.task_type,
                "reason_code": model.reason_code,
            },
        )
    except Exception:
        # Routing must remain available even when observability storage fails.
        pass


__all__ = [
    "DomainClassifier",
    "DomainRoute",
    "DomainRouterContext",
    "allowed_tool_names",
    "record_domain_route_trace",
    "route_domain",
]
