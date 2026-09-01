"""
pytest 共享 fixture。

- 将项目根目录加入 sys.path, 使 `import core` / `import ui` 生效
- db:            临时路径的数据库 (不触碰真实 data/api_ledger.db)
- import_env:    将 importer 的 input/ 与 archive/ 目录隔离到临时目录
- xlsx helpers:  构造测试用账单文件
"""

import os
import sys

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import Database  # noqa: E402

TEST_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def db(tmp_path):
    """临时数据库, 测试结束自动关闭删除"""
    database = Database(str(tmp_path / "test.db"))
    database.connect()
    yield database
    database.close()


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    """
    隔离 importer 的目录常量, 避免测试读写真实 data/input、data/archive。
    返回 (input_dir, archive_dir)。
    """
    import core.importer as importer

    input_dir = tmp_path / "input"
    archive_dir = tmp_path / "archive"
    input_dir.mkdir()
    archive_dir.mkdir()
    monkeypatch.setattr(importer, "INPUT_DIR", str(input_dir))
    monkeypatch.setattr(importer, "ARCHIVE_DIR", str(archive_dir))
    return str(input_dir), str(archive_dir)


# ── 文件构造 helpers ──────────────────────────────────

PARATERA_HEADERS = [
    "资源名称", "资源ID", "计费方式", "资源类型", "模型", "配置描述", "站点",
    "交易类型", "交易时间", "账单开始时间", "账单结束时间", "服务费(元)",
    "费用(元)", "结算状态",
]


def make_paratera_row(project="vscode", model="DeepSeek-V4-Flash",
                      desc="输入:1,000tokens", bill_start="2026-08-01 00:00:00",
                      cost=0.01):
    return {
        "资源名称": project, "资源ID": "TOKEN-x", "计费方式": "实时",
        "资源类型": "API_TOKEN", "模型": model, "配置描述": desc, "站点": "北京一区",
        "交易类型": "实时计费小时结", "交易时间": bill_start,
        "账单开始时间": bill_start, "账单结束时间": bill_start,
        "服务费(元)": 0.0, "费用(元)": cost, "结算状态": "已结算",
    }


def write_xlsx(path, rows, headers=None):
    """rows: list[dict] → xlsx 内容写入 (后缀可以是 .csv, 模拟平台重命名导出)"""
    body, ext = os.path.splitext(path)
    real = body + ".xlsx" if ext.lower() != ".xlsx" else path
    pd.DataFrame(rows, columns=headers).to_excel(real, index=False)
    if real != path:
        os.replace(real, path)


@pytest.fixture
def paratera_xlsx_factory(tmp_path):
    """生成 paratera 格式 xlsx 的工厂, 文件名可指定日期区段"""
    def _make(filename, rows):
        path = os.path.join(str(tmp_path), filename)
        write_xlsx(path, rows, PARATERA_HEADERS)
        return path
    return _make


@pytest.fixture
def deepseek_csv(tmp_path):
    """仓库自带 deepseek 测试账单的副本 (导入会归档移动原文件, 不能用原件)"""
    import shutil

    dst = os.path.join(str(tmp_path), "deepseek_2026-06-01_2026-07-01.csv")
    shutil.copy(os.path.join(TEST_DIR, "test_deepseek.csv"), dst)
    return dst
