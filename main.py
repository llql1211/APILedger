"""
APILedger - API账单管理工具

使用流程:
  1. 将 xlsx / csv 账单文件放入 data/input/ 文件夹
  2. 运行 python main.py
  3. 在 UI 中点击 "导入账单" 按钮
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import Database
from ui.app import run_ui


def main():
    db = Database()
    db.connect()
    try:
        run_ui(db)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
