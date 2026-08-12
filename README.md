# 学生材料智能文档助手（Student Document Agent）

## 项目目标

本项目用于学习 LLM Agent 的工作机制，并逐步构建一个学生材料智能文档助手。未来系统将理解用户的自然语言任务，通过 Python 工具读取和处理 Excel、Word 等材料，并在涉及写入操作时先请求用户确认。

当前第一版 MVP 已提供 FastAPI 后端、Streamlit 骨架、只读学生材料 Tools，以及基于 OpenAI-compatible Chat Completions API 的 Tool Calling。当前不包含文件上传、数据库或文件写入 Agent 能力。

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
```

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
streamlit run frontend/app.py
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

## 当前阶段

当前阶段完成了只读 Python Tools、FastAPI 接口和首次 LLM Tool Calling 循环。LLM 仅负责理解、工具选择和结果整合，学生数据与数值计算均来自 Python Tools。写文件工具未向 LLM 暴露。
