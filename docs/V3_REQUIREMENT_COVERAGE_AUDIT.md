# 学生材料智能文档助手 V3.12 最终需求覆盖审计

> 审计日期：2026-08-27  
> 审计性质：只读检查；除本报告外未修改代码、数据库、迁移、Tool、Prompt、依赖或前端。  
> 状态词仅使用：`IMPLEMENTED`、`PARTIAL`、`NOT_IMPLEMENTED`、`NOT_APPLICABLE`。

## 1. 审计基线与证据边界

| 项目 | 结果 |
| --- | --- |
| Branch | `feature/v3-development` |
| Commit | `f4bd609703ebea8d0a75fdb30dc10c8c6cdf98e4` |
| Commit subject | `docs: add v3 demo acceptance report` |
| 基础版本 | `V2.99-Final-v2` |
| 审计目标 | 当前工作树中的 V3 实现 |
| 审计前已有未提交修改 | `frontend/app.py`、`tests/test_frontend_workspace.py` |
| 本轮唯一新增文件 | `docs/V3_REQUIREMENT_COVERAGE_AUDIT.md` |

已检查 `README.md`、`docs/`、`backend/`、`frontend/`、`tests/`、`evals/`、`migrations/`、数据库初始化与 Repository/Service/Runtime/API/Tool 代码。项目中未找到独立命名的《V1 项目需求与测试说明》《V2 项目需求与测试说明》《第三版（V3）项目需求与测试说明》或“V3 AI Coding 提示词汇总”原文。因此，本报告以本次 V3.12 审计要求、现有 V2 测试清单、V2/V3 Demo 文档、当前代码和可运行测试为证据；F/O/R/WF/S 编号为对审计要求的可追踪映射，不声称复原缺失正式文档中的逐字定义。

README 和 Demo 文档中的声明只用于定位，不单独作为“已实现”证据。所有判定均回到代码、Schema、API、前端入口、测试或只读诊断。

## 2. 测试与回归结果

### 2.1 完整 pytest

执行命令：

```bash
conda run -n tuli_env pytest
```

```text
Total:    238
Passed:   238
Failed:   0
Skipped:  0
Duration: 10.06s
Result:   PASS
```

### 2.2 V1 回归

按 V1 核心测试文件执行，并排除属于后续版本的单个 API 节点：

```text
66 passed, 1 deselected in 2.58s
V1 Regression: PASS
```

Excel 查询、Word 读写、学生搜索与学号关联、重名消歧、多文档查询、学生比较、Python 精确计算、综合评价、HITL/写入确认、聊天/文件/操作记录、Tool Calling 和无数据不编造均有现存代码及回归测试支撑；未发现 V1 失败项。

### 2.3 V2 回归

按 `evals/coverage_manifest.json` 中 V2 固定节点执行：

```text
60 passed in 2.95s
V2 Regression: PASS
```

Schema Discovery、多 Sheet、字段语义映射、动态查询/统计、动态上传文件隔离、跨文档关联、文本 PDF、Word/PDF RAG、混合查询、Evidence、Task/Step、Retry/Resume、冲突/质量检查、批量任务与 partial failure、Diff/Version/Undo/Rollback、Trace、上下文及敏感字段保护均未出现测试回归。

> 注：V1/V2 回归 PASS 代表现有固定回归集合全部通过，不等于 V3 新增要求全部满足。

## 3. V3 Requirement Coverage Matrix

### 3.1 Document Workspace（F01-F12）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| F01 | Workspace | P0 | 文件列表含名称、类型、大小、上传时间 | IMPLEMENTED | `backend/services/file_view.py`; `backend/main.py`; `frontend/components/file_panel.py` | `tests/test_document_workspace.py`; `tests/test_frontend_workspace.py` | API 与卡片均覆盖 | 无 | 保持兼容 |
| F02 | Workspace | P0 | 展示来源 system/upload、状态与 queryable | IMPLEMENTED | `backend/services/file_view.py`; `frontend/components/file_panel.py` | `tests/test_document_workspace.py` | 数据模型和展示存在 | 无 | 保持兼容 |
| F03 | Workspace | P0 | 文件详情展开及类型化元数据 | PARTIAL | `frontend/components/file_panel.py`; `backend/services/file_view.py` | `tests/test_frontend_workspace.py` | 可展开并显示状态/元数据 | PDF/Excel/Word 的部分深层详情依赖 metadata，未提供内容预览 | 补充稳定详情契约和预览测试 |
| F04 | Workspace | P0 | 文件快速内容预览 | NOT_IMPLEMENTED | `frontend/components/file_panel.py` | 未发现 | 仅展示元数据 | 无 PDF/Excel/Word 内容预览器 | 新增只读预览 API 与组件 |
| F05 | Workspace | P0 | 文件名搜索、类型/状态筛选 | IMPLEMENTED | `backend/main.py`; `backend/services/file_view.py`; `frontend/components/file_panel.py` | `tests/test_document_workspace.py`; `tests/test_frontend_workspace.py` | API 与 UI 均覆盖 | 无 | 保持兼容 |
| F06 | Workspace | P0 | 来源筛选 | NOT_IMPLEMENTED | `backend/main.py`; `frontend/components/file_panel.py` | 未发现 | API/UI 均无 source filter | 无法只看 system 或 upload | 增加后端枚举过滤与前端选项 |
| F07 | Workspace | P0 | 最近上传、文件名、大小、类型排序 | PARTIAL | `backend/services/file_view.py`; `frontend/components/file_panel.py` | `tests/test_document_workspace.py` | 时间与大小排序存在 | 文件名、文件类型排序缺失 | 扩展白名单排序字段 |
| F08 | Workspace | P0 | 拖拽与多文件上传 | IMPLEMENTED | `frontend/components/file_panel.py`; `frontend/api_client.py`; `backend/main.py` | `tests/test_frontend_workspace.py`; `tests/test_upload.py` | Streamlit uploader 支持多文件 | 无 | 保持兼容 |
| F09 | Workspace | P0 | 每文件独立结果与批量部分失败 | IMPLEMENTED | `frontend/api_client.py`; `frontend/components/file_panel.py` | `tests/test_frontend_workspace.py`; `tests/test_upload.py` | 上传按文件提交并显示结果 | 无 | 保持兼容 |
| F10 | Workspace | P0 | 重新解析入口 | NOT_IMPLEMENTED | `backend/main.py`; `frontend/components/file_panel.py` | 未发现 | 未找到 API 或 UI 操作 | 无法由 Workspace 触发 | 先修复旧结果保留，再添加入口 |
| F11 | Workspace | P0 | 重新索引入口 | NOT_IMPLEMENTED | `backend/main.py`; `frontend/components/file_panel.py` | 未发现 | 未找到 API 或 UI 操作 | 无法由 Workspace 触发 | 采用构建成功后切换 active 的入口 |
| F12 | Workspace | P0 | 上传文件可删除、系统固定文件禁止删除 | PARTIAL | `backend/main.py`; `backend/services/file_upload.py` | `tests/test_api.py`; `tests/test_upload.py` | 后端限制和删除 API 存在 | Workspace 无删除按钮/确认交互 | 前端接入现有安全删除流程 |

### 3.2 Document Lifecycle

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| LC01 | Lifecycle | P0 | 统一正常状态和按类型跳阶段 | IMPLEMENTED | `backend/repositories/file_repository.py`; `backend/services/file_lifecycle.py` | `tests/test_file_lifecycle_v3.py` | 八个统一状态及合法转换存在 | 无 | 保持旧字段镜像兼容 |
| LC02 | Lifecycle | P0 | 非法状态转换保护 | IMPLEMENTED | `backend/repositories/file_repository.py` | `tests/test_file_lifecycle_v3.py` | `QUERYABLE -> UPLOADED` 被拒绝 | 无 | 保持测试 |
| LC03 | Lifecycle | P0 | 状态变化 Trace | IMPLEMENTED | `backend/services/file_lifecycle.py`; `backend/services/trace_service.py` | `tests/test_file_lifecycle_v3.py` | 每次实际转换写 Trace | 无 | 保持兼容 |
| LC04 | Lifecycle | P0 | 失败错误摘要与恢复 | PARTIAL | `backend/services/file_lifecycle.py`; `backend/repositories/file_repository.py` | `tests/test_file_lifecycle_v3.py` | 通用 FAILED、error、resume_status 可恢复 | 未细分 parse/ocr/layout/index failed | 增加向后兼容的失败阶段字段 |
| LC05 | Lifecycle | P0 | partial/cleanup/reprocessing/reindexing 状态 | NOT_IMPLEMENTED | `backend/repositories/file_repository.py` | 未发现 | 统一枚举中不存在 | 无法表达 OCR 部分成功和重处理阶段 | 设计兼容状态扩展 |
| LC06 | Lifecycle | P0 | 重新解析失败保持旧有效结果且不混用 | PARTIAL | `backend/services/document_index.py`; `backend/repositories/document_repository.py` | 未发现专门测试 | 成功替换在事务内完成 | 缺少显式 generation/active 版本和失败保留验收 | 建立双版本/active 切换测试 |
| LC07 | Lifecycle | P0 | 重新索引构建成功后切换 active | NOT_IMPLEMENTED | `backend/services/document_index.py`; `backend/repositories/document_repository.py` | 未发现 | 失败诊断显示旧 chunk 被清空 | 新索引失败会破坏旧有效检索结果，属 BLOCKER | 第一优先级改为 shadow build + atomic activate |
| LC08 | Lifecycle | P0 | 失败后查询状态与旧结果一致 | NOT_IMPLEMENTED | `backend/services/document_index.py::_index_failure` | 本次只读临时诊断 | 1 个旧 chunk 在失败后变为 0，状态 FAILED | 数据安全不满足 V3 P0 | 加失败注入与旧结果保持回归测试 |

### 3.3 OCR Pipeline（O01-O10）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| O01 | OCR | P0 | 检测 text/scanned PDF | IMPLEMENTED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | `tests/test_ocr_pipeline.py` | text 与全扫描分支已测 | 无 | 保持兼容 |
| O02 | OCR | P0 | 文本 PDF 不重复 OCR | IMPLEMENTED | `backend/services/document_index.py` | `tests/test_ocr_pipeline.py` | 有文本时走普通解析 | 无 | 保持测试 |
| O03 | OCR | P0 | 扫描 PDF 自动 OCR | IMPLEMENTED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | `tests/test_ocr_pipeline.py` | 无文本文件进入 OCR_PROCESSING | 测试主要使用注入适配器 | 增加受控真实引擎冒烟测试 |
| O04 | OCR | P0 | Mixed PDF 逐页检测与按页 OCR | NOT_IMPLEMENTED | `backend/services/ocr_service.py`; `backend/services/document_index.py` | 未发现 | 任一页有文本即整份跳过 OCR | 混合文档扫描页可能为空 | 增加逐页 classifier 与合并路径 |
| O05 | OCR | P0 | OCR 输出 page/text/confidence/bbox | IMPLEMENTED | `backend/services/ocr_service.py`; `backend/document_blocks.py` | `tests/test_ocr_pipeline.py` | 结构字段齐全 | 无 | 保持 Schema 校验 |
| O06 | OCR | P0 | OCR status 与失败原因 | IMPLEMENTED | `backend/services/file_lifecycle.py`; `backend/services/document_index.py` | `tests/test_ocr_pipeline.py` | PROCESSING/FAILED 和错误可追踪 | 仅文件级状态 | 扩展页级状态 |
| O07 | OCR | P0 | partial_success 与 failed_pages | NOT_IMPLEMENTED | `backend/services/ocr_service.py` | 未发现 | 任一异常导致整份 FAILED | 19/20 成功场景不能保留成功页 | 增加 page result ledger |
| O08 | OCR | P0 | 仅重试失败页/指定 pages OCR | NOT_IMPLEMENTED | `backend/services/ocr_service.py` | 未发现 | 接口只接受整个文件 | 无页选择、失败页重试 | 扩展受校验 pages 参数 |
| O09 | OCR | P0 | 低 confidence 提示 | NOT_IMPLEMENTED | `backend/services/ocr_service.py`; `frontend/components/file_panel.py` | 未发现 | 仅保存 confidence | 用户看不到低置信警告 | 定义阈值和 Evidence/UI 提示 |
| O10 | OCR | P0 | OCR 失败不伪造内容 | IMPLEMENTED | `backend/services/document_index.py`; `backend/services/ocr_service.py` | `tests/test_ocr_pipeline.py` | 失败返回错误且不创建伪造文本 | 无 | 保持负向测试 |

### 3.4 Layout、DocumentBlock 与 Table/Cell

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| B01 | Block | P0 | DocumentBlock 核心字段 | PARTIAL | `backend/document_blocks.py` | `tests/test_document_blocks.py` | file/page/id/type/content/bbox/confidence 存在 | 缺 parent_id、source_parser | 兼容扩展可选字段 |
| B02 | Block | P0 | title/paragraph/table/cell 类型 | IMPLEMENTED | `backend/document_blocks.py`; `backend/services/document_index.py` | `tests/test_document_blocks.py` | 四类可构建且可转 chunk | 无 | 保持旧 chunk 兼容 |
| B03 | Block | P0 | image/header/footer 类型 | NOT_IMPLEMENTED | `backend/document_blocks.py` | 未发现 | 枚举中不存在 | Layout 类型不完整 | 增加类型及降级策略 |
| B04 | Layout | P0 | PDF 真实版面分析 | NOT_IMPLEMENTED | `backend/services/document_index.py` | 未发现 | 普通 PDF 按页生成 paragraph | 无标题、表格、页眉页脚版面识别 | 引入可插拔 layout parser |
| B05 | Block | P0 | Block 持久化且保持旧 chunk | PARTIAL | `backend/repositories/document_repository.py`; `backend/services/document_index.py` | `tests/test_document_blocks.py` | Block 写入 chunk metadata，可生成旧 chunk | 无独立 Block 表/查询契约 | 若需稳定定位，增加兼容 Block 存储 |
| B06 | Table | P0 | Table/Row/Column/Cell 真实结构 | PARTIAL | `backend/services/document_index.py`; `backend/document_blocks.py` | `tests/test_document_blocks.py` | Word 表格生成 table/cell，cell 有行列 metadata | 无 table_id、Row/Column 实体和统一 bbox/confidence | 建立规范表格 Schema |
| B07 | Table | P0 | 简单二维表格 Cell 定位 | PARTIAL | `backend/services/document_index.py` | `tests/test_document_blocks.py` | 可保存 table_no/row_no/column_no | 命名与需求 Schema 不一致，定位依赖 metadata | 统一 table_id/row_index/column_index |
| B08 | Table | P0 | 合并单元格失败时安全降级 | NOT_IMPLEMENTED | `backend/services/document_index.py` | 未发现 | 未见 merged-cell 检测或降级标记 | 可能重复/错误构造 Cell | 保留原始 table block 并标结构不确定 |

### 3.5 RAG 2.0（R201-R210）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| R201 | RAG | P0 | Query Understand / Controlled Rewrite | PARTIAL | `backend/runtime/retry_executor.py`; `backend/agent.py` | `tests/test_rag_retry.py` | 重试时有确定性关键词降级 | 无通用受控 query understanding/rewrite 阶段 | 建立可审计规则式改写 |
| R202 | RAG | P0 | FTS5/BM25 关键词检索 | IMPLEMENTED | `backend/repositories/document_repository.py` | `tests/test_rag.py`; `tests/test_hybrid_retrieval.py` | FTS5 trigram + bm25，异常 LIKE fallback | 无 | 保持旧检索 |
| R203 | RAG | P0 | Embedding 接口与 Vector Search | PARTIAL | `backend/services/embedding_service.py`; `backend/services/hybrid_retrieval.py` | `tests/test_hybrid_retrieval.py` | Provider 接口和向量召回存在 | 默认 LocalHash 不是可靠生产语义模型，遍历式搜索 | 接入可配置语义模型/向量索引并保留 fallback |
| R204 | RAG | P0 | Keyword + Vector 融合 | IMPLEMENTED | `backend/services/hybrid_retrieval.py` | `tests/test_hybrid_retrieval.py` | 两路召回使用 RRF，不是二选一 | 无 | 保持融合测试 |
| R205 | RAG | P0 | 保存 keyword/vector/combined score | PARTIAL | `backend/services/hybrid_retrieval.py`; `backend/evidence.py` | `tests/test_hybrid_retrieval.py` | 内部有 rank/vector/fusion/retrieval score | 无稳定 keyword_score/combined_score 对外契约 | 统一检索结果 Schema |
| R206 | RAG | P0 | file_id/file set/type/page filter | IMPLEMENTED | `backend/services/hybrid_retrieval.py`; `backend/tools/document_tools.py` | `tests/test_hybrid_retrieval.py` | 四类过滤可执行 | 无 | 保持校验 |
| R207 | RAG | P0 | slide/document_type/year/student_id 过滤 | NOT_IMPLEMENTED | `backend/tools/document_tools.py`; `backend/services/hybrid_retrieval.py` | 本次参数 Schema 只读诊断 | `year` 被 Schema 拒绝，其余未定义 | 无法严格隔离 2025/2026，除非预先选定 file_id | 扩展受控 metadata 并加跨年份反例 |
| R208 | RAG | P0 | Top-N 后 Rerank 再 Top-K | IMPLEMENTED | `backend/services/hybrid_retrieval.py` | `tests/test_hybrid_retrieval.py` | 先扩大候选再确定性 rerank | 无 | 增加质量指标 |
| R209 | RAG | P0 | Rerank 失败回退 Hybrid 排序并 Trace | PARTIAL | `backend/services/hybrid_retrieval.py`; `backend/services/trace_service.py` | `tests/test_hybrid_retrieval.py` | 异常可回退关键词并记录 warning | 不是回退到 rerank 前 Hybrid；warning 归为 vector failure | 分离 retrieval/rerank 异常边界 |
| R210 | RAG | P0 | 最终 Evidence 只含过滤后 Top-K | IMPLEMENTED | `backend/services/hybrid_retrieval.py`; `backend/evidence.py` | `tests/test_hybrid_evidence.py` | 未参与答案的数据源不进入 Evidence | 扩展过滤缺口见 R207 | 加年份隔离验收 |

### 3.6 Evidence 2.0

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| E01 | Evidence | P0 | file/sheet/field/record/page/chunk/block/table/cell/bbox/confidence | IMPLEMENTED | `backend/evidence.py` | `tests/test_hybrid_evidence.py`; `tests/test_evidence_v2.py` | 除 slide 外字段均存在且兼容旧格式 | 无 | 保持兼容 |
| E02 | Evidence | P0 | slide 定位字段 | NOT_IMPLEMENTED | `backend/evidence.py` | 未发现 | 无 slide 字段 | PPT 定位不可表达 | 作为 P1 PPT 配套扩展 |
| E03 | Evidence | P0 | PDF/扫描 PDF page/block/bbox/confidence 溯源数据 | IMPLEMENTED | `backend/evidence.py`; `backend/services/document_index.py` | `tests/test_hybrid_evidence.py` | 后端 locator 可生成 | 普通 PDF bbox 取决于解析器能力 | 增加真实文档定位集 |
| E04 | Evidence | P0 | Excel sheet/field/record 定位 | IMPLEMENTED | `backend/evidence.py`; `backend/tools/table_tools.py` | `tests/test_hybrid_evidence.py` | 测试可回读到对应单元格 | 无 | 保持测试 |
| E05 | Evidence | P0 | Word 对应段落/Block 定位 | IMPLEMENTED | `backend/services/document_index.py`; `backend/evidence.py` | `tests/test_hybrid_evidence.py` | paragraph block locator 存在 | 无可视跳转 | 见 E07 |
| E06 | Evidence | P0 | 来源仅限实际参与答案的数据 | IMPLEMENTED | `backend/agent.py`; `backend/evidence.py` | `tests/test_hybrid_evidence.py` | 存在负向断言 | 无 | 保持负向测试 |
| E07 | Evidence | P0 | 点击后打开原文件、跳页/行/段并高亮 | NOT_IMPLEMENTED | `frontend/components/evidence_panel.py` | 未发现 | 当前只渲染文本卡片 | 无 click/open/jump/highlight/row preview | 建立安全只读预览与 locator action |
| E08 | Evidence | P0 | Evidence 可定位性校验 | IMPLEMENTED | `backend/evidence.py` | `tests/test_hybrid_evidence.py` | 按 source type 校验 locator | 未验证 UI 实际跳转 | 增加端到端定位测试 |

### 3.7 Workflow Runtime（WF01-WF10）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| WF01 | Workflow | P0 | Plan/Task/Step/Tool/Trace 一致关联 | IMPLEMENTED | `backend/runtime/planner.py`; `backend/runtime/task_runner.py`; `backend/agent.py` | `tests/test_workflow_v3.py` | task_id/step_id/tool_name 链路已测 | 无 | 保持兼容 |
| WF02 | Workflow | P0 | Node Schema 含 depends_on/input_ref/output_ref | NOT_IMPLEMENTED | `backend/runtime/planner.py`; `backend/repositories/task_repository.py` | 未发现 | Step 有 id/type/tool/input/output，但无依赖/引用字段 | 不能表达真实执行图 | 增量扩展 Node/Step Schema |
| WF03 | Workflow | P0 | Sequential 依赖未满足时禁止后继执行 | NOT_IMPLEMENTED | `backend/runtime/task_runner.py::start_tool_step` | 本次只读临时诊断 | 前六步 CREATED 时可直接 claim 第七步 | 顺序约束未在 Runner 执行，属 BLOCKER | 先加依赖门控及反例测试 |
| WF04 | Workflow | P0 | Parallel 无依赖节点可并行，Aggregate 等待全部 | NOT_IMPLEMENTED | `backend/runtime/planner.py`; `backend/runtime/task_runner.py` | 未发现 | 无 parallel/dependency/join 语义 | 固定列表顺序执行不等于并行，属 BLOCKER | 在现有 Runner 上增加 ready-set 调度 |
| WF05 | Workflow | P0 | Conditional 依据结构化数据安全分支 | NOT_IMPLEMENTED | `backend/runtime/planner.py`; `backend/runtime/task_runner.py` | 未发现 | 无条件节点/受控条件表达式 | 无 YES/NO 分支，属 BLOCKER | 使用白名单比较器，禁止任意代码 |
| WF06 | Workflow | P0 | Approval 节点与 WAITING_CONFIRMATION | IMPLEMENTED | `backend/runtime/task_runner.py`; `backend/services/confirmation.py` | `tests/test_workflow_v3.py`; `tests/test_confirmation.py` | 高风险写入等待确认 | 无 | 保持冻结参数 |
| WF07 | Workflow | P0 | Cancel 不写、Confirm 后前置节点不重跑 | IMPLEMENTED | `backend/agent.py`; `backend/services/confirmation.py`; `backend/runtime/task_runner.py` | `tests/test_workflow_v3.py` | 正反路径均有测试 | 无 | 保持幂等约束 |
| WF08 | Workflow | P0 | Retry 有上限且记录失败 | IMPLEMENTED | `backend/runtime/retry_executor.py`; `backend/runtime/task_runner.py` | `tests/test_rag_retry.py`; `tests/test_workflow_v3.py` | 有明确最大次数 | 无 | 保持上限 |
| WF09 | Workflow | P0 | Checkpoint/Resume 跳过 SUCCESS，写入不重复 | IMPLEMENTED | `backend/runtime/task_runner.py`; `backend/repositories/task_repository.py` | `tests/test_workflow_v3.py` | SUCCESS 跳过与确认写入恢复已测 | 无 | 保持测试 |
| WF10 | Workflow | P0 | 复用有效输出，文件版本变化使相关节点失效 | PARTIAL | `backend/runtime/task_runner.py`; `backend/services/file_versioning.py` | `tests/test_workflow_v3.py`; `tests/test_file_versioning.py` | 可保存 step result/checkpoint | 无 input_ref/output_ref 和版本依赖失效传播 | 增加结果引用和版本指纹 |

### 3.8 Async Task Runtime

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| A01 | Async | P0 | 创建任务立即返回 task_id 并后台执行 | IMPLEMENTED | `backend/runtime/async_task_runtime.py`; `backend/main.py` | `tests/test_async_task_runtime.py` | FastAPI BackgroundTasks，创建/查询已测 | 非持久 worker，进程退出可靠性有限 | 保持轻量队列并补恢复策略 |
| A02 | Async | P0 | queued/running/paused/waiting/success/partial/failed/cancelled | PARTIAL | `backend/runtime/async_task_runtime.py` | `tests/test_async_task_runtime.py` | created/running/success/failed/cancelled 存在 | queued 命名、paused、waiting_confirmation、partial_success 缺失 | 兼容扩展状态枚举 |
| A03 | Async | P0 | progress 与 current step/node | PARTIAL | `backend/runtime/async_task_runtime.py`; `backend/main.py` | `tests/test_async_task_runtime.py` | 保存 progress/message 和单 Step | 默认 worker 使用阶段常量，非页/项/节点计数 | 记录 processed/total，无法计算时仅显示阶段 |
| A04 | Async | P0 | cancel/retry/resume | IMPLEMENTED | `backend/runtime/async_task_runtime.py`; `backend/main.py` | `tests/test_async_task_runtime.py` | 三类 API/状态转换已测 | 无 | 保持幂等测试 |
| A05 | Async | P0 | checkpoint 与 failed_reason | IMPLEMENTED | `backend/runtime/async_task_runtime.py`; `backend/repositories/task_repository.py` | `tests/test_async_task_runtime.py` | checkpoint/message/error 入库 | 仅轻量任务级语义 | 扩展到页/项/节点 checkpoint |
| A06 | Async | P0 | OCR 按页、批量按项、Workflow 按节点进度 | NOT_IMPLEMENTED | `backend/main.py`; `backend/runtime/async_task_runtime.py` | 未发现 | 默认以 10/90 等阶段值更新 | 不能验证真实完成比例 | 接入处理器真实计数回调 |
| A07 | Async | P0 | 安全点取消，不产生半写入 | PARTIAL | `backend/runtime/async_task_runtime.py`; `backend/services/file_versioning.py` | `tests/test_async_task_runtime.py`; `tests/test_file_versioning.py` | 文件写入原子替换/补偿；任务边界检查取消 | 索引执行中不能即时中断，仅完成后检查 | 明确不可中断区并记录 cancel_pending |
| A08 | Async | P0 | Task Center 查询全局持久任务 | PARTIAL | `frontend/components/task_center.py`; `frontend/api_client.py` | `tests/test_frontend_workspace.py` | 浏览器会话中已知 task 可展示 | 无任务列表 API，刷新后无法发现全部任务 | 增加只读分页任务列表 |

### 3.9 Agent Safety 与 V2 写入安全（S01-S10）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| S01 | Safety | P0 | system/upload 信任来源区分 | IMPLEMENTED | `backend/services/safety_policy.py`; `backend/services/file_upload.py` | `tests/test_agent_safety.py` | 系统文件与未知上传来源可区分 | 无 | 保持来源不可由 LLM 伪造 |
| S02 | Safety | P0 | LOW/MEDIUM/HIGH Tool 风险策略 | IMPLEMENTED | `backend/services/safety_policy.py` | `tests/test_agent_safety.py` | read/reprocess/write-delete 类别存在 | Tool registry 当前以读取 Tool 为主 | 保持统一策略入口 |
| S03 | Safety | P0 | Tool 参数 Schema Validation 后 Policy Check | IMPLEMENTED | `backend/agent.py::_execute_tool`; `backend/tool_registry.py` | `tests/test_agent_safety.py`; `tests/test_tools.py` | 参数先经 Pydantic Schema，再安全检查 | 无 | 保持顺序测试 |
| S04 | Safety | P0 | HIGH 经 Diff/Preview、Approval 后执行 | IMPLEMENTED | `backend/services/confirmation.py`; `backend/services/file_versioning.py` | `tests/test_confirmation.py`; `tests/test_file_versioning.py` | 写/delete/rollback 受确认约束 | 部分操作入口不带 task_id | 见 S06 |
| S05 | Safety | P0 | Workflow/LLM 不能用 confirmed=true 绕过 | IMPLEMENTED | `backend/services/confirmation.py`; `backend/agent.py` | `tests/test_confirmation.py`; `tests/test_workflow_v3.py` | 服务端 pending action/原子 reserve 决定确认 | 无 | 保持伪确认负测 |
| S06 | Safety | P0 | Approval 绑定 task_id/approval_id/operation/target 且不可复用 | PARTIAL | `backend/services/confirmation.py`; `backend/database.py` | `tests/test_confirmation.py` | action_id、operation、target 冻结且一次性消费 | 独立 delete/undo/rollback 的 task_id 可为空 | 所有高风险入口先创建/绑定 Task |
| S07 | Safety | P0 | 上传/OCR/RAG/Cell 内容均作为不可信数据 | PARTIAL | `backend/agent.py`; `backend/prompts.py`; `backend/tool_registry.py` | 未发现专项 prompt-injection 测试 | 检索内容以 Tool 结果进入，任意代码执行被禁止，高风险 Tool 不直接暴露 | 无显式 untrusted content envelope/指令分层与恶意语料回归 | 增加结构化数据边界和四类注入负测 |
| S08 | Safety | P0 | 文档内 delete/write JSON 不触发 Tool | PARTIAL | `backend/agent.py`; `backend/services/safety_policy.py` | 未发现专项测试 | 架构上文档文本不直接注册为调用且高风险受确认 | 缺端到端恶意 Word/PDF/OCR/Cell 验证 | 固化攻击样本集 |
| S09 | Write Safety | P0 | Preview/Diff 不改文件，Cancel 不改文件 | IMPLEMENTED | `backend/services/file_versioning.py`; `backend/services/confirmation.py` | `tests/test_file_versioning.py`; `tests/test_confirmation.py` | 文件哈希负向断言通过 | 无 | 保持测试 |
| S10 | Write Safety | P0 | Version、失败原文件保持、Undo/Rollback 再确认、Trace | IMPLEMENTED | `backend/services/file_versioning.py`; `backend/services/confirmation.py`; `backend/services/trace_service.py` | `tests/test_file_versioning.py`; `tests/test_confirmation.py` | V2 写入安全回归通过 | 无 | 保持兼容 |

### 3.10 Trace / Observability

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| T01 | Trace | P0 | task_id/step_id/tool/状态/错误 | IMPLEMENTED | `backend/services/trace_service.py`; `backend/agent.py` | `tests/test_trace_evaluation.py`; `tests/test_workflow_v3.py` | 核心链路字段已测 | 无 | 保持兼容 |
| T02 | Trace | P0 | workflow_id/node_id | NOT_IMPLEMENTED | `backend/services/trace_service.py`; `backend/runtime/task_runner.py` | 未发现 | 只有 task_id/step_id | 与缺失的图 Workflow 对应 | 随 Node Schema 一并增加 |
| T03 | Trace | P0 | 输入/结果摘要、retry、approval | IMPLEMENTED | `backend/services/trace_service.py`; `backend/runtime/retry_executor.py`; `backend/services/confirmation.py` | `tests/test_trace_evaluation.py`; `tests/test_confirmation.py` | 摘要/重试/确认状态可追踪 | Evidence 仅间接在结果中 | 增加受限 Evidence ID 摘要 |
| T04 | Trace | P0 | Tool/Workflow/Task 耗时 | PARTIAL | `backend/services/trace_service.py`; `backend/services/evaluation_service.py` | `tests/test_trace_evaluation.py` | Tool duration 和任务起止时间存在 | 无 queue/execution/task total 的统一指标 | 统一 span 时间模型 |
| T05 | Trace | P0 | OCR/Layout/Index 专项 duration 与计数 | NOT_IMPLEMENTED | `backend/services/document_index.py`; `backend/services/trace_service.py` | 未发现 | 生命周期有状态 Trace | 缺 OCR 成功/失败页、Block 数、各阶段 duration | 为各阶段记录结构化 metrics |
| T06 | Trace | P0 | Retrieval candidate/rerank/Top-K/fallback 指标 | PARTIAL | `backend/services/hybrid_retrieval.py`; `backend/services/trace_service.py` | `tests/test_trace_evaluation.py` | mode/fallback/result_count/top_score 存在 | 缺候选总数、rerank duration、明确 Top-N/Top-K | 扩充白名单指标 |
| T07 | Trace | P0 | LLM/Tool call、Token、Cost | IMPLEMENTED | `backend/services/trace_service.py`; `backend/services/evaluation_service.py` | `tests/test_trace_evaluation.py` | Tool 计数及可得 Token/Cost 汇总 | 当前流程不一定获得供应商精确 token/cost | 保留 null/estimated 区分 |
| T08 | Trace | P0 | 不记录完整 Chain-of-Thought | IMPLEMENTED | `backend/services/trace_service.py` | `tests/test_trace_evaluation.py` | 只记录参数/结果摘要与状态 | 无 | 保持字段白名单 |
| T09 | Trace | P0 | 敏感字段脱敏 | IMPLEMENTED | `backend/services/trace_service.py` | `tests/test_trace_evaluation.py`; V2 evaluation | 手机/邮箱/证件/地址摘要脱敏 | 非结构化新敏感类型仍需维护 | 扩展规则时保留回归 |
| T10 | Trace | P0 | 前端可观察 Trace/指标 | PARTIAL | `frontend/components/task_center.py`; `frontend/app.py` | `tests/test_frontend_workspace.py` | Task 状态可展示 | 无完整 Trace/Token/Cost/Retrieval dashboard | 作为非阻断可视化增强 |

### 3.11 Evaluation

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| EV01 | Evaluation | P0 | 固定 V3 测试集与可复现 runner | PARTIAL | `evals/v3_10_cases.json`; `evals/run_v3_10_evals.py` | `evals/v3_10_results.md` | V3.10 固定 5 例为 5/5 | 仅覆盖 Trace Evaluation 子集 | 建立覆盖各 V3 模块的 manifest |
| EV02 | Evaluation | P0 | OCR page success/key field accuracy | NOT_IMPLEMENTED | `evals/`; `tests/test_ocr_pipeline.py` | 未发现指标测试 | 有功能单测，无准确率数据集 | 不能量化 OCR 质量 | 增加脱敏标注页集和指标 |
| EV03 | Evaluation | P0 | Table structure accuracy | NOT_IMPLEMENTED | `evals/`; `tests/test_document_blocks.py` | 未发现指标测试 | 只有构造级单测 | 无 merged cell/真实表格准确率 | 增加表结构金标集 |
| EV04 | Evaluation | P0 | Hybrid Recall@K / Rerank hit rate | NOT_IMPLEMENTED | `evals/`; `tests/test_hybrid_retrieval.py` | 未发现指标测试 | 有排序行为单测 | 无检索质量基准 | 建立查询-Evidence 金标 |
| EV05 | Evaluation | P0 | Evidence localization accuracy | PARTIAL | `evals/coverage_manifest.json`; `tests/test_hybrid_evidence.py` | `tests/test_hybrid_evidence.py` | 后端 locator 精确性被抽样验证 | 无 UI 跳转准确率和规模指标 | 加端到端定位评分 |
| EV06 | Evaluation | P0 | Workflow/Resume success rate | PARTIAL | `evals/coverage_manifest.json`; `tests/test_workflow_v3.py` | `tests/test_workflow_v3.py` | 固定顺序流程通过 | 无 parallel/conditional，未形成成功率指标 | 图运行时完成后建 case set |
| EV07 | Evaluation | P0 | Async retry/resume 指标 | PARTIAL | `tests/test_async_task_runtime.py` | `tests/test_async_task_runtime.py` | 行为测试通过 | 无批量成功率/部分失败率统计 | 加固定异步数据集 |
| EV08 | Evaluation | P0 | Prompt Injection 防御与 HIGH approval rate | PARTIAL | `evals/coverage_manifest.json`; `tests/test_agent_safety.py` | `tests/test_agent_safety.py`; `tests/test_confirmation.py` | HIGH 风险确认行为有固定测试 | 无恶意文档注入集；V3.10 未发布专项比率 | 增加安全攻击语料和 100% 阻断指标 |
| EV09 | Evaluation | P0 | Average/P95 latency、Tool/LLM call count | PARTIAL | `backend/services/evaluation_service.py`; `evals/run_v2_evals.py` | `tests/test_trace_evaluation.py`; `evals/v3_10_results.md` | 平均耗时/Tool 次数可汇总 | 无 P95；LLM call 在离线测试中缺真实样本 | 增加分位数和环境说明 |
| EV10 | Evaluation | P0 | 错误率/成功率汇总 | IMPLEMENTED | `backend/services/evaluation_service.py`; `evals/run_v3_10_evals.py` | `evals/v3_10_results.md` | V3.10 输出 5/5、0% 错误率 | 范围仅 V3.10 子集 | 汇总时明确分母与模块范围 |

### 3.12 P1 / P2（不计入 P0 Blocker）

| 编号 | 模块 | 优先级 | 需求 | 实现状态 | 真实代码位置 | 测试位置 | 验证结果 | 缺口 | 建议 |
| -- | -- | --- | -- | ---- | ------ | ---- | ---- | -- | -- |
| P101 | PPT | P1 | PPT/PPTX 基础解析 | NOT_IMPLEMENTED | `backend/services/document_index.py`; `backend/services/file_upload.py` | 未发现 | 支持类型中无 PPT/PPTX | OPTIONAL | V4 独立增量实现 |
| P102 | PPT | P1 | Slide 文本与 slide number | NOT_IMPLEMENTED | `backend/document_blocks.py`; `backend/evidence.py` | 未发现 | 无 slide 模型 | OPTIONAL | 与 PPT parser 同步设计 |
| P103 | PPT | P1 | PPT 基础表格 | NOT_IMPLEMENTED | `backend/services/document_index.py` | 未发现 | 无 PPT 解析 | OPTIONAL | 先定义降级规则 |
| P104 | PPT | P1 | Slide Chunk/Block | NOT_IMPLEMENTED | `backend/document_blocks.py` | 未发现 | 无 slide block | OPTIONAL | 保持通用 Block 兼容 |
| P105 | PPT | P1 | PPT Evidence | NOT_IMPLEMENTED | `backend/evidence.py` | 未发现 | 无 slide locator | OPTIONAL | 增加 file/slide/block/bbox |
| P106 | UX | P1 | Trace/Token/Cost 可视化 | PARTIAL | `frontend/app.py`; `frontend/components/task_center.py` | `tests/test_frontend_workspace.py` | 有 Task/基础状态展示 | 无完整指标面板 | OPTIONAL，后端指标完整后实现 |
| P107 | UX | P1 | Evidence Preview 体验优化 | PARTIAL | `frontend/components/evidence_panel.py` | `tests/test_frontend_workspace.py` | 文本来源卡片存在 | 无点击跳转、高亮 | OPTIONAL，但与 P0 可定位体验重叠 |
| P108 | UX | P1 | 文件内容 Preview 优化 | NOT_IMPLEMENTED | `frontend/components/file_panel.py` | 未发现 | 仅元数据详情 | OPTIONAL | 先提供安全只读 API |
| P201 | Memory | P2 | 已确认字段语义映射轻量 Memory | NOT_IMPLEMENTED | `backend/services/schema_mapping.py`; `backend/services/session_context.py` | `tests/test_schema_mapping.py`; `tests/test_context_state.py` | 有确定性映射/会话状态，但无用户确认映射记忆 | OPTIONAL | V4 设计带来源、置信度和撤销的记忆 |

## 4. 关键实测与代码级结论

### 4.1 BLOCKER-1：重新索引失败会删除旧有效索引

`backend/services/document_index.py::_index_failure` 调用 `database.delete_document_chunks(file_id)`。只读失败注入诊断先建立 1 个可查询旧 chunk，再触发 Word 解析失败，观察结果：

```text
before_chunks: 1
result_ok: false
error_code: WORD_PARSE_ERROR
after_chunks: 0
queryable: false
status: FAILED
```

这不满足“构建新索引 → 成功 → 切换 active”，也不满足失败时保留旧有效结果。诊断使用临时数据库，没有修改项目数据库或代码。

### 4.2 BLOCKER-2：Workflow 没有可执行依赖图

Planner 产出固定顺序 Step，但没有 `depends_on`、`input_ref`、`output_ref`。`TaskRunner.start_tool_step()` 按 tool_name 找到未完成步骤，不检查之前步骤或依赖。只读诊断创建组合计划后，在前 6 步均为 CREATED 时请求后续 `retrieve_document`，实际成功 claim 第 7 步：

```text
later_step_claimed_before_dependencies: true
claimed_sequence: 7
prior_statuses: [created, created, created, created, created, created]
```

因此 Sequential 执行约束不成立；Parallel ready-set/join 和 Conditional 白名单分支也不存在。现有测试证明固定 happy path、确认和恢复可用，但不能证明 V3 图 Workflow。

### 4.3 OCR Mixed/Partial

PDF 检测使用整份 `any(page has text)`。只要任一页存在文本，普通解析分支就会处理整份文件，空的扫描页不会逐页 OCR。OCR 适配器异常会使整份文件 FAILED，没有 `partial_success`、`failed_pages` 或指定页重试。因此 text/scanned 两类可用，mixed 和 19/20 页成功场景不满足正式要求。

### 4.4 RAG 年份隔离

`RetrieveDocumentArguments` 支持 file_id/file_ids/file_type/page；传入 `year=2026` 会被 Schema 拒绝。只有调用方已确定 2026 文件 ID 时才能间接隔离，系统不能凭结构化 `year` 过滤保证“2025 不进入 Evidence”。这不是数据串档实测失败，而是需求所需约束字段不存在。

### 4.5 Evidence 定位

后端 Evidence JSON 和测试可定位 PDF/扫描 PDF、Excel 单元格和 Word block；但 `frontend/components/evidence_panel.py` 仅展示来源文本，没有打开文件、跳转 PDF page、bbox 高亮、Excel 行预览或 Word 段落跳转。因此后端溯源数据为 IMPLEMENTED，最终用户定位体验为 NOT_IMPLEMENTED，模块总体 PARTIAL。

## 5. 正式测试类别映射

| 正式类别 | 本报告映射 | 当前测试依据 | 覆盖判断 |
| --- | --- | --- | --- |
| F01-F12 | Workspace 文件、筛选、上传、重处理、删除 | `tests/test_document_workspace.py`; `tests/test_frontend_workspace.py`; `tests/test_upload.py` | PARTIAL |
| O01-O10 | text/scanned/mixed、页级 OCR、失败与置信度 | `tests/test_ocr_pipeline.py` | PARTIAL |
| R201-R210 | Keyword、Vector、Fusion、Filter、Rerank、Evidence | `tests/test_hybrid_retrieval.py`; `tests/test_hybrid_evidence.py` | PARTIAL |
| WF01-WF10 | Plan/Step、顺序/并行/条件、确认、重试、恢复 | `tests/test_workflow_v3.py`; `tests/test_rag_retry.py` | PARTIAL；WF03-WF05 为核心缺口 |
| S01-S10 | 来源信任、Policy、Approval、注入防御、写入安全 | `tests/test_agent_safety.py`; `tests/test_confirmation.py`; `tests/test_file_versioning.py` | PARTIAL |

现有测试采用不同命名，本报告建立映射，没有为了编号一致新增重复测试。

## 6. 缺口分级

### BLOCKER

1. **索引失败破坏旧有效检索结果**：`backend/services/document_index.py::_index_failure`、`backend/repositories/document_repository.py`；缺少失败注入下旧 active 索引保持测试。
2. **Workflow 不是真实依赖图运行时**：`backend/runtime/planner.py`、`backend/runtime/task_runner.py`、`backend/repositories/task_repository.py`；缺少 depends_on 门控、Parallel join、Conditional 结构化分支测试。

### HIGH

1. Mixed PDF 无逐页 OCR，且无 partial_success/failed_pages/失败页重试：`backend/services/ocr_service.py`、`backend/services/document_index.py`、`tests/test_ocr_pipeline.py`。
2. Layout 仅基础 Block，PDF 无真实版面分析，Table/Cell 无规范 Table/Row/Column/Cell 与合并单元格降级：`backend/document_blocks.py`、`backend/services/document_index.py`、`tests/test_document_blocks.py`。
3. Evidence 后端可溯源但前端不能打开原文、跳页/行/段或 bbox 高亮：`frontend/components/evidence_panel.py`、`tests/test_frontend_workspace.py`。
4. RAG 缺 year/document_type/student_id 等强约束，默认 hash embedding 语义能力有限，rerank 失败不回到 pre-rerank Hybrid：`backend/services/hybrid_retrieval.py`、`backend/services/embedding_service.py`、`backend/tools/document_tools.py`。
5. Async 状态与真实进度不完整：缺 paused/waiting_confirmation/partial_success，默认百分比是阶段常量：`backend/runtime/async_task_runtime.py`、`backend/main.py`。
6. 不可信文档指令边界没有专项实现契约和攻击样本测试：`backend/agent.py`、`backend/services/safety_policy.py`、`tests/test_agent_safety.py`。
7. Workspace 缺重新解析/重新索引入口；删除仅后端可用：`backend/main.py`、`frontend/components/file_panel.py`。
8. V3 Evaluation 缺 OCR/Table/Retrieval/Evidence/Workflow/Safety/Latency 的量化基准：`evals/`、`tests/`。

### MEDIUM

1. Lifecycle 只有通用 FAILED，不能表达各阶段失败、partial、reprocessing/reindexing。
2. Block 缺 parent_id/source_parser；Block 只随 chunk metadata 持久化。
3. 高风险独立操作的 confirmation `task_id` 可为空。
4. Trace 缺 workflow/node、阶段耗时/页数/候选数/rerank/queue 等结构化指标。
5. Async Task Center 只知道当前浏览器会话内 task，无全局列表 API。
6. OCR 测试主要通过注入适配器，缺受控真实引擎质量冒烟。
7. 独立正式 V1/V2/V3 需求原文和 V3 AI Coding 提示词汇总未在项目中找到，需求追踪基线不完整。

### LOW

1. Workspace 缺来源筛选及文件名/类型排序。
2. 文件详情主要依赖 metadata，类型化展示契约不完整。
3. 前端仅提供基础 Task/Trace 展示，刷新后任务发现和指标可视化有限。

### OPTIONAL（P1/P2）

1. PPT/PPTX 解析、Slide 文本/编号/表格、Slide Block 与 Evidence 未实现。
2. Trace/Token/Cost 可视化只有基础展示。
3. 文件和 Evidence Preview 体验未完成跳转、高亮和内容预览。
4. 已确认字段语义映射轻量 Memory 未实现。

## 7. 覆盖率与判定

P0 原子需求共 **104** 项：

| 状态 | 数量 |
| --- | ---: |
| IMPLEMENTED | 47 |
| PARTIAL | 30 |
| NOT_IMPLEMENTED | 27 |
| NOT_APPLICABLE | 0 |

覆盖率采用透明加权口径：

```text
(IMPLEMENTED + 0.5 × PARTIAL) / P0 总数
= (47 + 0.5 × 30) / 104
= 59.6%
```

严格完整实现率为 `47 / 104 = 45.2%`。P1/P2 不计入 P0 覆盖率，也不作为 P0 Blocker。

## 8. 推荐补丁顺序（本轮不执行）

1. **先保数据**：修复 reparse/reindex 的 shadow build、失败保留和 atomic active switch；补失败注入测试。
2. **再补 Workflow 执行语义**：在现有架构上增量加入 depends_on、ready-set、join、受控条件和依赖门控；补 Sequential/Parallel/Conditional 反例。
3. **补 OCR 页级容错**：mixed 检测、page ledger、partial_success、failed-pages retry 和置信提示。
4. **补 Layout/Table Schema**：parent/source、PDF layout、规范 table/cell 和 merged-cell 降级。
5. **补 RAG 强过滤与回退**：年份/文档类型/学生约束、真实语义 Provider、rerank 异常回到 Hybrid。
6. **补 Evidence 用户定位闭环**：安全预览、PDF 跳页/高亮、Excel 行预览、Word 段落定位。
7. **补 Async/Safety/Trace/Evaluation**：真实计数进度、完整状态、恶意语料回归和量化指标。
8. **最后补 Workspace 操作入口和 P1/P2 体验**。

## 9. 最终结论

```text
========== V3 FINAL AUDIT ==========

Branch:
feature/v3-development

Commit:
f4bd609703ebea8d0a75fdb30dc10c8c6cdf98e4

Pytest:
Total: 238
Passed: 238
Failed: 0
Skipped: 0

V1 Regression:
PASS

V2 Regression:
PASS

V3 P0:
IMPLEMENTED: 47
PARTIAL: 30
NOT_IMPLEMENTED: 27

BLOCKER:
1. 重建索引失败会删除旧有效 chunk。
2. Workflow 缺真实依赖门控、Parallel 和 Conditional。

HIGH:
1. Mixed/partial OCR 与页级恢复缺失。
2. Layout/Table 结构不足。
3. Evidence 前端原文跳转/高亮缺失。
4. RAG 强 metadata filter、真实默认语义能力和 rerank fallback 不完整。
5. Async 状态与真实进度不完整。
6. 不可信文档 Prompt Injection 专项防御验证缺失。
7. Workspace 重解析/重索引/删除操作入口不完整。
8. V3 量化 Evaluation 不完整。

MEDIUM:
1. Lifecycle/Block/Trace/Approval/Task Center 工程契约仍有缺口。
2. 真实 OCR 冒烟与正式需求追踪文档缺失。

LOW:
1. Workspace 来源筛选、名称/类型排序和详情契约不完整。
2. 前端指标与任务发现体验有限。

OPTIONAL:
1. PPT/Slide/PPT Evidence 未实现。
2. Preview、Trace 可视化和字段映射 Memory 未完成。

V3 P0 Coverage:
59.6%

是否满足 V3 核心完成判定：
NO

是否建议冻结 V3：
NO

是否建议进入 V4：
NO
```

结论：现有 238 项测试全部通过，V1/V2 无固定测试回归；但测试集没有覆盖全部正式 V3 语义。由于存在 2 项 P0 BLOCKER 和多项 HIGH，当前不建议冻结 V3，也不建议在补齐核心缺口前进入 V4。
