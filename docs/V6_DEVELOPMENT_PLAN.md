# V6 开发计划

日期：2026-09-14。基线：`36ccc19aac056b337747f7c40e0674809b2b41b6`。
当前分支：`feature/v6-development`。当前阶段：Phase 0 完成；Phase 1–6 未开始。

V6 目标是复杂研究任务的可靠执行、Evidence 质量检查和可审阅报告交付。
本计划是未来阶段范围，不授予本轮实现许可。依据见 [架构审计](V6_ARCHITECTURE_AUDIT.md) 和 [模块地图](V5_STABLE_MODULE_MAP.md)。

## 共同约束

- 复用单 Document Pipeline、单 Async Runtime、现有 Workflow，不另建 Agent 执行体系。
- 冻结 Evidence Schema / Factory、Source Router、Tool Boundary、Retriever、Web Safety/SSRF 和 Diff/Approval/Version/Rollback 契约。
- 每阶段只实施自身范围；既有测试不得通过删除、改弱断言或人为 skip 来获得通过。
- 每阶段执行针对性测试及完整 `pytest -q`；报告 passed/failed/skipped/耗时和实际 commit。失败先定位，不顺手修复冻结 V5。
- 默认测试采用 Fake Provider、MockTransport、确定性时钟和临时数据库，不把线上服务波动作为单元验收依据。
- 不默认新增数据库迁移、搜索 Provider 或大型工具集；如实现发现需要突破约束，应先报告具体冲突。

## Phase 0 — V5 基线验证与 V6 架构审计

| 项目 | 内容 |
| --- | --- |
| 目标 | 确认冻结基线、完整回归、扫描架构、形成稳定地图/审计/阶段计划 |
| 依赖 | 正确仓库目录；V5 冻结分支/标签可解析；既有测试环境可用 |
| 修改模块 | 仅新增 `docs/V5_STABLE_MODULE_MAP.md`、`docs/V6_ARCHITECTURE_AUDIT.md`、`docs/V6_DEVELOPMENT_PLAN.md` |
| 测试 | 实际仓库、既有 tuli_env 中执行 `pytest -q`；Git 分支/HEAD/冻结引用/工作区核验 |
| 验收标准 | HEAD 与冻结分支、解引用标签相同；478 项全通过；仅新增三份文档；没有业务实现 |
| 本次结果 | 478 passed，0 failed，0 skipped，38.80s；目录错误与 PATH 问题通过选择正确目录/已有环境解决，无 Git 或依赖变更 |

## Phase 1 — Markdown

| 项目 | 内容 |
| --- | --- |
| 目标 | `.md` 和 `.markdown` 进入既有 Upload → Parser → Block → Index → Evidence 路径，并可预览来源 |
| 依赖 | Phase 0 通过；采用 Markdown 作为内部 txt 子格式的兼容策略 |
| 修改模块 | `file_upload.py`、`input_router.py`、`multiformat_parser.py`、`tools/file_tools.py` 的格式入口；前端 `components/file_panel.py` 等上传/展示入口；新增 Markdown 专项测试。Index/Async/定位器优先复用 txt 路径 |
| 测试 | 两种扩展名、MIME、中文/编码、标题/列表/代码/表格、空文件/大文件/伪装二进制；长块拆分、原始行定位、上传/重索引/删除；HTML/链接不执行不联网；Evidence 3/4 兼容与全部 V5 回归 |
| 验收标准 | 两种格式全链路可检索且 Evidence ID 能定位；原文件名保留；Chunk 有格式/层级信息；不引入 markdown Evidence 枚举，不改变 Factory/Schema/检索算法；旧格式全通过 |

最小方案不新增独立文件类型。TXT 筛选包含 Markdown，界面依据扩展名显示具体格式。
标题路径和行终点存在 Chunk 元数据，不承诺新增 Evidence 字段或切分后精确行范围。

## Phase 2 — Research Task

| 项目 | 内容 |
| --- | --- |
| 目标 | 有界子问题计划、可观察长任务、节点 checkpoint、取消、失败恢复与审批等待 |
| 依赖 | Phase 1 通过；复用 V5 Source/Tool scope、TaskPlan、Cross Source Retrieval；质量判断暂沿用既有 sufficiency |
| 修改模块 | 新增窄研究计划/服务适配层；`schemas.py` 的 Async 请求类型和 payload 校验；`runtime/async_task_runtime.py` 的 handler 注册及研究恢复接入；任务展示接口按需增量适配。复用 `planner.py`/`task_runner.py` 的公开能力，不重写执行器 |
| 测试 | 多子问题与依赖、局部失败、恢复跳过已完成节点、全局预算跨恢复不重置、输入变化失效、取消、审批等待、进程中断/重启、重复领取及写入不重放；既有 Async/Workflow/安全回归 |
| 验收标准 | 单一队列和任务身份；完成每个节点持久化可靠结果引用；恢复沿用原计划/子任务；只读重试有上限，失败单元可诊断；取消在安全边界生效；研究不越过来源范围；无第二 Async Runtime |

必须明确处理三个缺口：默认 workflow handler 重新调用 `run_agent`；retry 清空 checkpoint；
当前 running 状态没有自动接管。研究适配必须区分 Retry/Resume 并验证崩溃恢复安全性。
若不能确认旧 worker 停止，禁止自动双重执行；不能以持久化队列为由宣称已解决故障恢复。
生成节点须明确衔接既有生成步骤记录，不能假定 `execute_workflow` 会执行所有 StepType。

## Phase 3 — Evidence Quality Gate

| 项目 | 内容 |
| --- | --- |
| 目标 | 在 Unified Evidence 与 Answer/Report 之间加入可解释、可复现的质量检查 |
| 依赖 | Phase 2 的研究需求/节点结果与 Evidence ID；现有 sufficiency 和定位能力 |
| 修改模块 | 新增独立 Gate 服务与 assessment 模型（不是 Evidence 模型）；研究/回答适配层调用；复用 checkpoint/Trace 保存有界结果 |
| 测试 | 数量足够但论点无支持、缺字段、重复转载、来源未知、过期/无发布日期、显式冲突、OCR review、不可定位本地证据、网页来源检查；同输入确定输出；序列化前后原 Evidence 不变；生成后引用越界 |
| 验收标准 | 独立 pass/needs_review/insufficient 及原因码、覆盖缺口和引用 ID；关键缺口不输出确定结论；Gate 不修改 Evidence、不授予 Tool/审批权限；补检索服从已有 scope/预算；旧 Evidence 精确兼容 |

质量评价不宣称自动证明事实真伪。对无法判断的支持度保留限制，禁止用单一综合分数掩盖冲突。

## Phase 4 — Web Reliability

| 项目 | 内容 |
| --- | --- |
| 目标 | 单 Tavily Provider 在限流、等待、超时、鉴权错误和空结果下有界且可观察 |
| 依赖 | Phase 2 的 deadline/预算/取消接口；Phase 3 的证据不足返回语义；V5 Web 契约 |
| 修改模块 | `tavily_search_provider.py` 的 HTTP 调用适配；`search_provider.py` 的错误元数据；窄重试策略；研究/Web 外层预算与 Trace 接入。保持既有服务成功/失败响应兼容 |
| 测试 | MockTransport 覆盖 429→成功、持续 429、Retry-After 秒数/日期/非法/超长值、Timeout、401/403、无效 JSON/results、空结果和无可引用正文；确定性 clock/sleep；取消等待、总尝试数和 deadline；密钥脱敏及全部 Web Safety 回归 |
| 验收标准 | Auth 不重试；瞬态失败最多在预设次数和 deadline 内重试；每次请求/等待可追踪；无多层重试倍增；HTTP attempts 计入研究预算；空结果不造证据；仍只有 Tavily 一个真实搜索 Provider |

不扩展 SSRF、Direct URL、安全路由策略，不增加 failover、爬虫、浏览器或缓存。
具体最大重试次数和等待上限需在该阶段以命名配置固定，并由边界测试验证，不交给模型动态决定。

## Phase 5 — Evidence Report

| 项目 | 内容 |
| --- | --- |
| 目标 | 研究结果生成可审阅报告草稿，并通过既有安全写入链交付 DOCX 内容 |
| 依赖 | Phase 2 可恢复任务，Phase 3 Gate，Phase 4 Web Reliability；已有可写 Word 目标 |
| 修改模块 | 新增报告组织/引用校验服务；研究服务的草稿/动作状态衔接；按需增加报告预览展示。只调用现有 Word Writer、Diff、Approval、Version、Rollback |
| 测试 | 本地/网页混合引用、缺失引用/编造 ID、冲突和限制呈现、空证据、超长正文、通用报告空学生身份、只读目标；预览无副作用、取消不写入、正文/文件变化重新审批、确认仅一次、恢复不重复追加、版本失败补偿、undo/rollback |
| 验收标准 | 报告包含问题/范围、方法、结论、引用、冲突/限制和来源列表；关键论点均有核验引用或明确证据不足；仅冻结内容被审批执行；成功写入有新 Version，回滚走既有 Approval；全量回归通过 |

最小交付使用已有可写 DOCX 模板和 append 语义；正文在现有 100,000 字符限制内。
既有 Writer 新增一个段落，不能把复杂排版、自动新建 DOCX 或整篇替换当作已具备能力。
如要求这些能力，先调整范围并审查与冻结 Diff/Version 的冲突，禁止绕过审批直接写文件。

## Phase 6 — Evaluation Freeze

| 项目 | 内容 |
| --- | --- |
| 目标 | 建立 V6 可复现评估与发布冻结证据，确认 V5 行为未退化 |
| 依赖 | Phase 1–5 各自验收通过，关键可靠性缺口闭环 |
| 修改模块 | `evals/`、`tests/evals/` 与版本验收文档；必要时仅增量扩展 `evaluation_service.py` 的统计能力；不再新增业务能力 |
| 测试 | V5 全回归 + V6 固定样例：Markdown、复杂多源、任务中断恢复、取消、冲突/过期/空证据、429/鉴权、报告审批/回滚；固定 Mock 与故障注入，汇总对应 Trace |
| 验收标准 | 既有 478 项与新增测试全部通过，0 failed、0 skipped；固定集引用 ID 有效率 100%；关键无支持论点均阻断或明确标记；恢复测试已完成节点重放次数 0；越权/未经审批写入 0；每例均有结果、原因及 Trace；发布 commit/环境/耗时/已知限制完整 |

性能以固定数据、固定环境记录任务总耗时、恢复耗时和 HTTP 尝试数，并与本阶段基准对照；
不预先虚构线上成功率或延迟保证。冻结标签在最终发布步骤单独执行，本轮不创建。

## 当前交接

Phase 0 技术验收通过，具备进入 Phase 1 条件。当前仅新增三份审计文档，未开发 Markdown 或任何 V6 功能。
下一步等待用户发出 Phase 1 指令，再按本计划进入实现。
