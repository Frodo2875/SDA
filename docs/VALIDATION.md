# 真实环境验证与可靠性修复

验证日期：2026-10-19。所有材料均为合成数据，真实验收实际运行 FastAPI、Streamlit、Chromium、模型服务和 Tavily，没有替换模型回答、搜索结果或业务模块。

## 结果

| 验证 | 结果 |
| --- | --- |
| 全量 pytest | 852 passed，0 failed，0 skipped，约 135 秒 |
| 真实 API 场景 | 14 / 14 通过 |
| 冷启动浏览器专项 | 9 / 9 检查点通过 |
| 原完整浏览器流程 | 13 / 13 检查点通过 |
| 实际取消后恢复 | CANCELLED → COMPLETED，保留取消前已完成的 3 次工具结果 |
| 报告下载边界 | 未确认、只预览及取消后返回 409；确认后返回 200；重复确认不新增版本 |

机器可读摘要见 [validation-summary.json](validation-summary.json)。这些结果是有限样本与检查点的验收，不是生产准确率或并发性能指标。

## 本轮修复

1. **资格判断的材料发现**：奖学金计划显式允许文件发现与 Schema 检查，有独立准备调用预算，并保留业务依赖与安全策略。上传目录中的约定学生材料通过受控定位器读取。
2. **空检索纠正**：支持 Markdown 范围；当检索没有证据且下游分析未开始时，允许在既有轮数预算内纠正检索。已有有效证据和副作用操作不通过该路径重放。
3. **聚合来源**：`aggregate_table` 返回真实贡献单元格、操作、筛选条件、结果与证据 ID。前端显示计算方式并可打开原文。
4. **来源限制**：明确“不联网”或只使用本地材料时优先限制为 LOCAL，Web 工具在执行边界也被禁止。
5. **网页解析**：兼容不含名称的 HTML meta 标签，已实际读取 Python 官方 asyncio 文档。

## 真实样例

在全新隔离数据库中，通过浏览器上传材料，确认成绩与科研没有已保存 Schema，再提交：

> 请根据本地学生材料，判断S001是否符合一等奖学金资格，并说明依据。不要联网。

模型实际完成 `search_student → list_files → get_table_schema → inspect_excel → query_table → retrieve_document → evaluate_scholarship_eligibility`。用户没有提供文件 ID、Sheet、字段或工具顺序。

- S001：成绩 92、排名 2、论文 1，由 Python 规则工具判断符合条件。
- S002：不满足条件，明确列出不满足项。
- S003：缺科研记录，返回材料不足，不把缺失解释为 0。
- 重名：列出两位合成候选人，要求澄清。
- 库存聚合：两条传感器记录 17、5，工具返回 22，并提供条件与原始单元格。

## 复现

普通回归：

```bash
pytest -q
```

真实浏览器专项需另行安装 Playwright / Chromium，并准备本地真实 API 配置。请使用全新的隔离数据目录，避免同名文件或已有 Schema 影响冷启动检查：

```bash
python tests/browser/generate_fix_fixtures.py --output /tmp/sda-review-fixtures

python scripts/ui_acceptance_server.py \
  --env-file .env --data-root /tmp/sda-review-data --port 18000
```

另开终端启动前端：

```bash
BACKEND_BASE_URL=http://127.0.0.1:18000 \
  python -m streamlit run frontend/app.py --server.port 18501
```

再运行浏览器专项：

```bash
SDA_UI_URL=http://127.0.0.1:18501 \
SDA_API_URL=http://127.0.0.1:18000 \
SDA_FIXTURE_DIR=/tmp/sda-review-fixtures \
SDA_EVIDENCE_DIR=/tmp/sda-review-evidence \
  node tests/browser/sda_fixes_acceptance.cjs
```

完整交互回归脚本为 `tests/browser/sda_ui_acceptance.cjs`；可使用 `SDA_PLAYWRIGHT_MODULE` 指向已有 Playwright 安装。

## 数据与结论边界

公开仓库仅保留合成截图、代码、回归场景和验证摘要。完整模型响应、Trace、数据库、导出文件、认证配置及本机诊断报告不随此次更新发布。

证据质量检查仍可能返回 WARNING，它是来源与引用关系检查，不等同逐句事实验证。本轮没有将独立的充分性补检索模块接入全部研究流程，也未验证多用户隔离、并发压力、扫描 PDF/OCR 或进程崩溃后的自动恢复。
