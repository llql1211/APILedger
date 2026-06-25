"""
APILedger - XLSX 文件扫描、读取、列匹配、两阶段导入、归档
"""

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


def scan_input_files() -> List[str]:
    """扫描 input/ 目录, 返回所有 .xlsx 文件路径 (按修改时间排序)"""
    if not os.path.isdir(INPUT_DIR):
        return []

    files = []
    for f in os.listdir(INPUT_DIR):
        if f.lower().endswith(".xlsx") and not f.startswith("~$"):
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


def parse_records_from_file(filepath: str) -> List[Dict[str, Any]]:
    """
    读取 xlsx 并解析为标准记录。
    不涉及数据库写入, 纯解析。
    """
    filename = os.path.basename(filepath)
    records = read_xlsx(filepath)

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

    return parsed


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
