"""
APILedger - 命令行导入入口

直接运行此脚本即可导入 input/ 目录中的所有账单文件，
无需启动 GUI。

用法:
    pixi run python cli_import.py              # 导入全部文件，有冲突则跳过
    pixi run python cli_import.py --force      # 有冲突时强制覆盖
    pixi run python cli_import.py --dry-run    # 仅检测，不写入不归档
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import Database
from core.importer import (
    scan_input_files,
    process_single_file,
    commit_import,
    INPUT_DIR,
)


def main():
    parser = argparse.ArgumentParser(description="APILedger 命令行导入")
    parser.add_argument(
        "--force", action="store_true",
        help="有冲突时强制覆盖已有数据",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅检测，不写入数据库也不归档文件",
    )
    args = parser.parse_args()

    os.makedirs(INPUT_DIR, exist_ok=True)
    files = scan_input_files()

    if not files:
        print("input/ 目录中没有待导入文件。")
        return

    print(f"发现 {len(files)} 个待导入文件\n")

    db = Database()
    db.connect()

    try:
        total_new = 0
        total_same = 0
        total_conflicts = 0
        total_errors = 0

        for fpath in files:
            try:
                res = process_single_file(db, fpath)
            except Exception as e:
                print(f"  [错误] {os.path.basename(fpath)}: {e}\n")
                total_errors += 1
                continue

            n_new = res.get("new_count", 0)
            n_same = res.get("same_count", 0)
            n_conflicts = len(res.get("conflicts", []))

            print(f"  检测结果: 新增 {n_new} 条, 无变化 {n_same} 条, 冲突 {n_conflicts} 条")

            if args.dry_run:
                print(f"  (dry-run 模式，跳过写入和归档)\n")
                total_new += n_new
                total_same += n_same
                total_conflicts += n_conflicts
                continue

            # 冲突处理
            if n_conflicts > 0:
                if args.force:
                    print(f"  --force 模式: 覆盖 {n_conflicts} 条冲突")
                    written = commit_import(db, fpath, res, force_overwrite_conflicts=True)
                else:
                    print(f"  跳过 {n_conflicts} 条冲突 (仅写入新增)")
                    written = commit_import(db, fpath, res, force_overwrite_conflicts=False)
            else:
                written = commit_import(db, fpath, res, force_overwrite_conflicts=True)

            print(f"  已写入 {written} 条，文件已归档\n")
            total_new += n_new
            total_same += n_same
            total_conflicts += n_conflicts

        # 汇总
        print("=" * 50)
        parts = []
        if total_new > 0:
            parts.append(f"新增/更新 {total_new} 条")
        if total_same > 0:
            parts.append(f"{total_same} 条无变化已跳过")
        if total_conflicts > 0:
            parts.append(f"{total_conflicts} 条冲突")
        if total_errors > 0:
            parts.append(f"{total_errors} 个文件出错")
        print(f"导入完成: {' | '.join(parts)}" if parts else "导入完成")

    finally:
        db.close()


if __name__ == "__main__":
    main()
