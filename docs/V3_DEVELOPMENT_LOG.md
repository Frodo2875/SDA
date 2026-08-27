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

## V3.13 Document Lifecycle and Atomic Reindex

### BLOCKER 原因

- 原 `_index_failure()` 会在解析、OCR 或索引写入失败时调用
  `delete_document_chunks(file_id)`。
- 已存在有效索引的 QUERYABLE 文档重新索引失败后，旧 chunk 和 FTS
  会被删除，文件因而失去原本可用的检索结果。
- chunk 替换虽使用 SQLite 事务，但文件恢复 QUERYABLE 原先是另一个状态事务，
  没有形成统一的 active 切换边界。

### 修复方式

- 增加兼容的 canonical 状态 `REPROCESSING`、`REINDEXING`，不修改 V2
  lifecycle/parse/index 字段定义。
- 旧有效索引在候选解析和构建期间继续保持 `ready + indexed + queryable`。
- 失败路径不再删除 chunk；存在旧 active 索引时恢复 `QUERYABLE`，并在
  lifecycle Trace 中保存 `failure_stage`、error code 和 error summary。
- 初次索引没有旧 active 数据时仍进入统一 `FAILED`，保持原接口行为。

### 原子切换策略

```text
active chunks / FTS
        ↓
内存构建 candidate chunks
        ↓
校验 file_id、chunk_id、连续 chunk_index、text_hash
        ↓
SQLite 单事务：替换 chunks + 替换 FTS + 文件状态切换 QUERYABLE
        ↓
提交成功后新结果成为唯一 active generation
```

- 事务中的任意插入或状态冲突会整体 rollback，旧 chunk/FTS 保持不变。
- 成功事务一次删除旧行并写入完整新行，不会出现新旧 chunk 混用。
- 重复 reindex 使用稳定 chunk ID，且每次原子替换，不产生重复 active 数据。
- 当前 Schema 已能用 active 表 + 内存 candidate 表达该策略，因此未增加 migration。

### Reprocess 安全策略

- `reprocess_document()` 使用 `REPROCESSING → REINDEXING → QUERYABLE`。
- 新解析结果在内存中完整生成并验证前不会触碰旧 active rows。
- parse/OCR/layout/index 失败阶段可通过 `failure_stage` 准确识别。
- 失败时旧 Block/chunk generation 继续可查询，不会与候选结果混用。

### 新增测试

- F09：候选索引事务中途失败后，旧 chunk、FTS 检索和 QUERYABLE 状态保持。
- F09：成功 reindex 只激活新 generation，构建期间旧结果仍可查询。
- F09：重复 reindex 不产生重复 active chunk。
- F10：reprocess 解析失败保留完整旧 generation 与 parse error summary。
- F10：reprocess 成功只激活新解析结果。
- F10：REINDEXING 状态拒绝逆向转换到 UPLOADED。

### 测试结果

```text
pytest: 243 passed, 0 failed, 0 skipped, 10.85s
V1 Regression: 66 passed, 1 deselected, PASS
V2 coverage manifest: 60 passed, PASS
```
