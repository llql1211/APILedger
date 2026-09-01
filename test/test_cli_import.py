"""cli_import.py - 命令行导入入口 (main 流程, 数据库与目录已隔离)"""

import os

import pytest

from conftest import make_paratera_row
import core.importer as importer
from core.db import Database
from core.importer import NoPresetError  # noqa: F401  (确保异常可导入)


@pytest.fixture
def cli_env(tmp_path, import_env, monkeypatch, paratera_xlsx_factory):
    """将 cli_import 的 Database 与目录都指向临时环境"""
    import cli_import

    db_path = tmp_path / "cli.db"
    monkeypatch.setattr(cli_import, "Database", lambda: Database(str(db_path)))
    return import_env, paratera_xlsx_factory


def run_cli(monkeypatch, *args):
    import sys

    import cli_import

    monkeypatch.setattr(sys, "argv", ["cli_import.py", *args])
    cli_import.main()


class TestCliImport:
    def test_import_and_summary(self, cli_env, capsys, monkeypatch):
        (input_dir, _), factory = cli_env
        factory(os.path.join(input_dir, "paratera_2026-08-01_2026-08-31.csv"),
                [make_paratera_row(desc="输入:1,000tokens", cost=0.5)])

        run_cli(monkeypatch)
        out = capsys.readouterr().out
        assert "发现 1 个待导入文件" in out
        assert "新增 1 条" in out
        assert "已写入" in out and "导入完成" in out

        # 第二次运行: 重新放置文件 (首次运行已归档) → 全部无变化
        factory(os.path.join(input_dir, "paratera_2026-08-01_2026-08-31.csv"),
                [make_paratera_row(desc="输入:1,000tokens", cost=0.5)])
        run_cli(monkeypatch)
        out = capsys.readouterr().out
        assert "无变化 1 条" in out

    def test_empty_input_dir(self, cli_env, capsys, monkeypatch):
        run_cli(monkeypatch)
        assert "没有待导入文件" in capsys.readouterr().out

    def test_dry_run_does_not_archive(self, cli_env, capsys, monkeypatch):
        (input_dir, archive_dir), factory = cli_env
        path = factory(os.path.join(input_dir, "paratera_2026-08-01_2026-08-31.csv"),
                       [make_paratera_row()])
        run_cli(monkeypatch, "--dry-run")
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert os.path.exists(path)  # 未归档

    def test_conflict_skipped_without_force(self, cli_env, capsys, monkeypatch):
        """区段重叠的重复数据 → 冲突跳过 (不带 --force)"""
        (input_dir, _), factory = cli_env
        factory(os.path.join(input_dir, "paratera_2026-08-01_2026-08-31.csv"),
                [make_paratera_row(desc="输入:1,000tokens", cost=0.5)])
        run_cli(monkeypatch)
        capsys.readouterr()

        factory(os.path.join(input_dir, "paratera_2026-08-15_2026-08-31.csv"),
                [make_paratera_row(desc="输入:9,999tokens", cost=9.9)])
        run_cli(monkeypatch)
        out = capsys.readouterr().out
        assert "冲突 1 条" in out
        assert "跳过 1 条冲突" in out
        assert "1 条冲突" in out.split("=" * 20)[-1]  # 汇总

    def test_force_overwrites(self, cli_env, capsys, monkeypatch):
        (input_dir, _), factory = cli_env
        factory(os.path.join(input_dir, "paratera_2026-08-01_2026-08-31.csv"),
                [make_paratera_row(desc="输入:1,000tokens", cost=0.5)])
        run_cli(monkeypatch)
        capsys.readouterr()

        factory(os.path.join(input_dir, "paratera_2026-08-15_2026-08-31.csv"),
                [make_paratera_row(desc="输入:9,999tokens", cost=9.9)])
        run_cli(monkeypatch, "--force")
        out = capsys.readouterr().out
        assert "--force 模式: 覆盖 1 条冲突" in out

    def test_no_preset_file_reported_as_error(self, cli_env, capsys, monkeypatch):
        (input_dir, _), _ = cli_env
        with open(os.path.join(input_dir, "mystery.csv"), "w", encoding="utf-8") as f:
            f.write("foo,bar\n1,2\n")
        run_cli(monkeypatch)
        out = capsys.readouterr().out
        assert "[跳过]" in out and "1 个文件出错" in out
