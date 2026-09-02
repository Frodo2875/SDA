# 学生材料智能文档助手（Student Document Agent）

## 项目目标

本项目是一个可信开放型 Document Agent。系统通过 Python 工具处理本地文档、受控网页检索、证据生成、状态管理和安全验证；LLM 只负责理解任务、选择允许的工具并组织结果。

V5 支持 Excel、Word、PDF、图片、PPT/PPTX、TXT、JSON 和 CSV，支持 Local、Web、Direct URL 与 Local+Web 检索，并统一生成 Evidence 4.0。外部网页一律作为 Untrusted Data，URL Fetch 具有 SSRF 防护，写入操作继续要求人工确认。

## Python 环境要求

- Python 3.11

## 安装依赖

进入项目目录后，在你已有的 Python 3.11 环境中执行：

```bash
pip install -r requirements.txt
```

## 启动 FastAPI 后端

```bash
uvicorn backend.main:app --reload --port 8000
```

健康检查地址：`http://127.0.0.1:8000/health`

Swagger 地址：`http://127.0.0.1:8000/docs`

## LLM 配置

复制 `.env.example` 为 `.env`，并手动填写：

```dotenv
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
# 可选：仅用于 Trace Cost 统计，单位 USD / 1M tokens
LLM_INPUT_COST_PER_1M=0
LLM_OUTPUT_COST_PER_1M=0
```

可选的 Tavily Web Search 配置：

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=
TAVILY_BASE_URL=https://api.tavily.com
WEB_SEARCH_TIMEOUT_SECONDS=8
WEB_SEARCH_DEFAULT_TOP_K=5
```

API Key 只能保存在本地 `.env` 或部署环境变量中，不得提交到 Git。

聊天接口：

```text
POST /api/chat
```

请求示例：

```json
{
  "session_id": "demo-session",
  "message": "查询S001的学生信息"
}
```

## 启动 Streamlit 前端

另开一个终端，在同一项目目录下执行：

```bash
python -m streamlit run frontend/app.py
```

## 当前项目结构

```text
student_document_agent/
├── frontend/
│   └── app.py
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── schemas.py
│   ├── agent.py
│   ├── llm_client.py
│   ├── database.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── file_tools.py
│   │   ├── student_tools.py
│   │   ├── analysis_tools.py
│   │   └── word_tools.py
│   └── services/
│       ├── __init__.py
│       ├── student_identity.py
│       └── confirmation.py
├── data/
├── tests/
├── scripts/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 当前能力

- 多格式 Document 上传、解析、生命周期、索引和检索；
- OCR、Layout、表格、Cell、BBox 和手写识别；
- Source Router 与程序级 Tool Boundary；
- Tavily Search、Direct URL Fetch 和网页正文解析；
- Local / Web / URL Unified Evidence；
- 有轮次、工具调用量和超时预算的 Cross-Source Retrieval；
- Prompt Injection、SSRF、Tool Risk、Trace 和 Evaluation；
- Workflow、Async Task、Diff、Version、Rollback 和 Human-in-the-loop。

## 当前版本

V5 Final Stable

Branch：`feature/v5-development`

Tag：`v5-final-stable`

## V3.10 Trace Evaluation

当前开发分支已增加不改变业务执行的可观测指标：

- Tool 调用次数、成功率、错误率和耗时；
- Retrieval 模式、Fallback、结果数和 Top score；
- Workflow Task/Step 状态与完成率；
- Provider 返回的输入、输出和总 Token；
- 基于显式环境变量单价计算的 USD Cost。

固定离线评估案例位于 `evals/v3_10_cases.json`，最新 V3.10 接受结果位于
`evals/v3_10_results.json`。未配置 Token 单价时 Cost 固定记录为 0，不推测模型价格。
