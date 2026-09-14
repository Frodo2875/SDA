# V6 Phase 0 架构审计

日期：2026-09-14。定位从 V5 Trusted General Document Agent 演进到 V6 Reliable Research & Report Agent。
本文件区分代码现状和未来方案；所有未来方案均未实现。

## 1. 基线与回归证据

初始工作目录 `Material_management_assistant/` 不是有效仓库根，真实仓库是其下的 `student_document_agent/`。
通过切换命令工作目录解决，未执行 `git init`、checkout、reset、标签变更或历史修复。

| 核验项 | 实测结果 |
| --- | --- |
| `git branch --show-current` | `feature/v6-development` |
| `git log --oneline -1` | `36ccc19 v5-finals` |
| 完整 HEAD | `36ccc19aac056b337747f7c40e0674809b2b41b6` |
| `git status`（审计开始、回归结束） | 工作区干净 |
| `git rev-parse refs/heads/v5-final-stable` | 与 HEAD 相同 |
| `git rev-parse 'refs/tags/v5-final-stable^{}'` | 与 HEAD 相同 |

`v5-final-stable` 同时存在分支和附注标签。裸名称有 ambiguous 警告；标签对象 ID 为
`fbd781af3d7eedc6a5d4fade976b7c431b352a8a`，不是另一个代码提交。解引用后与冻结分支和 HEAD 完全一致。
当前状态证明代码与冻结点相同，未依赖无法从当前引用单独证明的历史分支创建过程。

首次 `pytest -q` 因当前 shell 的 PATH 无 pytest 而以 127 退出，没有收集或运行测试。
定位既有 Conda 环境后，在实际仓库以非 login shell 执行：

```bash
export PATH=/home/gxh/miniconda3/envs/tuli_env/bin:$PATH
pytest -q
```

环境：Python 3.10.20，pytest 9.1.1。结果：**478 passed in 38.80s**，退出码 0；
**failed=0，skipped=0**。未修改、删除或增加 skip，未安装依赖，未更改环境配置文件。
38.80 秒为 pytest 报告耗时，不包括目录和环境排查。
`tests/conftest.py` 为每个测试隔离数据库，并替换真实搜索 Provider；此次结果不代表实时 Tavily 服务可用性验证。
历史 `docs/V5_FINAL_ACCEPTANCE_REPORT.md` 的 478 passed / 35.24s 仅用于对照，不代替本次实测。

## 2. 当前架构

```text
Upload -> Input Router / File Lifecycle
       -> Parser -> DocumentBlock -> Chunk -> 统一 Index
       -> Local Retriever -> UnifiedEvidenceFactory
CSV / Excel -> Schema / Table Query -> UnifiedEvidenceFactory

User Request -> Domain / Source Router -> Tool Boundary -> Agent Runtime
             -> Local Retrieval / Tavily Search / Direct URL Fetch
             -> Unified Evidence -> Sufficiency -> Answer + Trace

Async SQLite Queue -> 既有 handler -> Workflow / OCR / Index / Batch
Workflow -> TaskPlan / 节点结果 / Checkpoint / Approval
Word 提案 -> Diff -> Pending Action -> Approval -> Versioned Write
```

`DocumentAgentCore` 是 façade，不是第二个 Agent。V4 `agentic_retrieval.py` 与 V5
`cross_source_retrieval.py` 当前分别存在受控检索入口，V6 复用它们，不再复制检索循环。
详细路径和函数见 [V5 稳定模块地图](V5_STABLE_MODULE_MAP.md)。

## 3. Markdown 支持

### 现状

`.md`、`.markdown` 不在 `input_router.SUPPORTED_SUFFIXES`、`file_upload.SUPPORTED_UPLOADS`
和 `file_tools.FILE_TYPES` 内；`parse_local_document` 仅分派 TXT/JSON/PPT/PPTX。
`DocumentBlock` 类型有限，没有专门的 list/code 类型，但 Chunk 可保存 `metadata_by_block`。
`Evidence` 旧模型的 `locator_type` 是封闭枚举，不含 markdown；`UnifiedEvidence` 虽允许字符串，
`serialize_evidence3_compat` 仍会校验旧模型。因此仅增加新 locator 名称会破坏兼容。
Factory 按原文件名推断 locator，`.md` 当前会得到 `None`；具备 Block/Chunk ID 的本地来源仍可定位。

### 建议接入

```text
.md / .markdown Upload
 -> 既有校验与 TEXT 路由（内部 file_type=txt，保留原文件名）
 -> parse_local_document 新 Markdown 分派
 -> ParsedDocument / DocumentBlock / metadata_by_block
 -> blocks_to_chunks -> 既有 Index / 原子激活
 -> 既有 retrieve_document -> UnifiedEvidenceFactory.local
 -> Unified Evidence + 兼容序列化 -> 本地 Chunk/Block 预览
```

Phase 1 建议采用文本子格式策略：两个扩展名归入 `txt`，在解析产物和 Chunk 元数据中保存
`document_format=markdown`、标题层级和原始行范围。这样不增加数据库类型、Evidence 枚举或 Retriever 分支。
UI 需要区分格式时依据原扩展名展示 Markdown；现有 TXT 类型筛选同时涵盖 Markdown，必须明确这一语义。
若未来要求独立 markdown 文件类型，应单独审查 API/Tool/Async/预览所有类型白名单，不能在 Phase 1 顺带扩大范围。

- 标题映射 `title`，普通文本、列表和代码映射 `paragraph`，结构明确的表格可映射 `table`；细分语义放在 Chunk 元数据中，不更改 Block Schema。
- Evidence 使用现有 `file_id/file_name/block_id/chunk_id/line_number/source_parser`；允许 `locator_type=None`，不伪造 TXT 文件名，不修改 Factory。旧定位器按文件记录的 `txt` 类型进入 `_locate_document`。
- 当前 Retriever 只提取部分元数据，Factory 也不任意透传本地 metadata。标题路径和行终点保留在 Chunk；不能宣称它们已成为 Evidence 字段。报告需要时，通过来源定位读取并核验。
- Markdown 作为资料处理：HTML 不执行，代码块不执行，链接与图片地址不自动抓取。引用语法不能扩大 Source scope。
- 保持原始行号；长块切分以 Chunk ID 精确定位，`line_number` 只承诺块起始行，不能伪称是切分后精确行范围。
- 复用文本解码与大小限制；前端同步上传白名单和安全文本预览。Phase 1 测试必须验证完整上传到 Evidence 定位链路，不只验证 parser。

预计修改点为上传/输入路由/文件发现白名单、`multiformat_parser.py` 和前端格式展示；
Index、Async 和定位器优先直接复用 `txt` 路径，是否需要窄适配由该阶段回归验证。不得创建第二套 Document Pipeline。

## 4. Research Task

### 已有能力与实际限制

| 能力 | 已有实现 | 对研究任务的限制 |
| --- | --- | --- |
| Async Task | SQLite 持久化、原子领取、阶段/数量进度、取消、暂停及审批等待状态 | 类型白名单中没有 research；不能只新增一个 API 就视为接入完成 |
| Workflow | `TaskPlan/PlannedStep` 的依赖、条件、引用与审批；`execute_workflow` 分波次运行 | 可执行节点目前主要限 QUERY/ANALYSIS；GENERATE 需要现有生成路径或外层明确调度 |
| Checkpoint | 节点结果、completed_steps、async checkpoint、child_task_id | Async workflow handler 在 `run_agent` 返回后才绑定子任务；不是研究子问题逐步 checkpoint |
| Retry | `MAX_RETRIES=2`，只读 Tool 有界重试；OCR/Batch 失败单元定向重试 | `retry_async_task` 会清空 async checkpoint；研究任务不能误用为“保留已完成研究” |
| Resume | `resume_async_task` 保留 checkpoint；`resume_task` 跳过成功节点并检查输入指纹 | 默认 Async workflow handler 重新调用 `run_agent`，没有直接按已有 child ID 续跑 |
| 输入失效 | 已有版本变化可使读节点与下游失效 | 无版本文件指纹退化为创建时间/路径，并不保证发现外部原地修改 |
| 崩溃恢复 | 任务已持久化且有领取事务 | 未见 lease/heartbeat/运行中任务接管机制；running 状态也不被现有 resume 接受，不能承诺进程崩溃自动恢复 |

### 建议接入

新增窄的研究计划/服务适配层，将需求拆成有限子问题，再构造既有 `TaskPlan`；
在现有 `AsyncTaskCreateRequest`、`_validated_payload`、`_default_executor` 中增量注册 research handler。
复用同一队列、任务 repository、状态、Trace 和任务中心，不增加独立 Async Runtime。

研究 handler 应保存计划版本、稳定子问题/节点 ID、冻结 source scope、已完成结果引用、Evidence ID、
输入版本或内容指纹、剩余全局调用/时间预算及下一阶段。初始任务和节点身份必须在执行前持久化，
恢复复用同一子任务，不能在每次恢复时生成新计划和刷新预算。
状态数据优先使用现有 checkpoint JSON，限制大小，不复制完整文档到每个节点结果。

通过注入现有 Retriever callable 组织查询；每次调用仍经 Source/Tool scope 校验。
各子问题共享任务总预算，不能每个子问题重置为完整预算。成功节点不重做，失败只读节点有限重试；
取消在节点间检查，写入只走冻结审批动作，审批等待不算研究失败。
GENERATE 由研究适配层显式连接既有生成步骤记录，不能把未知节点悄悄标为成功。

Phase 2 必须定义同一 Runtime 内的过期运行状态识别及恢复边界，并用进程中断测试验证；
若无法安全确认原 worker 已停止，应报告可诊断状态，不能同时领取或声称 exactly-once。
本次不修改 Runtime、数据库或 V5 失败恢复语义。

## 5. Evidence Quality Gate

建议位置固定为：

```text
Unified Evidence（已规范化）
 -> Evidence Quality Gate（只读检查，独立结果）
 -> Answer / Report 内容生成与引用校验
```

V5 跨源 sufficiency 只检查数量、字段和词项；V4 本地受控检索有冲突/低置信度判断。
这些不等于报告论点的引用支持度，也不等于来源真实可信。V6 Gate 作为外层服务复用检查，
不改 `Evidence`、`UnifiedEvidence`、Factory 或既有 sufficiency 行为。

建议 Gate 输出独立 assessment：检查规则版本、任务/论点 ID、Evidence ID、
`pass / needs_review / insufficient`、原因码、覆盖缺口、冲突对和允许使用的引用 ID。
存入研究结果/checkpoint 与有界 Trace metrics，不能向 Evidence 顶层追加 quality_score 字段或原地改写来源。

检查范围：来源完整性与可定位性、按需求的覆盖、重复来源、明确冲突、OCR review 标记、
时效要求、显式来源权威信息及引用和内容对应。相同网站转载不算独立佐证；authority=unknown
不自动提升，retrieved_at 不等于 published_at，本地 freshness=not_applicable 不改成网页时效状态。
`is_evidence_locatable` 当前面向本地文件，不能直接用它判断 WEB/URL 全部无效；网页按现有 URL/domain/retrieved_at/content 契约检查。

Gate 不是事实真伪判定器。无法核验就说明缺口；部分支持仅形成带限制的草稿，关键冲突不输出确定结论。
需补检索时只向 Research 服务返回缺口，再由既有 scope 和剩余预算决定是否调用 Retriever。
Gate 自身不联网、不审批、不形成新的无限检索循环。生成后还要校验实际引用 ID 和论点关联，
防止生成器绕过生成前检查。

## 6. Web Reliability

`TavilySearchProvider._post` 已映射 401/403、429、Timeout、其他 HTTP 错误；
`search` 验证 JSON/results，`WebRetrievalService.retrieve` 返回统一失败或空结果。
当前无 Provider 内重试、Retry-After 解析和每次 HTTP attempt 的独立 Trace。
`runtime/policy.py` 的重试错误码不包含这些 Web 错误，不能假设通用 Tool 重试已覆盖它们。

| 场景 | V5 现状 | V6 Phase 4 方案 |
| --- | --- | --- |
| 429 | `WEB_SEARCH_RATE_LIMITED` | 单一 Tavily 适配器内有限重试；次数和等待均计入任务预算 |
| Retry-After | 未读取；`SearchProviderError` 仅保留消息/错误码 | 增量保留经验证的状态/等待元数据；支持秒数和 HTTP 日期，非法/过期/超长值有界处理 |
| Timeout | `WEB_SEARCH_TIMEOUT`；配置 timeout 被限制在 1–30 秒 | 每次请求受剩余 deadline 约束；仅瞬态只读请求可重试，预算不足立即停止 |
| Auth Error | 401/403 -> `WEB_SEARCH_AUTH_ERROR` | 不重试，提供脱敏可诊断结果 |
| Empty Result | 空 results -> `not_found`；有链接但无正文可能有结果却无 Evidence | 保留业务空结果与技术失败区别；不把空结果当瞬态无限重试，不制造 Evidence |
| Trace | Tool/服务总延迟与 Web 指标，跨源 attempt/stop | 记录 Provider、HTTP attempt、脱敏错误码、等待、耗时、预算和终止原因 |

只保留 Tavily 这一真实搜索 Provider，不增加多 Provider、failover、浏览器或缓存系统。
建议新增窄重试策略并扩展 Tavily/错误元数据，Web Retrieval 负责对外兼容。
防止 Tool Retry × Provider Retry × Research Retry 相乘；指定唯一 HTTP 重试责任层。
跨源当前按 Tool 调用计数，底层 HTTP attempt 必须另行计量且扣除研究总预算。
等待必须可检查取消，不能占用无限 worker 时间；长 Retry-After 超过 deadline 时返回明确终态。
不修改 URLFetcher/SSRF、来源路由、安全标记和 Factory。Trace 不包含 API Key、Authorization 或未经处理的响应正文。

## 7. Report Generation

当前没有独立 Evidence Report Writer。`word_tools.write_word` 只向已有 DOCX 新增一个段落；
`WordDiffOperation` 仅支持 append/undo/rollback，append 内容上限 100,000 字符。
`create_pending_action` 接收目标、正文、task_id、evidence_context，同时签名保留 student_id/student_name；
这不意味着报告服务必须伪造学生身份，通用报告可显式传空身份并验证审批绑定兼容性。

Phase 5 建议先提供可审阅 Evidence Report 草稿：研究问题/范围、方法与来源、结论、逐项证据引用、
冲突与限制、参考资料。以 Evidence ID 建立论点—来源映射，按真实来源生成本地位置或网页 URL/日期。
生成后的引用必须存在于 Gate 允许集合，不能补造作者、发表日期或正文。

```text
Research 结果 + Unified Evidence + Quality Assessment
 -> 报告草稿 / 引用校验
 -> 已登记可写 Word 目标
 -> create_pending_action（内部生成 Diff，冻结正文和目标 hash）
 -> 用户 Approval
 -> execute_versioned_word_action -> write_word -> 新 Version
 -> 同一 Approval 路径 Undo / Rollback
```

最小交付限定为向已有可写 Word 模板追加可读报告正文，包含编号引用和参考资料；
不承诺复杂排版、自动新建文件或替换整篇报告，因为现有 Writer/Diff 没有这些操作。
新建 DOCX 或复杂排版若成为硬需求，应另行设计，不能绕过冻结 Diff/Version 边界。
正文改变必须重新生成待确认动作；目标在预览后改变，现有 expected_hash 会拒绝执行。
研究 checkpoint 保存草稿版本/内容 hash、引用 ID、action_id、新版本 ID；恢复先读取动作状态，
不能重复创建写入动作或自动重放已执行写入。Gate 通过不替代 Approval，视觉高影响保护仍有效。

## 8. 审计结论与阶段入口

V5 冻结基线和 478 项回归均通过；可采用单 Document Pipeline、单 Async Runtime、
不变 Evidence Schema、单 Tavily Provider 和现有审批版本链推进 V6。
关键后续风险是节点级恢复/崩溃接管、嵌套预算、Markdown 旧定位兼容、报告引用支持度及 Writer 操作范围。
这些是未来阶段验收项，不是本轮已经实现的能力，也不是修改 V5 的理由。

**满足进入 Phase 1 的技术前置条件。当前仅完成 Phase 0，等待下一步指令，不开始 Markdown 开发。**
