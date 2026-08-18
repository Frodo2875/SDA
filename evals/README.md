# V2 Evaluation fixtures

该目录只保存固定评估输入和验收预期，不包含业务实现。

- `agent_cases.json`：自然语言任务、期望 Tool、Evidence 模式、状态和人工复核标记。
- `coverage_manifest.json`：U/R/P/D/W/B/E 编号到 pytest 用例的固定映射。
- `metric_definitions.json`：指标分子、分母和适用范围定义。
- `manual_review.json`：无法通过稳定断言判断的自然语言复核项。

自然语言内容不以固定答案做 100% 字符串匹配；自动化只判断 Tool、状态、Evidence、审批和安全约束。
