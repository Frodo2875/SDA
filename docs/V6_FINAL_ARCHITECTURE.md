# Student Document Agent V6 Final Architecture

冻结日期：2026-09-15  
分支：`feature/v6-development`  
V6.4 基线：`2bdc9c5`（`V6.4 evidence report generation`）

## 1. V6 目标

V6 将 V5 的 Trusted General Document Agent 提升为 Reliable Research & Report Agent。V5 的文档、检索、安全、Evidence 和文件事务能力继续作为稳定底座；V6 增加 Markdown 正式输入、复杂研究任务管理、交付前证据质量检查、单一 Web Provider 可靠性，以及带证据报告交付。

V6 的可靠性含义是：长任务有状态、来源可追溯、结构问题可见、Web 失败可分类、文件写入经过人工确认且保留版本。它不表示 Agent 能自主判定事实真假，也不表示所有外部故障都可自动恢复。

## 2. 最终架构

```text
User / API
    ↓
Task Complexity Router
    ├─ simple → Agent Runtime → Answer
    └─ complex → Research Task Runtime / existing Async Runtime
                        ↓
                 Agent / existing Workflow
                        ↓
             ┌──────────┴──────────┐
             ↓                     ↓
      Local Retrieval       Web Retrieval
      Document Pipeline     Tavily + Reliability Layer
             └──────────┬──────────┘
                        ↓
               Unified Evidence 4.0
                        ↓
              Evidence Quality Gate
                        ↓
               Answer / Report Draft
                        ↓
               Diff / Human Approval
                        ↓
              Markdown / Word Writer
                        ↓
              Version Storage / Rollback
```

只有复杂请求进入 Research Task；简单请求仍同步返回。Research 是用户级任务封装，复用既有 Async Runtime、Checkpoint、Retry、Resume 和 Workflow，不形成第二套执行器。

## 3. 模块状态

| 模块 | 版本 | 状态 | 最终职责 |
| --- | --- | --- | --- |
| V5 Document Pipeline | V5 | Frozen | 上传、生命周期、解析、Block、索引和检索主链路 |
| Source Router / Tool Boundary | V5 | Frozen | 来源选择、工具范围和执行前安全约束 |
| Cross Source Retrieval | V5 | Frozen | 在预算内组织 Local、Web 与补检索 |
| Unified Evidence 4.0 | V5 | Frozen | Local/Web/URL 的唯一规范证据契约 |
| Web Safety / SSRF | V5 | Frozen | 外部内容不可信语义及 URL 网络边界 |
| Approval / Version / Rollback | V5 | Stable, extended by adapter | 高影响写入确认、快照、历史和恢复 |
| Markdown Document Support | V6.0 | Stable | `.md`、`.markdown` 进入同一 Document Pipeline |
| Research Task Runtime | V6.1 | Stable | 复杂任务异步执行、查询、取消、恢复及结果持久化 |
| Evidence Quality Gate | V6.2 | Stable | 完整性、定位、时效、显式冲突和结论关联检查 |
| Web Search Reliability | V6.3 | Stable | Tavily 错误分类、429 有界重试、校验、任务内复用和 Trace |
| Evidence Report Generation | V6.4 | Stable | 从保存结果生成引用报告，经既有审批与版本链导出 |

`Stable, extended by adapter` 表示 V6.4 为 Markdown/Word 报告增加格式适配，同时继续使用原 pending action、审批绑定、版本事务和回滚实现，没有第二套审批或版本系统。

## 4. 关键数据流

### 4.1 Markdown Document

```text
.md / .markdown
  → File Type Router
  → Document Lifecycle
  → Markdown Parser
  → existing DocumentBlock metadata
  → existing Chunk / Index / Retriever
  → Unified Evidence 4.0
```

标题层级进入 `metadata.heading_path`，行号进入 `line_number/line_end`。表格复用 Table Block；代码语言、链接和列表作为 Block 内容及 metadata 保存。没有 Markdown 专用 Retriever。

### 4.2 Research Task

```text
Complexity rules
  → CREATED
  → RUNNING / PLANNING
  → WAITING_TOOL with LOCAL_RETRIEVAL or WEB_RETRIEVAL
  → EVIDENCE_CHECK / GENERATING
  → COMPLETED | FAILED | CANCELLED
```

复杂度判断使用确定性规则。Task 复用 Async Runtime，Checkpoint 保存已完成步骤；失败信息使用稳定错误码和脱敏消息。同一用户、查询及时间窗口内支持幂等提交。

### 4.3 Evidence Quality

```text
Answer Draft + Unified Evidence 4.0
  → deterministic structural/association checks
  → PASS or WARNING + warning codes
  → unchanged Answer / saved Research result
```

Quality Gate 不调用 LLM，不输出 truth score 或 accuracy score。WARNING 不将成功任务改成 FAILED，也不自动选择冲突来源。

### 4.4 Web Search

```text
retrieve_web
  → current-task reuse
  → Tavily
  → 429: Retry-After, wait at most 2 seconds, retry at most once
  → response validation
  → original normalization, Web Safety and Evidence Factory
  → provider Trace
```

V6 只支持 Tavily 单一搜索 Provider。认证、限流、超时、网络、非法响应、空结果和未配置状态具有程序化分类。Trace 不记录 API Key、Authorization Header 或完整敏感查询。

### 4.5 Evidence Report

```text
completed Research result
  → Quality Gate
  → deterministic Report Draft with original Evidence IDs
  → Preview + Diff
  → existing Human Approval
  → Markdown / existing Word Writer
  → existing Version Storage
  → approved Rollback when requested
```

生成报告不重新检索或调用 LLM。预览和拒绝不创建报告文件；只有确认后写入。Web 内容不拥有审批权限，不能从 Evidence 直接触发文件操作。

## 5. 测试体系

V6 Final 采用两层验收：

1. 全量 pytest 回归验证 V5、V6.0–V6.4 的现有契约。
2. `tests/evaluation/v6_cases/` 固定 5 个离线 Demo 场景，验证跨模块最终链路。

| Case | 文件 | 验收内容 |
| --- | --- | --- |
| 1 Markdown Document | `test_case1_markdown_document.py` | `student_info.md` 上传、标题路径、Unified Evidence、定位 |
| 2 Research Task | `test_case2_research_task.py` | Task 创建、真实 Local+Web Tool、结果、Quality Gate、Report |
| 3 Evidence Quality | `test_case3_evidence_quality.py` | Freshness Warning、Local/Web 显式冲突且不选赢家 |
| 4 Web Reliability | `test_case4_web_reliability.py` | 429 最多一次重试、timeout、invalid JSON，均稳定返回 |
| 5 Report Generation | `test_case5_report_generation.py` | Markdown/DOCX、Evidence、Warning、Approval、Version |

Evaluation 使用 SQLite 隔离数据库、临时文件目录、固定 LLM 替身和 httpx MockTransport，不读取 `.env` 中的真实凭证、不访问网络、不消耗 Tavily 配额。

冻结前验证结果：

- V6.4 基线完整回归：658 passed，0 failed，0 skipped，47.65 秒。
- V6 Final Evaluation Suite：8 passed，0 failed，0 skipped，1.49 秒。
- 加入 Final Suite 后的最终完整回归：666 passed，0 failed，0 skipped，45.85 秒（原有 658 项 + Final Evaluation 8 项）。
- 旧测试未删除、未修改、未 skip，也未降低断言。

## 6. V6 已实现能力

- `.md` 与 `.markdown` 的完整文档生命周期、结构解析、索引、检索和证据定位。
- 简单任务同步、复杂研究任务异步、状态进度、取消、幂等、Retry 与 Resume。
- Local、Web、Direct URL 与 Cross Source Retrieval 继续生成 Unified Evidence 4.0。
- Answer 和 Report 的证据结构、定位、时效、显式冲突和覆盖 warning。
- Tavily 429 Retry-After、超时/网络/认证/格式/空结果分类、任务内去重和 Trace。
- 从保存的 Research 结果生成结构化 Evidence Report。
- Markdown 与 Word 报告预览、人工审批、版本记录、下载及回滚。
- Web/Markdown 不可信内容不能授权工具调用、审批或写文件。

## 7. 明确未实现能力

- 多 Provider 路由、自动切换、Search Ensemble 或长期 Web 缓存。
- LLM 事实真假判断、可信度百分比、自动裁决冲突或自动补证。
- Streaming 输出、实时 Token/章节流式生成。
- 报告在线编辑器、前端报告工作台、富文本 Word 排版或自动发布。
- Evidence 图形化关系视图、前端引用定位交互或复杂来源比较 UI。
- 跨进程分布式队列、Celery、Redis、Kafka 或跨节点 Task 执行。
- 历史会话中心、跨会话研究项目管理或云端报告共享。

## 8. V7 规划接口与边界

V7 可以在不改变 V6 核心契约的前提下，通过现有接口建设体验层：

| V7 方向 | V6 可复用接口 |
| --- | --- |
| Streaming | Agent/Research 状态、Trace 和持久化结果；需新增只读事件传输层 |
| 前端优化 | 现有 `/api/chat`、文件、任务、Action 和报告 API |
| Evidence 展示 | Unified Evidence 4.0 与 `/api/evidence/{id}/locate` |
| Task 进度 UI | Research Task 查询中的 status、progress 和稳定错误字段 |
| 历史会话 | 已有 session、chat、Trace、Task 数据；需定义分页和授权接口 |
| 报告下载体验 | Report 查询、审批状态、download、Version 与 Rollback API |

V7 不应通过前端绕过 V6 的 Tool Boundary、Web Safety、Quality warning、Human Approval 或 Version 事务。若未来改变 Evidence Schema、任务状态或报告持久化模型，应作为显式版本迁移处理，而不是静默改变 V6 契约。

## 9. 冻结规则

V6 Final 提交仅包含本架构文档和固定 Evaluation Suite。业务代码、数据库 Schema、Agent、Retriever、Provider、Evidence、Runtime、安全、审批和版本实现应与 V6.4 基线 `2bdc9c5` 一致。

最终完整回归和冻结比对均已通过。冻结提交使用消息 `V6 final evaluation and freeze`，并在该提交创建 annotated tag `v6-final-stable`。标签创建后停止开发，后续功能进入 V7 分支或明确的新阶段。
