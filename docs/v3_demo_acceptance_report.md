# V3 Demo Acceptance Report

验收日期：2026-08-21  
版本定位：V3 Final  
基线版本：`V2.99-Final-v2`  
当前分支：`feature/v3-development`  
当前提交：`bb1793acce9cae82c077fcb4caeced89537955ea`  
当前标签：`v3.11-frontend-workspace`  
结论：自动化验收通过；真实 OCR 识别质量、真实 LLM Tool 选择稳定性和浏览器截图级 UI 体验仍需人工 Demo 复核  
业务代码修改：无（本报告生成阶段仅新增验收文档）

## 1. Demo Overview

V3 基于 `V2.99-Final-v2` 对学生材料智能文档管理 Agent 进行增量升级，保留 V2 的结构化查询、Python 统计、文档检索、Evidence、Human-in-the-loop、Word 版本管理和回归能力，没有重构既有 Agent 主循环。

本报告以当前代码、`tests/`、`tests/evals/` 和 `evals/` 中的固定验收材料为依据。自动测试使用 pytest 临时目录和隔离数据库；涉及 Agent 自然语言链路的组合场景使用离线 Replay Client，不读取 `.env`，不访问真实 LLM API，也不污染正式材料。

V3 重点能力：

1. Document Workspace：文件列表、搜索、筛选、排序、详情和多文件上传展示。
2. Document Lifecycle：统一状态机、非法转换限制、失败信息和断点恢复。
3. OCR Pipeline：检测 PDF 内嵌文本，对无文本 PDF 进入 OCR 流程。
4. Layout Block：以 title、paragraph、table、cell 表达解析结果，并兼容旧 chunk。
5. RAG 2.0：FTS5 关键词检索、可替换 Embedding、Hybrid Fusion、Rerank 和失败回退。
6. Evidence 2.0：扩展到 file、page、block、table、cell、bbox 和 confidence 定位。
7. Workflow Runtime：统一 Plan、Task、Step、Tool 和 Trace，支持组合任务及确认写入。
8. Async Task：使用 SQLite Task 记录支持进度、取消、重试和恢复。
9. Agent Safety：区分可信/未知文件，高风险操作执行前进行策略检查和确认。
10. Trace Evaluation：记录 Tool 耗时、Retrieval、Workflow、Token 和 Cost 指标，并发布固定评估结果。
11. Frontend Workspace：Streamlit 三栏工作区、Evidence Preview 和 Task Center。

## 2. System Capability Verification

### 2.1 Document Workspace

**功能目标**

- 通过后端 API 提供文件列表和稳定 `file_id` 详情，不允许前端直连数据库。
- 展示文件名、类型、大小、登记时间、生命周期、解析、索引和可查询状态。
- 支持文件名搜索、类型/状态筛选、时间/大小排序。

**实现位置**

- `backend/main.py`
- `backend/services/file_view.py`
- `backend/repositories/file_repository.py`
- `frontend/api_client.py`
- `frontend/components/file_panel.py`

**验证方法**

- `tests/test_document_workspace.py` 调用真实 ASGI API 验证列表、搜索、筛选、排序和详情。
- `tests/test_frontend_workspace.py` 验证卡片字段、筛选映射和 HTTP Client 参数。

**测试结果：PASS**

### 2.2 Document Lifecycle

**功能目标**

- 统一 `UPLOADED → DETECTING → PARSING → OCR_PROCESSING / LAYOUT_PROCESSING → INDEXING → QUERYABLE` 状态。
- 限制非法逆向转换，每次变化写 Trace；进入 `FAILED` 时保存错误和恢复点。

**实现位置**

- `backend/services/file_lifecycle.py`
- `backend/repositories/file_repository.py`
- `backend/database.py`

**验证方法**

- `tests/test_file_lifecycle_v3.py` 实际执行完整允许状态流、`QUERYABLE → UPLOADED` 非法转换和数据库重新初始化后的断点恢复。

**测试结果：PASS**

### 2.3 OCR Pipeline

**功能目标**

- 检测 PDF 是否含内嵌文本；普通 PDF 保持原解析流程。
- 无文本 PDF 进入 `OCR_PROCESSING`，输出 page、text、confidence、bbox。
- OCR 失败时不创建虚假 chunk，保存失败原因并允许从检查点恢复。

**实现位置**

- `backend/services/ocr_service.py`
- `backend/services/document_index.py`
- `backend/services/file_lifecycle.py`

**验证方法**

- `tests/test_ocr_pipeline.py` 覆盖普通 PDF、确定性 OCR Adapter、扫描 PDF 状态流、OCR 失败和恢复。
- 测试明确断言 OCR 失败后 chunk 为空，避免伪造识别结果。

**测试结果：PASS（真实 OCR 引擎识别质量为 Not Tested）**

### 2.4 Layout Block Schema

**功能目标**

- 使用 `DocumentBlock` 保存 `block_id / file_id / page_no / block_type / content / bbox / confidence`。
- 支持 title、paragraph、table、cell，Block 可转换为旧 `document_chunks` 记录。
- 旧 chunk 无 Block metadata 时仍可读取。

**实现位置**

- `backend/document_blocks.py`
- `backend/services/document_index.py`

**验证方法**

- `tests/test_layout_blocks.py` 使用真实生成的 Word 标题、正文和表格验证 Block、Chunk 和 Evidence 关联。
- 专项测试验证旧 chunk 不被修改且仍可读取。

**测试结果：PASS**

### 2.5 RAG 2.0

**功能目标**

- 保留 SQLite FTS5 关键词检索。
- 支持 `file_id / file_type / page` metadata filter。
- 提供可替换 Embedding 接口，执行关键词/向量 Fusion 和最终 Rerank。
- 向量链路失败时自动回退原 FTS5。

**实现位置**

- `backend/services/hybrid_retrieval.py`
- `backend/services/embedding_service.py`
- `backend/services/document_index.py`
- `backend/database.py`

**验证方法**

- `tests/test_rag_v3.py` 验证关键词命中、注入语义 Provider 后的语义命中、file_type + page 过滤以及向量失败回退。
- 回退场景实际返回 `retrieval_mode=keyword_fallback` 和 `VECTOR_RETRIEVAL_FAILED` warning。

**测试结果：PASS**

### 2.6 Evidence 2.0

**功能目标**

- 所有结论引用可回到实际原文位置。
- PDF/Word 支持 page、block、bbox、confidence；Excel 支持 table、cell、sheet、field。
- 保留旧 file/page/chunk Evidence 格式兼容。

**实现位置**

- `backend/evidence.py`
- `backend/services/document_index.py`
- `backend/tools/table_tools.py`
- `frontend/components/evidence_panel.py`

**验证方法**

- `tests/test_evidence_v3.py` 分别验证 PDF、Excel、Word 引用定位。
- Excel 测试按 Evidence 中的 `B2` 单元格重新打开工作簿核对原值；Word 测试按 `block_id` 回到原段落。

**测试结果：PASS**

### 2.7 Workflow Runtime

**功能目标**

- 组合 Plan 使用 QUERY、ANALYSIS、GENERATE、CONFIRM、WRITE Step。
- 每个 Step 持久化 `step_id / step_type / tool_name / description / input / expected_output`。
- 统一 Step 状态、Trace 关联、Checkpoint 恢复和成功 Step 跳过逻辑。
- 写入前等待用户确认，取消不写文件，确认后不重复执行成功前置查询。

**实现位置**

- `backend/runtime/planner.py`
- `backend/runtime/task_runner.py`
- `backend/runtime/tool_executor.py`
- `backend/agent.py`
- `backend/services/confirmation.py`
- `backend/services/trace_service.py`

**验证方法**

- `tests/test_workflow_v3.py` 验证普通查询保留 V2 轻量路径、奖学金 6-Step Plan，以及“奖学金判断 + Word 写入”12-Step 组合 Workflow。
- 组合测试同时执行取消和确认分支，逐字节检查 Word 是否变化，并检查每条 Step Trace 的 `task_id / step_id / tool_name`。
- 原 V2 Demo B 中“Diff、确认、写入 Step/Trace 不完整”的阻塞点已由该专项测试覆盖。

**测试结果：PASS**

### 2.8 Async Task Runtime

**功能目标**

- 不引入复杂消息队列，使用 SQLite Task/Step 记录长任务。
- 支持 pending、running、success、failed、cancelled 状态以及 progress、message、checkpoint。
- 支持取消、有限重试和断点恢复。

**实现位置**

- `backend/runtime/async_task_runtime.py`
- `backend/repositories/task_repository.py`
- `backend/main.py`

**验证方法**

- `tests/test_async_task_runtime.py` 覆盖创建任务、执行中 45% 进度查询、队列任务取消、40% checkpoint 恢复和失败后有界重试。

**测试结果：PASS**

### 2.9 Agent Safety

**功能目标**

- 系统文件标记为 trusted，上传文件标记为 unknown。
- 写入、删除、覆盖、Undo、Rollback 等高风险操作必须确认。
- 未登记文件在 Tool Handler 执行前阻止，并记录安全 Trace。

**实现位置**

- `backend/runtime/safety_policy.py`
- `backend/runtime/tool_executor.py`
- `backend/agent.py`
- `backend/services/confirmation.py`

**验证方法**

- `tests/test_agent_safety.py` 验证危险写入、未知上传删除、未知文件只读访问、未登记文件阻断和覆盖确认。
- 未登记文件测试同时断言 Tool Handler 未被调用。

**测试结果：PASS**

### 2.10 Trace Evaluation

**功能目标**

- Trace 记录 Tool 耗时、Retrieval 模式/结果、Workflow 状态、模型 Token 和显式配置的 Cost。
- 不记录完整 Chain-of-Thought，不根据模型名称猜测价格。
- 输出固定评估案例成功率和错误率。

**实现位置**

- `backend/services/trace_service.py`
- `backend/services/evaluation_service.py`
- `evals/v3_10_cases.json`
- `evals/v3_10_results.json`

**验证方法**

- `tests/evals/test_trace_evaluation_v3.py` 验证 37ms Tool 耗时、Hybrid Retrieval 指标、Workflow 完成率、100/20 Token 和显式费率计算。
- 未配置费率时明确断言 Cost 为 0 且 `pricing_configured=false`。
- 固定评估结果为 5/5 成功、0/5 失败。

**测试结果：PASS**

### 2.11 Frontend Workspace

**功能目标**

- Streamlit 页面提供 Document Workspace、Agent Chat、Evidence Preview 和 Task Center 三栏布局。
- 支持拖拽/多文件上传、文件卡片、搜索筛选排序、按类型展示详情。
- Task Center 展示 OCR、索引和 Workflow 的任务状态；Evidence Preview 展示 file/page/block/cell/bbox。

**实现位置**

- `frontend/app.py`
- `frontend/api_client.py`
- `frontend/components/file_panel.py`
- `frontend/components/evidence_panel.py`
- `frontend/components/task_center.py`

**验证方法**

- `tests/test_frontend_workspace.py` 使用 Streamlit `AppTest` 验证三栏区域和关键组件。
- HTTP Client 测试验证列表 API 参数及多文件上传成功/失败结果相互隔离。

**测试结果：PASS（浏览器截图级人工体验为 Not Tested）**

## 3. Demo Scenario

以下流程用于 V3 Demo 展示。自动化已验证其底层能力；标注为人工复核的观察项不计入自动 PASS。

### Scenario 1：文档上传、解析与工作区

**Demo 输入**

- 一份普通文本 PDF。
- 一份扫描 PDF。
- 一份包含标题、正文和表格的 Word。
- 一份学生材料 Excel。

**流程**

1. 在 Document Workspace 拖拽选择多个文件。
2. 提交上传，观察各文件独立的成功或失败提示。
3. 查看文件从 `UPLOADED` 经过检测、解析、OCR/Layout、索引到 `QUERYABLE` 的状态。
4. 使用文件名搜索、类型筛选、生命周期筛选和时间排序。
5. 打开文件详情；按文件类型查看 PDF、Excel 或 Word 的可用元数据。

**验收观察点**

- 普通 PDF 不进入 OCR；无内嵌文本 PDF 进入 OCR。
- OCR 失败显示真实错误，不产生虚假 chunk，可从 checkpoint 恢复。
- 文件卡片显示生命周期、索引和是否可查询。
- 后端缺少的详情字段显示“暂无数据”，不由前端猜测。

**自动验证：PASS；真实浏览器交互和截图：Not Tested**

### Scenario 2：复杂 Agent Workflow 与确认写入

**Demo 请求**

```text
根据S001的成绩、科研成果和奖学金办法判断是否符合一等奖学金，并生成综合评价写入综合评价.docx。
```

**流程**

1. Planner 创建 `scholarship_evaluation_and_word_write` Task。
2. QUERY Step 查询身份、基本信息、成绩、科研和奖学金规则。
3. ANALYSIS Step 调用 Python Tool 判断资格。
4. GENERATE Step 生成有 Evidence 支撑的内容和 Word Diff。
5. CONFIRM Step 进入 `waiting_confirmation`。
6. 用户取消时，CONFIRM/WRITE Step 进入 cancelled，Word 保持不变。
7. 重新执行并确认后，WRITE Step 执行冻结内容，12 个 Step 最终全部 success。

**验收观察点**

- Task、Step、Tool、Trace 使用同一 `task_id / step_id / tool_name` 关联。
- 确认前不写文件；取消不写；确认后不重复执行已成功的查询。
- 写入由已有版本与确认安全链路保护。

**自动验证：PASS；真实线上 LLM Tool 选择和语言质量：Not Tested**

### Scenario 3：Hybrid 文档检索与 Evidence 定位

**Demo 请求示例**

```text
奖学金申请需要提交哪些证明？
```

**流程**

1. 限定目标文件、文件类型或页码。
2. 执行 FTS5 keyword retrieval 和 vector retrieval。
3. 对候选执行 RRF Fusion 与 Rerank。
4. 在回答右侧 Evidence Preview 展示文件、页、Block/Cell、BBox 等定位信息。
5. 模拟 Embedding 失败，确认自动回退 FTS5 且保留 Evidence。

**验收观察点**

- PDF 引用可定位到 page/block/bbox/confidence。
- Excel 引用可定位到 sheet/table/cell/field。
- Word 引用可定位到原 paragraph block。
- 旧 chunk 仍使用兼容 Evidence，不要求补造 Block。

**自动验证：PASS**

### Scenario 4：异步任务进度、取消与恢复

**Demo 流程**

1. 创建索引或 OCR 异步任务，Task 初始为 pending、progress=0。
2. Worker 启动后观察 running、progress 和 message 更新。
3. 在任务执行前或执行中取消，确认后续工作不继续。
4. 从持久化 checkpoint 恢复任务，继续更新进度至 success。
5. 对失败任务执行 retry，确认 retry_count 有界且旧失败 checkpoint 不被误用。

**验收观察点**

- 任务状态和进度写入 SQLite，不依赖前端内存作为权威状态。
- 取消任务不会调用尚未执行的 Worker。
- 恢复任务读取原 checkpoint，成功完成后 progress=100。

**自动验证：PASS**

## 4. Test Cases

以下用例均来自当前 `tests/`、`tests/evals/` 或 `evals/` 固定案例；“实际结果”来自本次完整 pytest 运行，不代表浏览器或线上模型人工验收。

|编号|测试内容|输入|预期结果|实际结果|状态|
|---|---|---|---|---|---|
|V3-WF-01|V2 普通查询兼容|普通问候|不创建复杂 Task，保持轻量 Agent 路径|返回 completed，未返回 task_id|PASS|
|V3-WF-02|奖学金 Plan 合约|S001 奖学金资格判断|生成 6 个可执行 Step，字段完整|6 Step；QUERY/ANALYSIS/GENERATE 字段及状态断言通过|PASS|
|V3-WF-03|组合 Workflow 取消与确认|奖学金判断并写入 Word|12 Step；取消不写，确认后全部成功|取消后 Word 字节不变；确认后 12 Step success，Trace 关联完整|PASS|
|V3-WS-01|工作区列表|`GET /api/files`|返回卡片所需元数据|必需字段断言通过|PASS|
|V3-WS-02|搜索、筛选和排序|文件名“学生成绩”、Excel/ready/size asc|返回匹配文件并按大小升序|名称、类型、状态及排序断言通过|PASS|
|V3-WS-03|文件详情|稳定 file_id|返回同一文件及解析/索引状态|`综合评价.docx` 详情匹配登记记录|PASS|
|V3-LC-01|完整生命周期|UPLOADED 文件依次转换|所有允许状态成功并逐次写 Trace|最终 QUERYABLE，6 条转换 Trace 顺序正确|PASS|
|V3-LC-02|非法逆向转换|QUERYABLE → UPLOADED|拒绝且状态不变|返回 `INVALID_FILE_STATE`，仍为 QUERYABLE|PASS|
|V3-LC-03|失败恢复|PARSING 时模拟失败|保存错误和恢复点，重启后从 PARSING 继续|错误、resume_status 和最终 QUERYABLE 均通过|PASS|
|V3-OCR-01|普通 PDF|两页内嵌文本|不调用 OCR，直接生成文本 chunk|`ocr_used=false`，两页内容一致|PASS|
|V3-OCR-02|扫描 PDF|空页面文本 + OCR blocks|进入 OCR，保存 page/confidence/bbox|状态为 OCR_PROCESSING→INDEXING→QUERYABLE，Block metadata 完整|PASS|
|V3-OCR-03|OCR 失败与恢复|`OCR_ENGINE_ERROR` 后再次识别|失败不生成 chunk，保存原因，恢复后成功|失败时 chunk 为空；恢复后 QUERYABLE|PASS|
|V3-BLOCK-01|标题和正文 Block|含 heading/paragraph 的 Word|生成 title/paragraph Block 和兼容 chunk|Block 内容、类型和 block_id 引用正确|PASS|
|V3-BLOCK-02|表格和 Cell Block|2×2 Word 表格|table 与 4 个 cell 分别成 Block|表格内容、Cell 列表和 chunk metadata 正确|PASS|
|V3-BLOCK-03|旧 chunk 兼容|无 Block metadata 的旧记录|仍可读取，不修改旧 metadata|返回空 Block 列表，旧 metadata 保持原值|PASS|
|V3-RAG-01|关键词检索|“奖学金申请条件”|保留 FTS5 命中并输出 Evidence|Hybrid 模式命中真实 chunk|PASS|
|V3-RAG-02|语义检索|FTS 无短语“经济援助”|Embedding 命中“助学资金”内容|向量候选命中，未触发 fallback|PASS|
|V3-RAG-03|Metadata Filter|file_type=word、page=2|仅返回目标类型和页码|仅 1 条 Word 第 2 页 Evidence|PASS|
|V3-RAG-04|向量失败回退|Embedding Provider 抛错|自动使用旧 FTS5|keyword_fallback 命中，warning 正确|PASS|
|V3-EV-01|PDF Evidence|第 3 页 OCR Block|包含 file/page/block/bbox/confidence|定位字段及原 Block 内容一致|PASS|
|V3-EV-02|Excel Evidence|成绩表 S001 平均成绩|定位到真实单元格|Evidence 指向“成绩表”B2，重新打开文件核值一致|PASS|
|V3-EV-03|Word Evidence|检索“导师签字”|定位到原 paragraph Block|block_id 对应原 Word 段落|PASS|
|V3-ASYNC-01|创建和查询进度|索引任务|pending=0，执行中进度持久化|观察到 running=45，完成后 success=100|PASS|
|V3-ASYNC-02|取消任务|队列中的任务|cancelled 且 Worker 不执行|Worker 调用次数为 0|PASS|
|V3-ASYNC-03|恢复与重试|40% checkpoint / 失败任务|恢复原进度继续；重试次数有界|恢复读取 next_page=2；retry_count=1|PASS|
|V3-SAFE-01|危险写入与删除|Word 写入、未知上传删除|仅创建 pending action，不立即改变文件|文件保持不变，安全 Trace 为 confirmation_required|PASS|
|V3-SAFE-02|未知/未登记文件|未知上传只读、伪造 file_id|已登记未知文件只读允许；未登记文件阻断|unknown=medium/allow；未登记文件未调用 Handler|PASS|
|V3-TRACE-01|Tool 与 Retrieval 指标|37ms Hybrid 检索 Trace|保存耗时、结果数、Top score|average_duration=37、top_score=0.82|PASS|
|V3-TRACE-02|Workflow 指标|1 Step 成功 Task|汇总状态和完成率|task_status=success、completion_rate=1.0|PASS|
|V3-TRACE-03|Token 与 Cost|100 input、20 output；费率 2/4 USD per 1M|记录 120 tokens，按显式费率计算|total_cost=0.00028；未配置费率时为 0|PASS|
|V3-EVAL-01|固定评估比率|`evals/v3_10_cases.json` 5 项|发布成功率和错误率|5 成功、0 失败、成功率 100%、错误率 0%|PASS|
|V3-FE-01|三栏页面结构|Streamlit AppTest|显示 Workspace、Chat、Evidence、Task Center|4 个区域及关键按钮/任务分类存在|PASS|
|V3-FE-02|多文件上传状态|一个成功 PDF、一个失败 Word|逐文件保留独立结果|分别得到 success 和 failed|PASS|
|V3-FE-03|文件详情和引用展示|PDF/Excel/Word 元数据、Evidence locator|按类型展示，引用含 page/block/cell/bbox|组件映射断言通过|PASS|

## 5. Regression Test

执行环境：

```text
Conda environment: tuli_env
Python: 3.10.20
pytest: 9.1.1
```

执行命令：

```bash
conda run -n tuli_env pytest
```

本次实际结果：

```text
Total: 237
Passed: 237
Failed: 0
Duration: 9.74s
```

完整测试包含 `tests/evals/` 下 12 项 Evaluation 测试，以及 Workflow、Workspace、Lifecycle、OCR、Layout、RAG、Evidence、Async Task、Safety、Trace 和 Frontend 专项回归。没有删除旧测试或降低旧测试断言。

## 6. V3 Compared With V2

|能力|V2|V3|
|---|---|---|
|Workflow|组合请求可执行，但 Demo B 暴露 Planner、Step、Tool 和 Trace 不完整|完整 6/7/12 Step Plan；QUERY、ANALYSIS、GENERATE、CONFIRM、WRITE 状态和 Trace 闭环|
|Document Lifecycle|以 uploaded、processing、ready 等兼容状态为主|新增规范状态机、非法转换限制、失败错误和 checkpoint 恢复，同时映射旧状态|
|OCR|普通 PDF 文本解析，无完整扫描件自动识别链路|检测内嵌文本；扫描 PDF 进入 OCR，保留 page/confidence/bbox；失败不造结果|
|Layout|解析结果以 chunk 为主|新增 title/paragraph/table/cell Block，并可生成兼容 chunk|
|RAG|SQLite FTS5 关键词检索|FTS5 + Embedding 接口 + Hybrid Fusion + Rerank + metadata filter + FTS5 fallback|
|Evidence|以 file/page/chunk 和结构化字段为主|扩展 block/table/cell/bbox/confidence，保留旧 Evidence 形态|
|Document Workspace|基础文件面板和 `/api/files` 数据|文件卡片、搜索筛选排序、详情、多文件上传、三栏工作区|
|Task Runtime|同步 Task/Batch 和确认链路|保留 Task Runner，增加轻量 SQLite Async Task、Progress、Cancel、Retry、Resume|
|Agent Safety|写入、Undo、Rollback 已有 HITL 与版本安全|增加 trusted/unknown 分类、Tool 前策略检查、未登记文件阻断和安全 Trace|
|Trace|记录 Tool、步骤、重试、耗时和结果摘要|增加 Retrieval、Workflow、Token、Cost 统计及固定评估成功率/错误率|
|Frontend|文件区域、聊天、确认和消息内证据|三栏 Document Workspace / Agent Chat / Evidence Preview，并增加 Task Center|

## 7. Known Issues

1. **真实 OCR 质量尚未人工验收。** 自动测试使用确定性 OCR Adapter 验证 Pipeline、字段和失败恢复；生产环境仍依赖 PyMuPDF、numpy 和 `rapidocr-onnxruntime` 的实际安装、模型初始化与扫描件质量。
2. **默认向量能力不是外部语义模型。** 当前默认 `LocalHashEmbeddingProvider` 是本地确定性特征哈希；测试通过可替换 Provider 验证语义通路，但真实复杂语义召回质量需要接入实际 Embedding Provider 后评估。
3. **真实 LLM 稳定性为 Not Tested。** Workflow 和 Demo 链路使用离线 Replay/Fake Client 验证 Tool Calling、参数 Schema、Python 结果和安全边界；真实模型的 Tool 选择与自然语言质量仍需人工 Demo。
4. **前端截图级体验为 Not Tested。** Streamlit `AppTest` 已验证组件结构和状态映射，但未执行不同分辨率下的浏览器视觉、拖拽手感和长列表性能验收。
5. **Task Center 为当前浏览器会话视图。** 后端现有 API 支持按 task_id 查询，没有全局任务列表接口；前端因此只展示当前会话已知的 OCR、索引和 Workflow Task。
6. **部分文件详情取决于后端实际元数据。** 当 API 未提供 PDF 页数/OCR/Layout/Block/Chunk 或 Word Diff 状态时，前端显示“暂无数据”，不会推测或伪造。
7. **Cost 依赖显式费率。** 未设置 `LLM_INPUT_COST_PER_1M` 和 `LLM_OUTPUT_COST_PER_1M` 时，Token 仍记录，Cost 为 0 且标记 `pricing_configured=false`。
8. **Async Task 是轻量本地运行时。** 当前采用 SQLite 持久化和进程内执行器，不是多节点分布式消息队列；符合本阶段“不引入复杂消息队列”的范围。

## 8. Conclusion

V3 已达到自动化 Demo 展示要求。当前代码能够演示从文件工作区上传、生命周期处理、普通/扫描 PDF 分流、Layout Block、Hybrid Retrieval、Evidence 精确定位，到组合 Workflow、HITL 安全写入、异步任务和 Trace Evaluation 的完整链路。

本次完整回归为 `237 passed / 0 failed`。V2 Demo B 记录的组合 Task Step/Trace 完整性问题，已由 V3 Workflow 的 12-Step 取消/确认测试验证解决；V2 的结构化查询、文件写入确认、版本安全和旧 chunk/Evidence 兼容能力继续保留。

最终验收建议分为两层：

- **自动化验收：通过。** 当前所有 237 项测试通过。
- **现场 Demo 验收：待人工复核。** 建议重点观察真实 OCR 效果、真实 LLM Tool 选择、Streamlit 三栏视觉体验以及当前会话 Task Center 的展示边界。
