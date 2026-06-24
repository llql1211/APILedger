"""
APILedger - API账单管理工具

使用流程:
  1. 将 xlsx 账单文件放入 input/ 文件夹
  2. 运行 python main.py
  3. 自动导入 → UPSERT 去重 → 启动可视化窗口
"""

import os
import sys

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import Database
from core.importer import run_import
from ui.app import run_ui


def main():
    # 1. 打开数据库
    db = Database()
    db.connect()
    print("[DB] Database connected")

    # 2. 导入 input/ 中的 xlsx 文件
    imported = run_import(db)
    if imported:
        print(f"[OK] Imported {len(imported)} file(s)")
    else:
        print("[INFO] No new files to import")

    # 3. 启动 UI (阻塞至窗口关闭)
    print("[UI] Launching visualization window...")
    try:
        run_ui(db)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
        print("[BYE] Goodbye")


if __name__ == "__main__":
    main()
