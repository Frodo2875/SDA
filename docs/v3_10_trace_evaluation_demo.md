# V3.10 Trace Evaluation Demo

## 目标

验证现有 Agent、Tool、Retrieval 和 Workflow 执行产生的可观测指标，不增加业务功能。

## Demo 观察项

运行一次 Agent 查询后，通过已有 Task/Session Trace 可以观察：

| 范围 | 指标 |
|---|---|
| Tool | 调用次数、成功/失败、总耗时、平均耗时、最大耗时 |
| Retrieval | Hybrid/Fallback 模式、结果数、空结果、Top score |
| Workflow | Task 状态、Step 状态计数、完成率 |
| Token | Input、Output、Total tokens |
| Cost | USD 总成本、价格是否已配置 |

Trace 仍只记录步骤、Tool、参数摘要、结果摘要、状态、耗时、错误和上述统计字段，不记录模型完整推理过程。

## Cost 配置

成本不会根据模型名称自动猜测。需要在 `.env` 中显式提供：

```dotenv
LLM_INPUT_COST_PER_1M=0
LLM_OUTPUT_COST_PER_1M=0
```

单位为 USD / 1M tokens。未配置时仍记录真实 Token，Cost 为 `0`，并标记
`pricing_configured=false`。

## 离线评估

```bash
conda run -n tuli_env pytest tests/evals/test_trace_evaluation_v3.py
```

固定案例：5。

- 成功：5
- 失败：0
- 成功率：100%
- 错误率：0%

案例及结果分别保存在：

- `evals/v3_10_cases.json`
- `evals/v3_10_results.json`

## 完整回归

在项目根目录运行：

```bash
conda run -n tuli_env pytest
```

实际结果：

```text
233 passed in 8.78s
```
