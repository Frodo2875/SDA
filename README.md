# 学生材料智能文档助手（Student Document Agent）

## 项目目标

本项目用于学习 LLM Agent 的工作机制，并逐步构建一个学生材料智能文档助手。未来系统将理解用户的自然语言任务，通过 Python 工具读取和处理 Excel、Word 等材料，并在涉及写入操作时先请求用户确认。

当前为第一版 MVP 的初始阶段，仅提供能够启动的 FastAPI 后端和 Streamlit 前端，不包含学生查询、Excel 业务逻辑、文件上传、Agent、Tool Calling 或 LLM API 集成。

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

当前阶段只完成系统骨架。业务模块均为占位模块，后续功能将在明确需求后分阶段实现。
