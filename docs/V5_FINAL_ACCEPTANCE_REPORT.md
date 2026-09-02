# Student Document Agent V5 Final Acceptance Report

## 1. 验收结论

V5 在 V4 冻结基线上完成增量开发，核心后端、真实 Web Search、Streamlit
操作入口、安全边界、回归测试和发布文档均达到本版本定义的完成标准。

验收结论：**PASS / V5 Final Stable**。

## 2. 版本基线

- Branch：`feature/v5-development`
- V4 Base：`0554b279f5c2255d4f8fec64fd7b4c10915f0bc4`
- V5 功能验收基线：`03254e1`（Tavily Provider 接入完成）
- Release Tag：`v5-final-stable`（最终提交后更新）

## 3. 需求覆盖

| 能力 | 实现状态 | 主要验证 |
| --- | --- | --- |
| PPT/PPTX、TXT、JSON、CSV | PASS | Router、Parser、Lifecycle、Index、Query、Preview |
| Source Router | PASS | LOCAL、WEB、BOTH、DIRECT_URL |
| Tool Boundary | PASS | 策略在 Tool 调用前过滤和拒绝越界调用 |
| Tavily Web Search | PASS | 环境装配、结果映射、错误映射、在线最小检查 |
| Direct URL | PASS | Fetch、Parser、重定向校验、多个公网 IP 有界尝试 |
| Unified Evidence 4.0 | PASS | Local、Web、URL 共用 Factory，保留 V3 兼容序列化 |
| Cross-Source Retrieval | PASS | Evidence Sufficiency 后补检索，不默认 Local+Web 全搜 |
| Retrieval Budget | PASS | 最大轮次、最大 Tool 调用数、Timeout、循环停止 |
| Web Prompt Injection | PASS | 保留正文并标记 Untrusted Data，不能修改 Tool 策略 |
| SSRF / URL Safety | PASS | Fetch 前拒绝 localhost、私网、metadata 和非法协议 |
| Trace / Evaluation | PASS | 记录可观察状态，不记录内部推理或 API Key |
| Streamlit V5入口 | PASS | 新格式上传、筛选、预览、Evidence、Trace |

## 4. 最终数据流

```text
User Request
  -> Source Router
  -> Tool Scope Resolver
  -> Agent Runtime
  -> Local Retrieval / Tavily Search / Direct URL
  -> Security Metadata
  -> UnifiedEvidenceFactory
  -> UnifiedEvidence
  -> Evidence Sufficiency
  -> Answer + Trace
```

本流程没有把 Web Search 写死到 Agent 主循环。LOCAL 请求不开放 Web Tool；
BOTH 只有在策略和 Evidence Sufficiency 需要时才进行受控跨源检索。

## 5. 最终测试

- V5 前端、多格式、Tavily 定向回归：`28 passed`；
- 全量回归：`478 passed in 35.24s`；
- 测试使用 Fake Provider、`httpx.MockTransport` 和 Fake URL Transport，不访问公网；
- 测试 fixture 强制隔离开发者本地 Tavily Key，不消耗线上 credits；
- Git diff 检查不包含 `.env` 或任何真实 API Key。

## 6. Demo 验证

- Tavily 生产配置成功识别为 `TavilySearchProvider`；
- 最小在线查询成功返回搜索结果并生成 `WEB` Unified Evidence；
- Streamlit 天气问题成功展示网页结果；
- Local+Web 问题可以同时读取本地 Evidence 并调用 `retrieve_web`；
- Web 不可用、冲突或证据不足时，Agent 如实说明且不编造 Evidence。

## 7. 前端收尾

- 上传入口已开放：Excel、Word、PDF、图片、PPT/PPTX、TXT、JSON、CSV；
- 文件类型筛选和展示标签已同步；
- PPT/PPTX、TXT、JSON 可重新解析和重新索引；
- CSV 沿用结构化 Schema 流程；
- 页面版本文案已更新为 V5；
- 普通请求超时维持 60 秒，复杂 Chat 等待上限为 120 秒；
- 前端分别提示连接失败、处理超时和一般通信失败。

## 8. 安全边界

- Tavily Key 只从本地 `.env` 或部署环境变量读取；
- Key 不进入 Git、Trace、Provider错误或前端响应；
- Tavily `include_answer`、`include_raw_content`、`include_images` 和
  `auto_parameters` 均关闭；
- Web 结果必须经过安全标记和统一 Evidence Factory；
- Source Router、Tool Boundary、Cross-Source Retrieval 与 Evidence 核心没有因
  发布收尾发生架构改写。

## 9. 已知限制

1. Tavily 是通用 Web Search，不替代专用天气、金融或政策数据库 API；实时性和
   权威性取决于返回网页，Evidence 不代表来源内容必然真实。
2. V5 不包含浏览器自动化、网站遍历、长期爬取、多 Provider Failover、缓存或
   复杂 Ranking。
3. 复杂跨源 Chat 仍使用同步 HTTP 请求；前端已将等待上限提高到 120 秒。进一步的
   流式输出和长任务交互可在后续版本设计。
4. `.ppt` 的处理能力受旧二进制格式自身限制；复杂动画、SmartArt 和视觉语义不在
   V5 范围内。

## 10. 冻结条件

- `pytest -q` 全部通过；
- 工作区只包含本次发布收尾文件后提交，并在提交后保持干净；
- `v5-final-stable` 指向最终收尾提交；
- 不继续开发 V6 功能。
