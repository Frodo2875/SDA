# V2 最终 Demo B 验收报告

## 验收结论

**结论：核心数据、证据、HITL 和版本安全链路通过，但组合 Task 的 Step/Trace 完整性未通过，因此 Demo B 暂不能判定为完整验收通过。**

本次仅增加隔离、可重复的 Evaluation 测试，未修改任何业务模块。测试使用 `tuli_env`，不读取 `.env`，不访问真实 LLM API。固定学生 Excel 和 Word 被复制到 pytest 临时目录；数据库、PDF 索引、版本快照和全部写入均位于临时目录，不影响正式 `data/app.db` 与 `data/综合评价.docx`。

由于固定数据中存在两名“张三”（S001、S004），原始请求首先正确返回重名澄清并停止。随后在同一会话明确选择 S001，并重新提交完整任务，系统未随机选择学生。

## 主链路实测

| # | 验收项 | 结果 | 实测说明 |
|---|---|---|---|
| 1 | 创建 Task | 通过 | 原始重名请求和明确 S001 后的组合请求均创建持久化 Task。 |
| 2 | 创建合理 Step | **部分通过** | 创建了身份、成绩、科研、规则检索、Python 判断、解释 6 个 Step；没有为同一组合 Task 创建 Diff、确认、写入 Step。 |
| 3 | 确认张三身份 | 通过 | 首次返回 S001/S004 候选并停止；明确 S001 后继续。 |
| 4 | 查询成绩 | 通过 | `get_student_scores` 与 `query_table` 均读取实际 Excel；S001 平均分 90.67、专业排名 1。 |
| 5 | 查询科研 | 通过 | `get_student_research` 与 `query_table` 读取实际 Excel；论文 1、专利 0、竞赛 2。 |
| 6 | 检索奖学金办法 | 通过 | 实际调用 `retrieve_document`。 |
| 7 | PDF page/chunk Evidence | 通过 | Evidence 含 `page_no=2` 和实际 `chunk_id`。 |
| 8 | Python 执行条件判断 | 通过 | `evaluate_scholarship_eligibility` 使用明确规则：平均分 ≥ 90、排名 ≤ 3、论文数 ≥ 1；结论为 `eligible`。 |
| 9 | LLM 生成带依据解释 | **manual_review** | 离线 Replay 验证了 Tool Calling 回路；当前安全归一化会以 Python Tool 的证据化答案覆盖模型草稿。真实模型语言质量未读取 API Key 验证。 |
| 10 | Evidence 含实际 Excel + PDF | 通过 | 仅包含实际用于结论的 `学生成绩.xlsx`、`科研成果.xlsx`、`奖学金评审办法.pdf`。 |
| 11 | 生成综合评价 | 通过 | 最终证据化内容被冻结到 pending_action；模型虚构草稿未进入写入内容。 |
| 12 | preview Diff | 通过 | 返回 before/after、append、影响范围；预览不修改文件。 |
| 13 | Task waiting_confirmation | 通过 | Task 状态实际为 `waiting_confirmation`。 |
| 14 | 确认前 Word 未修改 | 通过 | 逐字节比较相同，且版本记录为空。 |
| 15 | 用户确认 | 通过 | 仅 `confirm_action(action_id)` 后执行冻结内容。 |
| 16 | 不重复前置查询 | 通过 | confirm 前后 Tool Call 数不变，没有重新查学生、Excel 或 PDF。 |
| 17 | 创建 Version Snapshot | 通过 | 首次确认创建 snapshot 版本和 append 新版本。 |
| 18 | 原子写入 | 通过 | 写入成功且 Word 可重新打开；失败补偿由既有 W07 回归覆盖。 |
| 19 | 返回新 version_id | 通过 | `write_result.data.new_version.version_id` 存在，并与版本历史最新记录一致。 |
| 20 | Trace 保存真实执行步骤 | **部分通过** | 记录了 5 个已规划 Tool 及耗时/Retry；未记录未匹配 Step 的 V1 查询，也未记录组合请求的 Diff、confirm、write。 |
| 21 | 执行 Undo | 通过 | 创建独立 Undo pending_action。 |
| 22 | Undo 经过确认 | 通过 | confirm 前文件保持写入态，确认后执行。 |
| 23 | Undo 成功恢复 | 通过 | 文件逐字节恢复为初始内容，新增 undo 版本而不改历史。 |
| 24 | 测试 Rollback | 通过 | 回滚到先前 append 版本成功。 |
| 25 | Rollback 前确认 | 通过 | preview/pending 阶段文件不变，确认后才恢复。 |
| 26 | 完整版本历史 | 通过 | 最终保留 4 个唯一版本：snapshot、append、undo、rollback；历史未覆盖或删除。 |

## Trace 与 Retry

主链路人为注入一次 `retrieve_document` 临时 IO 失败。系统在受控上限内第 1 次重试成功，Tool Call 与 Trace 的 `retry_count` 均为 1，没有无限重试。已保存的主 Task Trace 顺序为：

1. `search_student`
2. `query_table`（成绩）
3. `query_table`（科研）
4. `retrieve_document`
5. `evaluate_scholarship_eligibility`

这也直接暴露了当前 Trace 不完整的问题：组合 Planner 未声明的 `get_student_info`、`get_student_scores`、`get_student_research`、`preview_word_diff` 以及确认写入不会关联到该 Task Trace。

## 异常场景

| 场景 | 结果 | 自动验证 |
|---|---|---|
| RAG 无规则 | 通过 | 返回证据不足，不生成规则或确定结论。 |
| 数据冲突 | 通过 | 同学号不同姓名返回 conflict，不自动选真值。 |
| Tool 临时失败 | 通过 | 首次失败、一次重试成功，Retry 被记录。 |
| 用户取消写入 | 通过 | Word 字节不变，不产生该取消内容。 |
| 重复 confirm | 通过 | 返回 `ACTION_NOT_PENDING`，不重复写入、不新增版本。 |
| 缺失科研/规则 | 通过 | 返回 `insufficient_evidence`，不输出符合或不符合。 |
| Rollback 前取消 | 通过 | 文件与版本历史均不变化。 |

## 发现的验收阻塞点

组合消息同时满足“奖学金任务”和“Word 写入任务”时，`create_plan()` 优先返回奖学金 6-Step Plan。后续 Agent 确实创建 Diff 与 pending_action，但 `record_tool_execution()` 只能关联已有 Step，因此：

1. 同一 Task 缺少 `preview_word_diff`、confirmation、side_effect Step；
2. Task 虽能进入 `waiting_confirmation`，但 `current_step` 为空；
3. confirm 后 Task 会成功，但写入动作没有对应 Step Trace；
4. 部分为了 V1 写入安全而额外执行的查询也不会进入 Trace。

这是可观察性和可恢复计划完整性的真实缺口，不影响当前文件写入幂等性与版本安全，但不满足 Demo B“必须依次体现全部 Task/Step/Trace”的完整验收条件。根据本轮“禁止增加新功能”的要求，本次没有修改 Planner 或 Runtime。

## 测试命令

```bash
conda run -n tuli_env pytest -q tests/evals/test_demo_b.py
conda run -n tuli_env pytest -q \
  tests/test_document_rag.py::test_r05_missing_rule_is_not_fabricated_and_arguments_are_bounded \
  tests/test_data_quality_tools.py::test_d01_same_student_id_different_name_is_conflict \
  tests/test_runtime.py::test_p03_transient_failure_retries_once_then_records_success \
  tests/test_confirmation.py::test_cancel_keeps_file_unchanged \
  tests/test_confirmation.py::test_confirm_writes_frozen_content_once_and_repeat_is_idempotent \
  tests/test_hybrid_evidence.py::test_e02_missing_research_attempt_returns_insufficient_not_definite \
  tests/test_hybrid_evidence.py::test_e03_missing_or_ambiguous_rules_never_produce_a_definite_result \
  tests/test_file_versioning.py::test_w06_rollback_cancel_then_confirm_is_safe
conda run -n tuli_env pytest -q
```

Demo B 专项测试既验证可用链路，也把当前组合 Plan 边界固化为可观察的 Evaluation 事实；它没有把该边界伪装成完整通过。
