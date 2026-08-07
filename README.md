# APILedger

API 账单数据管理与可视化工具。

## 使用说明

1. **放入账单**：将 API 平台的账单文件（`.csv` / `.xlsx`）放入 `data/input/`
2. **导入数据**：
   - 命令行：`python cli_import.py`（仅导入，不打开界面）
   - 图形界面：`python main.py`，点导入按钮
3. **自动归档**：导入成功的文件自动移至 `data/archive/`
4. **导出报告**：图形界面点「📊 导出报告」，生成自包含的 HTML 报告（汇总卡片 + 交互图表 + 可筛选明细表），用浏览器打开。首次导出会自动下载 echarts 库到本地，之后离线可用

账单必须能匹配 `presets/` 下某个平台预设，否则导入会被拒绝并提示编写预设（参照 `presets/_template.py`）。

## 输入文件格式

### 平台导入预设

程序会优先匹配 `presets/` 下的**平台预设**。每个平台一个 `.py` 文件，定义该平台的相关规则：

- 列名映射（表头 → 标准字段）
- 默认值（如无 platform 列时自动填充）
- type 翻译（统一为：输入 / 输出 / 缓存输入）
- 模型名映射（平台原始名 → 统一名称）
- 可选 `parse_row` 函数（处理从单个单元格解析多字段、跳过特殊记录等复杂逻辑）

匹配规则：先按文件名关键词，再按表头关键词，命中即使用该预设。**匹配不到任何预设时拒绝导入**，提示参照 `presets/_template.py` 编写预设。

新增平台只需在 `presets/` 放一个 `.py` 文件，无需改代码。

### 标准字段

所有平台账单最终统一映射到以下标准字段（在预设的 `COLUMN_MAPPING` 中声明）：

| 标准字段 | 类型 | 说明 | 数据格式 / 可选范围 |
| :---: | :---: | :---: | ----- |
| `bill_start` | TEXT | 账单开始时间 | `yyyy-MM-dd` 或 `yyyy-MM-dd HH:mm:ss` |
| `bill_end` | TEXT | 账单截止时间 | `yyyy-MM-dd` 或 `yyyy-MM-dd HH:mm:ss`；缺省时自动取 `bill_start` |
| `platform` | TEXT | 平台名称 | 任意文本，如 `DeepSeek`、`Paratera` |
| `project` | TEXT | 项目名 | 任意文本，如 `sakura`、`vscode` |
| `model` | TEXT | 模型名称 | 任意文本，如 `DeepSeek-V4-Flash`、`Kimi-K2.5` |
| `type` | TEXT | 计费类型 | 仅限：`输入`、`输出`、`缓存输入` |
| `tokens` | INTEGER | Tokens 数量 | 非负整数 |
| `call_volume` | INTEGER | 调用量 | 非负整数 |
| `cost` | REAL | 金额 | 非负浮点数，单位：元 |
| `unit_price` | REAL | 单价 | 非负浮点数，单位：元/百万tokens |

### 未匹配的列

未在 `COLUMN_MAPPING` 中映射的列会被忽略，不写入数据库。

### 时间粒度兼容

- **按小时计费的账单**：`bill_start` 和 `bill_end` 各为精确时间戳（如 `2024-06-01 10:00:00` / `2024-06-01 11:00:00`）
- **按日计费的账单**：只需提供日期列（`bill_start`），`bill_end` 会自动填充为与 `bill_start` 相同，两条记录不会相互覆盖

---

## 目录结构

```text
APILedger/
├── core/                   # 核心逻辑层
│   ├── models.py           # 标准字段定义
│   ├── presets.py          # 平台预设引擎 (加载/匹配/应用 presets/*.py)
│   ├── report.py           # HTML 报告生成器 (ECharts 图表 + 前端筛选)
│   ├── db.py               # SQLite 数据库操作 (建表/UPSERT/聚合查询)
│   └── importer.py         # XLSX 文件扫描、列匹配、导入、归档
├── ui/                     # 可视化层 (CustomTkinter)
│   ├── app.py              # 主窗口布局 & Tab 管理 (含导出报告按钮)
│   ├── theme.py            # 主题颜色 & 样式常量
│   └── panels/
│       ├── filter_panel.py # 筛选面板 (日期/平台/项目/模型/类型/搜索)
│       ├── table_panel.py  # 数据表格 (排序/统计)
│       └── chart_panel.py  # 图表面板 (折线趋势/柱状对比/饼图分布)
├── presets/                # 平台导入预设 (每平台一个 .py 文件)
│   ├── deepseek.py
│   ├── paratera.py
│   └── _template.py        # 预设模板 (新建平台时复制参考)
├── data/                   # 用户数据目录
│   ├── input/              # 待导入的 XLSX / CSV 文件存放处
│   ├── archive/            # 已导入文件的归档目录
│   └── api_ledger.db       # SQLite 数据库文件
├── output/                 # HTML 报告输出 (含本地 echarts.min.js)
├── main.py                 # 程序入口
└── pixi.toml               # Pixi 环境配置
```

`input/`、`archive/`、`data/`、`output/` 目录均为运行时自动创建，不纳入版本控制。

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
| `type` | TEXT | 计费类型 (输入/输出/缓存输入) |
| `tokens` | INTEGER | Tokens 数量 |
| `call_volume` | INTEGER | 调用量 |
| `cost` | REAL | 金额 |
| `unit_price` | REAL | 单价 (元/百万tokens) |
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
