"""
APILedger - XLSX / CSV 文件扫描、读取、列匹配、两阶段导入、归档
"""

import os
import shutil
from typing import Any, Dict, List

import pandas as pd

from core.models import STANDARD_FIELDS
from core.db import Database
from core.presets import (
    load_all_presets,
    detect_preset,
    apply_column_mapping,
    apply_computed_fields,
    get_preset_name,
    get_pricing_dict,
    get_model_map,
)

# 目录常量
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INPUT_DIR = os.path.join(BASE_DIR, "data", "input")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archive")


SUPPORTED_EXTENSIONS = (".xlsx", ".csv")


class NoPresetError(Exception):
    """
    没有匹配到任何平台预设时抛出。
    提示用户参照 presets/_template.py 为当前账单格式编写预设。
    """


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


XLSX_MAGIC = b"PK\x03\x04"


def _is_xlsx_file(filepath: str) -> bool:
    """
    通过魔数判断文件是否为 xlsx 格式。
    避免用户将 .xlsx 重命名为 .csv 后 CSV 解析器卡死。
    """
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == XLSX_MAGIC
    except OSError:
        return False


def parse_records_from_file(filepath: str) -> List[Dict[str, Any]]:
    """
    读取 xlsx / csv 并解析为标准记录。
    不涉及数据库写入, 纯解析。

    匹配流程:
      尝试平台预设 (presets/*.py)，匹配则按预设解析。
      未匹配到任何预设时抛出 NoPresetError（严格模式，拒绝导入）。
    """
    filename = os.path.basename(filepath)
    if filepath.lower().endswith(".csv") and not _is_xlsx_file(filepath):
        records = read_csv(filepath)
    else:
        records = read_xlsx(filepath)

    if not records:
        return []

    headers = list(records[0].keys())

    # ── 预设匹配 ──
    presets = load_all_presets()
    preset = detect_preset(filename, headers, presets)

    if preset is None:
        raise NoPresetError(
            f"\n  [拒绝] {filename}: 没有匹配到任何平台预设。\n"
            f"     表头: {headers}\n"
            f"     请参照 presets/_template.py 为当前账单格式编写预设，"
            f"放入 presets/ 目录后重新导入。"
        )

    preset_name = get_preset_name(preset)
    col_map = apply_column_mapping(preset.COLUMN_MAPPING, headers)

    # 终端提示: 显示列名匹配情况
    print(f"\n  [读取] {filename}", flush=True)
    print(f"     预设: {preset_name}", flush=True)
    print(f"     原始表头: {headers}", flush=True)
    print(f"     字段映射: {col_map}", flush=True)
    ignored_cols = [h for h in headers if h not in col_map.values()]
    if ignored_cols:
        print(f"     忽略列: {ignored_cols}", flush=True)
    print(f"     数据行数: {len(records)}", flush=True)

    parsed: List[Dict[str, Any]] = []
    for row in records:
        entry: Dict[str, Any] = {}

        # 1. 列名映射提取字段
        for field in STANDARD_FIELDS:
            original_col = col_map.get(field)
            if original_col:
                val = row.get(original_col, "")
                if field == "unit_price":
                    try:
                        val = float(str(val).replace(",", ""))
                    except (ValueError, TypeError):
                        val = 0.0
                elif field == "tokens":
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
                elif field == "cost":
                    val = 0.0
                elif field == "unit_price":
                    val = 0.0

            entry[field] = val

        # 2. 预设行级处理
        row_result = _apply_preset_row(preset, row, entry)
        if row_result is None:
            continue  # 预设跳过此行
        entry = row_result

        # 时间粒度统一为日期 (YYYY-MM-DD): 截断小时部分
        # 按天聚合, 跨天记录归入 bill_start 那天
        if entry.get("bill_start"):
            entry["bill_start"] = str(entry["bill_start"])[:10]

        entry["source_file"] = filename
        parsed.append(entry)

    # ── 后处理: unit_price 计算、过滤、价格匹配 ──
    pricing = get_pricing_dict(preset)
    _post_process(parsed, pricing)

    # ── 文件内聚合: 同 key (date, platform, project, model, type) 求和 ──
    before = len(parsed)
    parsed = _merge_by_key(parsed)
    merged = before - len(parsed)
    if merged:
        print(f"     合并: {merged} 条同 key 记录", flush=True)

    # 终端提示: 显示处理完成
    models_set = set(r["model"] for r in parsed if r["model"])
    types_set = set(r["type"] for r in parsed if r["type"])
    platforms_set = set(r["platform"] for r in parsed if r["platform"])
    total_tokens = sum(r["tokens"] for r in parsed)
    total_cost = sum(r["cost"] for r in parsed)
    print(f"     完成: {len(parsed)} 条 -> 平台 {len(platforms_set)} 个, 模型 {len(models_set)} 个, 类型 {len(types_set)} 个", flush=True)
    print(f"     tokens: {total_tokens:,}, 金额: {total_cost:.2f}", flush=True)

    return parsed


def _merge_by_key(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按 (date, platform, project, model, type) 聚合求和。
    用于合并同一账单文件内同 key 的多条记录 (如平台调价对冲、同天多时段)。

    返回合并后的列表，顺序保持首次出现的顺序。
    """
    merged: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []

    for r in records:
        key = (
            str(r.get("bill_start", ""))[:10],
            r.get("platform", ""),
            r.get("project", ""),
            r.get("model", ""),
            r.get("type", ""),
        )
        if key in merged:
            merged[key]["tokens"] += int(r.get("tokens", 0) or 0)
            merged[key]["cost"] += float(r.get("cost", 0.0) or 0.0)
            # unit_price 取新值; source_file 合并去重
            src = merged[key].get("source_file", "")
            if r.get("source_file") and r["source_file"] not in src:
                merged[key]["source_file"] = (src + "," + r["source_file"]).strip(",")
        else:
            merged[key] = dict(r)
            order.append(key)

    return [merged[k] for k in order]


def _apply_preset_row(preset: Any, raw_row: Dict[str, Any], entry: Dict[str, Any]):
    """
    预设行级处理 (按顺序):
    1. DEFAULTS 固定值填充
    2. parse_row() 自定义解析 (如定义, 返回 None 则跳过此行)
    3. TYPE_MAP 类型翻译
    4. COMPUTED 计算字段
    5. MODEL_MAP 模型名归一化
    """
    # 1. 固定值填充
    for field, value in getattr(preset, "DEFAULTS", {}).items():
        if entry.get(field, "") in ("", None):
            entry[field] = value

    # 2. 自定义行解析 (可在此跳过调用量等特殊记录)
    parse_row = getattr(preset, "parse_row", None)
    if callable(parse_row):
        result = parse_row(raw_row, entry)
        if result is None:
            return None  # 跳过此行
        entry = result

    # 3. 类型翻译
    typ = entry.get("type", "")
    tl = getattr(preset, "TYPE_MAP", {}).get(typ)
    if tl:
        entry["type"] = tl

    # 4. 计算字段
    apply_computed_fields(getattr(preset, "COMPUTED", {}), entry)

    # 5. 模型名归一化
    raw_model = entry.get("model", "")
    if raw_model:
        model_map = get_model_map(preset)
        entry["model"] = model_map.get(raw_model, raw_model)

    return entry


def _post_process(records: List[Dict[str, Any]], pricing: dict):
    """
    解析后的后处理:
    1. 计算 unit_price (若为 0 则按 cost/tokens 推算)
    2. 过滤空记录 (无 tokens 且无费用)
    3. 根据 unit_price 匹配价格表 → 设置 type (可选, 预设定义了 PRICING 时才生效)

    注意: 负费用 (退款/对冲) 记录会被保留, 不计入过滤。
    """
    # ── 第一遍: 计算 unit_price ──
    for entry in records:
        if entry.get("unit_price", 0.0) == 0.0:
            tokens = entry.get("tokens", 0)
            cost = entry.get("cost", 0.0)
            if tokens > 0 and cost > 0:
                entry["unit_price"] = round(cost * 1_000_000 / tokens, 4)

    # ── 过滤: 仅丢弃 无 tokens 且无费用 的空记录 ──
    before = len(records)
    records[:] = [
        r for r in records
        if int(r.get("tokens", 0) or 0) != 0 or abs(float(r.get("cost", 0.0) or 0.0)) > 1e-9
    ]
    filtered = before - len(records)
    if filtered:
        print(f"     过滤: {filtered} 条（无 tokens 且无费用）", flush=True)

    # ── 第二遍: 根据 unit_price 匹配类型 ──
    _apply_price_hint(records, pricing)


def _apply_price_hint(records: List[Dict[str, Any]], pricing: dict):
    """
    根据预设价格表 (PRICING), 用 unit_price 匹配并设置 type。

    仅当预设定义了 PRICING 且有 type 未明确的记录时生效。
    pricing 结构: { model: {"input_hit":.., "input_miss":.., "output":..,
                            "history": [{"until": "日期", ...}]} }
    会依据 bill_start 匹配对应时间段的历史价格。
    """
    if not pricing:
        return

    for entry in records:
        typ = entry.get("type", "")
        # 已精确区分的不再修改
        if typ in ("输入", "输出", "缓存输入"):
            continue

        up = entry.get("unit_price", 0.0)
        if up <= 0:
            continue

        platform = entry.get("platform", "")
        model = entry.get("model", "")
        if not model:
            continue

        # 尝试预设格式 (model 顶层) 或旧格式 (platform → model)
        price_cfg = pricing.get(model)
        if not price_cfg:
            price_cfg = pricing.get(platform, {}).get(model, {})
        if not price_cfg:
            continue

        # 预设格式支持 history 时间段匹配
        if isinstance(price_cfg, dict) and "history" in price_cfg:
            bill_date = entry.get("bill_start", "")
            history = price_cfg.get("history", [])
            for h in sorted(history, key=lambda x: str(x.get("until", "")), reverse=True):
                if bill_date and bill_date <= str(h.get("until", "")):
                    price_cfg = h
                    break

        best_label = None
        best_diff = float("inf")

        for key, label in [
            ("input_hit", "输入(缓存命中)"),
            ("input_miss", "输入(缓存未命中)"),
            ("output", "输出"),
        ]:
            price = price_cfg.get(key)
            if price is not None:
                diff = abs(up - price)
                if diff < best_diff:
                    best_diff = diff
                    best_label = label

        if best_label:
            entry["type"] = best_label


def archive_file(filepath: str) -> str:
    """
    将文件移至 archive/ 目录。
    保持原文件名不变，若目标已存在则覆盖。
    返回归档后的文件名。
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dest_name = os.path.basename(filepath)
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
