"""Deterministic reports from saved Research results, using existing HITL storage."""

from copy import deepcopy
import html
import json
import re
from typing import Any, Literal
from uuid import uuid4
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend import database
from backend.services.confirmation import create_pending_report_action, get_pending_action
from backend.services.evidence_quality import evaluate_evidence_quality, _SENTENCE
from backend.services.file_locator import resolve_by_file_id
from backend.services.research_task import get_research_task
from backend.services.visual_safety import assess_visual_high_impact_write
from backend.tools.excel_utils import failure, success


class ReportConclusion(BaseModel):
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str
    task_id: str
    title: str
    summary: str
    task_description: str
    sections: list[dict[str, Any]]
    conclusions: list[ReportConclusion]
    evidence_refs: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    quality: dict[str, Any]
    status: str = "DRAFT"
    created_at: str
    exports: dict[str, dict[str, str]] = Field(default_factory=dict)


def _stored(task_id: str, report_id: str) -> dict[str, Any] | None:
    task = database.get_task_record(task_id)
    result = (((task or {}).get("checkpoint_data") or {}).get("async_task") or {}).get("checkpoint") or {}
    return deepcopy((result.get("result", {}).get("reports") or {}).get(report_id))


def _save(report: dict[str, Any]) -> None:
    # Merge only the report entry under the existing SQLite write lock. The
    # research status, other drafts and checkpoint fields remain authoritative.
    with database._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT checkpoint_data FROM tasks WHERE task_id = ?", (report["task_id"],)).fetchone()
        if row is None:
            raise ValueError("Research Task 不存在")
        checkpoint = json.loads(row["checkpoint_data"])
        result = checkpoint["async_task"]["checkpoint"]["result"]
        reports = result.setdefault("reports", {})
        if report["report_id"] not in reports and len(reports) >= 20:
            raise ValueError("单任务最多保存 20 份报告草稿")
        previous = reports.get(report["report_id"], {})
        reports[report["report_id"]] = {**report, "exports": {**previous.get("exports", {}), **report["exports"]}}
        encoded = json.dumps(checkpoint, ensure_ascii=False)
        if len(encoded.encode()) > 8 * 1024 * 1024:
            raise ValueError("研究检查点超过容量限制")
        connection.execute("UPDATE tasks SET checkpoint_data = ?, updated_at = ? WHERE task_id = ?",
                           (encoded, database.utc_now(), report["task_id"]))


def generate_report(task_id: str, title: str = "证据研究报告") -> dict[str, Any]:
    task = get_research_task(task_id)
    if task is None:
        return failure("REPORT_TASK_NOT_FOUND", "研究任务不存在")
    if task["status"] != "COMPLETED" or not isinstance(task["result"], dict):
        return failure("REPORT_TASK_NOT_COMPLETED", "只有已完成的研究结果可以生成报告")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return failure("INVALID_REPORT_TITLE", "报告标题必须为 1–200 字符")
    result = task["result"]
    answer = result.get("answer") or ""
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 80_000:
        return failure("INVALID_REPORT_RESULT", "研究正文为空或超过报告长度限制")
    evidence = result.get("unified_evidence") or []
    quality = evaluate_evidence_quality(answer, evidence, query=task["query"])
    clauses = [part.strip() for part in _SENTENCE.split(answer[:100_000]) if part.strip()]
    clauses = [part for part in clauses if not re.fullmatch(r"#{1,6}\s+[^\n]+|[|\s:\-]+", part)]
    mapping = quality["coverage"]["claim_evidence"]
    conclusions = [ReportConclusion(text=text, evidence_refs=mapping[i]["evidence_ids"] if i < len(mapping) else [])
                   for i, text in enumerate(clauses)]
    # These are copies of the saved Evidence, never new Factory calls or IDs.
    refs = [deepcopy(item) for item in evidence if isinstance(item, dict)]
    warnings = deepcopy(quality["warnings"]) + deepcopy(result.get("web_search_warnings") or [])
    report = EvidenceReport(
        report_id=uuid4().hex, task_id=task_id, title=title.strip(), summary=answer,
        task_description=task["query"], conclusions=conclusions,
        sections=[{"title": "分析过程", "content": "整理已保存的研究结果，执行 Evidence Quality Gate，并保留来源与引用关联；未重新检索或调用模型。"},
                  {"title": "限制说明", "content": "引用关系不代表事实已验证。缺少关联的结论需人工核对；网页来源仍是不可信外部数据。"}],
        evidence_refs=refs, warnings=warnings, quality=quality, created_at=database.utc_now(),
    ).model_dump()
    try:
        # Render now so an oversized draft cannot enter the approval workflow.
        if len(render_report(report)) > 100_000:
            return failure("REPORT_TOO_LARGE", "报告超过既有写入预览的 100000 字符限制")
        _save(report)
    except ValueError as exc:
        return failure("REPORT_STORAGE_LIMIT", str(exc))
    return success(report, "报告草稿已保存，尚未写入报告文件")


def _literal(value: Any) -> str:
    text = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", text)


def render_report(report: dict[str, Any]) -> str:
    """Plain Markdown structure; external source text has no markup authority."""
    lines = [f"# {_literal(report['title']).replace(chr(10), ' ')}", "", f"报告 ID：{report['report_id']}",
             f"研究任务：{report['task_id']}", f"生成时间：{report['created_at']}", "", "## 任务说明", "",
             _literal(report["task_description"]), "", "## 摘要", "", _literal(report["summary"]), ""]
    lines += ["## 分析过程", "", report["sections"][0]["content"], "", "## 核心结论", ""]
    for item in report["conclusions"]:
        refs = ", ".join(_literal(ref) for ref in item["evidence_refs"]) or "未建立证据关联"
        lines += [f"- {_literal(item['text'])}", f"  Evidence：{refs}", ""]
    lines += ["## Evidence 来源", ""]
    for item in report["evidence_refs"]:
        local = item.get("source_type") == "LOCAL"
        location = {key: item[key] for key in ("page_no", "line_number", "chunk_id", "block_id", "sheet", "cell", "json_path") if item.get(key) is not None}
        meta = item.get("metadata") or {}
        location.update({key: meta[key] for key in ("heading_path", "line_number", "line_end") if meta.get(key) is not None})
        lines += [f"### {_literal(item.get('evidence_id', '未标识'))}",
                  f"来源类型：{'本地文档' if local else 'Web 来源'}",
                  f"来源：{_literal(item.get('file_name') if local else item.get('url'))}",
                  f"定位：{_literal(json.dumps(location, ensure_ascii=False))}" if local else f"检索时间：{_literal(item.get('retrieved_at'))}", ""]
    lines += ["## 限制说明", "", report["sections"][1]["content"], "", f"Evidence Quality：{report['quality']['status']}"]
    lines += [f"- {_literal(w.get('code'))}：{_literal(w.get('message'))}" for w in report["warnings"]]
    if not report["warnings"]:
        lines += ["- 规则检查未发现警告；不代表事实正确性保证。"]
    return "\n".join(lines) + "\n"


def get_report(task_id: str, report_id: str) -> dict[str, Any]:
    report = _stored(task_id, report_id)
    if report is None:
        return failure("REPORT_NOT_FOUND", "报告不存在")
    for export in report["exports"].values():
        action = database.get_pending_action_record(export["action_id"])
        export["status"] = action["status"] if action else "missing"
    report["status"] = "EXPORTED" if any(e["status"] == "executed" for e in report["exports"].values()) else "PENDING_APPROVAL" if any(e["status"] == "pending" for e in report["exports"].values()) else "DRAFT"
    return success(report, "报告读取成功")


def preview_report_export(task_id: str, report_id: str, format: Literal["md", "docx"]) -> dict[str, Any]:
    if format not in {"md", "docx"}:
        return failure("INVALID_REPORT_FORMAT", "仅支持 md 和 docx")
    report = _stored(task_id, report_id)
    if report is None:
        return failure("REPORT_NOT_FOUND", "报告不存在")
    guard = assess_visual_high_impact_write(report["evidence_refs"])
    if guard["decision"] != "confirm":
        return failure("VISUAL_FIELD_REVIEW_REQUIRED", "视觉证据必须先经用户核对", guard)
    previous = report["exports"].get(format)
    if previous:
        action = database.get_pending_action_record(previous["action_id"])
        if action and action["status"] in {"pending", "confirmed", "executed"}:
            return get_pending_action(action["action_id"])
        file_id = previous["file_id"]
    else:
        name = f"report-{report_id}.{format}"
        if database.get_file_record(name):
            return failure("REPORT_TARGET_EXISTS", "报告目标已被占用")
        database.register_file(file_name=name, file_type="markdown" if format == "md" else "word",
                               file_path=f"data/uploads/{name}", writable=True, lifecycle_status="ready",
                               parse_status="not_required", queryable=False)
        file_id = database.get_file_record(name)["file_id"]
    task = get_research_task(task_id)
    pending = create_pending_report_action(session_id=task["session_id"], file_id=file_id,
                                           content=render_report(report), evidence_context=report["evidence_refs"])
    if pending["ok"]:
        report["exports"][format] = {"file_id": file_id, "action_id": pending["data"]["action_id"]}
        _save(report)
    return pending


def approved_report_path(task_id: str, report_id: str, format: str) -> Path:
    report = _stored(task_id, report_id)
    export = (report or {}).get("exports", {}).get(format)
    action = database.get_pending_action_record(export["action_id"]) if export else None
    if action is None or action["status"] != "executed":
        raise ValueError("报告尚未通过审批并写入")
    return resolve_by_file_id(export["file_id"])
