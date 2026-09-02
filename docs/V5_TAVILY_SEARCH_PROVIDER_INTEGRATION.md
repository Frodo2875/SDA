# V5 Tavily Search Provider Integration

## 目标

为 V5.1 已定义的 `SearchProvider` port 增加首个生产 Adapter，使 `WEB` 和
`BOTH` 策略可以执行真实关键词检索。该接入不修改 Source Router、Tool Boundary、
Cross-Source Retrieval、Unified Evidence 或 Web Safety 的核心逻辑。

## 配置

运行环境从项目根目录 `.env` 读取：

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=
TAVILY_BASE_URL=https://api.tavily.com
WEB_SEARCH_TIMEOUT_SECONDS=8
WEB_SEARCH_DEFAULT_TOP_K=5
```

API Key 只能填写在本地 `.env` 或部署环境变量中，不得提交到 Git、Trace 或日志。
未配置、Provider 名称未知或配置无效时，系统继续使用安全失败的
`UnavailableSearchProvider`。

## 数据流

```text
Environment configuration
        -> SearchProviderFactory
        -> TavilySearchProvider
        -> normalize_search_results
        -> Web security metadata
        -> UnifiedEvidenceFactory
        -> UnifiedEvidence
```

Adapter 使用 Tavily `/search`，固定使用 `basic` 搜索并关闭 `include_answer`、
`include_raw_content`、`include_images` 和自动参数。系统只消费真实搜索记录的标题、
URL 和摘要，不采用 Tavily 的生成式答案。

## 安全和错误

- Bearer Key 只进入 Authorization header；错误和 Trace 不记录 Key。
- 401/403、429、超时、Provider 错误和非法响应映射为稳定错误码。
- Provider 结果继续接受 HTTP(S) URL 校验和 Prompt Injection 标记。
- 自动测试通过 `httpx.MockTransport`，并强制隔离本地真实 Provider，不访问公网或
  消耗 credits。

## 限制

- V5 只接入单个 Tavily Adapter，不做多 Provider 切换、缓存或复杂 Ranking。
- 通用网页搜索不能替代专用天气 API；回答的实时性取决于实际返回网页。
- Direct URL Fetch 保持原有独立、安全且受限的实现。

## 验证结果

- 离线定向测试：45 passed；
- 全量回归：475 passed；
- 生产配置识别为 `TavilySearchProvider`；
- 最小在线检查返回 1 条结果并生成 1 条 `WEB` Unified Evidence；
- 在线检查仅输出状态、数量和来源域名，没有输出 API Key。
