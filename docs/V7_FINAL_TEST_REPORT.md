# V7 Final Test Report

## 1. 测试环境与 Git 基线

- Linux x86_64；Python 3.10.20。
- pytest 9.1.1、Streamlit 1.61.1、FastAPI 0.141.1、httpx 0.28.1、python-docx 1.2.0。
- 环境：`/home/gxh/miniconda3/envs/tuli_env`。
- 分支：`feature/v7-development`。
- 初次 Final 提交：`87c4e8c7e5b24bd5c33e727aa8147357e30c00fe`。
- `v6-final-stable` 是最终 V7 提交的祖先；最终冻结 tag 为 `v7-final-stable`。
- 初次冻结后的人工体验验收包含界面简化、Logo、聊天输入区布局和复用既有 Approval/Version 的文档操作入口；未新增 Agent、Retriever、Evidence Schema 或 Provider。

测试沿用隔离 SQLite 与临时上传目录；不向用户资料库提交测试文件。测试没有展示 `.env` 或认证信息。

## 2. 测试数量与命令

初次冻结时为 780 项测试；体验验收修订补充了权限说明、文档确认操作和界面默认状态测试。

单独 Demo：`pytest -q tests/evaluation/test_v7_final_demo.py`，**1 passed，3.82 秒**。

最终完整命令：`pytest -q`。最终结果：**总计 818，passed 818，failed 0，skipped 0，用时 87.66 秒**。V5/V6/V7 所有收集到的测试通过。

没有删除旧测试、skip 旧测试或降低断言。

## 3. V7 功能验收

| 阶段 | 验收内容 | 测试依据 |
| --- | --- | --- |
| V7.0 | Sidebar、Header、Dashboard、导航、原工作台可访问 | `test_frontend_foundation_v70.py`、`test_frontend_workspace.py` |
| V7.1 | 创建知识库、文件管理、索引状态、进入聊天 | `test_knowledge_base_v71.py`、`test_frontend_knowledge_v71.py` |
| V7.2 | 三种聊天模式、知识库上下文、Evidence、研究状态、浏览器历史 | `test_chat_experience_v72.py` |
| V7.3 | 列表、筛选、详情、Timeline、Trace、取消/重试/恢复 | `test_research_center_v73.py` |
| V7.4 | 报告列表、Markdown 展示、两种格式下载、审批、版本、Word Rollback | `test_report_center_v74.py` |
| V7.5 | Health、Provider 未验证状态、范围内统计、错误与敏感信息投影 | `test_system_dashboard_v75.py` |
| V5/V6 | 原核心能力与固定验收场景 | 全量 tests 与 `tests/evaluation/v6_cases/` |

上述功能的单元、API 集成和 Streamlit AppTest 均通过，统一结果见全量测试记录。手工外观、移动端布局和真实浏览器计时不在此次自动验收覆盖范围内。

## 4. 端到端 Demo 结果

脚本：`tests/evaluation/test_v7_final_demo.py`。

| 步骤 | 执行方式与检查 | 结果 |
| --- | --- | --- |
| 创建知识库 | AppTest 创建表单 → 真实 FastAPI → SQLite | 通过 |
| 上传 Markdown | 真实 multipart 知识库上传 API | 通过 |
| 建立索引 | 检查已有 file_id 可查询、Chunk 存在、详情 Indexed | 通过 |
| 新建聊天 | 知识库详情按钮，检查 knowledge_base_id | 通过 |
| 选择研究模式 | 选择“报告生成”，提交复杂问题，调用原 Research Task API | 通过 |
| 执行研究 | 原 Agent、Local/Web Retrieval、Async Runtime 和数据库 | COMPLETED |
| 查看 Evidence | 真实 LOCAL 与 WEB Evidence，右侧显示，Quality Gate PASS | 通过 |
| 生成报告 | 原报告 API，已保存草稿进入报告中心 | 通过 |
| 审批前下载 | 两种格式下载都返回 409 | 通过 |
| 预览并确认导出 | 原审批卡 → Action API → 真实文件写入 | 通过 |
| 下载 Markdown/Word | 实际 Download API 二进制；检查 Markdown 内容和 DOCX 可解析内容 | 通过 |
| 版本记录 | 每种导出检查原 Version API 至少两条记录 | 通过 |

替代边界：外部 LLM 返回固定工具调用和回答，Web Search 返回固定网页条目。未替换 Agent、Retriever、Evidence Factory、Quality Gate、Runtime、Report Generator、Approval、Version 或 Download。文件选择器未由 AppTest 自动化，上传步骤通过同一真实 multipart API 执行。HTTP 通过 ASGI 内存传输桥接，不启动外网服务。

因此结果证明可重复的应用链路，不证明线上模型输出质量、Tavily 凭据/网络可用性、真实浏览器下载交互或异步请求在网络层的返回时序。原 V6 Runtime 测试继续覆盖任务创建、重试和恢复契约。

## 5. 冻结模块检查

对 V6 tag 的 backend 全目录差异审计仅发现 V7.1 知识库管理四文件：`knowledge_api.py`、`repositories/knowledge_repository.py`、`main.py` 路由注册、`migrations.py` Migration 14。

Source Router、Tool Boundary/Tool Scope Resolver、Cross Source Retrieval、UnifiedEvidenceFactory、Evidence Schema、Web Safety、Research Task Runtime 核心逻辑与 V6 冻结版本完全一致。Async Runtime、Workflow、Report Generator、Approval、Version、Rollback 也一致。

这表示冻结核心未被 V7 改写，不表示整个 backend 目录与 V6 零差异。V7.1 管理 API 与持久化扩展是已接受的版本变更。

## 6. 问题与限制记录

- V5 分支/tag 同名：审计使用完整 `refs/tags/` 引用消除歧义，无需改动版本历史。
- 尚无按知识库隔离检索；聊天只绑定前端上下文。
- 历史与发现范围依赖浏览器已知会话，不是持久全局列表。
- Provider/模型/组件缺少公开状态接口，只标注未验证，不作为线上连通性验收。
- Markdown 没有原 Rollback 支持；Word 版本恢复走原审批。
- 自动刷新使用 Streamlit fragment；测试模拟重跑，不宣称完成真实浏览器 5 秒计时测试。
- 这些是已记录的版本边界，不在 Final 扩展功能或新增 API。

## 7. 最终结论

在上述公开边界内，V7 的产品工作空间、文档操作确认、全量回归和可重复 Demo 已完成验收，可冻结。`v7-final-stable` 指向最终体验验收提交；最终 commit/tag 对应关系以 Git 为准，提交与建 tag 后检查工作区 clean。

不执行远程 push，不开发 V8。线上 Provider 连通性和真实浏览器人工体验仍需在实际部署环境中验证，不能由本测试结果代替。
