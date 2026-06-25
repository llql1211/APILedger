"""
APILedger - 数据模型 & 列名映射定义

列名映射规则: 读取 xlsx 时, 用关键词模糊匹配表头,
将各列归类到标准字段。未匹配的列存入 extra JSON。
"""

import json
from typing import Any, Dict

# ── 标准字段列表 ──────────────────────────────────
STANDARD_FIELDS = [
    "bill_start",
    "bill_end",
    "platform",
    "project",
    "model",
    "type",
    "tokens",
    "call_volume",
    "cost",
]

# ── 列名映射表 (关键词 → 标准字段) ────────────────
# key: 标准字段名
# value: 需要匹配的关键词列表 (不区分大小写, 子串匹配)
COLUMN_MAPPING: Dict[str, list[str]] = {
    "bill_start": [
        "账单开始时间", "开始时间", "start_time", "start",
        "调用时间", "时间", "日期", "date", "账单日期",
        "utc_date",      # DeepSeek 账单
        "账单结束时间",   # 兼容只填 bill_start 却写在此列的情况
    ],
    "bill_end": [
        "账单截止时间", "截止时间", "结束时间", "end_time", "end",
        "账单结束时间",
    ],
    "platform": [
        "平台", "platform", "provider", "供应商",
    ],
    "project": [
        "项目", "项目名", "project", "应用", "app", "application",
        "资源名称", "resource",
        "api_key_name",   # DeepSeek 账单
    ],
    "model": [
        "模型", "模型名称", "model", "name", "名称",
        "API", "api", "接口", "service",
    ],
    "type": [
        "类型", "type", "category", "类别", "计费类型",
    ],
    "tokens": [
        "tokens", "token", "令牌",
        "amount",        # DeepSeek 账单的 amount（随后会被 request_count 分流覆盖）
    ],
    "call_volume": [
        "调用量", "调用次数", "调用", "calls", "count",
        "requests", "请求次数", "request_count",
    ],
    "cost": [
        "金额", "费用", "cost", "price", "价格",
        "消费", "spend", "total_cost", "总费用",
        "计费金额", "费用(元)",
    ],
}


def match_column(headers: list[str]) -> Dict[str, str]:
    """
    给定 xlsx 的表头列表, 返回 标准字段 → 原始列名 的映射。

    例如输入 ['日期','模型','tokens','费用','备注']
    返回 { 'bill_start': '日期', 'model': '模型', 'tokens': 'tokens', 'cost': '费用' }

    未匹配的列不会出现在返回中, 后续会被归入 extra。

    匹配策略: 建立 (关键词, 标准字段) 的扁平列表, 按关键词长度降序排列,
    长关键词优先匹配, 避免 "时间" 误匹配 "账单截止时间" 这类问题。
    """
    # 构建扁平列表: (关键词原文, 标准字段), 按关键词长度降序
    flat: list[tuple[str, str]] = []
    for field, keywords in COLUMN_MAPPING.items():
        for kw in keywords:
            flat.append((kw, field))
    flat.sort(key=lambda x: len(x[0]), reverse=True)

    matched: Dict[str, str] = {}
    used_headers: set[str] = set()
    used_fields: set[str] = set()

    for kw, field in flat:
        if field in used_fields:
            continue  # 每字段只匹配一列
        kw_norm = kw.lower().replace(" ", "")
        for header in headers:
            if header in used_headers:
                continue
            hl = header.lower().replace(" ", "")
            if kw_norm in hl:
                matched[field] = header
                used_headers.add(header)
                used_fields.add(field)
                break

    return matched


def make_extra(row: Dict[str, Any], matched_fields: set[str]) -> str:
    """
    将未匹配的列序列化为 JSON 字符串, 存入 extra 字段。
    """
    extra = {k: v for k, v in row.items() if k not in matched_fields and k != "source_file"}
    return json.dumps(extra, ensure_ascii=False, default=str)
