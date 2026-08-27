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

## V3.14 Document Workspace File Operations

### Workspace 入口

- 保留现有文件卡片、搜索、文件类型/生命周期筛选、时间/大小排序与多文件上传。
- 文件卡片增加“查看详情”和受限“快速预览”。
- Word/PDF 增加“重新解析”和“重新索引”，直接调用 V3.13
  `reprocess_document()` / `reindex_document()`，不通过删除后重新上传模拟。
- 操作请求期间显示 `REPROCESSING` / `REINDEXING` 状态；完成后自动刷新列表。
- 失败后卡片和详情展示 lifecycle Trace 中持久化的 `error_summary`；成功恢复
  `QUERYABLE`。
- Excel 继续使用结构化 Schema 流程，不伪装成 Document Block 重处理。

### 快速预览

- 新增只读 `GET /api/files/{file_id}/preview`，只接受稳定 file_id，不接受客户端路径。
- Excel 最多预览 3 个 Sheet、每 Sheet 6 行 × 12 列。
- Word/PDF 优先预览现有 active chunks；无 chunk 时使用对应只读解析器。
- 文本条目和字符长度均有上限，响应明确标记是否截断。

### 文件操作 API

- `POST /api/files/{file_id}/reprocess`
- `POST /api/files/{file_id}/reindex`
- 删除继续复用 `POST /api/files/{file_id}/delete` 与
  `POST /api/actions/{action_id}/confirm|cancel`，没有增加直接删除接口。

### 删除确认

- 上传文件卡片展示“删除文件”，点击后只创建冻结的 pending action。
- 确认卡明确显示目标文件；Confirm 后才执行真实删除，Cancel 不修改任何文件或索引。
- 系统固定文件不展示删除按钮，后端 `create_pending_delete_action()` 仍再次检查
  `source_type/deletable` 并返回 `FILE_DELETE_FORBIDDEN`。

### 新增测试

- F09/F10：Workspace API 调用真实安全 reprocess/reindex，并恢复 QUERYABLE。
- F09/F10：操作失败保留旧 chunks，并在文件详情返回 parse/index error summary。
- 快速预览：Word 真实文本、Excel 真实 Sheet/行及边界限制。
- F11：Cancel 后物理文件、file record、Block/chunk/index 完全不变。
- F11：Confirm 后物理上传文件与 active chunks 同步清理。
- F12：系统固定文件前端无删除入口；既有后端 403 拒绝测试继续通过。
- API Client：preview/reprocess/reindex/delete 请求路径和参数。
- 批量上传逐文件成功/失败隔离测试继续通过。

### 测试结果

```text
pytest: 251 passed, 0 failed, 0 skipped, 11.42s
V1 Regression: 66 passed, 1 deselected, PASS
V2 coverage manifest: 60 passed, PASS
```

## V3.15 Workflow Runtime Core Completion

### Task/Step 与 Workflow Node

- 保留 V2 `tasks` / `task_steps` 表、状态和 API；简单 Tool Calling 仍不强制创建
  Workflow。
- Task/Step 继续承担持久化、Trace 和兼容职责；Workflow Node 在其上增加图执行语义：
  `workflow_id`、`node_id`、`node_type`、`depends_on`、`input_ref`、
  `output_ref`、`condition` 和 `run_if`。
- Node 契约与最小结果引用保存在已有 `checkpoint_data.plan/node_results` 中，未增加
  migration。

### Dependency Gating

- Tool、Generation、Approval 和 Write 执行前均检查 `depends_on`。
- 前置 Node 未达到 `SUCCESS` / `CONFIRMED` 时，后置 Node 无法认领或执行。
- A → B → C 的后置节点不再能按 Tool 名称绕过依赖提前执行。

### Parallel / Aggregate

- Runtime 按依赖关系计算 ready wave；同一 wave 的无依赖 Node 通过受限
  `ThreadPoolExecutor` 并发执行。
- 所有并行结果落库后才进入下一 wave，Aggregate 必须等待其声明的全部前置 Node。
- 并发测试使用线程同步屏障验证成绩与科研查询真实重叠，而非只检查节点类型字段。

### Conditional

- 新增结构化 Condition Node，仅允许 `eq`、`ne`、`gt`、`gte`、`lt`、
  `lte`、`in`、`not_in`、`exists`。
- 条件输入只能通过安全的结构化 dotted reference 读取；未使用 `eval()`、`exec()`、
  任意 Python expression 或 SQL。
- `run_if` 选择一个分支，未选分支明确记录为 `CANCELLED / condition_not_selected`。

### Approval

- Approval 是 `node_type=approval` 的真实 Node；到达后进入
  `WAITING_CONFIRMATION`，后续高风险 Write 仍不可执行。
- Confirm 继续复用现有冻结 action 和幂等执行链路；Cancel 将 Approval/Write 标为
  `CANCELLED`，不会触发真实数据修改。

### Retry / Checkpoint / Resume

- 继续复用 `MAX_RETRIES` 有界重试，达到上限后 Node 持久化 `FAILED`、
  `retry_count` 和 `failed_reason`，不会自动无限循环。
- 每个成功 Node 在 checkpoint 保存最小结果、`output_ref` 和关联文件版本指纹。
- Resume 跳过仍有效的成功 Node，从未完成/失败 Node 继续；成功 Write 永不自动失效或
  重放。
- 只有输入文件版本指纹改变时，相关只读 Node 及其非写入下游才失效并重新执行。

### 新增测试

- WF01：Sequential dependency gating。
- WF02：Parallel 真实并发与 Aggregate 等待。
- WF03：缺材料分支 `report_missing`。
- WF04：材料完整分支 `eligibility_check`。
- WF05：Approval 等待与 Cancel 不执行 Write。
- WF06：有界 Retry 达到上限后 FAILED。
- WF07：Resume 跳过成功 Node，成功 Write 不重复执行；文件版本变化时仅使相关只读
  Node 及其下游失效。

### 测试结果

```text
pytest: 259 passed, 0 failed, 0 skipped, 11.71s
V1 Regression: 66 passed, 1 deselected, PASS
V2 Regression: included in full pytest, PASS
WF01-WF07: 8 passed, PASS
```
