"""
APILedger - API 账单管理工具 (命令行入口)

使用流程:
  1. 将 xlsx / csv 账单文件放入 data/input/ 文件夹
  2. 运行 python main.py
     - 自动扫描导入 input/ 中的账单文件
     - 遇到冲突数据时在终端逐条询问裁决 (覆盖 / 跳过)
     - 完成后生成 HTML 报告并自动用浏览器打开

命令行参数:
    --force        冲突全部覆盖, 不询问
    --yes          冲突全部跳过, 不询问 (非交互, 适合脚本)
    --dry-run      仅检测, 不写入不归档
    --no-report    只导入, 不生成报告
    --no-open      生成报告但不弹出浏览器
"""

import argparse
import os
import sys
import webbrowser

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import Database
from core.importer import (
    scan_input_files,
    process_single_file,
    commit_import,
    NoPresetError,
    INPUT_DIR,
)
from core.report import export_report


# ═══════════════════════════════════════════════════
# 冲突裁决
# ═══════════════════════════════════════════════════

def _print_conflict(index: int, total: int, conflict: dict):
    """打印单条冲突详情"""
    row = conflict.get("row", {})
    existing = conflict.get("existing", {})
    print(f"\n  冲突 {index}/{total}: "
          f"{row.get('bill_start', '')} | {row.get('platform', '')} | "
          f"{row.get('project', '')} | {row.get('model', '')} | {row.get('type', '')}")
    print(f"    已有: tokens={int(existing.get('tokens', 0) or 0):,}  "
          f"cost=¥{float(existing.get('cost', 0.0) or 0.0):.4f}  "
          f"(来源: {conflict.get('old_file', '')})")
    print(f"    本次: tokens={int(row.get('tokens', 0) or 0):,}  "
          f"cost=¥{float(row.get('cost', 0.0) or 0.0):.4f}  "
          f"(来源: {conflict.get('new_file', '')})")


def ask_conflicts(conflicts: list, filename: str) -> tuple:
    """
    逐条询问冲突裁决。

    返回 (overwrite_rows, aborted):
      overwrite_rows  用户决定覆盖的行列表
      aborted         用户选择中止整个导入
    """
    total = len(conflicts)
    print(f"\n  [冲突] {filename}: 发现 {total} 条冲突数据")
    print("  可选: [y]覆盖本条  [n]跳过本条  [a]覆盖全部  [s]跳过全部  [q]中止导入")

    overwrite: list = []
    i = 0
    while i < total:
        c = conflicts[i]
        _print_conflict(i + 1, total, c)
        try:
            ans = input("  裁决 (y/n/a/s/q): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "q"

        if ans == "y":
            overwrite.append(c["row"])
            i += 1
        elif ans == "n":
            i += 1
        elif ans == "a":
            overwrite.extend(c["row"] for c in conflicts[i:])
            print(f"  → 覆盖剩余 {total - i} 条冲突")
            i = total
        elif ans == "s":
            print(f"  → 跳过剩余 {total - i} 条冲突")
            i = total
        elif ans == "q":
            print("  → 已中止导入 (本文件未写入, 未归档)")
            return overwrite, True
        else:
            print("  无效输入, 请输入 y / n / a / s / q")
            continue
    return overwrite, False


# ═══════════════════════════════════════════════════
# 导入流程
# ═══════════════════════════════════════════════════

def run_import(db: Database, mode: str = "ask", dry_run: bool = False) -> dict:
    """
    扫描 input/ 目录并导入所有文件。

    mode: 'ask' (逐条询问) / 'force' (全部覆盖) / 'skip' (全部跳过)
    返回统计 dict。
    """
    os.makedirs(INPUT_DIR, exist_ok=True)
    files = scan_input_files()

    if not files:
        print("input/ 目录中没有待导入文件。")
        return {"new": 0, "merge": 0, "same": 0, "conflicts": 0, "errors": 0, "files": 0}

    print(f"发现 {len(files)} 个待导入文件\n")

    stats = {"new": 0, "merge": 0, "same": 0, "conflicts": 0, "errors": 0, "files": len(files)}

    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            res = process_single_file(db, fpath)
        except NoPresetError as e:
            print(f"\n  [跳过] {fname}: 无匹配预设")
            for line in str(e).splitlines():
                print(f"      {line.strip()}")
            print()
            stats["errors"] += 1
            continue
        except Exception as e:
            print(f"  [错误] {fname}: {e}\n")
            stats["errors"] += 1
            continue

        n_new = res.get("new_count", 0)
        n_merge = res.get("merge_count", 0)
        n_same = res.get("same_count", 0)
        conflicts = res.get("conflicts", [])
        n_conflicts = len(conflicts)

        merge_msg = f", 互补合并 {n_merge} 条" if n_merge else ""
        print(f"  检测结果: 新增 {n_new} 条{merge_msg}, 无变化 {n_same} 条, 冲突 {n_conflicts} 条")

        if dry_run:
            print("  (dry-run 模式, 跳过写入和归档)\n")
            stats["new"] += n_new
            stats["merge"] += n_merge
            stats["same"] += n_same
            stats["conflicts"] += n_conflicts
            continue

        # ── 冲突处理 ──
        if n_conflicts > 0:
            if mode == "force":
                print(f"  --force 模式: 覆盖 {n_conflicts} 条冲突")
                written = commit_import(db, fpath, res, force_overwrite_conflicts=True)
            elif mode == "skip":
                print(f"  跳过 {n_conflicts} 条冲突 (仅写入新增)")
                written = commit_import(db, fpath, res, force_overwrite_conflicts=False)
            else:
                overwrite, aborted = ask_conflicts(conflicts, fname)
                if aborted:
                    # 中止: 只写入新增和互补部分, 冲突行不写入, 文件不归档
                    to_write = list(res.get("_new_records", [])) + res.get("_merge_records", [])
                    if to_write:
                        db.upsert_batch(to_write)
                    stats["new"] += n_new
                    stats["merge"] += n_merge
                    stats["same"] += n_same
                    stats["conflicts"] += n_conflicts
                    return stats
                written = commit_import(
                    db, fpath, res,
                    force_overwrite_conflicts=False,
                    overwrite_rows=overwrite,
                )
        else:
            written = commit_import(db, fpath, res, force_overwrite_conflicts=True)

        print(f"  已写入 {written} 条, 文件已归档\n")
        stats["new"] += n_new
        stats["merge"] += n_merge
        stats["same"] += n_same
        stats["conflicts"] += n_conflicts

    # 汇总
    print("=" * 50)
    parts = []
    if stats["new"] > 0:
        parts.append(f"新增/更新 {stats['new']} 条")
    if stats["merge"] > 0:
        parts.append(f"互补合并 {stats['merge']} 条")
    if stats["same"] > 0:
        parts.append(f"{stats['same']} 条无变化已跳过")
    if stats["conflicts"] > 0:
        parts.append(f"{stats['conflicts']} 条冲突")
    if stats["errors"] > 0:
        parts.append(f"{stats['errors']} 个文件出错")
    print(f"导入完成: {' | '.join(parts)}" if parts else "导入完成")
    return stats


# ═══════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════

def open_in_browser(path: str):
    """用系统默认浏览器打开报告文件"""
    url = "file:///" + path.replace(os.sep, "/")
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="APILedger - API 账单管理")
    parser.add_argument("--force", action="store_true", help="有冲突时强制覆盖, 不询问")
    parser.add_argument("--yes", action="store_true", help="有冲突时全部跳过, 不询问")
    parser.add_argument("--dry-run", action="store_true", help="仅检测, 不写入不归档")
    parser.add_argument("--no-report", action="store_true", help="只导入, 不生成报告")
    parser.add_argument("--no-open", action="store_true", help="生成报告但不弹出浏览器")
    args = parser.parse_args()

    if args.force and args.yes:
        parser.error("--force 与 --yes 不能同时使用")

    db = Database()
    db.connect()
    try:
        if args.force:
            mode = "force"
        elif args.yes:
            mode = "skip"
        else:
            mode = "ask"

        run_import(db, mode=mode, dry_run=args.dry_run)

        if not args.dry_run and not args.no_report:
            path = export_report(db)
            print(f"\n报告已生成: {path}")
            if not args.no_open:
                open_in_browser(path)
    finally:
        db.close()


if __name__ == "__main__":
    main()
