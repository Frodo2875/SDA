# V3 Requirement Test Traceability

## 1. 范围与规则

- 版本：V3 Final（V2.99-Final-v2 增量升级）
- 分支：`feature/v3-development`
- 机器可读映射：`evals/v3_requirement_traceability.json`
- 映射原则：只引用仓库中真实存在的 pytest 测试；一个测试同时验证多个契约时只做映射，不复制测试。
- 状态口径：下表 PASS 以 V3.22 全量 pytest 与正式分组测试的本次实际结果为准。

## 2. Document Workspace（F01-F12）

|ID|需求/验收点|真实测试映射|状态|
|-|-|-|-|
|F01|文件卡片与列表字段|`test_workspace_lists_file_cards_with_required_metadata`；`test_file_cards_filters_and_type_specific_details`|PASS|
|F02|类型、生命周期、索引、可查询状态展示|`test_file_cards_filters_and_type_specific_details`|PASS|
|F03|稳定 file_id 文件详情|`test_workspace_reads_file_details_by_stable_id`|PASS|
|F04|有界真实内容快速预览|`test_workspace_quick_preview_is_bounded_and_uses_real_content`|PASS|
|F05|文件名搜索|`test_workspace_searches_by_partial_file_name`|PASS|
|F06|类型/状态过滤及时间/大小排序|`test_workspace_filters_and_sorts_in_python`|PASS|
|F07|多文件上传结果逐项展示|`test_api_client_sends_workspace_filters_and_multi_upload_statuses`|PASS|
|F08|单文件上传失败不影响其他文件|`test_api_client_sends_workspace_filters_and_multi_upload_statuses`|PASS|
|F09|安全 reindex、失败保旧、成功原子切换|`test_f09_reindex_activation_failure_keeps_old_chunks_and_queryability`；`test_f09_successful_reindex_atomically_switches_without_mixing_or_duplicates`；`test_f09_f10_workspace_calls_safe_reprocess_and_reindex_apis`|PASS|
|F10|安全 reprocess、失败保旧、非法状态拒绝|`test_f10_reprocess_parse_failure_preserves_one_complete_active_generation`；`test_f10_successful_reprocess_activates_only_the_new_parsed_generation`；`test_f10_reindexing_state_rejects_illegal_reverse_transition`|PASS|
|F11|上传文件删除真实确认、取消无变化、确认后清理|`test_f11_cancel_delete_preserves_file_record_blocks_chunks_and_index`；`test_f11_confirm_delete_removes_uploaded_file_and_active_chunks`；`test_f11_workspace_delete_shows_target_and_can_be_cancelled`|PASS|
|F12|系统文件前端无入口且后端拒绝|`test_system_file_cannot_enter_delete_flow`；`test_f12_system_file_has_no_workspace_delete_entry`|PASS|

## 3. OCR、Layout 与 Table（O01-O10）

|ID|需求/验收点|真实测试映射|状态|
|-|-|-|-|
|O01|PDF text/scanned/mixed 分类|`test_o01_o04_detect_pdf_type_per_page`|PASS|
|O02|文本 PDF 不重复 OCR|`test_normal_pdf_keeps_embedded_text_pipeline`|PASS|
|O03|扫描 PDF 进入 OCR 并保留真实块元数据|`test_scanned_pdf_enters_ocr_and_preserves_real_block_metadata`|PASS|
|O04|Mixed PDF 逐页判断，仅 OCR 无文本页|`test_o04_mixed_pdf_only_ocrs_pages_without_text`|PASS|
|O05|页级 text/bbox/confidence/status/error 与 partial success|`test_ocr_service_returns_page_text_confidence_and_bbox`；`test_o05_o07_page_ledger_partial_success_and_failed_pages`|PASS|
|O06|title/paragraph/header/footer/image、parent/source parser|`test_title_is_persisted_as_document_block`；`test_paragraph_chunk_produces_block_referenced_evidence`；`test_o06_header_footer_and_image_blocks_use_existing_schema`|PASS|
|O07|简单二维 Table/Row/Column/Cell|`test_table_and_cells_are_distinct_blocks_and_generate_chunks`|PASS|
|O08|复杂表格安全降级且不伪造 Cell|`test_o08_merged_table_degrades_without_inventing_cells`|PASS|
|O09|低置信度 Evidence 保留 confidence 与核对提示|`test_o09_o10_low_confidence_evidence_warns_and_failed_page_is_insufficient`|PASS|
|O10|失败页依赖查询返回证据不足|`test_o09_o10_low_confidence_evidence_warns_and_failed_page_is_insufficient`|PASS|

说明：`test_o03_real_ocr_backend_smoke_is_optional` 仅在本机 OCR 可执行依赖可用时运行；它不是 P0 单元测试通过的前提，也不用于伪造 OCR 指标。

## 4. RAG 2.0 与 Evidence 2.0（R201-R210）

|ID|需求/验收点|真实测试映射|状态|
|-|-|-|-|
|R201|精确标识召回与 score contract|`test_r201_exact_identifier_and_scores_are_preserved`|PASS|
|R202|语义 Vector 召回|`test_semantic_query_uses_embedding_when_fts_has_no_phrase`|PASS|
|R203|年份、学生、文档元数据硬过滤|`test_r203_year_student_and_document_metadata_are_hard_filters`|PASS|
|R204|Retrieve Top-N → Rerank → Top-K，失败回退|`test_r204_retrieves_top_n_then_reranks_to_top_k`；`test_rerank_failure_falls_back_to_hybrid_order`|PASS|
|R205|多 Evidence 仅来自最终真实候选|`test_r205_multiple_evidence_contains_only_final_candidates`|PASS|
|R206|PDF page/block/bbox/confidence 定位|`test_r206_pdf_location_returns_correct_page_bbox_and_confidence`|PASS|
|R207|Excel sheet/row/field/cell 定位|`test_r207_excel_location_opens_exact_sheet_row_and_field`|PASS|
|R208|Word paragraph/block 定位|`test_r208_word_location_opens_indexed_paragraph`|PASS|
|R209|定位失败安全提示与 Trace；未使用来源不展示|`test_r209_location_failure_keeps_safe_message_and_records_trace`；`test_unused_retrieval_source_is_not_exposed_after_evaluation`|PASS|
|R210|无结果不编造 Evidence|`test_r210_no_result_returns_no_evidence`|PASS|

## 5. Workflow 与 Async（WF01-WF10）

|ID|需求/验收点|真实测试映射|状态|
|-|-|-|-|
|WF01|Sequential dependency gating|`test_wf01_sequential_dependency_gating`|PASS|
|WF02|真实 Parallel 重叠执行与 Aggregate 等待|`test_wf02_parallel_nodes_overlap_and_aggregate_waits`|PASS|
|WF03|Conditional 缺材料分支|`test_wf03_wf04_safe_conditional_branches[WF03-missing-branch]`|PASS|
|WF04|Conditional 资格判断分支|`test_wf03_wf04_safe_conditional_branches[WF04-eligibility-branch]`|PASS|
|WF05|Approval 阻断写入，Cancel 不执行|`test_wf05_approval_blocks_write_and_cancel_preserves_it`|PASS|
|WF06|有界 Retry 与失败持久化|`test_wf06_retry_is_bounded_and_persisted_failed`|PASS|
|WF07|Checkpoint/Resume 跳过成功节点及文件版本失效|`test_wf07_resume_skips_successful_nodes_and_never_replays_write`；`test_wf07_changed_file_version_invalidates_related_read_nodes`|PASS|
|WF08|OCR 异步执行与真实页级 Progress|`test_wf08_ocr_async_uses_real_page_progress`；`test_wf08_default_ocr_worker_uses_page_pipeline`|PASS|
|WF09|安全点 Cancel 与原子区保护|`test_wf09_running_cancel_waits_for_atomic_safe_point`|PASS|
|WF10|partial_success、失败页明细和仅失败页 Retry|`test_wf10_partial_success_retry_only_failed_pages`|PASS|

## 6. Agent Safety（S01-S10）

|ID|需求/验收点|真实测试映射|状态|
|-|-|-|-|
|S01|全部文档派生来源均为 untrusted data|`test_s01_all_document_derived_sources_are_untrusted`|PASS|
|S02|“忽略规则”RAG 注入无权限|`test_s02_ignore_rules_in_rag_chunk_cannot_gain_authority`|PASS|
|S03|文档删除指令不能触发删除|`test_s03_delete_instruction_in_document_cannot_call_delete`|PASS|
|S04|JSON Tool 载荷仍仅是数据|`test_s04_json_tool_payload_in_document_remains_data`|PASS|
|S05|OCR 写入指令不能触发写入|`test_s05_ocr_write_instruction_cannot_execute_write`|PASS|
|S06|LOW/MEDIUM/HIGH 风险矩阵确定性|`test_s06_tool_risk_matrix_is_deterministic`|PASS|
|S07|Schema Validation 先于 Tool Handler|`test_s07_schema_validation_precedes_tool_handler`|PASS|
|S08|HIGH Risk Approval Trigger Rate = 100%|`test_s08_high_risk_approval_trigger_rate_is_100_percent`|PASS|
|S09|Approval 绑定 task/approval/operation/target，不可复用|`test_s09_approval_binds_task_operation_target_and_rejects_reuse`|PASS|
|S10|Workflow 不可绕过 Policy，安全决策留 Trace|`test_s10_workflow_cannot_bypass_policy_and_trace_records_approval`|PASS|

## 7. Trace 与 Evaluation 补充契约

|契约|测试/运行入口|状态|
|-|-|-|
|Tool/LLM 次数、Token/Cost 可用性、平均/P95 延迟|`tests/evals/test_trace_evaluation_v3.py`；`python evals/run_v3_final_evaluation.py`|PASS|
|OCR/Layout/Index、Retrieval/Rerank、Evidence、Async、Workflow Node Trace|`tests/evals/test_trace_evaluation_v3.py` 及对应运行时测试|PASS|
|固定 Evaluation Manifest 引用真实测试|`tests/evals/test_v3_final_evaluation.py`|PASS|
|自然语言表达质量|人工复核|MANUAL REVIEW|
