# V4 Final Acceptance Report

验收日期：2026-08-31  
结论：**PARTIAL**（固定自动验收通过；真实 OCR/手写与浏览器人工验收未完成）

## 1. Baseline

| 项目 | 结果 |
| --- | --- |
| V3 stable tag | `v3-final-stable` |
| V3 stable commit | `16bee95fb699c76e5200467d147161e3c81ec262` |
| V4 branch | `feature/v4-development`（沿用 V4.0 已记录基线） |
| Final commit | 未读取；本阶段遵循“不要执行 Git”，由人工提交时填写 |
| Evaluation mode | `offline_fixed_adapters` |

## 2. Implemented Scope

V4.1–V4.10 已增量交付：正式图片 Document 与统一 Router、图片/扫描/混合 PDF OCR、手写
region、VisualBlock/KIE、视觉表格与安全降级、Evidence 3.0 定位、General Core + Student
Adapter、Domain Router、Controlled Agentic Retrieval，以及 Visual Safety/Trace。V4.11 只新增
Evaluation/Demo/Audit 工程资产，没有改动业务模块。

Out of scope 保持：复杂自然图像、人脸识别、高级图表推理、生产级 OCR/手写模型训练、复杂
合并/无边框手绘表格强制结构化、大规模 UI 重构和 Chain-of-Thought 持久化。

## 3. Regression and Original Evaluation

| 范围 | 本次真实结果 |
| --- | --- |
| V1 regression command | `68 passed, 1 deselected in 3.65s` |
| V2 Evaluation | `4 passed in 1.25s` |
| V2 fixed Agent observations | tool selection `1.0`; average tool calls `2.6666666666666665`; average duration `51.73757392913103 ms`; manual review `6`; unsupported facts `0` |
| V3 Final Evaluation | PASS；37 samples；average `28.432 ms`；P95 `57 ms` |
| Full pytest after V4.11 | `410 passed in 31.34s` |

V1/V2/V3 使用原有命令、Manifest 和测试，不删除、不跳过、不弱化旧测试。

## 4. V4 Evaluation 2.0

数据集 `evals/v4_evaluation_dataset.json` 包含 17 个全虚构 case。运行命令：

```bash
conda run -n tuli_env python evals/run_v4_final_evaluation.py --compact
```

结果快照为 `evals/v4_final_results.json`。所有比例来自本次 JUnit；以下 accuracy/usable rate
仅描述固定离线适配器契约，不代表生产模型开放数据准确率。

| 指标 | 实际结果 |
| --- | ---: |
| Printed OCR usable rate | 4/4 = 100% |
| Handwriting usable rate | 4/4 = 100% |
| Low-confidence safety rate | 4/4 = 100% |
| Visual block accuracy | 1/1 = 100% |
| KIE field accuracy | 3/3 = 100% |
| Visual table cell accuracy | 3/3 = 100% |
| Visual Evidence localization | 4/4 = 100% |
| General task success rate | 3/3 = 100% |
| Domain routing accuracy | 4/4 = 100% |
| Evidence sufficiency accuracy | 3/3 = 100% |
| Retrieval completion rate | 3/3 = 100% |
| Visual prompt injection defense | 4/4 = 100% |
| V4 fixed-sample latency | 40 samples；average 24.325 ms；P95 53 ms |
| Average controlled retrieval rounds | 1.5（2 controlled workloads） |
| Unnecessary retrieval rate | 0/1 = 0%（simple query） |
| Budget termination rate | 1/1 = 100%（专门的 budget case） |
| Tool/LLM/OCR/Vision call count | 固定 Trace workload 各 1 |
| Token/Cost | `unavailable` / `unavailable` |

`budget termination rate=100%` 表示唯一刻意触发预算的 case 正确按 budget 停止，不是失败率。
没有 provider usage 时不以 0 冒充 Token 或 Cost。

## 5. Demo Results

| Demo | 自动验收 | 覆盖 | 人工状态 |
| --- | --- | --- | --- |
| A 图片 + 手写 | 10/10 PASS | upload、OCR/handwriting、VisualBlock、KIE、bbox Evidence、low-confidence review | 真实手写可用性与浏览器高亮待复核 |
| B General Document Agent | 3/3 PASS | 非学生 Excel 查询/筛选、PDF 规则检索、Python 条件判断、实际 Evidence | PASS；Word 通用解析由既有回归覆盖 |
| C Agentic Retrieval | 3/3 PASS | Need、首轮不足、受控第二轮、stop、not-found 非否定措辞、Trace | PASS |

## 6. Safety

S401–S404 固定视觉注入样本 4/4 通过；低置信度视觉字段不能直接进入高影响写入；HIGH-risk
仍要求数据库绑定的真实 Approval；scope expansion 在检索调用前失败。这里只验证安全契约，
不声明开放世界恶意内容检测准确率。

## 7. Requirement Coverage

V4.11 最终矩阵见 `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`：

- VERIFIED：9
- PARTIAL：3
- NOT_IMPLEMENTED：0
- N/A：0

PARTIAL 集中在可人工浏览的完整二进制样本包、真实手写/浏览器 Demo 和 Final PASS 签署；
最终全量 pytest 已通过，不表示已验证的 V4.1–V4.10 自动回归失败。

## 8. Remaining Limitations

1. 固定适配器验证数据结构、路由、Evidence 和安全边界，不测部署环境真实 OCR/手写模型准确率。
2. clear/medium/extremely unclear 手写样本仍需人工 review；当前不能据此发布“七七八八可读率”。
3. 图片/PDF bbox 高亮与 review 操作未做浏览器截图级人工验收。
4. 复杂合并单元格、无边框手绘表格按设计降级，不保证 Cell 结构。
5. Agentic Retrieval timeout 是总预算，不能强制中断正在运行的同步 RAG 调用。
6. 固定测试耗时不是生产端到端 latency/SLA；真实 Tool/LLM/Vision 用量取决于部署 provider。

## 9. Final Decision

固定自动验收满足图片 Document、image/mixed PDF、低置信度安全、视觉 Evidence、General
Document Agent、Student 回归、受控补检索、不足证据措辞和视觉注入防护。由于真实 OCR/手写
backend 与浏览器人工验收未执行，**当前不建议签署 V4 Final Acceptance PASS；建议结论为
PARTIAL**。

完成三档手写人工 review、真实 backend smoke、浏览器 bbox/review Demo，并复核最终全量 pytest
后，可由人工将结论升级为 PASS。推荐人工提交信息：

- Commit: `chore(v4.11): add evaluation demo audit and final acceptance`
- Stage tag: `v4.11-evaluation-final-acceptance`
- Final tag（仅在人工项完成并签署 PASS 后）：`v4-final-stable`

本阶段未执行 commit、tag 或任何 Git 命令。
