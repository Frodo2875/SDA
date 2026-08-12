"""Tools for reading student identity, score, and research records."""

from typing import Any

from backend.tools.excel_utils import failure, normalize_student_id, read_excel_rows, success


STUDENT_FILE = "学生基本信息.xlsx"
SCORE_FILE = "学生成绩.xlsx"
RESEARCH_FILE = "科研成果.xlsx"

STUDENT_FIELDS = ["学号", "姓名", "专业", "年级", "班级"]
SCORE_FIELDS = ["学号", "数学", "英语", "专业课", "平均分", "专业排名"]
RESEARCH_FIELDS = ["学号", "论文数", "专利数", "竞赛数"]


def _student_data(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "student_id": normalize_student_id(row.get("学号")),
        "name": row.get("姓名"),
        "major": row.get("专业"),
        "grade": row.get("年级"),
        "class_name": row.get("班级"),
    }


def _find_student(student_id: str) -> dict[str, Any]:
    result = read_excel_rows(STUDENT_FILE, STUDENT_FIELDS)
    if not result["ok"]:
        return result

    normalized_id = normalize_student_id(student_id)
    for row in result["data"]:
        if normalize_student_id(row.get("学号")) == normalized_id:
            return success(_student_data(row), "找到学生基本信息")
    return failure(
        "STUDENT_NOT_FOUND",
        f"未找到学号为 {normalized_id} 的学生",
        {"status": "not_found"},
    )


def search_student(name_or_id: str) -> dict[str, Any]:
    """Search by exact student ID or exact name without guessing duplicates."""
    query = normalize_student_id(name_or_id)
    if not query:
        return failure("INVALID_QUERY", "姓名或学号不能为空")

    result = read_excel_rows(STUDENT_FILE, STUDENT_FIELDS)
    if not result["ok"]:
        return result

    rows = result["data"]
    id_matches = [row for row in rows if normalize_student_id(row.get("学号")) == query]
    matches = id_matches or [row for row in rows if str(row.get("姓名") or "").strip() == query]

    if not matches:
        return success({"status": "not_found"}, "未找到匹配学生")
    if len(matches) > 1:
        return success(
            {"status": "ambiguous", "candidates": [_student_data(row) for row in matches]},
            "存在重名学生，请使用学号确认",
        )
    return success(
        {"status": "found", "student": _student_data(matches[0])},
        "找到唯一匹配学生",
    )


def get_student_info(student_id: str) -> dict[str, Any]:
    """Return basic information for an exact student ID."""
    normalized_id = normalize_student_id(student_id)
    if not normalized_id:
        return failure("INVALID_STUDENT_ID", "学号不能为空")
    return _find_student(normalized_id)


def _number(value: Any, field: str) -> int | float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"字段 {field} 不是有效数字")
    return value


def get_student_scores(student_id: str) -> dict[str, Any]:
    """Return subject scores and a Python-calculated average for one student."""
    normalized_id = normalize_student_id(student_id)
    if not normalized_id:
        return failure("INVALID_STUDENT_ID", "学号不能为空")

    student_result = _find_student(normalized_id)
    if not student_result["ok"]:
        return student_result

    result = read_excel_rows(SCORE_FILE, SCORE_FIELDS)
    if not result["ok"]:
        return result
    row = next(
        (row for row in result["data"] if normalize_student_id(row.get("学号")) == normalized_id),
        None,
    )
    if row is None:
        return success(
            {"status": "no_record", "student_id": normalized_id},
            "该学生存在，但没有成绩记录",
        )

    try:
        math = _number(row.get("数学"), "数学")
        english = _number(row.get("英语"), "英语")
        major_course = _number(row.get("专业课"), "专业课")
        rank = _number(row.get("专业排名"), "专业排名")
        average_score = round((math + english + major_course) / 3, 2)
    except ValueError as exc:
        return failure("INVALID_SCORE_DATA", str(exc))

    return success(
        {
            "status": "found",
            "student_id": normalized_id,
            "scores": {"数学": math, "英语": english, "专业课": major_course},
            "average_score": average_score,
            "rank": rank,
        },
        "成绩记录读取成功",
    )


def get_student_research(student_id: str) -> dict[str, Any]:
    """Return research records without treating a missing row as zero output."""
    normalized_id = normalize_student_id(student_id)
    if not normalized_id:
        return failure("INVALID_STUDENT_ID", "学号不能为空")

    student_result = _find_student(normalized_id)
    if not student_result["ok"]:
        return student_result

    result = read_excel_rows(RESEARCH_FILE, RESEARCH_FIELDS)
    if not result["ok"]:
        return result
    row = next(
        (row for row in result["data"] if normalize_student_id(row.get("学号")) == normalized_id),
        None,
    )
    if row is None:
        return success(
            {"status": "no_record", "student_id": normalized_id},
            "该学生存在，但没有科研记录；不能据此认定科研成果为零",
        )

    try:
        research = {
            "论文数": _number(row.get("论文数"), "论文数"),
            "专利数": _number(row.get("专利数"), "专利数"),
            "竞赛数": _number(row.get("竞赛数"), "竞赛数"),
        }
    except ValueError as exc:
        return failure("INVALID_RESEARCH_DATA", str(exc))

    return success(
        {"status": "found", "student_id": normalized_id, "research": research},
        "科研记录读取成功",
    )
