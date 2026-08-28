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
| V4-P0-003 | Visual OCR and Image PDF | P0 | V4.2 Visual OCR and Image PDF | NOT_STARTED | TBD | TBD | NOT_RUN | 具体图像 PDF 范围、OCR 契约和验收标准待确认 |
| V4-P0-004 | Handwriting Recognition | P0 | V4.3 Handwriting Recognition | NOT_STARTED | TBD | TBD | NOT_RUN | 具体手写材料范围、输出结构和质量验收标准待确认 |
| V4-P0-005 | Visual Understanding and KIE | P0 | V4.4 Visual Understanding and KIE | NOT_STARTED | TBD | TBD | NOT_RUN | KIE 字段、Schema、证据与失败语义待确认 |
| V4-P0-006 | Visual Table | P0 | V4.5 Visual Table | NOT_STARTED | TBD | TBD | NOT_RUN | 表结构、单元格定位和兼容策略待确认 |
| V4-P0-007 | Evidence 3.0 | P0 | V4.6 Evidence 3.0 | NOT_STARTED | TBD | TBD | NOT_RUN | 必须增量复用 Evidence 2.0；新增契约待确认 |
| V4-P0-008 | General Document Agent Core | P0 | V4.7 General Document Agent Core | NOT_STARTED | TBD | TBD | NOT_RUN | 核心 Agent 边界、Tool 与 Runtime 复用方案待确认 |
| V4-P0-009 | Domain Router | P0 | V4.8 Domain Router | NOT_STARTED | TBD | TBD | NOT_RUN | 领域集合、路由契约和 fallback 待确认 |
| V4-P0-010 | Agentic Retrieval | P0 | V4.9 Agentic Retrieval | NOT_STARTED | TBD | TBD | NOT_RUN | 必须保持 RAG 2.0 兼容；规划、检索与停止条件待确认 |
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

## 5. 后续阶段审计规则

每个 V4 阶段开始前，应以正式阶段需求补充或拆分对应 Requirement ID，并填写：

- 可验证的 Requirement 描述与 Priority；
- 实际 Code Location 和 Test Location；
- 实现状态及其证据；
- 完整 pytest 和阶段专项测试的真实 Verification Result；
- 未完成项、限制、兼容性风险和阻塞原因。

只有代码实现、对应测试和要求的回归验证全部完成后，才能将条目标记为 `VERIFIED`。
V1–V3 已有能力只能作为复用证据，不能替代 V4 新增要求的实现和测试。
