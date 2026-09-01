# Student Document Agent V5 Architecture Audit

审计日期：2026-09-01
阶段：V5 Phase 0（架构审计，不开发功能）
分支：`feature/v5-development`
冻结基线：`v4-final-stable`
基线提交：`0554b279f5c2255d4f8fec64fd7b4c10915f0bc4`

## 1. 审计结论

V4 已形成一个稳定的单执行体系：`DocumentAgentCore` 是通用文档能力的薄 façade，
`StudentDomainAdapter` 复用 Core 并保留学生域能力，`DomainRouter` 决定有效领域和 Tool
allow-list。二者底层共享文件生命周期、解析与索引、Workflow、Async Task、Safety、Trace、
Evidence 和 Evaluation，不是两套平行 Agent。

V5 应继续增量扩展：新格式进入既有 Document 生命周期和 Block/Chunk 索引链路；Web 通过新的
Source Router 和安全适配器接入；Evidence 4.0 扩展而不替换 Evidence 2.0/3.0；跨来源检索复用
V4 Agentic Retrieval 的 Need、Sufficiency、预算和停止条件。

本阶段未开发 Web Search、PPT Parser 或 Agent 能力，未修改业务代码、数据库、前端和依赖。

## 2. 当前代码结构与核心模块地图

```text
student_document_agent/
├── backend/
│   ├── main.py                     # FastAPI HTTP/API 边界
│   ├── agent.py                    # Agent Tool Calling 主循环
│   ├── llm_client.py               # OpenAI-compatible LLM 客户端
│   ├── schemas.py                  # API 请求/响应模型
│   ├── tool_models.py              # Tool 严格参数模型
│   ├── tool_registry.py            # Tool 单一注册表
│   ├── document_blocks.py          # DocumentBlock/Chunk 转换
│   ├── table_structure.py          # Table/Row/Column/Cell
│   ├── evidence.py                 # Evidence 2.0/3.0 canonical model
│   ├── database.py                 # 持久化兼容 façade
│   ├── migrations.py               # SQLite migration
│   ├── repositories/               # File/Document/OCR/Evidence/Task/Trace repositories
│   ├── runtime/                    # Planner/Workflow/Async/Safety/Context/Retry
│   ├── services/                   # 路由、解析、检索、视觉、安全、追踪服务
│   └── tools/                      # Agent 可调用的确定性 Python Tools
├── frontend/
│   ├── app.py                      # Streamlit 入口
│   ├── api_client.py               # 后端 API 客户端
│   └── components/                 # Chat/Workspace/Evidence/Task/Trace UI
├── evals/                          # 固定数据集、Manifest、runner、结果快照
├── tests/
│   ├── evals/                      # Evaluation contract tests
│   ├── fixtures/                   # 固定测试样本描述
│   └── test_*.py                   # Unit/Integration/Regression tests
├── scripts/                        # 测试数据脚本
├── data/                           # 系统材料与 uploads
├── docs/                           # 版本日志、审计和验收报告
├── requirements.txt
└── README.md
```

仓库没有独立的 `agent/`、`workflow/`、`document/`、`retrieval/` 顶层目录，其逻辑职责如下：

| 逻辑模块 | 实际位置 | 职责 |
| --- | --- | --- |
| Agent | `backend/agent.py`; `services/document_agent_core.py`; `student_domain_adapter.py`; `domain_router.py` | LLM 循环、Core/Adapter、领域识别和 Tool 权限 |
| Tool | `backend/tool_registry.py`; `tool_models.py`; `backend/tools/` | Tool Schema、注册、确定性解析/查询/计算 |
| Workflow | `backend/runtime/planner.py`; `task_runner.py`; `workflow_condition.py`; `async_task_runtime.py` | Plan、节点依赖、条件、恢复、长任务 |
| Document | `document_blocks.py`; `table_structure.py`; `services/input_router.py`; `document_index.py`; visual/OCR services | 输入路由、解析、Block/Table/Cell、Chunk 和索引 |
| Retrieval | `services/hybrid_retrieval.py`; `embedding_service.py`; `agentic_retrieval.py` | 本地关键词/向量/融合/重排和受控补检索 |
| Evidence | `backend/evidence.py`; `services/evidence_locator.py`; `repositories/evidence_repository.py` | 来源 envelope、持久化和原文定位 |
| Safety | `runtime/safety_policy.py`; `services/visual_safety.py`; `services/confirmation.py` | 信任、风险、审批和不可信视觉数据 |
| Evaluation | `services/evaluation_service.py`; `evals/`; `tests/evals/` | Trace 汇总、固定评估、Manifest 和验收 |

### 2.1 V4 主链路

```text
Upload/API
  -> file_upload validation
  -> input_router: STRUCTURED / TEXT / VISUAL / SAFE_REJECT
  -> file_lifecycle
  -> document_index / OCR / visual services
  -> DocumentBlock/Table/Cell -> document_chunks
  -> hybrid_retrieval -> Evidence 2.0/3.0 -> evidence_locator

User message
  -> domain_router
  -> agent Tool Calling
  -> tool_registry + safety_policy
  -> DocumentAgentCore / StudentDomainAdapter
  -> optional controlled agentic retrieval
  -> answer + Evidence + Trace
```

索引候选构建和激活使用现有事务边界。Async Task 在不可安全中断的激活阶段记录 atomic section，
结束后再响应取消。V5 新格式必须复用这条链路，不能建立平行文件或索引系统。

## 3. V4 能力映射

| 能力 | 文件路径 | 核心类/函数 | 测试位置 |
| --- | --- | --- | --- |
| Document Router | `backend/services/input_router.py`; `file_upload.py`; `document_index.py` | `route_document_input()`、`route_pdf_pages()`、`validate_declared_mime()`、`save_uploaded_file()` | `tests/test_visual_document_router_v4.py`; `test_upload.py`; `test_document_workspace.py` |
| Parser / Index | `backend/services/document_index.py`; `document_blocks.py`; `table_structure.py` | `index_document()`、`parse_pdf()`、`_word_chunks()`、`_persist_index()`、`DocumentBlock` | `test_document_rag.py`; `test_layout_blocks.py`; `test_file_lifecycle_v3.py` |
| Visual Pipeline | `services/document_index.py`; `visual_understanding.py`; `visual_table.py` | `_index_image()`、`_parse_ocr_pdf()`、`VisualBlock`、`extract_visual_blocks()`、`extract_visual_tables()` | `test_visual_ocr_pipeline_v4.py`; `test_visual_understanding_v4.py`; `test_visual_table_v4.py` |
| OCR / Handwriting | `services/ocr_service.py`; `services/document_index.py`; `repositories/ocr_repository.py` | `OCRPageResult`、`OCRRegionResult`、`ocr_visual()`、`ocr_image()`、`normalize_region_record()`、`retry_visual_regions()` | `test_ocr_pipeline.py`; `test_handwriting_recognition_v4.py` |
| Evidence | `backend/evidence.py`; `services/evidence_locator.py`; `repositories/evidence_repository.py` | `Evidence`、`build_evidence()`、`serialize_evidence()`、`is_evidence_locatable()`、`locate_evidence()` | `test_evidence_v3.py`; `test_evidence_v4.py`; `test_evidence_location_v3.py`; `test_hybrid_evidence.py` |
| Retrieval | `services/document_index.py`; `hybrid_retrieval.py`; `embedding_service.py` | `retrieve_document()`、`hybrid_retrieve()`、`LocalReranker` | `test_document_rag.py`; `test_rag_v3.py`; `test_hybrid_evidence.py` |
| Agentic Retrieval | `services/agentic_retrieval.py`; `document_agent_core.py` | `InformationNeed`、`RetrievalBudget`、`evaluate_evidence_sufficiency()`、`run_controlled_retrieval()` | `test_agentic_retrieval_v4.py` |
| Agent Runtime | `backend/agent.py`; `llm_client.py`; `runtime/context_manager.py` | `run_agent()`、`_run_agent_core()`、`_execute_tool()`、`LLMClient` | `test_agent.py`; `test_trace_context.py`; `test_domain_router_v4.py` |
| General Core / Domain | `services/document_agent_core.py`; `student_domain_adapter.py`; `domain_router.py` | `DocumentAgentCore`、`StudentDomainAdapter`、`route_domain()`、`allowed_tool_names()` | `test_document_agent_core_v4.py`; `test_domain_router_v4.py` |
| Workflow | `runtime/planner.py`; `task_runner.py`; `workflow_condition.py` | `TaskPlan`、`start_task()`、`execute_workflow()`、`resume_task()` | `test_workflow_v3.py`; `test_workflow_runtime_v3.py`; `test_runtime.py` |
| Async Task | `runtime/async_task_runtime.py`; `services/task_view.py`; `repositories/task_repository.py` | `AsyncTaskStatus`、`AsyncTaskContext`、`enqueue_async_task()`、`run_async_task()`、`retry_async_task()` | `test_async_task_runtime.py`; `test_async_task_runtime_v3.py` |
| Tool System | `backend/tool_registry.py`; `tool_models.py`; `tools/`; `runtime/tool_executor.py` | `ToolSpec`、`ToolRegistry`、`TOOL_REGISTRY`、`execute_with_retry()` | `test_tools.py`; `test_table_tools.py`; `test_schema_discovery.py` |
| Safety / HITL | `runtime/safety_policy.py`; `services/visual_safety.py`; `confirmation.py` | `SafetyAssessment`、`assess_tool_execution()`、`assess_high_risk_action()`、Approval binding | `test_agent_safety.py`; `test_agent_safety_v3.py`; `test_visual_safety_trace_v4.py`; `test_confirmation.py` |
| Trace | `services/trace_service.py`; `repositories/trace_repository.py` | `record_trace()`、`llm_usage_metrics()`、各服务 metrics | `test_trace_context.py`; `tests/evals/test_trace_evaluation_v3.py` |
| Evaluation | `services/evaluation_service.py`; `evals/`; `tests/evals/` | `evaluate_task()`、`evaluate_session()`、V3/V4 runners | `tests/evals/test_evaluation.py`; `test_v3_final_evaluation.py`; `test_v4_final_evaluation.py` |

## 4. V5 接入点与影响分析

### 4.1 PPT/PPTX/TXT/JSON/CSV/Markdown

所有新格式应依次穿过以下现有边界：

1. `input_router.py`：suffix/MIME/内容识别与 STRUCTURED/TEXT/VISUAL/SAFE_REJECT 路由。
2. `file_upload.py`：安全文件名、内容校验、注册类型和处理分派。
3. `file_tools.py`：可发现 suffix 与稳定 `file_type`。
4. `document_index.py`：parser dispatch，并统一转换成 DocumentBlock/Chunk，复用原子激活。
5. `tool_models.py`：扩展 `RetrievalScope.file_type`；现有 `slide` 字段可用于 PPT 定位。
6. `async_task_runtime.py`：允许新类型进入统一 index/reindex handler。
7. `evidence_locator.py`：支持 slide、line range、JSON Pointer、row/cell 等 locator。
8. `file_view.py`：后续增加有界后端预览；前端改动必须放到独立阶段。

建议 parser 作为窄 service 新增，由 `document_index.py` 统一调度，不为每种格式注册 Agent Tool：

| 格式 | 建议输出 | 定位 | 主要边界 |
| --- | --- | --- | --- |
| PPTX | slide title/text/table/image blocks | slide + block，必要时 bbox | zip bomb、隐藏内容、notes；当前依赖无 `python-pptx` |
| PPT | 独立识别；无可靠 parser 时解释性拒绝 | slide | 老二进制解析器/转换器安全和部署可用性 |
| TXT | 编码后按 line/paragraph 生成 blocks | line range + block | 编码、NUL、binary masquerade、长行/大文件 |
| JSON | 有界 object/array records | JSON Pointer + index | 深度/宽度炸弹、重复 key、巨型值 |
| CSV | schema/header/row/cell | row + column/cell | dialect、编码、超宽文件、formula injection |
| Markdown (P1) | heading/list/code/table blocks | heading path + line range | HTML/URL/代码块均按数据处理，不执行 |

PPT/PPTX 开发前应单独完成依赖和安全评审；不能为了新 parser 顺带升级既有依赖。

### 4.2 Web Retrieval

Web 不应写入现有 `hybrid_retrieval.py`。建议建立下列独立边界，再由统一 Source Router 编排：

| 建议模块 | 职责 |
| --- | --- |
| `services/source_router.py` | 决定 `local`、`web`、`direct_url`、`local_web`，冻结 scope 和预算 |
| `services/web_search.py` | Search provider port/adapter 和结果规范化 |
| `services/url_fetch.py` | 仅对通过安全策略的 HTTP(S) URL 做有界抓取和正文提取 |
| `services/web_safety.py` | URL/SSRF 策略、untrusted envelope、外部内容风险判断 |
| `services/web_evidence.py` | Search/fetch fragment 到 Evidence 4.0 的适配 |
| `services/cross_source_retrieval.py` | 将 Local/Web 适配为 V4.9 的统一 retriever contract |
| `tools/web_tools.py` | 经安全评审后暴露最少的只读 Search/Fetch handler，绝不暴露通用 HTTP client |

Source Router 语义：

- `local`：只查本地。
- `web`：显式外部/时效性任务使用 Web；不能由网页内容自行选择。
- `direct_url`：只抓用户明确给出的 URL，但仍不视为可信来源。
- `local_web`：共享全局预算；local-first 时只有 Evidence Sufficiency 明确不足才升级 Web。

未配置 provider 时必须安全失败或保持本地路径，旧 `retrieve_document` 绝不能偷偷联网。

### 4.3 Evidence 4.0 兼容策略

现有 `Evidence` 为 `extra="forbid"`，版本只接受 2.0/3.0，`build_evidence()` 对传统 Excel、文本
PDF 和 Word 有意输出 legacy key projection。因此 Evidence 4.0 必须显式扩展，不能随意塞字段：

1. 保留 2.0/3.0 读写，新增 4.0。
2. 保留 `source_type=structured|unstructured` 的内容语义，新增正交的
   `source_origin=local|web|url`。
3. Web/URL 可选字段包括 canonical URL、title、publisher/domain、query/result rank、provider、
   retrieved/published time、content hash、fragment locator、freshness 和 trust assessment。
4. V1-V4 本地路径继续产生原 Evidence 形状，legacy projection 不变。
5. `is_evidence_locatable()` 按 origin 分支；Web 至少需要 URL、抓取 hash 和 fragment。
6. locator 采用 adapter 分发；本地 locator 原样保留，前端不能通过 Evidence URL 触发服务端任意抓取。
7. 去重键使用 origin + file_id/canonical URL + content hash + locator，不只依赖 excerpt。

现有 Evidence repository 保存 JSON envelope；是否需要 schema migration 必须在实现 Phase 验证。
Phase 0 不修改数据库，也不预设必须迁移。

### 4.4 Cross-Source Agentic Retrieval

V4 `run_controlled_retrieval()` 已支持注入 `RetrievalCallable`，具备 Need、Sufficiency、query rewrite、
Evidence 去重、冲突/低置信度状态、轮次/调用/时间预算以及 sufficient、scope_exhausted、
no_new_evidence、budget、error 停止条件。

V5 应让 `cross_source_retrieval` 实现同一 callable contract，不复制循环。需要 additive 扩展：

- Need/scope 增加 source policy、freshness、domain allow-list 等可选约束，默认仍为 local。
- 使用全局预算和 per-source 上限；Search、Fetch、redirect 都必须计数。
- attempt Trace 增加 origin、provider、URL/fetch count 和 safety result，旧字段与 stop reason 保持。
- sufficiency 增加时效性、来源多样性和 Web trust 判断。
- local-first 只能根据未满足 Need 升级，网页内容不能扩 scope、提预算或决定下一 URL。
- “没有检索到”继续不能表达为事实不存在。

## 5. Web 安全约束与风险

| 风险 | 级别 | 控制措施 |
| --- | --- | --- |
| SSRF、内网和 metadata 访问 | P0 | 只允许 HTTP(S)；阻断 loopback/private/link-local/multicast/reserved/metadata；DNS 与连接双检；每跳 redirect 重验 |
| DNS rebinding | P0 | 连接目标绑定已验证解析结果，或在 transport 层重复校验，不能只检查 URL 字符串 |
| Web prompt injection | P0 | 搜索摘要、正文、metadata、JSON-LD、伪 Tool JSON 均为 `untrusted_web_data`、authority none |
| 内容触发 Tool/Approval | P0 | 外部数据 `can_trigger_tool=false`、`can_approve=false`；Tool/审批只接受可信控制状态 |
| Scope expansion | P0 | source mode、domain、URL、预算由用户请求与控制层冻结，页面不能改变 |
| 资源耗尽 | P0 | 限协议/端口/方法/redirect/时间/字节/MIME/解压后大小/解析深度 |
| 凭证和隐私泄露 | P0 | 禁止凭证 URL；header allow-list；URL query redaction；正文和密钥不进入普通 Trace |
| Evidence 兼容破坏 | P0 | 2.0/3.0 fixture 与精确 shape 回归；4.0 使用新 origin 分支 |
| Web 事实冲突和过期 | P1 | published/retrieved 分离、content hash、freshness、conflict 状态和多源 sufficiency |
| 双来源成本失控 | P1 | 全局/per-source 硬预算、停止条件和 Trace call count |
| PPT/JSON/CSV parser bomb | P0 | archive ratio、条目、深度、行列、文件大小和解析时间上限 |
| CSV formula injection | P0 | 输入仅作为数据；显示或导出时转义，不执行公式 |

外部内容的固定安全语义应为：`instruction_authority=none`、`approval_authority=none`、
`can_trigger_tool=false`、`can_change_tool_risk=false`、`can_approve=false`。

## 6. 测试体系与回归策略

项目使用 pytest，但没有将所有测试按 marker 严格互斥分类；同一文件可能同时覆盖 unit、integration
和 regression。

| 类型 | 位置与示例 |
| --- | --- |
| Unit | `tests/test_tools.py`; `test_table_tools.py`; `test_schema_discovery.py`; `test_evidence_v*.py`; visual/OCR service tests |
| Integration | `test_api.py`; `test_upload.py`; `test_document_workspace.py`; `test_document_rag.py`; `test_agent.py`; Workflow/Async tests |
| Evaluation | `tests/evals/`; `evals/run_v3_final_evaluation.py`; `run_v4_final_evaluation.py`; datasets/manifests/results |
| Regression | 全量 `pytest -q`; `evals/v1_regression.txt`; V2/V3/V4 旧测试与 Manifest |

保持 410 项基线的规则：

1. 每个 Phase 前后执行完整 `pytest -q`，不删除、跳过或弱化旧测试。
2. 新格式使用 additive dispatch，旧 suffix/MIME/file_type/route/Tool 名称和默认行为不变。
3. Pydantic 扩展使用有默认值的可选字段，精确序列化契约保持。
4. Evidence 2.0/3.0 legacy projection 和本地 locator 不改。
5. Web transport/provider/parser/clock 使用 port/adapter 注入；测试完全离线、可重复。
6. 新模块先做 unit 安全测试，再做 integration，最终由 V5 Evaluation 汇总。
7. 未配置 Web 时旧本地查询必须证明零网络调用。

## 7. V5 开发路线

### Phase 0：Architecture Audit（当前阶段）

- 目标：冻结基线，理解代码与测试，形成模块地图、影响、风险和路线。
- 涉及文件：仅 `docs/V5_ARCHITECTURE_AUDIT.md`。
- 测试：branch/HEAD/tag/worktree；完整 pytest。
- 完成标准：无业务/DB/前端/依赖变更，410 tests passed。

### Phase 1：兼容模型与扩展点

- 目标：定义格式、source mode、Web result、URL policy、Evidence 4.0 contract 和 adapter protocol；
  不联网、不实现 PPT parser、不注册新 Agent Tool。
- 预计文件：`tool_models.py`; `evidence.py`; 新 contract 模块；必要的 Block 可选 locator。
- 测试：Schema 正负例、Evidence 2/3 精确兼容、source 默认 local、非法 URL contract、全量回归。
- 完成标准：扩展全部 additive，无网络副作用，无数据库/前端改动。

### Phase 2：新文档格式

拆为 2A TXT/JSON/CSV、2B PPTX、2C PPT 可行性/实现、2D Markdown P1。

- 目标：进入统一上传、路由、生命周期、Block/Chunk、原子索引、本地检索和 Evidence。
- 预计文件：router/upload/file tools/document index、新 parser、scope、Async、locator、后端 preview。
- 测试：有效/损坏/伪装/超限/编码/部分失败、定位、reindex、Async retry、旧格式回归。
- 完成标准：不建平行系统；错误不产生半激活索引；真实 parser 输出可定位。

### Phase 3：Web Retrieval 基础设施

拆为 3A URL/SSRF policy、3B Direct URL fixed transport、3C Search adapter、3D Source Router。

- 目标：受控、可注入、可审计的 Search/Fetch；无 provider 时安全失败且不影响本地。
- 预计文件：新 Source/Web/URL/Safety services；Tool models；runtime safety；Trace；评审后才注册 Tool。
- 测试：fake DNS/transport/provider；协议/IP/IPv6/redirect/rebinding/超时/大小/MIME/凭证/redaction。
- 完成标准：固定 SSRF 集全部阻断；所有调用受预算并有 Trace；无通用 HTTP Tool。

### Phase 4：Evidence 4.0

- 目标：统一 Local/Web/URL Evidence，同时完整兼容 2.0/3.0。
- 预计文件：`evidence.py`; Web adapter；locator；repository；必要时单独提出 migration。
- 测试：2/3/4 round-trip、legacy projection、locator、canonical URL、hash/fragment 去重、redaction。
- 完成标准：旧 consumer 无需修改；Web 结论定位到 URL/时间/片段或明确不可重开。

### Phase 5：Cross-Source Agentic Retrieval

- 目标：复用 V4.9，在不足 Need 上受控调用 Web，支持四种 source mode 和共享预算。
- 预计文件：cross-source/source router；Agentic Retrieval additive hooks；Core/Domain Router。
- 测试：local sufficient 零联网、只检索 unmet Need、四 mode、scope 冻结、预算/停止/冲突/Trace。
- 完成标准：不复制循环；简单查询仍 bypass；不绕过 Workflow、Safety、Approval。

### Phase 6：Web Safety Hardening

- 目标：端到端覆盖 Web injection、伪 Tool/Approval、恶意 redirect、scope expansion 和高影响写入。
- 预计文件：web/runtime safety；confirmation；Trace；Agent untrusted envelope；安全 fixtures。
- 测试：网页/摘要/JSON-LD/伪 confirmed/诱导 URL/SSRF variants/冲突 Evidence/高影响写入。
- 完成标准：外部内容不能触发 Tool、提权、批准、扩 scope；关键攻击全部 fail closed。

### Phase 7：V5 Evaluation 与最终验收

- 目标：建立虚构固定数据集、Manifest、Demo、指标、覆盖审计和最终验收。
- 预计文件：`evals/v5_*`; `tests/evals/test_v5_final_evaluation.py`; V5 日志/矩阵/报告。
- 测试：新格式、Web/URL Evidence、source routing、cross-source、unnecessary web、SSRF/injection、
  latency、各类 call count、Token/Cost 真实语义和完整回归。
- 完成标准：指标来自真实执行和明确分母；离线结果不外推；旧 410 项及 V5 新测试全部通过；
  真实 provider/人工项未完成时结论保持 PARTIAL。

## 8. Phase 0 验证记录

- 初始工作区：干净。
- 业务代码、数据库、前端、依赖变更：无。
- 基础 shell 执行 `pytest -q`：`pytest: command not found`，未开始测试收集；项目既有 Conda
  环境执行 `conda run -n tuli_env pytest -q`：`410 passed in 28.87s`。
- 最终 `git status --short`：仅 `?? docs/V5_ARCHITECTURE_AUDIT.md`。
