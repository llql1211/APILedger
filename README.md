# APILedger

API 账单数据管理与可视化工具（命令行导入 + 自包含 HTML 报告）。

## 使用说明

1. **放入账单**：将 API 平台的账单文件（`.csv` / `.xlsx`）放入 `data/input/`
2. **运行主程序**：`python main.py`
   - 自动扫描并导入 `input/` 中的所有账单文件
   - 遇到冲突数据时在终端逐条询问裁决（覆盖 / 跳过 / 中止）
   - 导入完成自动生成 `output/report.html` 并用浏览器打开
3. **自动归档**：导入成功的文件自动移至 `data/archive/`
4. **随时查看报告**：双击 `output/report.html` 即可（单文件自包含，可复制分享，离线可用；导入新数据后重新运行 `main.py` 即会覆盖更新）

### 命令行参数

| 参数 | 作用 |
| :--- | :--- |
| （无参数） | 导入全部文件；有冲突时逐条询问裁决 |
| `--force` | 有冲突时全部覆盖，不询问 |
| `--yes` | 有冲突时全部跳过，不询问（非交互，适合脚本） |
| `--dry-run` | 仅检测，不写入数据库也不归档文件 |
| `--no-report` | 只导入，不生成报告 |
| `--no-open` | 生成报告但不弹出浏览器 |

### 冲突裁决（交互模式）

判定规则（按 key = 日期/平台/项目/模型/类型）：

- 文件名时间区段**不重叠** → 互补数据，自动累加，无需确认
- 文件名时间区段**重叠**且数值不同 → 冲突，终端逐条展示「已有值 vs 本次值」，可选：
  - `y` 覆盖本条　`n` 跳过本条
  - `a` 覆盖本文件全部冲突　`s` 跳过本文件全部冲突
  - `q` 中止导入（本文件不写入不归档）

账单必须能匹配 `presets/` 下某个平台预设，否则导入会被拒绝并提示编写预设（参照 `presets/_template.py`）。

## HTML 报告功能

报告为**单文件自包含 HTML**：全部明细以 JSON 内嵌，echarts 内联（首次自动下载到本地），无需服务器、离线可用。

- **筛选栏**（日期范围 / 平台 / 项目 / 模型 / 类型 / 关键词）——筛选同时作用于汇总卡片、全部图表和明细表
- **汇总卡片**：总费用 / 总 Tokens / 记录数 / 模型数 / 平台数（随筛选联动）
- **图表**（全部由前端按当前筛选实时计算）：
  - 使用趋势折线图：费用 + Tokens 双轴，日 / 周 / 月粒度切换，含均值线
  - 平台占比饼图：费用 / Tokens 指标切换
  - 模型费用对比柱状图 (Top-15)
  - 平台内各模型 Token 分布饼图（跟随平台筛选；指标可切换）
  - 模型内 Token 类型分布饼图（跟随模型筛选；指标可切换）
- **明细表**：默认按 平台 → 项目 → 日期 排序，支持表头点击排序、翻页；列含 时间 / 平台 / 项目 / 模型 / 类型 / Tokens / 金额 / 单价 / 来源文件

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
| `bill_end` | TEXT | 账单截止时间 | 缺省时自动取 `bill_start` |
| `platform` | TEXT | 平台名称 | 任意文本，如 `DeepSeek`、`Paratera` |
| `project` | TEXT | 项目名 | 任意文本，如 `sakura`、`vscode` |
| `model` | TEXT | 模型名称 | 任意文本，如 `DeepSeek-V4-Flash`、`Kimi-K2.5` |
| `type` | TEXT | 计费类型 | 仅限：`输入`、`输出`、`缓存输入` |
| `tokens` | INTEGER | Tokens 数量 | 非负整数 |
| `cost` | REAL | 金额 | 非负浮点数，单位：元 |
| `unit_price` | REAL | 单价 | 非负浮点数，单位：元/百万tokens |

### 未匹配的列

未在 `COLUMN_MAPPING` 中映射的列会被忽略，不写入数据库。

### 时间粒度兼容

按小时计费的账单会自动截断为按日聚合（归入 `bill_start` 那天）；按日计费的账单只需提供日期列。

---

## 目录结构

```text
APILedger/
├── core/                   # 核心逻辑层
│   ├── models.py           # 标准字段定义
│   ├── presets.py          # 平台预设引擎 (加载/匹配/应用 presets/*.py)
│   ├── report.py           # HTML 报告生成器 (数据提取 + 模板渲染)
│   ├── report_template.html# 报告 HTML 模板 (前端筛选/图表/表格)
│   ├── config.py           # 配置
│   ├── db.py               # SQLite 数据库操作 (建表/UPSERT/聚合查询)
│   └── importer.py         # XLSX / CSV 文件扫描、列匹配、导入、归档
├── presets/                # 平台导入预设 (每平台一个 .py 文件)
│   ├── deepseek.py
│   ├── paratera.py
│   └── _template.py        # 预设模板 (新建平台时复制参考)
├── test/                   # pytest 测试套件
├── data/                   # 用户数据目录
│   ├── input/              # 待导入的 XLSX / CSV 文件存放处
│   ├── archive/            # 已导入文件的归档目录
│   └── api_ledger.db       # SQLite 数据库文件
├── output/                 # 报告输出 (report.html + 本地 echarts.min.js)
├── main.py                 # 程序入口 (导入 → 冲突裁决 → 生成报告 → 弹出浏览器)
└── pixi.toml               # Pixi 环境配置
```

`input/`、`archive/`、`data/`、`output/` 目录均为运行时自动创建，不纳入版本控制。

---

## 数据库结构

### 表: `api_records` (聚合表, 每 key 一行)

| 列名 | 类型 | 说明 |
| :---: | :---: | ----- |
| `id` | INTEGER PK | 自增主键 |
| `date` | TEXT | 日期 (YYYY-MM-DD) |
| `platform` | TEXT | 平台名称 |
| `project` | TEXT | 项目名 |
| `model` | TEXT | 模型名称 |
| `type` | TEXT | 计费类型 (输入/输出/缓存输入) |
| `tokens` | INTEGER | Tokens 数量 |
| `cost` | REAL | 金额 |
| `unit_price` | REAL | 单价 (元/百万tokens) |
| `source_file` | TEXT | 来源文件 (多个以逗号分隔) |

唯一约束：`(date, platform, project, model, type)`。

### 表: `record_sources` (贡献明细表, 每 key × source_file 一行)

记录每个账单文件对该 key 的贡献（tokens / cost / unit_price）。聚合行 = 各贡献之和。
重复导入同一文件时替换其贡献；互补文件（时间区段不重叠）自动累加。

### 索引

- `date`、`platform`、`project`、`model`、`type` — 加速筛选与聚合

---

## 依赖

| 包 | 用途 |
| :---: | ----- |
| `pandas` | XLSX / CSV 文件读取 |
| `openpyxl` | XLSX 引擎 (pandas 依赖) |
