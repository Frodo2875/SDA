# V3 Development Log

## Base Version

V2.99-Final-v2

## Development Goal

基于 V2 Agent 文档管理助手升级 V3。

## Upgrade Direction

1. Document Workspace
2. Document Lifecycle Upgrade
3. OCR Pipeline
4. Layout and Block Schema
5. RAG 2.0
6. Evidence 2.0
7. Workflow Runtime
8. Async Task Runtime
9. Agent Safety
10. Trace and Evaluation

## Development Rule

- Incremental upgrade
- No large refactor
- Keep V2 compatibility
- Each feature requires:
  - implementation
  - test
  - git commit
  - git tag

## V3.10 Trace Evaluation

- Trace 增加 Tool/Retrieval/Token/Cost 指标。
- Workflow 评估从现有 Task/Step 状态确定性汇总。
- `evals/` 增加 V3.10 固定案例、成功率和错误率结果。
- 不记录完整 Chain-of-Thought，不根据模型名称推测价格。
