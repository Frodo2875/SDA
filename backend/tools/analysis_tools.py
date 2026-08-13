"""Deterministic Python tools for comparing student records."""

from itertools import combinations
from typing import Any

from backend.tools.excel_utils import (
    failure,
    normalize_student_id,
    read_excel_rows,
    success,
)
from backend.tools.student_tools import (
    SCORE_FIELDS,
    SCORE_FILE,
    STUDENT_FIELDS,
    STUDENT_FILE,
    get_student_info,
    get_student_research,
    get_student_scores,
)


METRICS = ("average_score", "rank", "paper_count", "patent_count", "competition_count")


def get_top_three_students() -> dict[str, Any]:
    """Return the three highest Python-calculated averages across score records."""
    student_result = read_excel_rows(STUDENT_FILE, STUDENT_FIELDS)
    if not student_result["ok"]:
        return student_result
    score_result = read_excel_rows(SCORE_FILE, SCORE_FIELDS)
    if not score_result["ok"]:
        return score_result

    students_by_id = {
        normalize_student_id(row.get("学号")): row for row in student_result["data"]
    }
    ranked_students = []
    for row in score_result["data"]:
        student_id = normalize_student_id(row.get("学号"))
        student = students_by_id.get(student_id)
        if student is None:
            return failure(
                "STUDENT_DATA_MISMATCH",
                f"成绩记录中的学号 {student_id} 缺少学生基本信息",
            )
        values = (row.get("数学"), row.get("英语"), row.get("专业课"))
        if any(
            value is None or isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            return failure("INVALID_SCORE_DATA", f"学号 {student_id} 的成绩不是有效数字")
        average_score = round(sum(values) / len(values), 2)
        ranked_students.append(
            {
                "student_id": student_id,
                "name": student.get("姓名"),
                "major": student.get("专业"),
                "average_score": average_score,
                "scores": {
                    "数学": values[0],
                    "英语": values[1],
                    "专业课": values[2],
                },
                "professional_rank": row.get("专业排名"),
            }
        )

    ranked_students.sort(key=lambda item: (-item["average_score"], item["student_id"]))
    top_students = []
    for position, student in enumerate(ranked_students[:3], start=1):
        top_students.append({"position": position, **student})
    return success(
        {"ranking_basis": "三科成绩的 Python 计算平均分（降序）", "students": top_students},
        "已计算平均成绩最高的三名学生",
    )


def _difference(left: Any, right: Any) -> int | float | None:
    if left is None or right is None:
        return None
    return round(left - right, 2)


def compare_students(student_ids: list[str]) -> dict[str, Any]:
    """Compare two or more students and calculate every pairwise difference."""
    if not isinstance(student_ids, list) or len(student_ids) < 2:
        return failure("INVALID_STUDENT_IDS", "至少需要提供 2 个学生学号")

    normalized_ids = [normalize_student_id(student_id) for student_id in student_ids]
    if any(not student_id for student_id in normalized_ids):
        return failure("INVALID_STUDENT_IDS", "学生学号不能为空")
    if len(set(normalized_ids)) != len(normalized_ids):
        return failure("DUPLICATE_STUDENT_IDS", "比较列表中不能包含重复学号")

    students = []
    for student_id in normalized_ids:
        info_result = get_student_info(student_id)
        if not info_result["ok"]:
            return failure(
                info_result["error_code"] or "STUDENT_DATA_ERROR",
                info_result["message"],
                {"student_id": student_id},
            )

        score_result = get_student_scores(student_id)
        if not score_result["ok"]:
            return score_result
        research_result = get_student_research(student_id)
        if not research_result["ok"]:
            return research_result

        score_data = score_result["data"]
        research_data = research_result["data"]
        scores_found = score_data["status"] == "found"
        research_found = research_data["status"] == "found"
        research = research_data.get("research", {})
        students.append(
            {
                "student_id": student_id,
                "name": info_result["data"]["name"],
                "average_score": score_data.get("average_score") if scores_found else None,
                "rank": score_data.get("rank") if scores_found else None,
                "paper_count": research.get("论文数") if research_found else None,
                "patent_count": research.get("专利数") if research_found else None,
                "competition_count": research.get("竞赛数") if research_found else None,
                "score_status": score_data["status"],
                "research_status": research_data["status"],
            }
        )

    differences = []
    for left, right in combinations(students, 2):
        differences.append(
            {
                "left_student_id": left["student_id"],
                "right_student_id": right["student_id"],
                "calculation": "left_minus_right",
                "values": {
                    metric: _difference(left[metric], right[metric]) for metric in METRICS
                },
            }
        )

    return success(
        {"students": students, "differences": differences},
        f"已完成 {len(students)} 名学生的比较",
    )
