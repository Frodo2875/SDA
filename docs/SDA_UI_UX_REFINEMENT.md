# SDA UI/UX 优化设计与实施

日期：2026-10-17。需求来源：项目根目录 ` SDA_UI_UX_PRD.md`。

## 范围与参考

OpenMedKit 位于 `/home/gxh/Projects/Agents_pro/open-medkit`，本轮仅阅读源码。参考其 `Layout.tsx`、`Sidebar.tsx`、`index.css`、`tailwind.config.js`、`AiPanel.tsx`、`MedGrid.tsx`、`SelectMenu.tsx` 与 `ConfirmDialog.tsx` 的层级、语义颜色、工具栏、反馈和窄屏思路；未复制 React、Tailwind、业务逻辑、素材或图标包。

SDA 保留 Python / Streamlit / FastAPI。没有修改 API 路径、数据结构、Agent、Retrieval、Evidence、任务运行时和审批执行逻辑。使用 SDA 已有 Logo 与 Streamlit 原生 Material 图标。

## P0：基础与聊天

- `frontend/styles.py` 集中维护蓝色强调、边框、弱背景、间距、圆角与响应式规则。颜色从当前前景色派生，避免 Python 判断主题在初次渲染时不准确，也避免固定浅色侧栏。
- `shell.py` 复用原导航 Key 与回调，增加原生图标，收紧侧栏间距；Header 使用独立 keyed container。
- `.streamlit/config.toml` 仅配置原生浅色/深色主题的 primaryColor，令标签选中态和输入焦点也使用 SDA 蓝色，不覆盖背景或主题选择方式。
- 聊天正文限制最大宽度 960px；欢迎标题居中；“更多”变为紧凑按钮；模式仍位于输入框左侧。保留三种模式和多行输入的原生行为。
- 参考资料与处理详情继续默认隐藏。两项显示偏好通过非 Widget Session State 保存，避免控件隐藏后 Streamlit 清理 Widget Key 造成偏好丢失。
- 聊天使用 SDA Logo 与原生用户图标；长文本可换行，代码与表格局部滚动。
- 普通问答、研究任务创建、报告整理显示等待信息，未引入新的业务执行路径。

## P1：业务页面

- 知识库创建表单收起；列表标题与进入操作同行。详情中的文件概览默认收起，文件管理保持主要入口。
- 文件搜索与类型、状态、排序组成公共工具区。详细技术元数据默认收起；错误、状态及原操作入口仍保留。
- 筛选无结果与没有任何文件分别提示；后端错误仍单独提示，不能伪装为成功空列表。
- 任务及报告编号查找默认收起，主要列表优先；卡片标题与操作同行。任务 5 秒轮询、重试、恢复、取消行为不变。
- 报告复用原预览、审批、下载、Version 和 Rollback 接口。审批确认和必要 Diff 不随诊断信息一同隐藏。
- Dashboard、设置、统计与确认卡片从公共样式获得一致的按钮、指标、边框和标题，无需重复改写业务代码。

## P2：适配与可访问性

- 900px、640px 为局部布局断点，聊天输入区在窄屏保留模式与输入两列，避免选择器挤占全部宽度。
- 保留 Streamlit 原生侧栏展开/收起、表单标签、键盘操作和主题切换。
- 提供可见键盘焦点；减少动画偏好下禁用按钮过渡；未增加装饰动画。
- 顶部留出原生 Streamlit 工具栏空间，避免 Header 被遮挡。
- 有限使用原生 `data-testid`，不依赖随机 Emotion 类名、不使用 `!important`。自有布局优先使用 keyed container。

## 测试与隔离

自动化验证沿用 Pytest / Streamlit AppTest，新增 `tests/test_ui_ux_refinement.py`。真实浏览器脚本为 `tests/browser/sda_ui_acceptance.cjs`，独立安装的 Playwright 仅用于验收，不是应用运行依赖。

`scripts/ui_acceptance_server.py` 通过启动脚本重定向数据库和 DATA_DIR，不修改后端代码；仅绑定 `127.0.0.1`。环境文件必须显式传入，测试数据目录必须位于项目之外。该脚本使用真实 LLMClient，仅附加调用耗时和成功标记；不替换模型响应、不保存密钥或模型请求正文。

当前用户提供的文件实际名为 `。env_local`，同时忽略 `.env_local`、`.env.local`、`。env_local`，防止误提交。凭据只在测试进程载入，不进入浏览器。

启动示例（从项目根目录；使用已具备 requirements 的 Python 环境）：

```bash
python scripts/ui_acceptance_server.py --env-file '。env_local' --data-root /tmp/sda-ui-test --port 18000
```

另一个终端：

```bash
BACKEND_BASE_URL=http://127.0.0.1:18000 NO_PROXY=localhost,127.0.0.1,::1 python -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 18501 --server.headless true --browser.gatherUsageStats false
```

真实浏览器（需要外部已安装的 Playwright 和 Chromium）：

```bash
SDA_PLAYWRIGHT_MODULE=/path/to/node_modules/playwright SDA_EVIDENCE_DIR=/tmp/sda-ui-test/browser node tests/browser/sda_ui_acceptance.cjs
```

自动化回归：

```bash
python -m pytest -q
```

具体执行结果见 `SDA_UI_UX_TEST_REPORT.md`。模拟测试、真实模型和真实浏览器证据分别记录。

## 保留的产品边界与维护风险

- 当前聊天为非流式 API，未新增 Streaming 或同步请求的“停止生成”。异步任务取消仍按原安全点生效。
- 知识库仍是管理上下文，并不保证仅检索该库；原提醒保留。
- 会话历史仍仅保存在浏览器会话；任务/报告仍沿用原发现范围。
- Provider 状态无公开接口时仍显示未检测，不伪造 Connected/Ready。
- UI 不承诺替代服务端权限或去重。写入、删除和恢复始终依赖后端最终校验。
- Streamlit 升级后需重跑真实浏览器用例；CSS 选择器兼容性属于持续维护项。
- 版本策略：保留原 `v7-final-stable` 冻结点。本轮 UI/UX 更新按用户后续授权独立提交并同步远程，不覆盖旧标签。
