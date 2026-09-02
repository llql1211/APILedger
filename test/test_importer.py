"""core/importer.py - 文件读取、解析、聚合、两阶段导入、归档"""

import os

import pytest

from conftest import make_paratera_row, write_xlsx
from core.importer import (
    NoPresetError,
    _is_xlsx_file,
    _merge_by_key,
    _post_process,
    commit_import,
    parse_records_from_file,
    process_single_file,
    read_csv,
    read_xlsx,
    scan_input_files,
)


# ── 文件读取 ──────────────────────────────────────────

class TestFileReading:
    def test_is_xlsx_magic(self, tmp_path):
        p = str(tmp_path / "renamed.csv")
        write_xlsx(p, [{"a": 1}])  # xlsx 内容, .csv 后缀
        assert _is_xlsx_file(p) is True
        plain = tmp_path / "plain.csv"
        plain.write_text("a,b\n1,2\n", encoding="utf-8")
        assert _is_xlsx_file(str(plain)) is False

    def test_read_csv_utf8(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text("模型,金额\nGLM-5,1.5\n", encoding="utf-8")
        rows = read_csv(str(p))
        assert rows == [{"模型": "GLM-5", "金额": "1.5"}]

    def test_read_csv_gbk_fallback(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text("模型,金额\nGLM-5,1.5\n", encoding="gbk")
        rows = read_csv(str(p))
        assert rows[0]["模型"] == "GLM-5"

    def test_read_xlsx(self, tmp_path):
        p = str(tmp_path / "t.xlsx")
        write_xlsx(p, [{"模型": "GLM-5", "金额": "1.5"}])
        rows = read_xlsx(p)
        assert rows[0]["模型"] == "GLM-5"

    def test_scan_input_files_sorted(self, import_env):
        input_dir, _ = import_env
        for name in ["b.csv", "a.csv", "c.txt", "~$lock.xlsx"]:
            open(os.path.join(input_dir, name), "w").close()
        files = scan_input_files()
        names = [os.path.basename(f) for f in files]
        assert "c.txt" not in names and "~$lock.xlsx" not in names
        assert set(names) == {"a.csv", "b.csv"}


# ── 解析 ──────────────────────────────────────────────

class TestParseRecords:
    def test_deepseek_fixture(self, deepseek_csv):
        records = parse_records_from_file(deepseek_csv)
        # 7 行数据, 2 行 request_count 被跳过
        assert len(records) == 5
        r = records[0]
        assert r["platform"] == "DeepSeek"
        assert r["model"] == "DeepSeek-V3"       # MODEL_MAP 归一化
        assert r["type"] == "缓存输入"
        assert r["bill_start"] == "2024-06-01"
        assert r["cost"] == pytest.approx(10.0)  # 0.002 * 5000
        assert r["unit_price"] == pytest.approx(2000.0)

    def test_paratera_synthetic(self, paratera_xlsx_factory):
        path = paratera_xlsx_factory(
            "paratera_2026-08-01_2026-08-31.csv",
            [
                make_paratera_row(desc="文本输入:1,000tokens", cost=0.03),
                make_paratera_row(desc="文本输出:2,000tokens", cost=0.06),
                make_paratera_row(desc="缓存存储:99tokens", cost=0.0),
            ],
        )
        records = parse_records_from_file(path)
        assert len(records) == 2  # 缓存存储被跳过
        assert {r["type"] for r in records} == {"输入", "输出"}
        assert all(r["platform"] == "Paratera" for r in records)
        assert all(r["bill_start"] == "2026-08-01" for r in records)

    def test_unknown_preset_rejected(self, tmp_path):
        p = tmp_path / "unknown_platform.csv"
        p.write_text("foo,bar\n1,2\n", encoding="utf-8")
        with pytest.raises(NoPresetError):
            parse_records_from_file(str(p))

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "paratera_empty.csv"
        p.write_text("模型,配置描述,费用(元)\n", encoding="utf-8")
        assert parse_records_from_file(str(p)) == []


class TestMergeByKey:
    def test_in_file_merge_sums(self):
        records = [
            {"bill_start": "2026-08-01", "platform": "P", "project": "p",
             "model": "m", "type": "输入", "tokens": 100, "cost": 1.0,
             "unit_price": 1.0, "source_file": "a.csv"},
            {"bill_start": "2026-08-01", "platform": "P", "project": "p",
             "model": "m", "type": "输入", "tokens": 50, "cost": 0.5,
             "unit_price": 1.0, "source_file": "a.csv"},
            {"bill_start": "2026-08-02", "platform": "P", "project": "p",
             "model": "m", "type": "输入", "tokens": 10, "cost": 0.1,
             "unit_price": 1.0, "source_file": "a.csv"},
        ]
        merged = _merge_by_key(records)
        assert len(merged) == 2
        first = merged[0]
        assert first["tokens"] == 150 and first["cost"] == pytest.approx(1.5)

    def test_order_preserved(self):
        def rec(date):
            return {"bill_start": date, "platform": "P", "project": "p",
                    "model": "m", "type": "输入", "tokens": 1, "cost": 1.0,
                    "unit_price": 1.0, "source_file": "a.csv"}
        merged = _merge_by_key([rec("2026-08-03"), rec("2026-08-01")])
        assert [m["bill_start"] for m in merged] == ["2026-08-03", "2026-08-01"]


class TestPostProcess:
    def test_unit_price_derived(self):
        records = [{"tokens": 100, "cost": 2.0, "unit_price": 0.0, "type": "输入"}]
        _post_process(records, {})
        assert records[0]["unit_price"] == pytest.approx(20000.0)

    def test_empty_records_filtered(self):
        records = [
            {"tokens": 0, "cost": 0.0, "unit_price": 0.0, "type": ""},
            {"tokens": 5, "cost": 0.0, "unit_price": 0.0, "type": ""},
        ]
        _post_process(records, {})
        assert len(records) == 1  # 空 (无 tokens 且无费用) 被丢弃

    def test_negative_cost_kept(self):
        """负费用 (退款) 不被过滤"""
        records = [{"tokens": 0, "cost": -1.0, "unit_price": 0.0, "type": ""}]
        _post_process(records, {})
        assert len(records) == 1

    def test_price_hint_sets_type(self):
        pricing = {"GLM-5": {"input_hit": 1.0, "input_miss": 2.0, "output": 8.0}}
        records = [{"tokens": 100, "cost": 0.0002, "unit_price": 2.0,
                    "type": "", "model": "GLM-5", "bill_start": "2026-08-01"}]
        _post_process(records, pricing)
        assert records[0]["type"] == "输入(缓存未命中)"


# ── 两阶段导入 (端到端) ───────────────────────────────

class TestTwoPhaseImport:
    def test_new_then_idempotent(self, db, import_env, tmp_path):
        import shutil

        # deepseek_csv fixture 每次生成副本, 这里手动放两次 (首次导入会归档移走)
        from conftest import FIXTURE_DIR
        src = os.path.join(FIXTURE_DIR, "test_deepseek.csv")
        target = str(tmp_path / "deepseek_2026-06-01_2026-07-01.csv")
        shutil.copy(src, target)

        res = process_single_file(db, target)
        assert res["new_count"] == 5 and not res["conflicts"]
        written = commit_import(db, target, res)
        assert written == 5

        # 重新导入同一文件 → 全部"无变化"
        shutil.copy(src, target)
        res2 = process_single_file(db, target)
        assert res2["same_count"] == 5 and not res2["conflicts"]

    def test_complementary_boundary_merge(self, db, import_env, paratera_xlsx_factory):
        """月账单边界日的互补切片自动合并 (真实场景: 9月文件带 8/31 尾时)"""
        sep = paratera_xlsx_factory(
            "paratera_2026-09-01_2026-09-01.csv",
            [
                # 9 月文件带上了 8 月最后小时的账单
                make_paratera_row(model="GLM-5", desc="输入:2,000tokens",
                                  bill_start="2026-08-31 23:00:00", cost=1.0),
                make_paratera_row(model="GLM-5", desc="输入:3,000tokens",
                                  bill_start="2026-09-01 00:00:00", cost=1.5),
            ],
        )
        res_sep = process_single_file(db, sep)
        assert res_sep["new_count"] == 2
        commit_import(db, sep, res_sep)

        # 8 月文件也含 8/31 的数据 → 与 9 月文件的切片互补, 自动合并
        aug = paratera_xlsx_factory(
            "paratera_2026-08-01_2026-08-31.csv",
            [make_paratera_row(model="GLM-5", desc="输入:5,000tokens",
                               bill_start="2026-08-31 10:00:00", cost=2.5)],
        )
        res_aug = process_single_file(db, aug)
        assert res_aug["merge_count"] == 1  # 区段不重叠 → 互补合并
        assert res_aug["new_count"] == 0 and not res_aug["conflicts"]
        commit_import(db, aug, res_aug)

        rows, _ = db.query(bill_start="2026-08-31", bill_end="2026-08-31")
        assert len(rows) == 1
        assert rows[0]["tokens"] == 7000  # 2,000 + 5,000
        assert rows[0]["cost"] == pytest.approx(3.5)
        assert "2026-09-01_2026-09-01" in rows[0]["source_file"]
        assert "2026-08-01_2026-08-31" in rows[0]["source_file"]

    def test_overlapping_file_conflicts(self, db, import_env, paratera_xlsx_factory):
        """区段重叠的重复文件 → 冲突, 默认跳过, force 覆盖"""
        rows = [make_paratera_row(model="GLM-5", desc="输入:1,000tokens", cost=0.5)]
        full = paratera_xlsx_factory("paratera_2026-08-01_2026-08-31.csv", rows)
        res = process_single_file(db, full)
        commit_import(db, full, res)

        # 同区段重叠、不同数值的重复导出
        dup_rows = [make_paratera_row(model="GLM-5", desc="输入:9,999tokens", cost=9.9)]
        dup = paratera_xlsx_factory("paratera_2026-08-15_2026-08-31.csv", dup_rows)
        res_dup = process_single_file(db, dup)
        assert len(res_dup["conflicts"]) == 1
        assert res_dup["merge_count"] == 0

        # 默认跳过: 数据库保持原值, 文件仍归档
        written = commit_import(db, dup, res_dup, force_overwrite_conflicts=False)
        got, _ = db.query()
        assert got[0]["tokens"] == 1000
        assert written == 0

        # force 覆盖: 重建该 key 的贡献, 以新文件数值为准
        full = paratera_xlsx_factory(  # 原文件已归档, 重建后再导入
            "paratera_2026-08-01_2026-08-31.csv", rows,
        )
        res2 = process_single_file(db, full)
        assert res2["same_count"] == 1  # 数值未变, 无变化
        commit_import(db, full, res2, force_overwrite_conflicts=True)

        # 换成 dup 的数值再 force → 覆盖生效
        dup = paratera_xlsx_factory(
            "paratera_2026-08-15_2026-08-31.csv", dup_rows,
        )
        res_dup2 = process_single_file(db, dup)
        commit_import(db, dup, res_dup2, force_overwrite_conflicts=True)
        got, _ = db.query()
        assert got[0]["tokens"] == 9999
        assert got[0]["source_file"] == "paratera_2026-08-15_2026-08-31.csv"

    def test_empty_file_archived(self, db, import_env):
        input_dir, archive_dir = import_env
        p = os.path.join(input_dir, "paratera_empty.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("模型,配置描述,费用(元)\n")
        res = process_single_file(db, p)
        assert res["new_count"] == 0
        assert not os.path.exists(p)                       # 已归档
        assert os.path.exists(os.path.join(archive_dir, "paratera_empty.csv"))
