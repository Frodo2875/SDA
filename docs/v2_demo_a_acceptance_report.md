# V2 最终 Demo A 验收报告

验收日期：2026-08-18  
对象：`2026学生综合信息登记表.xlsx`  
结论：通过  
业务代码修改：无

## 1. 验收方式

该文件在验证前不存在于项目目录。验收测试使用 openpyxl 在 pytest 临时目录生成一份虚拟陌生 Excel，然后通过现有 `save_uploaded_file` 上传服务执行完整流程。

陌生表结构：

- Sheet：`综合登记`
- 第 1 行：合并标题
- 第 2 行：空行
- 第 3 行：真实表头
- 字段：`学生学号 / 学生姓名 / 所属院系 / 移动电话 / 培养层次 / 行政班`
- 数据行：6

没有根据文件名增加判断，没有新增专用 Tool、专用字段映射或业务分支。测试文件和数据库均位于 pytest 临时目录，正式 `data/` 未被污染。

## 2. 上传与生命周期

实际观察状态：

```text
1. lifecycle_status=uploaded
   parse_status=pending
   queryable=false

2. lifecycle_status=processing
   parse_status=processing
   queryable=false

3. lifecycle_status=ready
   parse_status=parsed
   queryable=true
```

上传服务负责通用 Office 可打开性验证和生命周期解析；随后使用通用 `inspect_excel(file_id)` 完成 Schema Discovery。没有针对文件名进行适配。

## 3. Schema Discovery

识别结果：

| 项目 | 结果 |
|---|---:|
| Sheet 数 | 1 |
| Sheet 名 | 综合登记 |
| 表头行 | 3 |
| 字段数 | 6 |
| 数据行数 | 6 |

字段语义：

| 来源字段 | 标准语义 |
|---|---|
| 学生学号 | student_id |
| 学生姓名 | name |
| 所属院系 | college |
| 移动电话 | phone |
| 培养层次 | unknown |
| 行政班 | unknown |

`培养层次` 和 `行政班` 没有被强行映射，符合低置信字段保持 unknown 的安全原则。

## 4. 前端文件区域数据

前端使用的 `/api/files` 只读视图数据已经实际验证：

```json
{
  "sheet_count": 1,
  "field_count": 6,
  "row_count": 6,
  "sheets": [
    {
      "sheet_name": "综合登记",
      "field_count": 6,
      "row_count": 6
    }
  ]
}
```

因此现有文件面板可以显示 Sheet、字段和数据行数。此次为自动验收，没有进行浏览器截图级人工 UI 复核。

## 5. 查询张三学院和联系电话

自然语言任务：

```text
张三哪个学院、联系电话是多少？
```

Agent 合约调用：

```json
{
  "tool": "query_table",
  "sheet": "综合登记",
  "filters": [
    {"field": "学生姓名", "op": "=", "value": "张三"}
  ],
  "select": ["学生学号", "学生姓名", "所属院系", "移动电话"]
}
```

Python Tool 真实返回：

```json
{
  "学生学号": "S001",
  "学生姓名": "张三",
  "所属院系": "计算机学院",
  "移动电话": "13812345678"
}
```

最终回答包含：

```text
张三所在学院为计算机学院，联系电话为13812345678。
```

## 6. Evidence

返回 4 条实际结构化 Evidence，均来自：

- 文件：`2026学生综合信息登记表.xlsx`
- Sheet：`综合登记`
- 字段：`学生学号`
- 字段：`学生姓名`
- 字段：`所属院系`
- 字段：`移动电话`

Evidence 没有引用未调用文件或未使用字段。

## 7. 计算机学院人数统计

自然语言任务：

```text
计算机学院有多少人？
```

实际调用：`aggregate_table`。

```json
{
  "operation": "count",
  "filters": [
    {"field": "所属院系", "op": "=", "value": "计算机学院"}
  ]
}
```

Python 统计结果：

```text
3 人
```

统计没有交给 LLM 计算。

## 8. 联系电话为空

自然语言任务：

```text
找出联系电话为空的学生。
```

实际结构化过滤：

```json
{
  "field": "移动电话",
  "op": "is_null",
  "value": null
}
```

真实结果：

```text
李四、赵敏
```

没有把空字符串或未匹配解释为学生不存在。

## 9. 跨文件可靠关联

关联实体：`S001`。

关联优先级：`student_id`。上传表和固定成绩表均通过相同学号唯一匹配，未使用姓名猜测。

```json
{
  "student_id": "S001",
  "college": "计算机学院",
  "average_score": 90.67,
  "relation_method": {
    "uploaded": "student_id",
    "scores": "student_id"
  }
}
```

学院来自陌生上传 Excel，平均成绩来自固定 `学生成绩.xlsx`，关联结果没有数据冲突或歧义。

## 10. 测试结果

专项命令：

```bash
pytest -q -s tests/evals/test_demo_a.py
```

实际结果：

```text
1 passed in 0.55s
```

## 11. 验收结论与边界

Demo A 通过。现有通用能力可以在不修改业务代码的情况下完成陌生 Excel 的上传、生命周期管理、Schema Discovery、字段语义识别、精确查询、Python 聚合、空值过滤、Evidence 和基于 student_id 的跨文件关联。

本次自然语言链路使用离线 Replay Client 指定预期 Tool Calling，不读取 `.env`、不调用真实 LLM API。因此已验证 Agent Runtime、参数 Schema 和真实 Python Tool 结果，但真实线上模型的 Tool 选择稳定性仍属于模型接入后的人工 Demo 项。
