# Evaluation fixtures

该目录只保存固定评估输入和验收预期，不包含业务实现。

- `agent_cases.json`：自然语言任务、期望 Tool、Evidence 模式、状态和人工复核标记。
- `coverage_manifest.json`：U/R/P/D/W/B/E 编号到 pytest 用例的固定映射。
- `metric_definitions.json`：指标分子、分母和适用范围定义。
- `manual_review.json`：无法通过稳定断言判断的自然语言复核项。

自然语言内容不以固定答案做 100% 字符串匹配；自动化只判断 Tool、状态、Evidence、审批和安全约束。

## V3.10 Trace Evaluation

- `v3_10_cases.json`：Tool 耗时、Retrieval、Workflow、Token/Cost 和评估比率案例。
- `v3_10_results.json`：案例总数、成功数、失败数、成功率和错误率。

成功率定义为 `successful_cases / total_cases`，错误率定义为
`failed_cases / total_cases`。Token 仅使用模型 API 返回的 usage；Cost 仅使用显式配置的
USD/1M tokens 单价，未配置时为 0。

## V3 Final Evaluation

- `v3_final_manifest.json`：OCR、Table、Retrieval、Evidence、Workflow、Async 与
  Safety 固定验收数据集；每项只引用真实 pytest 节点。
- `run_v3_final_evaluation.py`：逐指标执行 Manifest，以本次 JUnit 结果计算分子、分母、
  成功率、平均延迟和 P95；同时通过隔离数据库运行固定 Trace workload 统计 Tool/LLM
  调用次数。
- `v3_requirement_traceability.json`：F01-F12、O01-O10、R201-R210、WF01-WF10、
  S01-S10 到既有测试的机器可读映射。
- `run_v3_requirement_tests.py`：按正式类别去重并执行映射中的真实测试。
- `v3_final_results.json`：V3.22 本次实际运行结果快照，不作为后续运行的预设期待值。

运行命令：

```bash
python evals/run_v3_final_evaluation.py
python evals/run_v3_requirement_tests.py
```

这里的 accuracy/rate 分母是 Manifest 中的固定、受控验收案例，不代表开放世界生产数据的
统计精度。自然语言表达质量继续人工复核。Token usage 或价格不可获得时输出
`unavailable`，不以 0 伪装为真实用量或成本。
