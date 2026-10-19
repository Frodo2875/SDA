"""Generate synthetic input for sda_fixes_acceptance.cjs without overwriting files."""

import argparse
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook


def generate(output: Path) -> None:
    tables = [
        ("学生基本信息.xlsx", ["学号", "姓名", "专业", "年级", "班级"], [
            ["S001", "合成甲", "计算机", "研一", "1班"],
            ["S002", "合成乙", "计算机", "研一", "1班"],
            ["S003", "合成丙", "计算机", "研一", "1班"],
            ["S004", "测试重名", "计算机", "研一", "1班"],
            ["S005", "测试重名", "计算机", "研一", "1班"],
        ]),
        ("学生成绩.xlsx", ["学号", "平均成绩", "专业排名"], [
            ["S001", 92, 2], ["S002", 85, 5], ["S003", 94, 1],
        ]),
        ("科研成果.xlsx", ["学号", "论文数", "专利数", "竞赛数"], [
            ["S001", 1, 0, 2], ["S002", 0, 0, 1],
        ]),
        ("fix_stock.xlsx", ["编号", "类别", "数量"], [
            ["A1", "传感器", 17], ["A2", "电池", 23], ["A3", "传感器", 5],
        ]),
    ]
    documents: dict[str, bytes] = {}
    for name, headers, rows in tables:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        content = BytesIO()
        workbook.save(content)
        workbook.close()
        documents[name] = content.getvalue()
    documents["修复验收评审办法.md"] = (
        "# 奖学金评审办法\n\n"
        "一等奖学金：平均成绩不低于90分，专业排名在前3名，论文数不少于1篇。\n\n"
        "以上全部为合成实验数据。"
    ).encode("utf-8")
    if any((output / name).exists() for name in documents):
        raise FileExistsError("验收材料已经存在，请使用新的输出目录")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in documents.items():
        with (output / name).open("xb") as target:
            target.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output)
