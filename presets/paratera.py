"""Paratera 账单导入预设。

真实账单列: 资源名称, 资源ID, 计费方式, 资源类型, 模型, 配置描述, 站点,
交易类型, 交易时间, 账单开始时间, 账单结束时间, 服务费(元), 费用(元), 结算状态

关键点:
  - type 和 tokens 合并在 "配置描述" 列, 需单格解析, 兼容两种格式:
      旧版 (2026-07 及以前): "输入:52,110tokens" / "输出:..." / "缓存输入:..."
      新版 (2026-08 起):    "文本输入:61,444tokens" / "文本输出:..." / "缓存输入:..."
  - 无 platform 列, 默认填 Paratera
  - 金额在 "费用(元)" 列
"""

import re

# 文件名关键词
MATCH_FILENAME = ["paratera"]

# 表头关键词
MATCH_HEADERS = ["配置描述", "费用(元)"]

# 列名 → 标准字段关键词映射
COLUMN_MAPPING = {
    "bill_start": ["账单开始时间"],
    "project":    ["资源名称"],
    "model":      ["模型"],
    "cost":       ["费用(元)"],
}

# 固定值填充
DEFAULTS = {"platform": "Paratera"}

# type 翻译: 配置描述中的原始值 → 标准中文 (标准 type 仅三种: 输入/输出/缓存输入)
# 2026-08 账单改版, token 类型细分, 按单价归并到等价的标准类型:
#   文本输入/图片输入 ¥3 = 输入, 文本输出/图片输出/缓存输出 ¥9 = 输出
# 视频输入/输出按 "个" 计费且免费 (¥0), DESC_RE 不匹配 tokens 单位, parse_row 自然跳过
# 缓存存储 (免费, cost=0) 在 parse_row 中跳过, 不导入
TYPE_MAP = {
    "输入":     "输入",
    "输出":     "输出",
    "文本输入": "输入",
    "文本输出": "输出",
    "图片输入": "输入",
    "图片输出": "输出",
    "缓存输出": "输出",
    "缓存输入": "缓存输入",
}

# 需要跳过的 type 原始值
SKIP_TYPES = {"缓存存储"}

# 模型名映射
MODEL_MAP = {
    "DeepSeek-V4-Flash": "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro":   "DeepSeek-V4-Pro",
    "Qwen3.5-122B-A10B": "Qwen3.5-122B-A10B",
    "Qwen3.5-35B-A3B":   "Qwen3.5-35B-A3B",
    "Qwen3.6-Plus":      "Qwen3.6-Plus",
    "Kimi-K2.5":         "Kimi-K2.5",
    "GLM-5":             "GLM-5"
}

# 解析 "配置描述" 列: "输入:52,110tokens" → ("输入", 52110)
DESC_RE = re.compile(r'^(\S+)\s*:\s*([\d,]+)\s*tokens?\s*$')


def parse_row(raw_row: dict, mapped_row: dict) -> dict | None:
    """
    从 "配置描述" 列解析 type 和 tokens。

    raw_row:     CSV 原始行 {原始列名: 值}
    mapped_row:  COLUMN_MAPPING 已提取的字段
    返回合并后的 row。配置描述解析失败则返回 None 跳过该行。
    """
    # 跳过未结算账单 (结算中: 金额为 0; 待 "已结算" 账单出来后再导入, 避免污染统计)
    if str(raw_row.get("结算状态", "")).strip() == "结算中":
        return None

    desc = str(raw_row.get("配置描述", "")).strip()
    m = DESC_RE.match(desc)
    if not m:
        return None

    type_raw = m.group(1)
    # 跳过免费类型 (如缓存存储)
    if type_raw in SKIP_TYPES:
        return None

    mapped_row["type"] = TYPE_MAP.get(type_raw, type_raw)
    mapped_row["tokens"] = int(m.group(2).replace(",", ""))
    return mapped_row
