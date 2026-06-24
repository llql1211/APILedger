"""
APILedger - XLSX 文件扫描、自动读取、列匹配、UPSERT 写入、归档
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

INPUT_DIR = os.path.join(BASE_DIR, "input")
COMPLETED_DIR = os.path.join(BASE_DIR, "completed")


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

    # 按修改时间排序, 旧的先处理
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def read_xlsx(filepath: str) -> List[Dict[str, Any]]:
    """
    用 pandas 读取 xlsx, 返回 list of dict。
    自动处理表头行。
    """
    df = pd.read_excel(filepath, dtype=str)
    df = df.fillna("")  # 空值统一转为空字符串

    # 清理列名: 去除前后空格
    df.columns = [str(c).strip() for c in df.columns]

    records = df.to_dict(orient="records")
    return records


def import_file(db: Database, filepath: str) -> int:
    """
    导入单个 xlsx 文件:
    1. 读取
    2. 列匹配
    3. UPSERT 写入数据库
    4. 移至 completed/

    返回写入的记录数。
    """
    filename = os.path.basename(filepath)
    records = read_xlsx(filepath)

    if not records:
        # 空文件也移走
        _archive_file(filepath)
        return 0

    # 获取表头
    headers = list(records[0].keys())

    # 列名匹配
    col_map = match_column(headers)
    matched_fields = set(col_map.keys())

    now = datetime.now().isoformat(timespec="seconds")

    processed: List[Dict[str, Any]] = []
    for row in records:
        entry: Dict[str, Any] = {}

        # 标准字段: 从原始行取值
        for field in STANDARD_FIELDS:
            original_col = col_map.get(field)
            if original_col:
                val = row.get(original_col, "")
                if field in ("tokens", "call_volume"):
                    # 尝试转为 int
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

        # fallback: 只有 bill_start 没有 bill_end 时, 用 bill_start 填充
        if not entry.get("bill_end") and entry.get("bill_start"):
            entry["bill_end"] = entry["bill_start"]

        # 未匹配的列入 extra
        entry["extra"] = make_extra(row, matched_fields)
        entry["source_file"] = filename
        entry["imported_at"] = now

        processed.append(entry)

    # UPSERT 写入
    count = db.upsert_batch(processed)

    # 移至 completed/
    _archive_file(filepath)

    return count


def _archive_file(filepath: str):
    """
    将文件移至 completed/ 文件夹。
    若已完成文件夹中有同名文件, 加时间戳后缀。
    """
    os.makedirs(COMPLETED_DIR, exist_ok=True)
    filename = os.path.basename(filepath)
    dest = os.path.join(COMPLETED_DIR, filename)

    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(COMPLETED_DIR, f"{base}_{ts}{ext}")

    shutil.move(filepath, dest)


def run_import(db: Database) -> List[str]:
    """
    执行导入流程:
    1. 扫描 input/ 目录
    2. 逐个导入
    3. 返回已导入的文件名列表

    可在 main.py 中调用, 无文件时跳过 UI 启动前显示提示。
    """
    files = scan_input_files()
    imported = []

    if not files:
        return imported

    print(f"[Import] Found {len(files)} file(s) to process...")

    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            count = import_file(db, fpath)
            print(f"  [OK] {fname} -> {count} records processed")
            imported.append(fname)
        except Exception as e:
            print(f"  [FAIL] {fname}: {e}")

    print(f"[Done] {len(imported)} file(s) processed")
    return imported
