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
