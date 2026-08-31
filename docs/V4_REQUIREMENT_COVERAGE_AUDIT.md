# Student Document Agent V4 P0 Requirement Coverage Audit

## 1. 审计范围与状态口径

- 审计日期：2026-08-28
- V4 开发分支：`feature/v4-development`
- V3 稳定基线 tag：`v3-final-stable`
- V3 稳定基线 commit：`16bee95fb699c76e5200467d147161e3c81ec262`
- 状态仅使用：`NOT_STARTED`、`PARTIAL`、`IMPLEMENTED`、`VERIFIED`、`BLOCKED`。
- `IMPLEMENTED` 表示代码已实现但尚未完成要求的全部验证；`VERIFIED` 表示实现及对应验证均通过。
- 未实现功能不得因 V1–V3 已有相邻能力而标记为完成。

仓库中未发现独立 V4 需求原文。本矩阵依据 V4.0 任务中明确给出的阶段与能力名称，
建立可追踪的 P0 顶层初始条目。除 V4.0 基线外，条目不会推测具体接口、指标或验收阈值；
收到正式阶段需求后，应先拆分为可验证的细粒度 Requirement ID，再开始实现。

## 2. V4 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4-P0-001 | 建立并验证 V4 工程开发基线 | P0 | V4.0 Baseline | VERIFIED | `docs/V4_DEVELOPMENT_LOG.md`; `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md` | 既有完整 `tests/` 测试集 | 修改前：`313 passed in 18.10s`；修改后：`313 passed in 18.31s` | 仅文档与基线核验，不修改 V1–V3 业务代码 |
| V4-P0-002 | Visual Document Router 顶层能力 | P0 | V4.1 Visual Document Router | VERIFIED | `backend/services/input_router.py`; `backend/services/document_index.py` | `tests/test_visual_document_router_v4.py` | 专项 `13 passed`；完整回归 `326 passed` | 细分证据见第 4 节；未包含 V4.2 OCR |
| V4-P0-003 | Visual OCR and Image PDF | P0 | V4.2 Visual OCR and Image PDF | VERIFIED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | `tests/test_visual_ocr_pipeline_v4.py`; `tests/test_ocr_pipeline.py` | 专项 `8 passed`；完整回归 `334 passed` | 复用 V3 OCR page ledger、mixed routing、Async Task 与原子索引；细分证据见第 5 节 |
| V4-P0-004 | Handwriting Recognition | P0 | V4.3 Handwriting Recognition | VERIFIED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | `tests/test_handwriting_recognition_v4.py`; `tests/fixtures/v4_handwriting_cases.json` | 专项 `12 passed`；完整回归 `346 passed` | 验证管线和安全契约，不将固定样本结果表述为准确率；细分证据见第 6 节 |
| V4-P0-005 | Visual Understanding and KIE | P0 | V4.4 Visual Understanding and KIE | VERIFIED | `backend/services/visual_understanding.py`; `backend/document_blocks.py` | `tests/test_visual_understanding_v4.py` | 专项 `9 passed`；完整回归 `355 passed` | 只读派生 VisualBlock/分类/KIE；细分证据见第 7 节 |
| V4-P0-006 | Visual Table | P0 | V4.5 Visual Table | VERIFIED | `backend/services/visual_table.py`; `backend/table_structure.py` | `tests/test_visual_table_v4.py` | 专项 `8 passed`；完整回归 `363 passed` | 复用 V3 Table schema；细分证据见第 8 节 |
| V4-P0-007 | Evidence 3.0 | P0 | V4.6 Evidence 3.0 | VERIFIED | `backend/evidence.py`; `backend/services/evidence_locator.py`; existing `evidence_locations` | `tests/test_evidence_v4.py`; legacy Evidence tests | 专项 `7 passed`；完整回归 `370 passed` | 增量复用 Evidence 2.0 与原定位/Trace；细分证据见第 9 节 |
| V4-P0-008 | General Document Agent Core | P0 | V4.7 General Document Agent Core | VERIFIED | `document_agent_core.py`; `student_domain_adapter.py` | `tests/test_document_agent_core_v4.py`; legacy Agent/Tool tests | 专项 `4 passed`；完整回归 `374 passed` | 薄 façade/port/adapter，旧 Tool 名称与 handler 保持；细分证据见第 10 节 |
| V4-P0-009 | Domain Router and General Document Tasks | P0 | V4.8 Domain Router | VERIFIED | `backend/services/domain_router.py`; `backend/tools/general_tools.py`; `backend/agent.py` | `tests/test_domain_router_v4.py`; legacy Agent/Trace tests | 专项 `9 passed`；完整回归 `383 passed` | 严格 Schema、General fallback、执行级 Tool 隔离和 Evidence 驱动的 Excel+PDF 规则任务；细分证据见第 11 节 |
| V4-P0-010 | Controlled Agentic Retrieval | P0 | V4.9 Agentic Retrieval | VERIFIED | `backend/services/agentic_retrieval.py`; Core/Student Adapter thin ports | `tests/test_agentic_retrieval_v4.py`; legacy RAG/Evidence/Safety tests | 专项 `13 passed`；完整回归 `396 passed` | RAG 2.0 上方只读控制层；细分证据见第 12 节 |
| V4-P0-011 | Visual Safety and Trace | P0 | V4.10 Visual Safety and Trace | NOT_STARTED | TBD | TBD | NOT_RUN | 必须复用 Agent Safety 与 Trace；视觉输入威胁模型待确认 |
| V4-P0-012 | V4 Evaluation and Final Acceptance | P0 | V4.11 Evaluation and Final Acceptance | NOT_STARTED | TBD | TBD | NOT_RUN | 只登记真实固定数据集、测试结果和指标，不预设或编造数值 |

## 3. V4.0 基线审计证据

| 检查项 | 验证方式 | 真实结果 | 结论 |
| --- | --- | --- | --- |
| 当前分支 | `git branch --show-current` | `feature/v4-development` | PASS |
| 当前 HEAD | `git rev-parse HEAD` | `16bee95fb699c76e5200467d147161e3c81ec262` | PASS |
| V3 tag 指向 | `git rev-parse 'v3-final-stable^{commit}'` | `16bee95fb699c76e5200467d147161e3c81ec262` | PASS |
| 基线继承关系 | `git merge-base` 与 `git merge-base --is-ancestor` | merge-base 为指定 commit，ancestor 检查退出码为 0 | PASS |
| 初始工作区 | `git status --porcelain=v1` | 无输出 | PASS |
| 修改前完整测试 | `conda run -n tuli_env pytest -q` | `313 passed in 18.10s` | PASS |
| V1–V3 核心业务代码 | 审查 V4.0 diff | 仅新增两份 `docs/` 文档，无业务代码变更 | PASS |
| 修改后完整测试 | `conda run -n tuli_env pytest -q` | `313 passed in 18.31s` | PASS |

说明：`v3-final-stable` 是 annotated tag，其 tag object ID 与 commit ID 不同；
本审计使用 `^{commit}` 解引用后的 commit 进行基线核验。

## 4. V4.1 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.1-P0-01 | JPG/JPEG/PNG 上传支持 | P0 | V4.1 | VERIFIED | `backend/services/file_upload.py`; `backend/main.py` | `tests/test_visual_document_router_v4.py::test_jpg_jpeg_png_upload_as_formal_visual_documents` | 3 参数 case PASS | 使用既有单文件上传 API |
| V4.1-P0-02 | 扩展名、内容格式与 MIME 检测 | P0 | V4.1 | VERIFIED | `backend/services/input_router.py` | `tests/test_visual_document_router_v4.py::test_invalid_mismatched_and_corrupt_images_are_rejected`; `test_unsupported_image_mime_is_explainably_rejected` | PASS | JPEG/PNG 内容检测；generic MIME 向后兼容 |
| V4.1-P0-03 | 图片进入既有 Document/File 模型 | P0 | V4.1 | VERIFIED | `backend/services/file_upload.py`; `backend/tools/file_tools.py`; `backend/repositories/document_repository.py` | `test_jpg_jpeg_png_upload_as_formal_visual_documents` | PASS | 复用 `files`、`document_chunks`，无第二套模型 |
| V4.1-P0-04 | 统一 Structured/Text/Visual/Unknown 路由 | P0 | V4.1 | VERIFIED | `backend/services/input_router.py`; `backend/services/document_index.py` | `test_router_preserves_excel_word_pdf_routes_and_safely_rejects_unknown`; 既有 PDF/OCR tests | PASS | mixed PDF 进入 Visual；unknown 为 `SAFE_REJECT` |
| V4.1-P0-05 | 图片生命周期支持 detecting/visual/layout/index/queryable/failed | P0 | V4.1 | VERIFIED | `backend/repositories/file_repository.py`; `backend/services/file_lifecycle.py`; `backend/services/document_index.py` | `test_image_lifecycle_uses_visual_layout_index_and_queryable_states`; `test_image_processing_failure_persists_error_summary_after_registration` | PASS | 新增 canonical `VISUAL_PROCESSING`，保留兼容列 |
| V4.1-P0-06 | 图片处理失败保存 error_summary | P0 | V4.1 | VERIFIED | `backend/services/document_index.py::_index_failure`; `backend/services/file_lifecycle.py` | `test_image_processing_failure_persists_error_summary_after_registration` | PASS | 上传前非法内容仍按 V3 安全规则拒绝且不登记 |
| V4.1-P0-07 | 图片 reprocess | P0 | V4.1 | VERIFIED | `backend/services/document_index.py::_index_image`; existing reprocess API | `test_image_reprocess_replaces_candidate_and_failure_preserves_old_index` | PASS | 复用原 API 与候选构建 |
| V4.1-P0-08 | reprocess 失败保留旧有效结果 | P0 | V4.1 | VERIFIED | `backend/services/document_index.py`; `backend/repositories/document_repository.py` | `test_image_reprocess_replaces_candidate_and_failure_preserves_old_index` | PASS | active chunks 与 QUERYABLE 保持 |
| V4.1-P0-09 | 批量图片上传逐文件失败隔离 | P0 | V4.1 | VERIFIED | `frontend/api_client.py::upload_files`; existing upload API | `test_batch_image_upload_keeps_per_file_partial_failure` | PASS | 复用 V3 逐文件提交逻辑 |
| V4.1-P0-10 | Workspace 识别图片 | P0 | V4.1 | VERIFIED | `backend/services/file_view.py`; `frontend/app.py`; `frontend/components/file_panel.py` | `test_workspace_lists_and_previews_image_document`; `test_frontend_minimally_recognizes_image_documents_and_visual_status` | PASS | 仅增加类型、状态、详情和操作入口 |
| V4.1-P0-11 | Preview 显示图片 | P0 | V4.1 | VERIFIED | `backend/services/input_router.py::build_image_preview`; `backend/services/file_view.py`; `frontend/components/file_panel.py` | `test_workspace_lists_and_previews_image_document` | PASS | 返回最长边 1200 的 base64 缩略图 |
| V4.1-P0-12 | Task Center 显示图片处理状态 | P0 | V4.1 | VERIFIED | `backend/runtime/async_task_runtime.py`; `backend/services/task_view.py`; `frontend/components/task_center.py` | `test_task_center_exposes_image_processing_document` | PASS | 图片 layout/index/reindex Task 暴露 file name/type |
| V4.1-P0-13 | 前端最低必要接入 | P0 | V4.1 | VERIFIED | `frontend/app.py`; `frontend/components/file_panel.py`; `frontend/components/task_center.py` | `test_frontend_minimally_recognizes_image_documents_and_visual_status`; existing frontend tests | PASS | 无视觉重构 |
| V4.1-P0-14 | Excel/Word/PDF 与 V1–V3 回归保持 | P0 | V4.1 | VERIFIED | 原 V1–V3 modules | 原测试集；`test_router_preserves_excel_word_pdf_routes_and_safely_rejects_unknown` | 完整 pytest：`326 passed in 20.43s`；文档更新后 `326 passed in 16.50s` | 无删除、跳过或弱化旧测试 |

### 4.1 V4.1 边界与限制

- 图片只形成带真实尺寸、格式和 MIME 元数据的 image Block；chunk 文本为空，
  不会把文件名或推测内容作为检索证据。
- 图片 OCR 与 Image PDF 的进一步视觉识别属于 V4.2，当前为 `NOT_STARTED`。
- 手写识别、KIE、视觉表格、Evidence 3.0、Domain Router 和 Agentic Retrieval 未提前实现。
- 格式/MIME 不是新增数据库列；内容派生元数据随 active image Block/chunk 保存并由
  Workspace 读取，文件身份与生命周期仍由原 `files` 表负责。

## 5. V4.2 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.2-P0-01 | JPG/JPEG/PNG 进入统一 Visual OCR | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py::ocr_visual`; `backend/services/document_index.py::_index_image` | `test_printed_jpg_and_png_use_visual_ocr_regions` | JPG、PNG 两参数 case PASS | JPEG 与 JPG 使用同一已验证 JPEG 内容路由/解码分支 |
| V4.2-P0-02 | 扫描、图片型 PDF 复用 V3 OCR | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py`; `backend/services/document_index.py::_parse_ocr_pdf` | `test_image_only_pdf_uses_same_region_ledger`; V3 `tests/test_ocr_pipeline.py` | PASS | 未新增第二套 PDF OCR |
| V4.2-P0-03 | Mixed PDF page-level 文本/视觉路由 | P0 | V4.2 | VERIFIED | `backend/services/document_index.py::_parse_ocr_pdf`; existing PDF detector/router | `test_mixed_pdf_routes_only_visual_pages_and_prevents_text_ocr_duplicates`; V3 mixed PDF tests | PASS | 有文本页优先 pypdf，空文本页才 OCR |
| V4.2-P0-04 | OCR region 契约与持久化 | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py::OCRRegionResult`; `backend/repositories/ocr_repository.py` | `test_printed_jpg_and_png_use_visual_ocr_regions`; updated `tests/test_ocr_pipeline.py` | PASS | region 保存于既有 `blocks_json` 并提供扁平 getter |
| V4.2-P0-05 | 保留 bbox 与 confidence | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py::_normalize_block` | printed/image PDF/empty region tests | PASS | 原始区域坐标与置信度均被保留 |
| V4.2-P0-06 | empty/failed region 可表达且不自动补字 | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | `test_ocr_empty_region_keeps_bbox_confidence_and_never_fills_text`; partial failure test | PASS | 只有真实非空 success region 进入检索文本 |
| V4.2-P0-07 | 中英文与常见数字混合文本可透传 | P0 | V4.2 | VERIFIED | existing RapidOCR adapter; region normalizer | `test_printed_jpg_and_png_use_visual_ocr_regions` | PASS | 验证文字原样保存，不声明 OCR 准确率 |
| V4.2-P0-08 | 文本层与 OCR 去重 | P0 | V4.2 | VERIFIED | `backend/services/document_index.py::_parse_ocr_pdf` | `test_mixed_pdf_routes_only_visual_pages_and_prevents_text_ocr_duplicates` | PASS | 文本页不调用 OCR，chunk 中各出现一次 |
| V4.2-P0-09 | OCR 失败保留旧有效文本/region | P0 | V4.2 | VERIFIED | `backend/services/document_index.py::_recognized_page_results`; atomic activation | `test_scanned_pdf_reprocess_failure_preserves_old_chunks_and_regions`; async image preservation test | PASS | 本次刷新失败由 `refresh_status` 和 `failed_pages` 暴露 |
| V4.2-P0-10 | OCR/visual 接入既有 Async Task | P0 | V4.2 | VERIFIED | `backend/runtime/async_task_runtime.py` | `test_image_async_ocr_failure_preserves_old_regions_and_retry_only_page_one`; existing async tests | PASS | image 为单视觉页；PDF 页数仍使用既有检测器 |
| V4.2-P0-11 | 单页失败 partial_success，retry 仅失败页 | P0 | V4.2 | VERIFIED | `backend/services/ocr_service.py`; `backend/services/document_index.py`; existing retry runtime | partial/retry tests；V3 `test_o08_retry_only_failed_page_preserves_successful_pages` | PASS | checkpoint 保留 `failed_pages` |
| V4.2-P0-12 | V1–V3 mixed PDF/OCR 与全量回归保持 | P0 | V4.2 | VERIFIED | 原 V1–V3 modules | 完整 `tests/` | 首次 `334 passed in 27.68s`；最终 `334 passed in 27.25s` | 无删除或 skip 旧测试 |

### 5.1 V4.2 边界与限制

- `recognition_type` 本阶段仅实现 printed OCR；手写识别属于 V4.3。
- 未开发 KIE、视觉表格、Evidence 3.0、Domain Router 或 Agentic Retrieval。
- region 复用 `document_ocr_pages.blocks_json`，避免新增平行存储；扁平 region getter 是
  兼容视图，不改变 V3 page ledger 的数据库结构。
- 不提供未经真实评测的 OCR 准确率或性能指标。

## 6. V4.3 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.3-P0-01 | printed/handwritten/mixed/unknown 统一 region 类型 | P0 | V4.3 | VERIFIED | `backend/services/ocr_service.py::OCRRegionResult`; `_normalize_block` | mixed、unknown、固定手写测试 | PASS | 仍使用 V4.2 同一 Visual OCR 入口和 ledger |
| V4.3-P0-02 | 清晰手写进入可用结果 | P0 | V4.3 | VERIFIED | `backend/services/ocr_service.py` | `test_fixed_clear_medium_and_extremely_unclear_handwriting[clear_handwriting-False]` | PASS | 固定 adapter 样本验证管线，不代表真实准确率 |
| V4.3-P0-03 | 中等潦草手写基础可用并可核对 | P0 | V4.3 | VERIFIED | handwriting confidence policy | medium fixed sample case | PASS | 文本可索引，同时保留 review 标记 |
| V4.3-P0-04 | 极潦草允许 low_confidence/failed，禁止强猜 | P0 | V4.3 | VERIFIED | `_apply_handwriting_safety`; index success-region filter | extremely unclear fixed sample case | PASS | backend 原值保留，但不进入检索 chunk |
| V4.3-P0-05 | 手写 region 保存文本、位置、置信度、类型、模型、ID、状态 | P0 | V4.3 | VERIFIED | `OCRRegionResult`; existing `document_ocr_pages.blocks_json` | fixed samples；conflict persistence test | PASS | 不新增表或第二套模型 |
| V4.3-P0-06 | 关键字段低置信度禁止高影响/唯一身份匹配并保留核对 | P0 | V4.3 | VERIFIED | `_detect_key_field_type`; `_apply_handwriting_safety`; retrieval metadata | low-confidence name/phone tests；high-impact rejection test | PASS | 支持 name/student_id/phone/amount/date 风险标签；不是 KIE |
| V4.3-P0-07 | 单页内 partial region failure 支持 partial_success | P0 | V4.3 | VERIFIED | `ocr_visual` failed-region aggregation | `test_partial_region_failure_is_reported_without_losing_successful_text` | PASS | 成功 region 保留并可索引 |
| V4.3-P0-08 | 失败/review region 局部 retry | P0 | V4.3 | VERIFIED | `retry_visual_regions`; `retry_ocr_regions` | `test_failed_region_retry_crops_only_that_region_and_keeps_region_id` | PASS | bbox crop；保持原 region_id；原子激活 |
| V4.3-P0-09 | OCR/handwriting 模型冲突不静默选边 | P0 | V4.3 | VERIFIED | `_normalized_conflict_sources`; `_apply_handwriting_safety` | `test_model_conflict_preserves_both_sources_and_never_silently_selects` | PASS | 主文本留空，保存候选文本、模型、parser 与 confidence |
| V4.3-P0-10 | confidence threshold 配置化且模型无关 | P0 | V4.3 | VERIFIED | `HandwritingThresholds`; `get_handwriting_thresholds` | `test_handwriting_thresholds_are_configurable_not_model_specific` | PASS | 使用归一化 0..1 契约，不绑定特定模型分数常量 |
| V4.3-P0-11 | 手写安全标记传递到 Evidence/高影响判断 | P0 | V4.3 | VERIFIED | `document_index.retrieve_document`; `backend/tools/hybrid_tools.py`; Agent prompt | high-impact rejection test；Agent/Hybrid regressions | PASS | unsafe handwriting Evidence 不生成高影响规则条件 |
| V4.3-P0-12 | V1–V3 与 V4.1–V4.2 回归保持 | P0 | V4.3 | VERIFIED | 原有 modules | 完整 `tests/` | 修复后 `346 passed in 30.09s`；最终 `346 passed in 30.19s` | 首轮发现的 V4.1 状态兼容问题已修复；无删除或 skip 旧测试 |

### 6.1 V4.3 边界与限制

- 本阶段固定样本只提供可计算的数据结构、adapter 测试入口和安全验收，不产生或声称
  手写准确率、“七七八八可读率”或性能指标；真实 Evaluation 留到 V4.11。
- 默认本地 backend 仍为 V4.2 既有 RapidOCR；专用手写模型必须输出同一归一化 region
  契约后接入，不改变索引、Async Task 或持久化系统。
- 关键字段识别只用于风险标记，不构成 V4.4 KIE 实现。

## 7. V4.4 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.4-P0-01 | 在 V3 Block 与 V4 region 上扩展统一 VisualBlock | P0 | V4.4 | VERIFIED | `backend/services/visual_understanding.py::VisualBlock`; `backend/document_blocks.py` | `test_visual_blocks_cover_required_types_and_parent_provenance` | PASS | VisualBlock 继承 DocumentBlock；无第二套持久化 |
| V4.4-P0-02 | VisualBlock 保留文档、页/图、文本、位置、置信度、识别与来源字段 | P0 | V4.4 | VERIFIED | `VisualBlock.visual_dump`; `extract_visual_blocks` | required-field assertion | PASS | source_region_ids 额外保留 OCR 来源 |
| V4.4-P0-03 | title/paragraph/table/image/signature/stamp/unknown | P0 | V4.4 | VERIFIED | `_infer_block_type`; OCR `visual_block_type` | `test_visual_blocks_cover_required_types_and_parent_provenance` | PASS | 不包含复杂自然图像语义 |
| V4.4-P0-04 | extract_visual_blocks | P0 | V4.4 | VERIFIED | `backend/services/visual_understanding.py` | VisualBlock tests | PASS | active OCR ledger 的确定性只读派生 |
| V4.4-P0-05 | classify_document 基础分类 | P0 | V4.4 | VERIFIED | `classify_document`; `_classify_from_blocks` | certificate/form/notice 参数测试 | 3 cases PASS | 还支持 table/letter/report/unknown；不声明准确率 |
| V4.4-P0-06 | 分类失败 unknown 且保留通用查询 | P0 | V4.4 | VERIFIED | `classify_document` exception fallback | unknown query；classification exception tests | PASS | envelope 保持成功，分类 status=failed，通用 RAG 不受影响 |
| V4.4-P0-07 | basic KIE / extract_key_fields | P0 | V4.4 | VERIFIED | `extract_key_fields` | field+bbox test | PASS | 只解析明确 label:value，不推断缺失内容 |
| V4.4-P0-08 | KIE field 绑定 evidence/source region | P0 | V4.4 | VERIFIED | `VisualKeyField`; KIE evidence builder | `test_basic_kie_field_keeps_bbox_and_source_region_without_mutating_ocr` | PASS | 绑定 block_id、region_id、bbox、confidence、parser/model |
| V4.4-P0-09 | 低 confidence 字段不得升级为确定事实 | P0 | V4.4 | VERIFIED | `_kie_confidence_threshold`; `extract_key_fields` | `test_low_confidence_kie_stays_review_candidate` | PASS | status=review_required；is_confirmed_fact=false |
| V4.4-P0-10 | KIE 不替代原始 OCR | P0 | V4.4 | VERIFIED | visual understanding service is read-only | OCR before/after equality assertion | PASS | 无数据库迁移或 OCR 写入 |
| V4.4-P0-11 | Student hint 与 General field/value schema 隔离 | P0 | V4.4 | VERIFIED | `_validated_schema_hints`; `STUDENT_SCHEMA_HINTS` | `test_student_and_general_kie_schema_are_strictly_separated` | PASS | General 拒绝 student schema hint，不强映射 student_id/score |
| V4.4-P0-12 | V1–V3 与 V4.1–V4.3 回归保持 | P0 | V4.4 | VERIFIED | 原有 modules | 完整 `tests/` | 首次 `355 passed in 28.92s`；中断恢复复核 `355 passed in 31.98s` | 无删除或 skip 旧测试 |

### 7.1 V4.4 边界与限制

- 分类是基础规则结果，不是自然图像理解模型输出，也不代表准确率。
- KIE 仅处理明确的单 region `label: value`，不执行跨 region 推理、复杂表格推理或隐式字段补全。
- VisualBlock/分类/KIE 是 OCR ledger 的可重复派生视图；KIE evidence 直接绑定 source region，
  原始 OCR 保持事实源。
- Student schema hint 只在显式 `domain=student` 时启用；General 始终保持通用 field/value。

## 8. V4.5 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.5-P0-01 | 复用 V3 Table/Row/Column/Cell Schema | P0 | V4.5 | VERIFIED | `backend/table_structure.py`; `visual_table.py` | printed table；existing `test_layout_blocks.py` | PASS | 增量扩展可选视觉字段，旧调用兼容 |
| V4.5-P0-02 | 清晰二维打印图片表格 | P0 | V4.5 | VERIFIED | `extract_visual_tables`; V3 `build_simple_table` | `test_simple_printed_table_image_reuses_table_row_column_cell_schema` | PASS | 只接受完整显式矩形网格 |
| V4.5-P0-03 | 基础手写表格 | P0 | V4.5 | VERIFIED | OCR recognition metadata；visual table builder | `test_simple_handwritten_table_preserves_recognition_type` | PASS | 不声明真实识别准确率 |
| V4.5-P0-04 | Table/Cell 关联 page/image、bbox、text、confidence | P0 | V4.5 | VERIFIED | extended `TableStructure` / `TableCell` | `test_reliable_cells_keep_page_image_bbox_text_and_confidence` | PASS | Cell 同时关联 source_region_id |
| V4.5-P0-05 | 手写 Cell 支持 low confidence | P0 | V4.5 | VERIFIED | `_cell_confidence_threshold`; cell geometry mapping | `test_low_confidence_handwritten_cell_is_kept_but_not_calculable` | PASS | 文本与位置保留，禁止精确计算 |
| V4.5-P0-06 | 结构可靠后才生成 Row/Column/Cell，禁止补造 | P0 | V4.5 | VERIFIED | `_structure_warning`; `_build_visual_table` complete-grid check | printed/incomplete/merged tests | PASS | 缺格或重复坐标直接零 Cell 降级 |
| V4.5-P0-07 | 精确数值仅交给 Python | P0 | V4.5 | VERIFIED | `calculate_visual_table` | `test_reliable_numeric_cells_are_calculated_only_by_python_decimal` | PASS | execution_engine=python_decimal；显式 Cell IDs |
| V4.5-P0-08 | 低置信度和非数值 Cell 禁止猜测计算 | P0 | V4.5 | VERIFIED | calculation reliability/numeric guards | low-confidence test；non-numeric assertion | PASS | 返回可解释错误，不调用 LLM |
| V4.5-P0-09 | 合并单元格结构失败安全降级 | P0 | V4.5 | VERIFIED | `_structure_warning`; V3 `degraded_table` | `test_merged_cell_structure_degrades_without_fabricating_cells` | PASS | table block + OCR text + original region；零 Cell |
| V4.5-P0-10 | 无边框手绘表格失败安全降级 | P0 | V4.5 | VERIFIED | `table_structure_hint=borderless_hand_drawn` | `test_hand_drawn_borderless_table_degrades_safely` | PASS | 不推测网格边界 |
| V4.5-P0-11 | 表格失败不使整份文档不可查询 | P0 | V4.5 | VERIFIED | visual table is read-only derived view | `test_incomplete_grid_safe_fallback_keeps_document_queryable` | PASS | 原 OCR chunk 与 QUERYABLE 保持 |
| V4.5-P0-12 | V1–V3 与 V4.1–V4.4 回归保持 | P0 | V4.5 | VERIFIED | 原有 modules | 完整 `tests/` | 首次 `363 passed in 31.09s`；最终 `363 passed in 29.48s` | 无删除或 skip 旧测试 |

### 8.1 V4.5 边界与限制

- 本阶段只处理 layout backend 明确给出 row/column/bbox 的简单二维网格，不从 OCR 文本
  或图片外观猜测 Cell。
- merged-cell、borderless hand-drawn、缺格、重复坐标与无 bbox 均保留原始 OCR 和图像
  region，但不生成任何结构化 Cell。
- 手写 Cell 可以保留为 low-confidence 候选；只有可靠 Cell 可进入 Python Decimal 计算。
- 不开发复杂表格语义、图表推理或 LLM 图片数值读取。

## 9. V4.6 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.6-P0-01 | Evidence 统一序列化并兼容 Evidence 2.0 | P0 | V4.6 | VERIFIED | `backend/evidence.py::Evidence`; `serialize_evidence`; `deserialize_evidence` | `test_evidence_3_serialization_accepts_legacy_evidence_2_payload`; V3 Evidence tests | PASS | 原 `evidence_locations.locator_json` 继续使用，无迁移/新表；旧响应字段集合保持 |
| V4.6-P0-02 | Excel file/sheet/field/record 与 Table/Cell 不回归 | P0 | V4.6 | VERIFIED | existing `build_evidence`; `_locate_excel` | `tests/test_evidence_v3.py`; `test_evidence_location_v3.py`; Hybrid tests | PASS | Evidence 2.0 精确响应契约保留 |
| V4.6-P0-03 | Text PDF 与 Word file/page/chunk/block 定位不回归 | P0 | V4.6 | VERIFIED | `_locate_document`; existing RAG builder | V3 location；Document RAG tests | PASS | chunk/block/paragraph 行为保持 |
| V4.6-P0-04 | Visual PDF page/region/block/bbox/confidence 定位 | P0 | V4.6 | VERIFIED | `document_index.retrieve_document`; `_locate_document` | `test_visual_pdf_region_localizes_without_requiring_a_text_chunk` | PASS | 无 text chunk 时可由活动 OCR region 验证定位 |
| V4.6-P0-05 | 独立图片 image/region/bbox/confidence 打开与高亮 | P0 | V4.6 | VERIFIED | `_locate_image`; `build_image_preview`; evidence panel | `test_image_region_evidence_opens_preview_and_highlights_bbox` | PASS | 返回受限 base64 preview 与 highlight bbox；UI 明示 bbox |
| V4.6-P0-06 | KIE 字段回溯实际 bbox/source region | P0 | V4.6 | VERIFIED | `visual_understanding.extract_key_fields`; existing Evidence store | `test_kie_field_persists_actual_region_and_bbox_evidence` | PASS | 无 bbox 时不生成 KIE 候选；不替代 OCR |
| V4.6-P0-07 | OCR/handwriting 结果形成 Evidence | P0 | V4.6 | VERIFIED | `build_evidence`; RAG OCR ledger matching | image/PDF/handwriting tests；V4.3 regressions | PASS | 保留 recognition/parser/model/confidence；手写另存 handwriting confidence |
| V4.6-P0-08 | Visual Table 定位 table/row/column/cell/bbox | P0 | V4.6 | VERIFIED | `visual_table.calculate_visual_table`; `_locate_visual_table` | `test_visual_table_calculation_evidence_highlights_exact_cell`; V4.5 tests | PASS | 只为实际参与 Python Decimal 计算的可靠 Cell 形成 chain |
| V4.6-P0-09 | 最终回答只暴露实际参与的 Evidence | P0 | V4.6 | VERIFIED | `agent._collect_evidence`; existing conclusion-chain rule | `test_unused_retrieval_source_is_not_exposed_after_evaluation`; memory test | PASS | authoritative conclusion chain 排除未使用检索候选 |
| V4.6-P0-10 | Memory/历史回答不得作为事实 Evidence | P0 | V4.6 | VERIFIED | Agent prompt；`_collect_evidence` source guard | `test_memory_and_history_are_never_exposed_as_fact_evidence` | PASS | memory/history/chat 来源被过滤 |
| V4.6-P0-11 | OCR/KIE 冲突不静默选边，低置信度手写展示 text+confidence | P0 | V4.6 | VERIFIED | Evidence conflict/review fields；locator；evidence panel | `test_handwriting_conflict_keeps_all_sources_and_requires_review`; V4.3 conflict tests | PASS | 保存全部 conflict_sources，status=conflict，强制 review |
| V4.6-P0-12 | 定位失败不改变回答并记录 Trace | P0 | V4.6 | VERIFIED | `_location_failure`; existing Trace service | `test_r209_location_failure_keeps_safe_message_and_records_trace` | PASS | 定位 API 只读；返回明确失败，不改事实内容 |
| V4.6-P0-13 | V1–V3 与 V4.1–V4.5 完整回归保持 | P0 | V4.6 | VERIFIED | 原有 modules | 完整 `tests/` | 首次 `369 passed, 1 failed`；修复后 `370 passed in 27.50s`；最终 `370 passed in 35.45s` | 首轮精确 metadata 回归已通过只读 ledger 匹配修复；无删除或 skip 旧测试 |

### 9.1 V4.6 边界与限制

- 当前 Streamlit 图片预览展示原图并列出 bbox，未实现浏览器 canvas 覆盖层；返回的
  `highlight.bbox` 已可供后续 UI 消费。PDF 定位返回 page + region/bbox。
- Visual Table 只定位 V4.5 已可靠解析的 Cell；降级表格没有伪造 Cell，因此只能保留
  table block、OCR text 与 original image region。
- 定位服务验证活动 OCR region/Cell 后返回预览；来源已失效时明确失败并写 Trace，不对
  答案事实内容做任何修改。
- 未实现跨页组合 Evidence、复杂 merged-cell 定位或定位缓存，也未提前开发 V4.7。

## 10. V4.7 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.7-P0-01 | 开发前审计 student-specific 与 generic document 边界 | P0 | V4.7 | VERIFIED | `CORE_CAPABILITY_MODULES`; stage audit log | `test_core_is_a_thin_facade_and_keeps_legacy_tool_handlers` | PASS | 审计覆盖 query/aggregate/retrieval/evidence/workflow/safety/write-version/validation/comparison |
| V4.7-P0-02 | 建立 Document Agent Core 薄接口，不复制稳定实现 | P0 | V4.7 | VERIFIED | `backend/services/document_agent_core.py` | Core capability test；完整回归 | PASS | façade 全部委托原模块，无新数据库、executor、parser 或 Evidence 系统 |
| V4.7-P0-03 | Core 覆盖 File/Schema/Parser 与 OCR/Vision 所有权 | P0 | V4.7 | VERIFIED | `DocumentAgentCore`; `CORE_CAPABILITY_MODULES` | capability catalog assertion；既有 Schema/OCR/Visual tests | PASS | 稳定模块保持原路径，不移动或重命名 |
| V4.7-P0-04 | Core 覆盖 query_table/aggregate_table 与 Retrieval/Rerank/Evidence | P0 | V4.7 | VERIFIED | Core table/retrieval/evidence delegates | unknown Excel test；legacy table/RAG/Evidence tests | PASS | 直接复用旧函数和 Evidence chain |
| V4.7-P0-05 | Core 覆盖 Workflow/Async、Safety、Diff/Version/Rollback、Trace/Evaluation | P0 | V4.7 | VERIFIED | Core runtime/safety/version/evaluation delegates；capability map | capability assertion；legacy Runtime/Safety/Version/Evaluation tests | PASS | 只提供边界，状态仍归原服务所有 |
| V4.7-P0-06 | 非学生未知 Excel 完成字段查询、筛选与 aggregate | P0 | V4.7 | VERIFIED | `DocumentAgentCore.inspect_excel/query_table/aggregate_table` | `test_general_core_unknown_excel_field_filter_and_aggregate` | PASS | 仓库物料样本；上海库存 sum=155，由 Python 计算 |
| V4.7-P0-07 | General Core 不要求 student_id，不伪造学生实体 | P0 | V4.7 | VERIFIED | Core query signature；existing generic schema semantics | unknown Excel test | PASS | 物料字段保持原名，未映射 student_id；Core/Student tool sets 隔离 |
| V4.7-P0-08 | Student Domain Adapter 包含身份、查询、比较、validation、奖学金与报告边界 | P0 | V4.7 | VERIFIED | `backend/services/student_domain_adapter.py` | adapter identity compatibility；legacy student/hybrid/report tests | PASS | 包含当前学生语义的 cross-file conflict；不重写算法，全部 delegate |
| V4.7-P0-09 | Student Adapter 可以调用通用 Core | P0 | V4.7 | VERIFIED | `DocumentCorePort`; `query_domain_table`; `retrieve_domain_document` | `test_student_adapter_can_consume_general_core_without_a_domain_router` | PASS | 显式依赖注入，不做自动 domain selection |
| V4.7-P0-10 | 原学生 Demo 与 Tool 名称/handler 兼容 | P0 | V4.7 | VERIFIED | existing `ToolRegistry`; Agent report adapter call | handler identity test；Agent/Confirmation tests；完整回归 | PASS | Registry 未换 handler；报告仍走原 HITL/Version 流程 |
| V4.7-P0-11 | 最小重构且不提前实现 V4.8 Domain Router | P0 | V4.7 | VERIFIED | 两个新增 service modules；无 router | code audit；capability separation test | PASS | 无文件搬迁、模块改名、动态 prompt/Tool 路由 |
| V4.7-P0-12 | V1–V3 与 V4.1–V4.6 全量回归保持 | P0 | V4.7 | VERIFIED | 原有 modules | 完整 `tests/` | 首次 `374 passed in 28.72s`；最终 `374 passed in 29.76s` | 无删除、skip 或弱化旧测试 |

### 10.1 V4.7 边界与限制

- `DocumentAgentCore` 是稳定模块的 façade，不是新的 Agent loop、Tool executor、数据库或
  文件管理系统；外部 Tool 仍由原 `ToolRegistry` 注册。
- 当前 Agent 保持 Student Demo 的 prompt 和确定性流程校验；General/Student 自动选择属于
  V4.8，未在本阶段实现。
- `StudentDomainAdapter` 已提供 Core port 与学生业务委托边界，但不会把通用文档强制映射成
  学生实体，也不会绕过 Confirmation/Safety/Version 执行写入。

## 11. V4.8 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.8-P0-01 | 输出 student/general/unknown、六类 task_type 与 reason_code 的严格结构 | P0 | V4.8 | VERIFIED | `DomainRoute`; `route_domain` | student/general/unknown/ambiguous tests | PASS | 额外输出 effective_domain，unknown 必为 general |
| V4.8-P0-02 | Router 不仅依赖关键词 if/else | P0 | V4.8 | VERIFIED | `_deterministic_route`; `DomainRouterContext` | general schema/context test；ambiguous test | PASS | 加权融合消息结构、Schema、file type、Tool family 与任务信号 |
| V4.8-P0-03 | 辅助 classifier 输出必须 Schema Validate | P0 | V4.8 | VERIFIED | `DomainClassifier`; `DomainRoute.model_validate` | `test_invalid_router_schema_and_classifier_failure_use_validated_fallback` | PASS | 支持 dict/JSON；extra 字段和非法 enum 均拒绝 |
| V4.8-P0-04 | Router failure/invalid/unknown 安全降级 General | P0 | V4.8 | VERIFIED | `_fallback_route`; route exception handling | invalid schema、classifier failure、unknown tests | PASS | reason_code 区分失败原因，不拒绝文件 |
| V4.8-P0-05 | Student route 允许 General Core + Student Adapter | P0 | V4.8 | VERIFIED | `allowed_tool_names`; V4.7 capability sets | `test_student_route_allows_core_and_student_adapter_tools`; legacy Agent tests | PASS | 不改旧 Tool 名称、定义或 handler |
| V4.8-P0-06 | General/Unknown 默认 Core，不强制 student schema/entity | P0 | V4.8 | VERIFIED | Agent `GENERAL_DOMAIN_PROMPT`; execution allow-list | general Tool guard；non-student Excel query | PASS | Student Tool 的合法参数也在执行前被领域隔离；不生成 student_id |
| V4.8-P0-07 | Trace 记录 domain + task_type + reason_code | P0 | V4.8 | VERIFIED | `record_domain_route_trace`; Agent LLM Trace metrics | standalone/Agent Trace tests；legacy Trace exact-cardinality test | PASS | Agent 复用现有 event，独立 Router 使用 domain_route event |
| V4.8-P0-08 | 简单任务不增加不必要复杂流程 | P0 | V4.8 | VERIFIED | `run_agent` domain-aware plan selection | Agent Trace/simple final-answer test；legacy Agent tests | PASS | General 简单查询不创建学生 Planner workflow，仅做路由和原 loop |
| V4.8-P0-09 | 非学生 Excel 可查询、筛选且无学生实体 | P0 | V4.8 | VERIFIED | V4.7 `DocumentAgentCore.query_table` | `test_non_student_excel_query_does_not_require_or_create_student_entity` | PASS | `项目预算.xlsx` 两条阈值筛选结果由 Python query Tool 产生 |
| V4.8-P0-10 | Excel + PDF General 混合任务完成规定执行链 | P0 | V4.8 | VERIFIED | `evaluate_project_approval`; Core delegates | `test_excel_pdf_general_mixed_task_uses_python_and_explicit_rule_evidence` | PASS | query/aggregate → retrieve → Python condition → answer + Evidence |
| V4.8-P0-11 | 规则判断引用实际 Evidence，LLM 不计算预算 | P0 | V4.8 | VERIFIED | `_explicit_rule`; `_compare`; evidence filtering | mixed task assertions | PASS | 只解析检索命中的明确阈值；最终 chain 仅含实际预算 cell 与规则 Evidence |
| V4.8-P0-12 | Student 与 V1–V4.7 全量回归保持 | P0 | V4.8 | VERIFIED | 原有 modules；兼容 Agent integration | legacy Agent/Trace tests；完整 `tests/` | 首次 `383 passed in 28.08s`；最终 `383 passed in 30.69s` | 无删除、skip 或弱化旧测试；Tool definitions 集合不变 |

### 11.1 V4.8 边界与限制

- 默认 Router 是确定性、可解释的加权信号模型；本阶段没有声明领域分类准确率，也没有
  新增外部 LLM 调用。注入 classifier 只能在严格 Schema 验证后影响路由。
- 为保持依赖完整 Tool definitions 的 V1–V3 client/test 兼容，发送给模型的 definitions
  集合保持不变；General/Unknown 在 prompt 与执行 allow-list 双重约束，Student Tool 不会执行。
- 项目专项审批任务只执行从真实 PDF Evidence 提取出的明确预算数值阈值；多条件法条、
  隐含规则、字段单位不确定或非数值预算均不猜测，需后续显式扩展规则 schema。
- V4.9 Agentic Retrieval、V4.10 Visual Safety/Trace 专项和 V4.11 Evaluation 未提前实现。

## 12. V4.9 P0 Requirements Coverage Matrix

| Requirement ID | Requirement | Priority | Target V4 Stage | Implementation Status | Code Location | Test Location | Verification Result | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4.9-P0-01 | 简单查询不强制 Agentic loop，复杂任务才创建 Need | P0 | V4.9 | VERIFIED | `is_complex_retrieval_task`; `build_information_needs`; bypass branch | `test_ar_simple_query_does_not_create_need_or_loop`; domain complex test | PASS | bypass 为零 Need、零 retrieval call；原单次 RAG 路径保持 |
| V4.9-P0-02 | Information Need 包含规定身份、类型、描述、状态、scope、Evidence IDs | P0 | V4.9 | VERIFIED | `InformationNeed` | 专项所有 Need assertions | PASS | Pydantic extra=forbid；另含 query/required terms/minimum/rewrite 配置 |
| V4.9-P0-03 | Sufficiency 支持 sufficient/partial/missing/conflict/low_confidence | P0 | V4.9 | VERIFIED | `evaluate_evidence_sufficiency`; `SufficiencyEvaluation` | missing/partial/conflict/low/second-round tests | PASS | 冲突不选边；低 confidence/review 不升级为 sufficient |
| V4.9-P0-04 | 只补检索未满足 Need，已满足 Need 不重复搜索 | P0 | V4.9 | VERIFIED | unresolved Need loop；per-Need Evidence ledger | `test_ar_satisfied_need_is_not_retrieved_again_while_missing_need_retries` | PASS | satisfied fact 仅调用一次，missing rule 独立补检索 |
| V4.9-P0-05 | Controlled Query Rewrite 与 Controlled Re-retrieval | P0 | V4.9 | VERIFIED | `rewrite_retrieval_query`; `run_controlled_retrieval` | second-round；no-new；scope tests | PASS | 最多两种内建补充/交叉核验改写；可提供最多五个受控候选；不修改 scope |
| V4.9-P0-06 | max_rounds、max_tool_calls、timeout/top_k 预算配置 | P0 | V4.9 | VERIFIED | `RetrievalBudget`; budget checks | `test_ar_budget_and_timeout_terminate_bounded_retrieval` | PASS | 默认 3 rounds/6 calls/10 seconds；严格上界；总 timeout 不强杀当前同步调用 |
| V4.9-P0-07 | 每轮记录规定的 query/scope/candidates/selected/new/sufficiency/stop | P0 | V4.9 | VERIFIED | `_attempt_record`; `_trace_attempt`; `_trace_stop` | `test_ar_round_and_stop_trace_contain_control_fields` | PASS | 返回结构保存所有字段；Trace 保存 round metrics 与最终 stop event |
| V4.9-P0-08 | 支持 sufficient/budget/no_new_evidence/scope_exhausted/error 停止原因 | P0 | V4.9 | VERIFIED | controlled loop stop conditions | sufficient、budget、no-new、scope exhausted/error tests | PASS | max rounds耗尽也归 budget；错误不继续检索 |
| V4.9-P0-09 | 用户限定 file/year/page/metadata scope 不可突破 | P0 | V4.9 | VERIFIED | `_constrain_scope`; immutable scope copy；V3 hard filters | `test_ar_explicit_file_year_and_metadata_scope_never_expands`; V3 metadata tests | PASS | Need 只能等于或窄于全局 scope；冲突扩大直接参数失败 |
| V4.9-P0-10 | 未找到 Evidence 不等于事实不存在，最终四态明确 | P0 | V4.9 | VERIFIED | `_answer_status`; `_answer_message` | missing/conflict/low/confirmed tests | PASS | confirmed/not_found/conflict/low_confidence；not_found 有明确非否定提示 |
| V4.9-P0-11 | 不能信任 Memory/历史声称的已满足状态 | P0 | V4.9 | VERIFIED | run 初始化当前 Evidence ledger | `test_ar_does_not_trust_caller_claimed_sufficient_without_current_evidence` | PASS | 每次运行从 missing/空 Evidence IDs 开始，只信任本次 RAG Evidence |
| V4.9-P0-12 | 不绕过 Workflow/Safety/Approval，Student/General 共用 Core | P0 | V4.9 | VERIFIED | Core/Student Adapter ports；Core Safety assessment；safety trace | AR Trace；Student/General complex task tests；legacy Workflow/Safety tests | PASS | 每次 retrieval 先过原 Safety；控制层只读，不执行写/确认操作；边界标记为 false |
| V4.9-P0-13 | 复用 Hybrid Retrieval/Metadata/Rerank/Evidence 且 V1–V4.8 不回归 | P0 | V4.9 | VERIFIED | 委托 `DOCUMENT_AGENT_CORE.retrieve_document`; no new retriever/index/store | legacy RAG/Evidence + 完整 `tests/` | 受影响 `110 passed in 4.05s`；首次 `396 passed in 37.70s`；最终 `396 passed in 30.07s` | 未改 `hybrid_retrieval.py`、`document_index.retrieve_document`、Evidence schema 或 ToolRegistry |

### 12.1 V4.9 边界与限制

- 控制层不包含新的 keyword/vector/rerank 实现或 Evidence 存储；所有候选与 Evidence 来自
  V3 `retrieve_document()`，因此 RAG 2.0 的硬 scope 与 fallback 行为保持权威。
- 默认 Need builder 只对有明确多步骤/规则/比较信号的复杂请求进行保守拆分；调用方可用
  严格 schema 显式提供 Need，但不能预先声明 sufficient 绕过本次检索。
- timeout 是整个控制循环的墙钟预算；为避免线程化改变 SQLite/RAG 2.0 行为，当前同步
  retrieval 返回后才判断超时并停止后续调用，不对正在执行的底层调用做强制取消。
- 本阶段未把控制层注册为新 LLM Tool，保持既有 Tool definitions 精确兼容；V4.10/V4.11
  安全专项与 Evaluation 未提前实现。

## 13. 后续阶段审计规则

每个 V4 阶段开始前，应以正式阶段需求补充或拆分对应 Requirement ID，并填写：

- 可验证的 Requirement 描述与 Priority；
- 实际 Code Location 和 Test Location；
- 实现状态及其证据；
- 完整 pytest 和阶段专项测试的真实 Verification Result；
- 未完成项、限制、兼容性风险和阻塞原因。

只有代码实现、对应测试和要求的回归验证全部完成后，才能将条目标记为 `VERIFIED`。
V1–V3 已有能力只能作为复用证据，不能替代 V4 新增要求的实现和测试。
