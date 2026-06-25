"""
APILedger - XLSX / CSV 文件扫描、读取、列匹配、两阶段导入、归档
"""

import os
import shutil
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from core.models import match_column, make_extra, STANDARD_FIELDS
from core.db import Database

# 目录常量
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INPUT_DIR = os.path.join(BASE_DIR, "data", "input")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archive")


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
    """
    filename = os.path.basename(filepath)
    if filepath.lower().endswith(".csv") and not _is_xlsx_file(filepath):
        records = read_csv(filepath)
    else:
        records = read_xlsx(filepath)

    if not records:
        return []

    headers = list(records[0].keys())
    col_map = match_column(headers)
    matched_fields = set(col_map.keys())
    now = datetime.now().isoformat(timespec="seconds")

    # 终端提示: 显示列名匹配情况
    print(f"\n  [读取] {filename}", flush=True)
    print(f"     原始表头: {headers}", flush=True)
    print(f"     字段映射: {col_map}", flush=True)
    extra_cols = [h for h in headers if h not in col_map.values()]
    if extra_cols:
        print(f"     未匹配列 -> extra: {extra_cols}", flush=True)
    print(f"     数据行数: {len(records)}", flush=True)

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

    # 终端提示: 显示处理完成
    models_set = set(r["model"] for r in parsed if r["model"])
    types_set = set(r["type"] for r in parsed if r["type"])
    platforms_set = set(r["platform"] for r in parsed if r["platform"])
    total_tokens = sum(r["tokens"] for r in parsed)
    total_cost = sum(r["cost"] for r in parsed)
    print(f"     完成: {len(parsed)} 条 -> 平台 {len(platforms_set)} 个, 模型 {len(models_set)} 个, 类型 {len(types_set)} 个", flush=True)
    print(f"     tokens: {total_tokens:,}, 金额: {total_cost:.2f}", flush=True)

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
    根据配置的单价表, 为输入的记录区分类型。

    逻辑:
    1. 对每条 type 含"input"/"输出"但还未明确区分的记录
    2. 查出对应平台 x 模型的 input_hit / input_miss / output 单价
    3. 计算 unit_cost (cost/tokens × 1000), 与各单价比较
    4. 最接近的匹配结果直接覆写 type 字段:
       - input_hit  → "输入(缓存命中)"
       - input_miss → "输入(缓存未命中)"
       - output     → "输出"
    """
    if not pricing:
        return

    TYPE_LABELS = {
        "input_hit": "输入(缓存命中)",
        "input_miss": "输入(缓存未命中)",
        "output": "输出",
    }

    for entry in records:
        typ = entry.get("type", "")
        # 只处理 type 尚未精确区分的情况
        if typ in ("输入(缓存命中)", "输入(缓存未命中)", "输出", "调用量"):
            continue

        tokens = entry.get("tokens", 0)
        cost = entry.get("cost", 0.0)
        if not tokens or not cost:
            continue

        platform = entry.get("platform", "")
        model = entry.get("model", "")
        if not platform or not model:
            continue

        price_cfg = pricing.get(platform, {}).get(model, {})
        if not price_cfg:
            continue

        unit_cost = cost / tokens * 1000  # 每 1K tokens 单价
        best_label = None
        best_diff = float("inf")

        for key, label in TYPE_LABELS.items():
            price = price_cfg.get(key)
            if price is not None:
                diff = abs(unit_cost - price)
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
