# Student Document Agent V4 Development Log

## 1. V4 项目背景

Student Document Agent V4 在 V1–V3 已稳定交付的代码和测试体系上增量开发。
V4 将围绕视觉文档处理、视觉理解、Evidence 3.0、通用文档 Agent、领域路由、
Agentic Retrieval、视觉安全与可观察性继续演进，同时保持 Excel、Word、PDF、
RAG 2.0、Evidence 2.0、Workflow Runtime、Async Task、Agent Safety、
Diff / Version / Rollback、Trace 以及 V1–V3 Evaluation 的兼容与回归安全。

V4.0 只建立工程开发基线，不实现 V4 新业务能力，不修改 V1–V3 核心业务代码。

## 2. V3 最终稳定基线

| 项目 | 值 |
| --- | --- |
| Tag | `v3-final-stable` |
| Commit | `16bee95fb699c76e5200467d147161e3c81ec262` |
| V4 开发分支 | `feature/v4-development` |
| V4.0 核验结果 | HEAD、tag 解引用结果及 merge-base 均为上述 commit |
| 初始工作区 | 干净，无未知残留修改 |

## 3. V4 开发原则

- 基于 V1–V3 稳定实现增量开发，优先复用现有模块和接口。
- 每一阶段只实现该阶段明确范围，不提前建设后续阶段。
- 不进行需求范围外的大规模重构。
- 所有新增能力必须有对应自动化测试，所有原有测试必须继续通过。
- 每阶段完成后执行完整 `pytest -q`；项目既有测试环境为 Conda `tuli_env`，
  因此实际命令记录为 `conda run -n tuli_env pytest -q`。
- 不删除、跳过或弱化既有测试，不编造测试数量、性能或质量指标。
- 阶段结束时更新本日志和 `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`。
- 开发完成后由维护者人工执行 commit 和 tag。

## 4. V4 开发阶段规划

| Stage | 主题 | 当前状态 | 阶段边界 |
| --- | --- | --- | --- |
| V4.0 | Baseline | VERIFIED | 核验 V3 基线，建立 V4 日志、覆盖矩阵和测试基线；不开发新功能 |
| V4.1 | Visual Document Router | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.2 | Visual OCR and Image PDF | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.3 | Handwriting Recognition | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.4 | Visual Understanding and KIE | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.5 | Visual Table | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.6 | Evidence 3.0 | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.7 | General Document Agent Core | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.8 | Domain Router | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.9 | Agentic Retrieval | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.10 | Visual Safety and Trace | NOT_STARTED | 需求和验收标准待对应阶段确认 |
| V4.11 | Evaluation and Final Acceptance | NOT_STARTED | 汇总 V4 固定评估、完整回归与最终验收 |

状态统一使用：`NOT_STARTED`、`PARTIAL`、`IMPLEMENTED`、`VERIFIED`、`BLOCKED`。

## 5. 当前测试基线

### 5.1 V3 最终接受基线

V3 最终稳定文档记录的完整测试基线为 `313 passed`。该数字仅作为历史基线，
V4 阶段结果以下述实际执行为准。

### 5.2 V4.0 修改前实测

执行日期：2026-08-28

直接执行 `pytest -q` 时，当前基础 shell 未安装或未暴露 `pytest`，命令返回
`pytest: command not found`，未启动测试收集。随后按项目既有环境执行：

```bash
conda run -n tuli_env pytest -q
```

真实结果：

```text
313 passed in 18.10s
```

结论：修改前完整测试基线通过，与 V3 最终接受基线一致。

## 6. V4 阶段开发记录

### V4.0 Baseline

- 本阶段目标：核验 V3 稳定基线，建立 V4 开发日志、P0 需求覆盖矩阵和完整测试基线。
- 实际修改文件：
  - `docs/V4_DEVELOPMENT_LOG.md`
  - `docs/V4_REQUIREMENT_COVERAGE_AUDIT.md`
- 新增/修改接口：无。
- 新增测试：无；本阶段没有新增业务能力，仅执行既有完整测试集。
- 测试结果：修改前 `313 passed in 18.10s`；修改后 `313 passed in 18.31s`，均为 PASS。
- 未完成项：V4.1–V4.11 均未开始；各阶段细化需求和验收标准待后续阶段输入。
- 已知限制：仓库中未发现独立 V4 需求原文，本阶段矩阵仅依据已给出的阶段名称建立
  顶层 P0 条目，不能替代后续正式需求分解。

### 每阶段开发记录模板

```markdown
### V4.x <Stage Name>

- 本阶段目标：
- 基线 commit：
- 实际修改文件：
- 新增/修改接口：
- 新增测试：
- 测试命令：`conda run -n tuli_env pytest -q`
- 测试结果：
- Requirement IDs：
- 未完成项：
- 已知限制：
- 推荐 commit message：
- 推荐 tag：
```

## 7. 回归测试记录

| 日期 | Stage | Commit/工作树 | 命令 | 结果 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-28 | V4.0 修改前 | `16bee95fb699c76e5200467d147161e3c81ec262` | `conda run -n tuli_env pytest -q` | `313 passed in 18.10s` | PASS；V3 稳定基线复现 |
| 2026-08-28 | V4.0 修改后 | 未提交工作树 | `conda run -n tuli_env pytest -q` | `313 passed in 18.31s` | PASS；仅新增文档，无业务回归 |

后续阶段必须追加实际执行记录，不得以历史结果替代当前回归。

## 8. V4 最终验收记录

| 项目 | 结果 |
| --- | --- |
| 最终版本 | 待 V4.11 登记 |
| 最终 commit | 待 V4.11 登记 |
| 最终 tag | 待 V4.11 登记 |
| P0 Requirements Coverage | 待 V4.11 审计 |
| 完整 pytest | 待 V4.11 实测 |
| V1–V3 Regression | 待 V4.11 实测 |
| V4 Evaluation | 待 V4.11 实测 |
| 已知限制 | 待 V4.11 汇总 |
| 最终结论 | 待 V4.11 验收 |
