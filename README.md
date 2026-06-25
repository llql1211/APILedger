# APILedger

API 账单数据管理与可视化工具。

## 输入文件格式

### 列名自动匹配

程序对 `.xlsx` / `.csv` 表头进行**关键词模糊匹配**，无需严格的列名约定。匹配按关键词长度降序优先（长关键词优先），避免误匹配。

以下为各标准字段的可识别关键词：

| 标准字段 | 类型 | 说明 | 可匹配的列名关键词 |
| :---: | :---: | :---: | ----- |
| `bill_start` | TEXT | 账单开始时间 | 账单开始时间、开始时间、start_time、start、调用时间、时间、日期、date、账单日期 |
| `bill_end` | TEXT | 账单截止时间 | 账单截止时间、截止时间、结束时间、end_time、end |
| `platform` | TEXT | 平台名称 | 平台、platform、provider、供应商 |
| `project` | TEXT | 项目名 | 项目、项目名、project、应用、app、application |
| `model` | TEXT | 模型名称 | 模型、模型名称、model、name、名称、API、api、接口、service |
| `type` | TEXT | 计费类型 | 类型、type、category、类别、计费类型 |
| `tokens` | INTEGER | Tokens 数量 | tokens、token、令牌 |
| `call_volume` | INTEGER | 调用量 | 调用量、调用次数、调用、calls、count、requests、请求次数 |
| `cost` | REAL | 金额 | 金额、费用、cost、price、价格、消费、spend、amount、total_cost、总费用、计费金额 |

**匹配示例**：

| 表头 | 映射结果 | 说明 |
| ----- | ----- | :---: |
| `["日期", "API", "消费", "项目"]` | `bill_start`, `model`, `cost`, `project` | 最简场景 |
| `["账单开始时间", "账单截止时间", "平台", "项目名", "模型名称", "类型", "tokens", "调用量", "金额", "备注"]` | 全部标准字段逐一匹配 | 完整场景 |
| `["Date", "Model", "Tokens", "Cost", "Platform", "Notes"]` | 同上，英文列名 | 同样识别 |

### 未匹配的列

任何未在上述映射范围内的列，其全部数据会被序列化为 JSON 存入 `extra` 字段，数据不会丢失。

### 时间粒度兼容

- **按小时计费的账单**：`bill_start` 和 `bill_end` 各为精确时间戳（如 `2024-06-01 10:00:00` / `2024-06-01 11:00:00`）
- **按日计费的账单**：只需提供日期列（`bill_start`），`bill_end` 会自动填充为与 `bill_start` 相同，两条记录不会相互覆盖

---

## 目录结构

```text
APILedger/
├── core/                   # 核心逻辑层
│   ├── models.py           # 数据模型 & 列名映射规则
│   ├── db.py               # SQLite 数据库操作 (建表/UPSERT/聚合查询)
│   └── importer.py         # XLSX 文件扫描、列匹配、导入、归档
├── ui/                     # 可视化层 (CustomTkinter)
│   ├── app.py              # 主窗口布局 & Tab 管理
│   ├── theme.py            # 主题颜色 & 样式常量
│   └── panels/
│       ├── filter_panel.py # 筛选面板 (日期/平台/项目/模型/类型/搜索)
│       ├── table_panel.py  # 数据表格 (排序/统计)
│       └── chart_panel.py  # 图表面板 (折线趋势/柱状对比/饼图分布)
├── input/                  # [运行时] 待导入的 XLSX / CSV 文件存放处
├── archive/                # [运行时] 已导入文件的归档目录
├── data/                   # [运行时] SQLite 数据库文件 (api_ledger.db)
├── main.py                 # 程序入口
└── pixi.toml               # Pixi 环境配置
```

`input/`、`archive/`、`data/` 三个目录均为运行时自动创建，不纳入版本控制。

---

## 数据库结构

### 表: `api_records`

| 列名 | 类型 | 说明 |
| :---: | :---: | ----- |
| `id` | INTEGER PK | 自增主键 |
| `bill_start` | TEXT | 账单开始时间 (ISO-8601) |
| `bill_end` | TEXT | 账单截止时间 (ISO-8601) |
| `platform` | TEXT | 平台名称 |
| `project` | TEXT | 项目名 |
| `model` | TEXT | 模型名称 |
| `type` | TEXT | 计费类型 (输入/输出/缓存输入 等) |
| `tokens` | INTEGER | Tokens 数量 |
| `call_volume` | INTEGER | 调用量 |
| `cost` | REAL | 金额 |
| `extra` | TEXT | 未匹配列的原始数据 (JSON) |
| `source_file` | TEXT | 来源文件名 |
| `imported_at` | TEXT | 导入时间戳 |

### 去重机制

唯一约束为 `(bill_start, bill_end, platform, project, model, type)` 六字段组合。重复记录导入时会 UPSERT（覆盖更新 `tokens`、`call_volume`、`cost` 字段），不会产生重复行。

### 索引

- `(bill_start, bill_end)` — 加速时间范围查询
- `(platform)`, `(project)`, `(model)`, `(type)` — 加速筛选下拉

---

## 依赖

| 包 | 用途 |
| :---: | ----- |
| `customtkinter` | 桌面 UI 框架 |
| `matplotlib` | 图表绘制 |
| `pandas` | XLSX 文件读取 |
| `openpyxl` | XLSX 引擎 (pandas 依赖) |
