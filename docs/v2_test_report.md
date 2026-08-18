# Student Document Agent V2 最终测试报告

生成日期：2026-08-18  
代码基线：`134f300`（V2.11）  
Evaluation 类型：离线、确定性、无真实 LLM API 调用

## 1. 环境

| 项目 | 实际环境 |
|---|---:|
| Conda 环境 | `tuli_env` |
| Python | 3.10.20 |
| SQLite | 3.53.3 |
| FastAPI | 0.141.1 |
| Streamlit | 1.61.1 |
| pytest | 9.1.1 |
| pandas | 2.3.3 |
| openpyxl | 3.1.5 |
| python-docx | 1.2.0 |
| pypdf | 6.16.1 |

项目目标运行环境仍为 Python 3.11。本次按项目既有 `tuli_env` 执行；Python 3.10.20 下没有发现影响结果的错误，本阶段未修改 Python 环境。

## 2. V1 Regression

V1 测试范围通过 Git 基线 `950ade3` 确认，包括：

- `tests/test_agent.py`
- `tests/test_api.py`
- `tests/test_confirmation.py`
- `tests/test_database.py`
- `tests/test_tools.py`
- `tests/test_upload.py`

V2.11 在 `test_api.py` 新增了一个文件详情/Trace API 测试，因此 V1 回归明确排除该新增节点。固定命令保存在 `evals/v1_regression.txt`。

实际结果：

```text
66 passed, 1 deselected in 2.17s
```

结论：V1 原 66 项核心能力全部通过，没有回归。

## 3. V2 Tool Test

`evals/coverage_manifest.json` 建立了完整验收编号映射：

- U01–U11：未知 Excel、Schema、字段语义、通用查询和聚合
- R01–R05：PDF、Word Chunk、RAG 与混合规则判断
- D01–D06：关联、冲突和数据质量
- W01–W07：Diff、Version、Undo、Rollback 和 HITL
- B01–B05：Batch、进度和部分失败
- E01–E06：Evidence、Trace、脱敏和 Context

其中 U05 对应全部白名单过滤操作及 null/not_found 语义；U10 对应字段语义映射持久化；U11 对应 Tool Registry 单一注册源和 SQL 参数拒绝。

V2 独立测试集合实际结果：

```text
124 passed in 4.06s
```

## 4. Integration Test

已覆盖的主要集成链路：

- Agent → Tool Calling → Python Tool → 最终回答
- 重名候选 → clarification_required → 禁止随机选择
- 结构化成绩/科研 + RAG 规则 → Python 条件判断 → Evidence Chain
- Task → Step → Retry → Checkpoint → Resume
- Diff Preview → pending_action → Confirm/Cancel → Version
- Undo/Rollback → HITL → 原子恢复 → 新版本
- Batch 20 项 → 18 成功、2 失败 → 继续执行 → 整批确认
- Trace → 参数/结果摘要 → 敏感信息脱敏
- Session Context → 唯一学生代词承接 / 重名再次确认

完整测试最终结果：

```text
190 passed in 7.60s
```

## 5. Agent Eval

固定任务保存在 `evals/agent_cases.json`。自动部分仅检查：

- Tool 名称与结构化参数
- Tool 调用顺序
- 最终状态
- Evidence 模式
- 写操作是否触发确认
- 禁止事实短语
- 确认前文件是否不变

自然语言可读性、语气和综合评价质量不做固定字符串匹配，统一进入人工复核。

离线 Agent 合约评估结果：

```text
4 passed in 0.96s
Tool 选择正确率：100%（6/6 固定合约任务）
平均 Tool Call 数：2.67
平均任务耗时：27.10 ms
无依据事实输出数：0
manual_review：6
```

这里的 Tool 选择率验证 Agent Runtime 对固定 Tool 请求的执行、排序和安全约束，不代表未接入真实模型时的线上模型选择准确率。

## 6. 指标

| 指标 | 结果 | 自动评估范围 |
|---|---:|---|
| Tool 选择正确率 | 100%（6/6） | A001–A006 固定离线 Agent 合约 |
| 字段语义识别正确率 | 100%（18/18） | 高置信映射与低置信拒绝 |
| 结构化查询准确率 | 100%（5/5） | U04–U08 |
| 学生关联准确率 | 100%（3/3） | D01、D02、D05 |
| RAG 检索有效性 | 100%（5/5） | R01–R05 |
| Evidence 引用正确率 | 100%（6/6） | E01–E06 |
| 冲突识别率 | 100%（3/3） | D01–D03 |
| Planning 任务成功率 | 100%（6/6） | P01–P06 |
| 写操作审批触发率 | 100%（5/5） | A006、W02、W04–W06 |
| Batch 场景成功率 | 100%（5/5） | B01–B05；包含部分失败继续执行 |
| 无依据事实输出数 | 0 | 固定禁止事实断言检查 |
| 平均 Tool Call 数 | 2.67 | A001–A006 |
| 平均任务耗时 | 27.10 ms | 本机离线 Replay Client，不含网络 LLM 延迟 |

指标分子、分母和来源固定保存在 `evals/metric_definitions.json`，避免只在报告中给出不可追溯的百分比。

## 7. Failed Cases

自动测试失败数：0。  
V1 Regression 失败数：0。  
V2 Evaluation 失败数：0。

没有删除失败测试、降低断言强度或硬编码业务答案。

## 8. Known Limitations

1. 真实 LLM 的自然语言 Tool 选择稳定性需要配置实际模型后另行运行；当前 Evaluation 不读取 `.env`，也不调用外部 API。
2. 6 个自然语言质量项目标记为 `manual_review`，包括综合评价语气、比较表达和重名提示可读性。
3. 平均任务耗时来自本地 Python Tool 和 Replay Client，不包含公网延迟、模型排队时间和 Token 生成时间。
4. 当前只支持普通文本 PDF，扫描 PDF/OCR 仍明确不支持。
5. 当前验证环境为 Python 3.10.20；目标部署环境 Python 3.11 仍应在正式部署流水线中再执行一次完整回归。

## 9. V2 验收结论

V2 自动化验收通过：

- V1：66/66 通过
- V2：124/124 通过
- 总计：190/190 通过
- 自动测试失败：0
- 人工复核项：6

结论：在当前固定虚拟数据、离线 Tool/Agent 合约和既定测试范围内，V2 后端、前端安全链路及 Regression 均满足验收要求。自然语言质量和真实模型稳定性保留为明确的人工/上线前复核项，不计入自动化 100% 结论。
