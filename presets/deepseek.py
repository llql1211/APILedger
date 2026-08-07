"""DeepSeek 官方账单导入预设。

真实账单列: user_id, utc_date, model, api_key_name, api_key, type, price, amount

关键点:
  - utc_date 是 yyyymmdd 格式 (如 20260621), 需转 ISO 日期
  - price 是每 token 单价 (如 0.00000002)，非金额
  - amount 是 token 数
  - type 是英文 (input_cache_hit_tokens 等)，需翻译
  - 无 cost 列，需计算: cost = price * amount
  - 无 platform 列，默认填 DeepSeek
"""

import re

# 文件名关键词
MATCH_FILENAME = ["deepseek"]

# 表头关键词
MATCH_HEADERS = ["utc_date", "price"]

# 列名 → 标准字段关键词映射
COLUMN_MAPPING = {
    "bill_start": ["utc_date"],
    "model":      ["model"],
    "project":    ["api_key_name"],
    "type":       ["type"],
    "tokens":     ["amount"],
    "unit_price": ["price"],
}

# 固定值填充
DEFAULTS = {"platform": "DeepSeek"}

# type 翻译: 原始值 → 标准中文
# 标准 type 仅三种: 输入 / 输出 / 缓存输入
# request_count (调用量) 记录在 parse_row 中跳过，不导入
TYPE_MAP = {
    "input_cache_hit_tokens":  "缓存输入",
    "input_cache_miss_tokens": "输入",
    "output_tokens":           "输出",
}

# 计算字段 (按声明顺序执行)
COMPUTED = {
    # price 是每 token 单价，先算总金额
    "cost": "unit_price * tokens",
    # 再把 unit_price 换算成 元/百万tokens
    "unit_price": "unit_price * 1000000",
}

# 模型名映射: 平台原始名 → 统一名称
MODEL_MAP = {
    "deepseek-chat":     "DeepSeek-V3",
    "deepseek-reasoner": "DeepSeek-R1",
    "deepseek-v4-flash": "DeepSeek-V4-Flash",
    "deepseek-v4-pro":   "DeepSeek-V4-Pro",
}

# utc_date 格式: yyyymmdd (如 20260621) → ISO yyyy-mm-dd
DATE_RE = re.compile(r'^(\d{4})(\d{2})(\d{2})$')


def parse_row(raw_row: dict, mapped_row: dict) -> dict | None:
    """
    1. 跳过 request_count (调用量) 记录: 无单价, 不入库
    2. 将 utc_date (yyyymmdd) 转换为 ISO 日期格式 (yyyy-mm-dd)

    raw_row:     CSV 原始行 {原始列名: 值}
    mapped_row:  COLUMN_MAPPING 已提取的字段
    返回合并后的 row, 或 None 跳过该行。
    """
    # 跳过调用量记录 (无单价, 无 tokens)
    if mapped_row.get("type") == "request_count":
        return None

    # 日期格式转换
    d = mapped_row.get("bill_start", "")
    m = DATE_RE.match(str(d).strip())
    if m:
        mapped_row["bill_start"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return mapped_row
