# V3 Final Acceptance

## 1. 冻结基线

|项目|实际结果|
|-|-|
|Branch|`feature/v3-development`|
|Commit before final commit|`c8ed56f7dd44da3f4a7856a7269302e4ad71446b`|
|Base version|`V2.99-Final-v2`|
|Final acceptance date|2026-08-27|
|Auto commit|未执行|

## 2. Regression

|范围|命令/口径|实际结果|
|-|-|-|
|Full pytest|`conda run -n tuli_env pytest`|`313 passed, 0 failed, 0 skipped, 19.09s`|
|V1 Regression|`evals/v1_regression.txt` 所列核心文件；排除后续版本 API 节点|`68 passed, 1 deselected, PASS`|
|V2 Regression|`evals/coverage_manifest.json` 去重真实节点|`60 passed, PASS`|
|现有 Eval suite|`pytest tests/evals -s`|`18 passed, PASS`|

V1 当前命令收集到 68 个通过节点，较 V3.12 记录的 66 个多出后续新增测试；没有删除或降低原 V1 断言。V2 仍使用原固定 Manifest。

## 3. V3 正式测试

机器可读映射见 `evals/v3_requirement_traceability.json`，完整说明见
`docs/V3_REQUIREMENT_TEST_TRACEABILITY.md`。本次运行
`python evals/run_v3_requirement_tests.py` 的实际结果：

|正式类别|需求编号|去重测试节点|实际 pytest cases|结果|
|-|-:|-:|-:|-|
|F|F01-F12|18|18|PASS|
|O|O01-O10|12|14|PASS|
|R|R201-R210|12|14|PASS|
|WF|WF01-WF10|11|12|PASS|
|S|S01-S10|10|10|PASS|

正式需求编号共 52 项，52/52 均具有真实自动化测试映射且本次通过。pytest cases
数量包含参数化分支，因此可能大于去重节点或需求编号数。

## 4. Evaluation

运行命令：

```bash
conda run -n tuli_env python evals/run_v3_final_evaluation.py --compact
```

结果快照：`evals/v3_final_results.json`。所有比例均从本次实际 JUnit 或隔离的生产服务
fixture 计算，不使用预填分子/分母。

|指标|实际结果|分母口径|
|-|-:|-|
|OCR page success rate|3/3 = 100%|固定 OCR service 页 fixture|
|OCR key-field accuracy|9/9 = 100%|3 页 × text/bbox/confidence 三字段|
|Table structure accuracy|4/4 = 100%|2×2 TableCell 文本及坐标|
|Hybrid Recall@K|3/3 = 100%|固定精确、语义、强过滤检索 cases|
|Rerank Top-K hit rate|4/4 = 100%|Top-K 及 exception/invalid/timeout 回退参数 cases|
|Evidence localization accuracy|5/5 = 100%|PDF/Excel/Word/失败/未使用来源 cases|
|Workflow success rate|7/7 = 100%|Sequential/Parallel/Conditional/Approval/Retry/Resume cases|
|Resume success rate|3/3 = 100%|Workflow 及 Async checkpoint cases|
|Async retry/resume success|2/2 = 100%|partial retry 与 resume cases|
|Prompt Injection defense rate|4/4 = 100%|RAG/Delete/JSON/OCR 攻击文本 cases|
|HIGH Risk approval trigger rate|5/5 = 100%|write/delete/overwrite/undo/rollback operations|
|Average latency|32.324 ms|37 个固定 Evaluation pytest samples|
|P95 latency|58.0 ms|同一 37 samples，nearest-rank|
|Tool Call count|2|隔离的固定 Trace workload|
|LLM Call count|1|隔离的固定 Trace workload|
|Token usage|`unavailable`|固定 client 未返回 API usage，不伪造|
|Estimated cost|`unavailable`|没有可用 usage/pricing，不伪造|

这些指标描述固定、受控的 V3 验收集，不代表开放世界生产文档的统计准确率。自然语言表达质量
保留人工复核。

## 5. Trace / Observability Contract

V3.22 在现有 `traces.metrics_json` 上增量补充，无数据库 migration：

- Task/Workflow/Node/Step/Tool：保留 `task_id`、`step_id`、`tool_name`，新增
  `workflow_id`、`node_id`、`node_type`、`step_type` 指标；状态、Retry 与 Error 继续持久化。
- Approval/Safety：继续记录 policy decision、risk、approval id/result/rejection，不记录
  Chain-of-Thought。
- Document Pipeline：记录 OCR/Layout/Index 实际 duration；OCR 页总数、成功/失败数；
  Layout block count；Index candidate/active chunk count。
- Retrieval/Evidence：记录 Hybrid duration、candidate count、Rerank duration、Top-K、
  final evidence ids；Evidence 成功/失败定位均可观察。
- Async：记录 queue time、execution time、task total duration 与真实 progress detail。
- LLM：API usage 可得时记录 Token；配置价格且 usage 可得时估算 Cost，否则显式
  `unavailable`。
- Evaluation summary：提供 Tool/LLM call count、平均/P95 latency、阶段聚合和 Error code
  计数。

## 6. V3.12 审计复核

未改写 `docs/V3_REQUIREMENT_COVERAGE_AUDIT.md` 的历史结论。按 V3.13-V3.22 的代码、
开发日志和本次测试证据复核：

### BLOCKER

|V3.12 问题|修复版本|最终状态|
|-|-|-|
|Reindex 失败删除旧有效 chunk|V3.13|CLOSED；F09/F10 原子切换与失败保旧测试 PASS|
|Workflow 缺依赖门控、Parallel、Conditional|V3.15|CLOSED；WF01-WF07 PASS|

剩余 BLOCKER：**0**。

### HIGH

|V3.12 问题组|修复版本|最终状态|
|-|-|-|
|Mixed/partial OCR 与页级恢复|V3.16|CLOSED|
|基础 Layout/Table/Cell 与安全降级|V3.17|CLOSED|
|RAG 强过滤、Hybrid/Rerank fallback|V3.18|CLOSED|
|Evidence 原文定位|V3.19|CLOSED|
|Async 状态、真实 Progress、Task Center|V3.20|CLOSED|
|Prompt Injection 与 Tool Policy|V3.21|CLOSED|
|Workspace reprocess/reindex/delete 入口|V3.14|CLOSED|
|V3 量化 Evaluation|V3.22|CLOSED|

剩余 HIGH：**0**。

### MEDIUM

当前保留 3 项非 P0 阻断限制：

1. 真实 OCR backend、字体和系统二进制仍依赖部署环境；CI 以可重复单元 fixture 为主，
   可用环境才运行真实 smoke。
2. 默认本地 Hash Embedding 与固定 Evaluation 集适合离线验收，不代表生产级大规模语义
   召回质量；开放数据集仍需持续扩充。
3. 当前是基础 Layout/Table 与稳定预览定位，不覆盖高级视觉版面、跨页复杂表格或完整
   Office/PDF 编辑器能力。

剩余 MEDIUM：**3**。

### OPTIONAL

1. PPT/PPTX、Slide Block 与 PPT Evidence。
2. 高级 PDF/Office Preview 与编辑器级高亮交互。
3. 用户确认字段语义的长期 Memory。
4. Trace/Token/Cost 专用可视化 Dashboard。

OPTIONAL：**4 类**。

## 7. Final Decision

|判定条件|结果|
|-|-|
|V1 Regression PASS|YES|
|V2 Regression PASS|YES|
|BLOCKER = 0|YES|
|HIGH = 0|YES|
|正式 Safety tests PASS|YES|
|Workflow tests PASS|YES|
|写入安全 PASS|YES|
|主要 V3 Eval 可重复运行|YES|

正式 F/O/R/WF/S P0 覆盖：**52/52 = 100%**。

**V3 READY TO FREEZE = YES**

在完成用户侧 Demo/部署环境 smoke 并创建人工 Git commit/tag 后，可进入 V4 需求设计。
