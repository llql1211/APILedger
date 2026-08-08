"""
APILedger - 数据模型

标准字段列表定义。
列名映射已迁移到平台预设 (presets/*.py)，不再在这里维护全局关键词表。
"""

# ── 标准字段列表 ──────────────────────────────────
STANDARD_FIELDS = [
    "bill_start",
    "platform",
    "project",
    "model",
    "type",
    "tokens",
    "cost",
    "unit_price",
]
