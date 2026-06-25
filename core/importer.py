"""
APILedger - XLSX / CSV 文件扫描、读取、列匹配、两阶段导入、归档
"""

import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from core.models import match_column, make_extra, STANDARD_FIELDS
from core.db import Database

# 目录常量
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INPUT_DIR = os.path.join(BASE_DIR, "input")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")


SUPPORTED_EXTENSIONS = (".xlsx", ".csv")


def scan_input_files() -> List[str]:
    """扫描 input/ 目录, 返回所有 .xlsx / .csv 文件路径 (按修改时间排序)"""
    if not os.path.isdir(INPUT_DIR):
        return []

    files = []
    for f in os.listdir(INPUT_DIR):
        lower = f.lower()
        if lower.endswith(SUPPORTED_EXTENSIONS) and not f.startswith("~$"):
            full = os.path.join(INPUT_DIR, f)
            if os.path.isfile(full):
                files.append(full)

    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def read_xlsx(filepath: str) -> List[Dict[str, Any]]:
    """用 pandas 读取 xlsx, 返回 list of dict"""
    df = pd.read_excel(filepath, dtype=str)
    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return df.to_dict(orient="records")


def read_csv(filepath: str) -> List[Dict[str, Any]]:
    """用 pandas 读取 csv, 自动探测编码 (utf-8 → gbk 兜底), 返回 list of dict"""
    for enc in ["utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"]:
        try:
            df = pd.read_csv(filepath, dtype=str, encoding=enc)
            df = df.fillna("")
            df.columns = [str(c).strip() for c in df.columns]
            return df.to_dict(orient="records")
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 最后兜底
    df = pd.read_csv(filepath, dtype=str, encoding="utf-8", errors="replace")
    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return df.to_dict(orient="records")


def parse_records_from_file(filepath: str) -> List[Dict[str, Any]]:
    """
    读取 xlsx 并解析为标准记录。
    不涉及数据库写入, 纯解析。
    """
    filename = os.path.basename(filepath)
    records = read_csv(filepath) if filepath.lower().endswith(".csv") else read_xlsx(filepath)

    if not records:
        return []

    headers = list(records[0].keys())
    col_map = match_column(headers)
    matched_fields = set(col_map.keys())
    now = datetime.now().isoformat(timespec="seconds")

    parsed: List[Dict[str, Any]] = []
    for row in records:
        entry: Dict[str, Any] = {}

        for field in STANDARD_FIELDS:
            original_col = col_map.get(field)
            if original_col:
                val = row.get(original_col, "")
                if field in ("tokens", "call_volume"):
                    try:
                        val = int(float(str(val).replace(",", "")))
                    except (ValueError, TypeError):
                        val = 0
                elif field == "cost":
                    try:
                        val = float(str(val).replace(",", ""))
                    except (ValueError, TypeError):
                        val = 0.0
            else:
                val = ""
                if field == "tokens":
                    val = 0
                elif field == "call_volume":
                    val = 0
                elif field == "cost":
                    val = 0.0

            entry[field] = val

        if not entry.get("bill_end") and entry.get("bill_start"):
            entry["bill_end"] = entry["bill_start"]

        entry["extra"] = make_extra(row, matched_fields)
        entry["source_file"] = filename
        entry["imported_at"] = now
        parsed.append(entry)

    # 解析后处理: request_count 分流、type 翻译等
    post_process_records(parsed)

    return parsed


DEEPSEEK_INPUT_TYPES = [
    "input_cache_hit_tokens",
    "input_cache_miss_tokens",
]

# type 值的翻译对照表 (DeepSeek -> 通用中文)
TYPE_TRANSLATIONS = {
    "input_cache_hit_tokens": "输入(缓存命中)",
    "input_cache_miss_tokens": "输入(缓存未命中)",
    "output_tokens": "输出",
    "request_count": "调用量",
}

# 类型关键词映射 — 根据 type 原文判断属于输入 / 输出 / 其他
_INPUT_KEYWORDS = ["input", "入"]
_OUTPUT_KEYWORDS = ["output", "出"]


def _classify_type(typ: str) -> Optional[str]:
    """判断一条记录的 type 属于 'input' / 'output' / None"""
    if not typ:
        return None
    t = typ.lower()
    if any(kw in t for kw in _INPUT_KEYWORDS):
        return "input"
    if any(kw in t for kw in _OUTPUT_KEYWORDS):
        return "output"
    return None


def post_process_records(records: List[Dict[str, Any]]):
    """
    解析后的后处理:
    - type 值的翻译 (英文 → 中文)
    - request_count 行的 amount → call_volume
    - 模型名称归一化 (查 model_mapping 配置)
    - 根据配置的单价表推算类型 (如两个"输入"行)
    """
    from core.config import normalize_model_name, get_pricing

    for entry in records:
        typ = entry.get("type", "")
        tl = TYPE_TRANSLATIONS.get(typ)
        if tl:
            entry["type"] = tl

        # request_count 分流: tokens → call_volume
        if typ == "request_count":
            entry["call_volume"] = entry["tokens"]
            entry["tokens"] = 0

        # 模型名称归一化
        platform = entry.get("platform", "")
        raw_model = entry.get("model", "")

        if platform and raw_model:
            entry["model"] = normalize_model_name(platform, raw_model)

    # ── 第二遍: 根据单价表辅助识别类型 ──
    pricing = get_pricing()
    _apply_price_hint(records, pricing)


def _apply_price_hint(records: List[Dict[str, Any]], pricing: dict):
    """
    根据配置的单价表, 为记录添加价格标记到 extra。
    用于辅助识别缓存命中/未命中这类同类型不同单价的行。

    遍历逻辑:
    1. 对每组 (platform, model, bill_start, bill_end) 内的记录
    2. 若该组内有多条输入 (或输出) 行, 且配置有价格
    3. 计算 tokens × price 与 cost 的差值, 最接近的标记为对应的价格类型
    """
    if not pricing:
        return

    # 按 (平台, 模型, 开始时间, 结束时间) 分组
    groups: Dict[tuple, List[int]] = {}
    for idx, entry in enumerate(records):
        key = (entry.get("platform"), entry.get("model"),
               entry.get("bill_start"), entry.get("bill_end"))
        groups.setdefault(key, []).append(idx)

    for key, indices in groups.items():
        if len(indices) < 2:
            continue  # 单条记录不需要区分

        platform, model = key[0], key[1]
        price_cfg = pricing.get(platform, {}).get(model, {})
        if not price_cfg:
            continue

        for idx in indices:
            entry = records[idx]
            typ_class = _classify_type(entry.get("type", ""))
            if typ_class not in ("input", "output"):
                continue

            tokens = entry.get("tokens", 0)
            cost = entry.get("cost", 0.0)
            if not tokens or not cost:
                continue

            unit_cost = cost / tokens
            # 尝试匹配 input / output 单价
            expected_input = price_cfg.get("input")
            expected_output = price_cfg.get("output")
            best_match = None
            best_diff = float("inf")

            for label, price in [("input", expected_input), ("output", expected_output)]:
                if price is not None:
                    diff = abs(unit_cost * 1000 - price)  # 通常以 /1K tokens 计价
                    if diff < best_diff:
                        best_diff = diff
                        best_match = label

            if best_match:
                extra = json.loads(entry.get("extra", "{}"))
                extra["_price_hint"] = best_match
                extra["_unit_cost_per_1k"] = round(unit_cost * 1000, 4)
                entry["extra"] = json.dumps(extra, ensure_ascii=False)


def archive_file(filepath: str) -> str:
    """
    将文件移至 archive/ 目录。
    归档时始终添加时间戳防止重名。
    返回归档后的文件名。
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(filepath))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_name = f"{base}_{ts}{ext}"
    dest = os.path.join(ARCHIVE_DIR, dest_name)
    shutil.move(filepath, dest)
    return dest_name


def process_single_file(
    db: Database, filepath: str
) -> Dict[str, Any]:
    """
    单文件两阶段导入:

    第一阶段 (检测):
      解析文件 → check_conflicts → 分出 new / same / conflicts

    第二阶段 (执行):
      由调用方决定如何处理 conflicts, 然后调用 db.upsert_batch() 写入。

    返回:
    {
        "filename": str,
        "new_count": int,
        "same_count": int,
        "conflicts": List[conflict],
    }
    """
    filename = os.path.basename(filepath)

    # 空文件直接归档
    records = parse_records_from_file(filepath)
    if not records:
        archive_file(filepath)
        return {"filename": filename, "new_count": 0, "same_count": 0, "conflicts": []}

    # 检测冲突
    result = db.check_conflicts(records)
    conflicts: List = result.get("conflicts", [])

    # 给每条冲突行附上文件名信息供 UI 展示
    for c in conflicts:
        c["filename"] = filename

    return {
        "filename": filename,
        "new_count": len(result["new"]),
        "same_count": len(result["same"]),
        "conflicts": conflicts,
        "_new_records": result["new"],
        "_same_records": result["same"],
    }


def commit_import(
    db: Database,
    filepath: str,
    file_result: Dict[str, Any],
    force_overwrite_conflicts: bool = False,
) -> int:
    """
    第二阶段执行：确认导入。
    写入 new + (若 force_overwrite_conflicts 则含 conflicts 中的行)，
    然后归档文件。

    返回实际写入行数。
    """
    to_write: List[Dict[str, Any]] = list(file_result.get("_new_records", []))

    if force_overwrite_conflicts:
        for c in file_result.get("conflicts", []):
            to_write.append(c["row"])

    written = 0
    if to_write:
        written = db.upsert_batch(to_write)

    # 归档
    archive_file(filepath)
    return written
