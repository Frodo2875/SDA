# V5 稳定模块地图

审计日期：2026-09-14。阶段：V6 Phase 0，仅文档审计。

实际仓库：`student_document_agent/`，以下路径均相对此仓库。
分支：`feature/v6-development`。
冻结提交：`36ccc19aac056b337747f7c40e0674809b2b41b6`（`v5-finals`）。
`refs/heads/v5-final-stable` 与 `refs/tags/v5-final-stable^{}` 均解析到该提交。
本次全量回归：**478 passed，0 failed，0 skipped，38.80 秒**。

## 策略定义

- REUSE：直接调用现有能力，保留契约。
- EXTEND：未来获准阶段只增量扩展列明的入口或适配点，不重写底层体系。
- WRAP：由 V6 外层服务组织现有能力，底层算法、状态和安全契约保持稳定。
- DO_NOT_TOUCH：冻结实现及契约，只允许调用和回归验证。

策略描述未来 V6 的设计方向，不是本阶段修改许可。本阶段所有业务模块均不得修改。
同一文件承担多种职责时，以具体函数为边界；例如扩展 Parser 分派不意味着可以修改 Retriever。

## 模块地图

| 模块 | 文件路径 | 核心类/函数 | 当前作用 | V6策略 |
| --- | --- | --- | --- | --- |
| Upload / 输入路由 | `backend/services/file_upload.py`；`backend/services/input_router.py`；`backend/tools/file_tools.py` | `save_uploaded_file`、`route_document_input`、`SUPPORTED_UPLOADS`、`FILE_TYPES` | 后缀、MIME、内容验证、安全文件名与文件注册；区分结构化、文本、视觉输入 | EXTEND |
| Document Pipeline / 生命周期 | `backend/services/file_lifecycle.py`；`backend/repositories/file_repository.py` | `process_uploaded_file`、`transition_file_lifecycle`、`resume_file_lifecycle` | 持久化状态转换、失败状态和恢复，统一文件身份 | REUSE |
| Parser | `backend/services/multiformat_parser.py`；`backend/services/document_index.py` | `ParsedDocument`、`parse_local_document`、`parse_txt`、`parse_json`、`parse_presentation`、`parse_pdf`、`_word_chunks` | 多格式解析；CSV 使用 `parse_csv_table` 进入结构化查询链路 | EXTEND |
| Block / Chunk | `backend/document_blocks.py`；`backend/table_structure.py` | `DocumentBlock`、`make_document_block`、`blocks_to_chunks` | 稳定 Block/Chunk ID，文本分块及表格定位；元数据随 Chunk 保存 | REUSE |
| Index 分派 | `backend/services/document_index.py` | `index_document`、`reindex_document`、`reprocess_document`、`_index_document` | 统一解析分派、构建候选索引与重建入口 | EXTEND |
| Index 原子激活 | `backend/services/document_index.py`；`backend/repositories/document_repository.py` | `_persist_index`、`_begin_candidate_build`、`_validate_candidate_chunks`、`DocumentRepository` | 校验候选索引、事务切换，失败时保护已有活动索引 | DO_NOT_TOUCH |
| OCR / 图片理解 | `backend/services/ocr_service.py`；`backend/services/visual_understanding.py`；`backend/services/visual_table.py`；`backend/services/visual_safety.py` | `ocr_visual`、`extract_visual_blocks`、`extract_visual_tables`、`assess_visual_high_impact_write` | OCR、视觉块、视觉表格及低置信度高影响写入保护 | DO_NOT_TOUCH |
| Local Retriever | `backend/services/document_index.py`；`backend/services/hybrid_retrieval.py`；`backend/services/embedding_service.py` | `retrieve_document`、`hybrid_retrieve`、`LocalReranker` | 本地关键词/向量融合、重排、范围过滤及 Evidence 构造调用 | DO_NOT_TOUCH |
| V4 受控检索 | `backend/services/agentic_retrieval.py` | `run_controlled_retrieval`、`evaluate_evidence_sufficiency` | 本地 Information Need、补检索、冲突/置信度与停止条件 | REUSE |
| Agent Runtime | `backend/agent.py`；`backend/runtime/context_manager.py` | `run_agent`、`_run_agent_core`、`_execute_tool` | 对话、Tool Calling、上下文、结果与证据收集 | WRAP |
| General Core / Domain | `backend/services/document_agent_core.py`；`backend/services/student_domain_adapter.py`；`backend/services/domain_router.py` | `DocumentAgentCore`、`StudentDomainAdapter`、`route_domain` | 通用能力 façade 与学生域适配，共用底层执行和文档体系 | REUSE |
| Source Router | `backend/services/source_router.py` | `SourceStrategy`、`SourceRoute`、`route_source` | LOCAL / WEB / BOTH / DIRECT_URL 来源决策 | DO_NOT_TOUCH |
| Tool Boundary | `backend/services/tool_scope_resolver.py`；`backend/tool_registry.py`；`backend/runtime/safety_policy.py`；`backend/agent.py` | `resolve_source_tool_scope`、`source_tool_violation`、`ToolRegistry`、`assess_tool_execution`、`_execute_tool` | 来源和领域权限在工具执行前校验，参数校验及高风险审批约束 | DO_NOT_TOUCH |
| Cross Source Retrieval | `backend/services/cross_source_retrieval.py` | `run_cross_source_retrieval`、`plan_sources`、`merge_unified_evidence` | 在来源计划及轮次/调用/时间预算下跨源补检索 | WRAP |
| V5 Evidence Sufficiency | `backend/services/cross_source_retrieval.py` | `evaluate_evidence_sufficiency`、`CrossSourceInformationNeed` | 检查证据数量、显式字段和 required terms，决定是否需要补检索 | REUSE |
| UnifiedEvidenceFactory | `backend/evidence.py` | `UnifiedEvidenceFactory.local`、`.web`、`UNIFIED_EVIDENCE_FACTORY` | 本地/Web/URL 唯一规范化创建入口，统一不可信来源语义 | DO_NOT_TOUCH |
| Evidence Schema / 兼容序列化 | `backend/evidence.py` | `Evidence`、`UnifiedEvidence`、`serialize_evidence3_compat`、`serialize_unified_evidence` | Evidence 2/3/4 模型、旧响应投影、定位持久化 | DO_NOT_TOUCH |
| Evidence 定位 | `backend/services/evidence_locator.py`；`backend/repositories/evidence_repository.py` | `locate_evidence`、`_locate_document`、`EvidenceRepository` | 已保存本地 Evidence ID 对应活动 Chunk/Block/页/单元格预览 | REUSE |
| Web Retrieval | `backend/services/web_retrieval.py`；`backend/tools/web_tools.py` | `WebRetrievalService.retrieve`、`configure_search_provider` | 搜索/直接 URL 分派、规范化结果、统一 Evidence 与失败返回 | WRAP |
| Tavily Provider | `backend/services/tavily_search_provider.py`；`backend/services/search_provider.py`；`backend/services/search_provider_factory.py` | `TavilySearchProvider.search`、`_post`、`SearchProviderError`、`build_search_provider_from_env` | 单个真实搜索 Provider；已映射限流、鉴权、超时和无效响应 | EXTEND |
| Web Safety / SSRF / Direct URL | `backend/services/web_safety.py`；`backend/services/url_fetch.py`；`backend/services/web_page_parser.py`；`backend/services/redaction.py` | `assess_web_content`、`web_security_metadata`、`URLFetcher`、`PinnedHTTPTransport`、`validate_public_url` | 不可信网页标记、URL/DNS/IP/重定向校验、有界正文提取及脱敏 | DO_NOT_TOUCH |
| Workflow Plan | `backend/runtime/planner.py` | `TaskPlan`、`PlannedStep`、`create_plan` | 依赖节点、条件/审批节点模型；当前计划识别以奖学金与 Word 写入为主 | WRAP |
| Workflow Runtime | `backend/runtime/task_runner.py`；`backend/runtime/workflow_condition.py` | `start_task`、`execute_workflow`、`resume_task`、`_save_node_result` | 依赖波次执行、节点结果 checkpoint、输入版本失效传播和审批等待 | WRAP |
| Async Task Runtime | `backend/runtime/async_task_runtime.py`；`backend/schemas.py` | `AsyncTaskContext`、`enqueue_async_task`、`run_async_task`、`_validated_payload`、`_default_executor` | SQLite 队列和统一任务状态；现有 OCR/layout/index/reindex/batch/workflow handler | EXTEND |
| Queue 持久化 | `backend/repositories/task_repository.py` | `TaskRepository.claim_async` | 事务领取任务、保存任务与步骤；不能据此认定已有崩溃后自动接管 | REUSE |
| Retry / Resume | `backend/runtime/policy.py`；`backend/runtime/tool_executor.py`；`backend/runtime/async_task_runtime.py` | `execute_with_retry`、`retry_async_task`、`resume_async_task` | 有界只读重试；失败单元重试与保留 checkpoint 恢复的语义不同 | WRAP |
| Trace | `backend/services/trace_service.py`；`backend/repositories/trace_repository.py` | `record_trace`、`llm_usage_metrics`、`_web_observability_metrics` | 脱敏、时长、重试、状态、Token 与自定义 metrics | REUSE |
| Report Writer / Word Writer | `backend/tools/word_tools.py` | `read_word`、`write_word` | 安全追加到已有 DOCX；没有独立 Evidence Report 模板、引用校验或新建报告服务 | WRAP |
| Diff | `backend/services/file_versioning.py` | `WordDiffOperation`、`preview_word_diff` | append/undo/rollback 确定性预览，冻结文件 expected_hash | DO_NOT_TOUCH |
| Approval | `backend/services/confirmation.py`；`backend/runtime/safety_policy.py` | `create_pending_action`、`confirm_action`、`cancel_action`、`_approval_binding_error` | 冻结具体操作、校验审批绑定、阻止重复执行并同步任务状态 | DO_NOT_TOUCH |
| Version | `backend/services/file_versioning.py`；`backend/repositories/version_repository.py` | `execute_versioned_word_action`、`list_versions`、`VersionRepository` | 写前/写后快照、新版本事务与失败补偿 | DO_NOT_TOUCH |
| Rollback / Undo | `backend/services/confirmation.py`；`backend/services/file_versioning.py` | `rollback_file`、`create_pending_undo_action`、`_apply_operation` | 经同一审批链恢复快照，并新增历史版本 | DO_NOT_TOUCH |
| Database Schema | `backend/migrations.py`；`backend/database.py` | migration 定义、持久化 façade | 既有文件、任务、Evidence、Trace、版本存储边界 | DO_NOT_TOUCH |
| Evaluation | `backend/services/evaluation_service.py`；`evals/`；`tests/evals/` | `evaluate_task`、`evaluate_session`、固定评估 runner | 固定数据与 Trace 验收；不能用历史结果代替本轮回归 | EXTEND |

## 冻结边界与验证入口

必须保护 Source Router、Tool Boundary、UnifiedEvidenceFactory、Evidence Schema、Web Safety/SSRF、Retriever、索引原子激活、Approval、Diff、Version、Rollback。
V6 外层服务仍必须经过这些边界；Quality Gate 通过不授予写入权限。

关键既有回归包括：

- 来源与检索：`tests/test_web_retrieval_v5.py`、`tests/test_cross_source_agentic_retrieval_v53.py`、`tests/test_document_rag.py`、`tests/test_rag_v3.py`。
- Evidence：`tests/test_unified_evidence_factory_v521.py`、`tests/test_unified_evidence_v52.py`、`tests/test_evidence_location_v3.py`。
- 安全：`tests/test_web_safety_v54.py`、`tests/test_agent_safety_v3.py`、`tests/test_visual_safety_trace_v4.py`。
- Runtime：`tests/test_workflow_runtime_v3.py`、`tests/test_async_task_runtime.py`、`tests/test_async_task_runtime_v3.py`。
- 交付：`tests/test_confirmation.py`、`tests/test_file_versioning.py`、`tests/test_tools.py`。

V6 方案及限制见 [架构审计](V6_ARCHITECTURE_AUDIT.md)，阶段验收见 [开发计划](V6_DEVELOPMENT_PLAN.md)。
