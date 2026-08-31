# Student Document Agent V4 Development Log

## 1. V4 项目背景

Student Document Agent V4 在 V1–V3 已稳定交付的代码和测试体系上增量开发。
V4 将围绕视觉文档处理、视觉理解、Evidence 3.0、通用文档 Agent、领域路由、
Agentic Retrieval、视觉安全与可观察性继续演进，同时保持 Excel、Word、PDF、
RAG 2.0、Evidence 2.0、Workflow Runtime、Async Task、Agent Safety、
Diff / Version / Rollback、Trace 以及 V1–V3 Evaluation 的兼容与回归安全。

V4.0 只建立工程开发基线，不实现 V4 新业务能力，不修改 V1–V3 核心业务代码。

## 2. V3 最终稳定基线

| 项目 | 值 |
| --- | --- |
| Tag | `v3-final-stable` |
| Commit | `16bee95fb699c76e5200467d147161e3c81ec262` |
| V4 开发分支 | `feature/v4-development` |
| V4.0 核验结果 | HEAD、tag 解引用结果及 merge-base 均为上述 commit |
| 初始工作区 | 干净，无未知残留修改 |

## 3. V4 开发原则

- 基于 V1–V3 稳定实现增量开发，优先复用现有模块和接口。
- 每一阶段只实现该阶段明确范围，不提前建设后续阶段。
- 不进行需求范围外的大规模重构。
- 所有新增能力必须有对应自动化测试，所有原有测试必须继续通过。
- 每阶段完成后执行完整 `pytest -q`；项目既有测试环境为 Conda `tuli_env`，
  因此实际命令记录为 `conda run -n tuli_env pytest -q`。
- 不删除、跳过或弱化既有测试，不编造测试数量、性能或质量指标。
- 阶段结束时更新本日志和 `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`。
- 开发完成后由维护者人工执行 commit 和 tag。

## 4. V4 开发阶段规划

| Stage | 主题 | 当前状态 | 阶段边界 |
| --- | --- | --- | --- |
| V4.0 | Baseline | VERIFIED | 核验 V3 基线，建立 V4 日志、覆盖矩阵和测试基线；不开发新功能 |
| V4.1 | Visual Document Router | VERIFIED | 图片正式进入既有 Document、生命周期、Workspace、Preview、Task 与原子重处理链路 |
| V4.2 | Visual OCR and Image PDF | VERIFIED | 复用 V3 PDF OCR，统一图片、扫描/图片型 PDF 与 mixed PDF 的 page-level Visual OCR |
| V4.3 | Handwriting Recognition | VERIFIED | 在统一 Visual OCR region 管线中支持手写类型、低置信度安全、冲突核对与 region retry |
| V4.4 | Visual Understanding and KIE | VERIFIED | 基于 DocumentBlock/OCR region 派生 VisualBlock、保守分类与带来源的基础 KIE |
| V4.5 | Visual Table | VERIFIED | 复用 V3 Table/Row/Column/Cell，支持视觉/手写 Cell、可靠计算门槛与安全降级 |
| V4.6 | Evidence 3.0 | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.7 | General Document Agent Core | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.8 | Domain Router | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.9 | Agentic Retrieval | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.10 | Visual Safety and Trace | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.11 | Evaluation and Final Acceptance | NOT_STARTED | 汇总 V4 固定评估、完整回归与最终验收 |

状态统一使用：`NOT_STARTED`、`PARTIAL`、`IMPLEMENTED`、`VERIFIED`、`BLOCKED`。

## 5. 当前测试基线

### 5.1 V3 最终接受基线

V3 最终稳定文档记录的完整测试基线为 `313 passed`。该数字仅作为历史基线，
V4 阶段结果以下述实际执行为准。

### 5.2 V4.0 修改前实测

执行日期：2026-08-28

直接执行 `pytest -q` 时，当前基础 shell 未安装或未暴露 `pytest`，命令返回
`pytest: command not found`，未启动测试收集。随后按项目既有环境执行：

```bash
conda run -n tuli_env pytest -q
```

真实结果：

```text
313 passed in 18.10s
```

结论：修改前完整测试基线通过，与 V3 最终接受基线一致。

## 6. V4 阶段开发记录

### V4.0 Baseline

- 本阶段目标：核验 V3 稳定基线，建立 V4 开发日志、P0 需求覆盖矩阵和完整测试基线。
- 实际修改文件：
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：无。
- 新增测试：无；本阶段没有新增业务能力，仅执行既有完整测试集。
- 测试结果：修改前 `313 passed in 18.10s`；修改后 `313 passed in 18.31s`，均为 PASS。
- 未完成项：V4.1–V4.11 均未开始；各阶段细化需求和验收标准待后续阶段输入。
- 已知限制：仓库中未发现独立 V4 需求原文，本阶段矩阵仅依据已给出的阶段名称建立
  顶层 P0 条目，不能替代后续正式需求分解。

### V4.1 Visual Document Router

- 本阶段目标：让 JPG/JPEG/PNG 成为正式 Document 类型，并通过统一 Input Router
  进入现有 File、生命周期、原子索引、Workspace、Preview 和 Task Center，不建设
  平行文件管理系统。
- 基线 commit：`e1f3e833e3a0398a1f8c740bce1d7834b37c42c3`。
- 实际修改文件：
  - `backend/main.py`
  - `backend/repositories/file_repository.py`
  - `backend/runtime/async_task_runtime.py`
  - `backend/services/document_index.py`
  - `backend/services/file_lifecycle.py`
  - `backend/services/file_upload.py`
  - `backend/services/file_view.py`
  - `backend/services/input_router.py`
  - `backend/services/task_view.py`
  - `backend/tools/file_tools.py`
  - `frontend/app.py`
  - `frontend/components/file_panel.py`
  - `frontend/components/task_center.py`
  - `requirements.txt`
  - `tests/test_visual_document_router_v4.py`
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：
  - 新增内部 `route_document_input()`、`route_pdf_pages()`、
    `route_pdf_detection()`、`validate_declared_mime()`、`detect_image()`、
    `build_image_preview()`。
  - `save_uploaded_file()` 增加可选 `declared_mime_type`，保持旧调用兼容。
  - `POST /api/files/upload` 扩展 JPG/JPEG/PNG 与 MIME 校验。
  - `GET /api/files` 的 `file_type` 扩展 `image`，生命周期筛选支持 canonical 状态。
  - 既有 `GET /api/files/{file_id}/preview` 返回受限图片缩略图。
  - 既有 `POST /api/files/{file_id}/reprocess|reindex` 支持图片。
  - 既有 Async `layout/index/reindex` Task 接受图片并向 Task Center 暴露文档类型与名称。
  - canonical lifecycle 增加 `VISUAL_PROCESSING`；兼容字段仍复用 V2/V3 列。
- 路由结果：Excel → `STRUCTURED`；Word → `TEXT`；文本 PDF → `TEXT`；
  扫描或 mixed PDF → `VISUAL`；JPG/JPEG/PNG → `VISUAL`；未知类型 →
  `SAFE_REJECT` 与可解释错误。
- 数据与安全策略：图片继续登记到 `files`，image Block/空文本 chunk 继续写入
  `document_chunks`，激活仍使用原子事务。V4.1 不伪造 OCR 文本；Block 明确带有
  `VISUAL_TEXT_EXTRACTION_DEFERRED_TO_V4_2`。
- 新增测试：`tests/test_visual_document_router_v4.py`，共 13 个 pytest cases，覆盖
  JPG/JPEG/PNG、扩展名与内容不匹配、损坏图片、unsupported MIME、状态迁移、
  处理失败摘要、批量部分失败、reprocess 成功/失败原子保留、Workspace/Preview、
  Task Center、前端最小接入以及 Excel/Word/PDF/unknown 路由。
- 测试结果：专项 `13 passed in 1.41s`；受影响 V3 回归组
  `93 passed in 11.12s`；首次完整回归 `326 passed in 20.43s`；文档更新后完整回归
  `326 passed in 16.50s`。
- 未完成项：图片 OCR、手写识别、KIE、视觉表格、Evidence 3.0、Domain Router、
  Agentic Retrieval 均未在本阶段实现。
- 已知限制：V4.1 只完成图片格式验证、视觉路由、布局占位和安全原子激活；图片不产生
  可检索文本或 Evidence。图片上传仍沿用现有同步上传接口；Task Center 支持图片的
  layout/index/reindex 长任务，但上传本身不会额外创建异步任务。
- Requirement IDs：`V4.1-P0-01` 至 `V4.1-P0-14`。

### V4.2 Visual OCR、Image PDF 与 Mixed PDF Unified Pipeline

- 本阶段目标：在 V3 扫描 PDF 的逐页 OCR、mixed PDF 路由、异步任务、索引和原子
  reprocess 基础上，建立一个同时服务 JPG/JPEG/PNG、扫描/图片型 PDF 和 mixed PDF
  视觉页的统一 OCR 管线，不建设第二套 PDF OCR。
- 基线 commit：`352b23676f4df3db06f3c8eb153b23d160f7854e`。
- 实际修改文件：
  - `backend/services/ocr_service.py`
  - `backend/services/document_index.py`
  - `backend/runtime/async_task_runtime.py`
  - `backend/repositories/ocr_repository.py`
  - `backend/database.py`
  - `tests/test_ocr_pipeline.py`
  - `tests/test_visual_ocr_pipeline_v4.py`
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：
  - 新增内部 `ocr_visual()`、`ocr_image()`、`normalize_region_record()`，既有
    `ocr_pdf()` / `ocr_document()` 复用同一适配器和页渲染/OCR 引擎。
  - 新增 `OCRRegionResult` 等价契约，包含 `file_id`、`page_no`/`image_no`、
    `region_id`、`text`、`bbox`、`confidence`、`recognition_type`、
    `source_model`、`source_parser`、`status`。
  - 新增 `get_document_ocr_regions()` 只读扁平视图；region 继续原子保存于 V3
    `document_ocr_pages.blocks_json`，未新增平行 OCR 表或迁移。
  - 既有 `ocr_document()` 与 Async OCR Task 扩展为接受 image Document；PDF 仍由
    V3 page-level router 决定文本页或视觉页。
- 数据与失败策略：只把 `success` 且非空的 region 文本写入检索 chunk；`empty` 与
  `failed` region 仍保存位置、置信度和错误，不补写或推测文字。文本页不再进入 OCR，
  因而不会与 OCR 文本重复。单页失败返回 `partial_success` 和 `failed_pages`；刷新失败页
  可继续使用旧成功页，并以 `refresh_status=failed` 暴露本次失败供定向 retry。
- 新增/更新测试：新增 `tests/test_visual_ocr_pipeline_v4.py` 的 8 个 pytest cases，覆盖
  printed JPG、PNG、中英文数字混合、image-only PDF、mixed PDF page routing、文本/OCR
  去重、empty region、partial page failure、扫描 PDF 旧结果保护、图片 Async OCR 失败与
  仅失败页 retry；同步更新 V3 OCR 契约断言以验证新增字段及持久化视图，未删除、跳过或
  弱化旧行为断言。
- 测试结果：V4.2 专项 `8 passed in 0.88s`；OCR/Async/V4.1 关键回归组
  `49 passed in 16.81s`；首次完整回归 `334 passed in 27.68s`；文档更新后最终完整
  回归 `334 passed in 27.25s`。
- 未完成项：完整手写识别、KIE、视觉表格、Evidence 3.0、Agentic Retrieval 与
  Domain Router 均未在本阶段开发。
- 已知限制：本阶段 `recognition_type` 仅为 `printed`；OCR 效果取决于已有
  RapidOCR 运行环境和输入质量，未声明或编造准确率。失败页 retry 仍通过既有任务 API
  触发，不新增自动无限重试机制。
- Requirement IDs：`V4.2-P0-01` 至 `V4.2-P0-12`。

### V4.3 Handwriting Recognition

- 本阶段目标：在 V4.2 Visual OCR Pipeline 内增量支持 `printed`、`handwritten`、
  `mixed`、`unknown`，并建立手写低置信度、关键字段、模型冲突和局部 region retry 的
  可审计安全语义；未创建独立 OCR 管线或新存储表。
- 基线：V4.2 tag `v4.2-visual-ocr`，commit
  `f690506ace691b363acd13be4860ae9434359087`。
- 实际修改文件：
  - `backend/services/ocr_service.py`
  - `backend/services/document_index.py`
  - `backend/runtime/async_task_runtime.py`
  - `backend/tools/hybrid_tools.py`
  - `backend/agent.py`
  - `tests/fixtures/v4_handwriting_cases.json`
  - `tests/test_handwriting_recognition_v4.py`
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：
  - `OCRRegionResult.recognition_type` 扩展为四种类型，region 状态扩展
    `low_confidence`，并增加 `key_field_type`、`review_required`、
    `review_reason`、`safe_for_high_impact`、`safe_for_identity_match` 和
    `conflict_sources`。
  - 新增 `HandwritingThresholds` / `get_handwriting_thresholds()`；通过
    `HANDWRITING_USABLE_CONFIDENCE`、`HANDWRITING_REVIEW_CONFIDENCE`、
    `HANDWRITING_KEY_FIELD_CONFIDENCE` 配置归一化置信度策略，要求
    `usable <= review <= key_field`。
  - 新增 `retry_visual_regions()`，按既有 bbox 只裁剪并重试指定 region；新增
    `retry_ocr_regions()`，将成功结果合并进原 page ledger 并通过既有原子索引事务激活。
  - 既有 `ocr_document()` 与 Async OCR payload 增加可选 `region_ids`；与 `pages`
    互斥，checkpoint 保存 `failed_region_ids`，Task retry 优先只重试失败 region。
- 安全策略：手写/unknown 的极低置信度结果保存原始 backend 输出但不写入检索 chunk；
  姓名、学号、电话、金额、日期只做风险标签（不是 KIE），低于配置阈值时禁止作为
  high-impact 或唯一身份匹配依据并保留人工核对标记。模型候选文本冲突时不选边，
  主文本留空，完整保存双方来源；奖学金高影响规则提取会拒绝 unsafe Evidence。
- 新增测试：固定样本描述 `tests/fixtures/v4_handwriting_cases.json` 与
  `tests/test_handwriting_recognition_v4.py` 的 12 个 pytest cases，覆盖 clear、medium、
  extremely unclear、printed+handwritten mixed、低置信度姓名/电话、partial region、
  model conflict、Async region crop retry、threshold 配置、unknown 以及高影响阻断。
- 测试结果：V4.3 专项 `12 passed in 0.55s`；OCR/Async/Agent Safety 关键回归
  `84 passed in 5.29s`；V4.1/V4.2/V4.3 兼容组 `33 passed in 12.70s`。首次完整回归
  为 `345 passed, 1 failed in 29.13s`，定位并修复普通空白图片 reindex 状态兼容问题；
  修复后完整回归 `346 passed in 30.09s`；文档更新后最终完整回归
  `346 passed in 30.19s`。
- 未完成项：KIE、视觉表格、Evidence 3.0、Domain Router、Agentic Retrieval 以及真实
  手写 Evaluation 未在本阶段开发。
- 已知限制：固定样本验证的是可计算契约、管线和安全行为，不代表手写准确率；默认本地
  backend 仍为既有 RapidOCR，专用手写 backend 需按同一归一化 region 契约接入。
  clear/medium/extremely unclear 的真实质量和“七七八八可读”等指标留待 V4.11 固定数据集
  人工评估，本阶段不声明准确率或性能数据。
- Requirement IDs：`V4.3-P0-01` 至 `V4.3-P0-12`。

### V4.4 Visual Document Understanding

- 本阶段目标：在 V3 `DocumentBlock` 和 V4 OCR region 上建立统一 `VisualBlock`，提供
  基础文档分类与带 source region 的 KIE 候选；不开发通用图片问答、自然图像识别、
  人脸识别或复杂图表推理。
- 基线：V4.3 tag `v4.3-handwriting-recognition`，commit
  `5822f657c271fecb20ecd9c3b4d79455caf06610`。
- 实际修改文件：
  - `backend/document_blocks.py`
  - `backend/services/ocr_service.py`
  - `backend/services/visual_understanding.py`
  - `backend/tools/document_tools.py`
  - `tests/test_visual_understanding_v4.py`
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：
  - V3 `BlockType` 增量增加 `signature`、`stamp`、`unknown`；新增继承
    `DocumentBlock` 的 `VisualBlock`，对外等价暴露 `document_id`、`image_no`、`text`、
    `recognition_type`、`source_model` 和 `source_region_ids`。
  - OCR region 增加可选 `visual_block_type`，允许 layout backend 在同一 ledger 中提供
    保守布局提示。
  - 新增 `extract_visual_blocks()`、`classify_document()`、`extract_key_fields()`，并从
    `backend.tools.document_tools` 导出。
  - `classify_document()` 支持 form/certificate/notice/table/letter/report/unknown；匹配失败
    或分类异常均返回 `unknown`、`general_query_allowed=true`，不阻断既有检索。
  - `extract_key_fields()` 支持 `general` 与 `student` domain；General 保留原 label/value，
    Student 才使用默认或调用方提供的受控 schema hint。
- 数据与安全策略：VisualBlock、分类和 KIE 均从 active OCR region 只读派生，不新增表，
  不覆盖原始 OCR。每个 KIE field 绑定 `block_id`、`region_id`、bbox、confidence、parser、
  model 和稳定 evidence ID；所有字段都是候选，`is_confirmed_fact=false`。低 confidence、
  手写 review 或 unsafe region 只能输出 `review_required`，不得用于高影响事实。
- 新增测试：`tests/test_visual_understanding_v4.py` 共 9 个 pytest cases，覆盖 certificate、
  form、notice；title/paragraph/table/image/signature/stamp/unknown；KIE field+bbox+region
  evidence；低置信度 KIE；unknown 通用检索；分类异常 fallback；Student/General schema 隔离。
- 测试结果：V4.4 专项 `9 passed in 0.64s`；Document Block/OCR/Evidence/Async 受影响回归
  `87 passed in 5.81s`；首次完整回归 `355 passed in 28.92s`；文档更新后最终完整回归
  `355 passed in 29.65s`；中断恢复后重新核验 `355 passed in 31.98s`。
- 未完成项：复杂视觉表格结构属于 V4.5；Evidence 3.0、Domain Router、Agentic Retrieval
  及真实分类/KIE Evaluation 未在本阶段开发。
- 已知限制：分类为保守、确定性的基础规则，不代表真实分类准确率；KIE 只解析明确的
  `label: value` 文本，不推断缺失字段、不合并跨 region 内容，也不将通用字段强制映射为
  学生字段。VisualBlock 是可重复派生视图，原子事实源仍为 OCR page/region ledger。
- Requirement IDs：`V4.4-P0-01` 至 `V4.4-P0-12`。

### V4.5 Visual Table and Handwritten Table Processing

- 本阶段目标：优先复用 V3 `TableStructure` / `TableRow` / `TableColumn` / `TableCell`，
  在 OCR region 和 VisualBlock 上支持清晰打印图片表格、基础手写表格、Cell 几何与
  可靠性门槛；结构不可靠时保留 table block、OCR 文本和原图 region，不伪造 Cell。
- 基线：V4.4 tag `v4.4-visual-understanding`，commit
  `947ce61255dc49b768b2ebc48c1150aace9736e3`。
- 实际修改文件：
  - `backend/table_structure.py`
  - `backend/services/ocr_service.py`
  - `backend/services/visual_table.py`
  - `backend/tools/document_tools.py`
  - `tests/test_visual_table_v4.py`
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：
  - V3 `TableCell` 增加 `image_no`、recognition/source region/parser/model、
    `status` 与 `safe_for_calculation`；Table、Row、Column 增加视觉 page/image/bbox，
    Table 增加 confidence、recognition、source regions 和 fallback。
  - `build_simple_table()` 与 `degraded_table()` 保持旧调用兼容，同时接受视觉几何与来源。
  - OCR region 增加可选 `table_id_hint`、row/column index、row/column span、table bbox 和
    structure hint；继续保存在原 `document_ocr_pages.blocks_json`。
  - 新增 `extract_visual_tables()` 与 `calculate_visual_table()`，并从既有
    `backend.tools.document_tools` 导出。
- 可靠性与安全策略：只有显式提供、坐标唯一、从 0 连续、完整矩形且每个候选 Cell
  都有 bbox 时才调用 V3 `build_simple_table()`。缺格、重复坐标、合并 Cell、无边框手绘
  或无几何信息时直接生成 `degraded_table()`，Row/Column/Cell 均为空。手写低 confidence
  Cell 保留文本、bbox 和 confidence，但 `safe_for_calculation=false`。
- 精确计算：调用方必须显式提交同一可靠 Table 的 Cell IDs；服务再次校验 Table/Cell
  状态和严格数值格式后，才交给 Python `Decimal` 执行 sum/avg/min/max/count。低置信度、
  非数值或降级表格均返回可解释失败，不存在 LLM 从图片读取或猜测数字的路径。
- 新增测试：`tests/test_visual_table_v4.py` 共 8 个 pytest cases，覆盖 printed table
  image、基础 handwritten table、Table/Cell page/image/bbox/text/confidence、低置信度手写
  Cell、Python Decimal 计算及非数值拒绝、merged-cell 降级、borderless hand-drawn 降级、
  incomplete grid 零 Cell fallback 和原文继续可查询。
- 测试结果：V4.5 专项 `8 passed in 0.60s`；Table/Visual/OCR/RAG/Evidence 受影响回归
  `121 passed in 6.27s`；首次完整回归 `363 passed in 31.09s`；文档更新后最终完整回归
  `363 passed in 29.48s`。
- 未完成项：复杂合并单元格、无边框手绘结构推断、复杂表格语义/图表推理、Evidence 3.0、
  Domain Router 与 Agentic Retrieval 未在本阶段开发。
- 已知限制：视觉 layout backend 必须给出明确 cell row/column 与 bbox；本阶段不从 OCR
  文本猜测网格。Cell confidence 使用可配置的归一化阈值
  `VISUAL_TABLE_CELL_CONFIDENCE_THRESHOLD`（默认 0.85），不声明表格识别准确率。
- Requirement IDs：`V4.5-P0-01` 至 `V4.5-P0-12`。

### 每阶段开发记录模板

```markdown
### V4.x <Stage Name>

- 本阶段目标：
- 基线 commit：
- 实际修改文件：
- 新增/修改接口：
- 新增测试：
- 测试命令：`conda run -n tuli_env pytest -q`
- 测试结果：
- Requirement IDs：
- 未完成项：
- 已知限制：
- 推荐 commit message：
- 推荐 tag：
```

## 7. 回归测试记录

| 日期 | Stage | Commit/工作树 | 命令 | 结果 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-28 | V4.0 修改前 | `16bee95fb699c76e5200467d147161e3c81ec262` | `conda run -n tuli_env pytest -q` | `313 passed in 18.10s` | PASS；V3 稳定基线复现 |
| 2026-08-28 | V4.0 修改后 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `313 passed in 18.31s` | PASS；仅新增文档，无业务回归 |
| 2026-08-28 | V4.1 专项 | 未提交工作树 | `conda run -n tuli_env pytest -q tests/test_visual_document_router_v4.py` | `13 passed in 1.41s` | PASS |
| 2026-08-28 | V4.1 受影响回归 | 未提交工作树 | 受影响的上传/生命周期/PDF/OCR/Workspace/Async/前端测试 | `93 passed in 11.12s` | PASS |
| 2026-08-28 | V4.1 完整回归（首次） | 未提交工作树 | `conda run -n tuli_env pytest -q` | `326 passed in 20.43s` | PASS |
| 2026-08-28 | V4.1 文档更新后回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `326 passed in 16.50s` | PASS |
| 2026-08-28 | V4.2 专项 | 未提交工作树 | `conda run -n tuli_env pytest -q tests/test_visual_ocr_pipeline_v4.py` | `8 passed in 0.88s` | PASS |
| 2026-08-28 | V4.2 关键回归 | 未提交工作树 | OCR、Async Task、V4.1 Router 相关测试 | `49 passed in 16.81s` | PASS |
| 2026-08-28 | V4.2 完整回归（首次） | 未提交工作树 | `conda run -n tuli_env pytest -q` | `334 passed in 27.68s` | PASS |
| 2026-08-28 | V4.2 文档更新后最终回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `334 passed in 27.25s` | PASS |
| 2026-08-28 | V4.3 专项 | 未提交工作树 | `conda run -n tuli_env pytest -q tests/test_handwriting_recognition_v4.py` | `12 passed in 0.55s` | PASS；固定样本管线测试，非准确率指标 |
| 2026-08-28 | V4.3 关键回归 | 未提交工作树 | OCR、Async、Agent Safety 相关测试 | `84 passed in 5.29s` | PASS |
| 2026-08-28 | V4.3 首次完整回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `345 passed, 1 failed in 29.13s` | 发现 V4.1 空白图片普通 reindex 状态兼容问题，已修复 |
| 2026-08-28 | V4.3 修复后完整回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `346 passed in 30.09s` | PASS |
| 2026-08-28 | V4.3 文档更新后最终回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `346 passed in 30.19s` | PASS |
| 2026-08-28 | V4.4 专项 | 未提交工作树 | `conda run -n tuli_env pytest -q tests/test_visual_understanding_v4.py` | `9 passed in 0.64s` | PASS |
| 2026-08-28 | V4.4 受影响回归 | 未提交工作树 | Document Block、OCR、Evidence、Async 相关测试 | `87 passed in 5.81s` | PASS |
| 2026-08-28 | V4.4 首次完整回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `355 passed in 28.92s` | PASS |
| 2026-08-28 | V4.4 文档更新后最终回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `355 passed in 29.65s` | PASS |
| 2026-08-31 | V4.4 中断恢复后复核 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `355 passed in 31.98s` | PASS；使用重新执行的可确认结果 |
| 2026-08-31 | V4.5 专项 | 未提交工作树 | `conda run -n tuli_env pytest -q tests/test_visual_table_v4.py` | `8 passed in 0.60s` | PASS |
| 2026-08-31 | V4.5 受影响回归 | 未提交工作树 | Table、Visual、OCR、RAG、Evidence 相关测试 | `121 passed in 6.27s` | PASS |
| 2026-08-31 | V4.5 首次完整回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `363 passed in 31.09s` | PASS |
| 2026-08-31 | V4.5 文档更新后最终回归 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `363 passed in 29.48s` | PASS |

后续阶段必须追加实际执行记录，不得以历史结果替代当前回归。

## 8. V4 最终验收记录

| 项目 | 结果 |
| --- | --- |
| 最终版本 | 待 V4.11 登记 |
| 最终 commit | 待 V4.11 登记 |
| 最终 tag | 待 V4.11 登记 |
| P0 Requirements Coverage | 待 V4.11 审计 |
| 完整 pytest | 待 V4.11 实测 |
| V1–V3 Regression | 待 V4.11 实测 |
| V4 Evaluation | 待 V4.11 实测 |
| 已知限制 | 待 V4.11 汇总 |
| 最终结论 | 待 V4.11 验收 |
