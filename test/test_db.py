"""core/db.py - 数据库: 贡献明细、冲突检测、聚合、查询"""

import pytest

from core.db import Database, _record_key, parse_file_date_range, ranges_overlap


def make_row(date="2026-08-01", platform="Paratera", project="vscode",
             model="GLM-5", type_="输入", tokens=1000, cost=0.01,
             source_file="a.csv", unit_price=0.0):
    return {
        "bill_start": date, "platform": platform, "project": project,
        "model": model, "type": type_, "tokens": tokens, "cost": cost,
        "source_file": source_file, "unit_price": unit_price,
    }


# ── 文件名时间区段 ────────────────────────────────────

class TestFileDateRange:
    @pytest.mark.parametrize("filename,expected", [
        ("paratera_2026-08-01_2026-08-31.csv", ("2026-08-01", "2026-08-31")),
        ("deepseek_2026-06-01_2026-07-01.csv", ("2026-06-01", "2026-07-01")),
        ("bill_2026-08-15.xlsx", ("2026-08-15", "2026-08-15")),   # 单日期
        ("test_basic_chinese.xlsx", None),                          # 无日期
        ("20260801.csv", None),                                     # 非 ISO 格式
    ])
    def test_parse(self, filename, expected):
        assert parse_file_date_range(filename) == expected

    @pytest.mark.parametrize("a,b,expected", [
        (("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-01"), False),  # 相邻不重叠
        (("2026-08-01", "2026-08-31"), ("2026-08-15", "2026-08-31"), True),   # 部分重叠
        (("2026-08-01", "2026-08-31"), ("2026-08-01", "2026-08-31"), True),   # 完全重叠
        (("2026-08-01", "2026-08-31"), ("2026-07-01", "2026-08-02"), True),   # 包含
        (None, ("2026-08-01", "2026-08-31"), True),                            # 未知保守重叠
        (("2026-08-01", "2026-08-31"), None, True),
    ])
    def test_overlap(self, a, b, expected):
        assert ranges_overlap(a, b) == expected


def test_record_key():
    row = make_row(date="2026-08-01 12:00:00")
    assert _record_key(row) == ("2026-08-01", "Paratera", "vscode", "GLM-5", "输入")


# ── 建表与迁移 ────────────────────────────────────────

class TestInitAndMigration:
    def test_fresh_db_empty(self, db):
        assert db.conn.execute("SELECT COUNT(*) FROM api_records").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0] == 0

    def test_legacy_backfill(self, tmp_path):
        """旧库只有 api_records 时, 重新连接应回填 record_sources"""
        path = str(tmp_path / "legacy.db")
        db = Database(path)
        db.connect()
        # 模拟旧库数据 (绕过贡献写入, 直接插聚合表)
        db.conn.execute(
            "INSERT INTO api_records (date, platform, project, model, type,"
            " tokens, cost, unit_price, source_file)"
            " VALUES ('2026-08-01','Paratera','vscode','GLM-5','输入',100,1.0,10.0,'old.csv')"
        )
        db.conn.commit()
        db.close()

        # 重新连接 → 自动迁移
        db2 = Database(path)
        db2.connect()
        n_src = db2.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        assert n_src == 1
        row = db2.conn.execute(
            "SELECT tokens, cost, source_file FROM record_sources"
        ).fetchone()
        assert tuple(row) == (100, 1.0, "old.csv")
        db2.close()

    def test_backfill_not_repeated(self, tmp_path):
        """已有贡献明细时, 重复连接不重复回填"""
        path = str(tmp_path / "legacy.db")
        db = Database(path)
        db.connect()
        db.conn.execute(
            "INSERT INTO api_records (date, platform, project, model, type,"
            " tokens, cost, unit_price, source_file)"
            " VALUES ('2026-08-01','P','p','m','输入',100,1.0,10.0,'old.csv')"
        )
        db.conn.commit()
        db.close()
        db2 = Database(path)
        db2.connect()  # 第一次回填
        db2.close()
        db3 = Database(path)
        db3.connect()  # 不应再回填
        n = db3.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        assert n == 1
        db3.close()


# ── 写入: 贡献累加与聚合 ──────────────────────────────

class TestUpsertBatch:
    def test_new_key_creates_aggregate(self, db):
        n = db.upsert_batch([make_row(tokens=100, cost=1.0)])
        assert n == 1
        rows, total = db.query()
        assert total == 1
        assert rows[0]["tokens"] == 100 and rows[0]["cost"] == pytest.approx(1.0)
        # 单价按总量重算: 1.0 / 100 * 1e6 = 10000
        assert rows[0]["unit_price"] == pytest.approx(10000.0)

    def test_complementary_files_accumulate(self, db):
        """同 key 两个不重叠文件的贡献 → 聚合行累加"""
        db.upsert_batch([make_row(tokens=100, cost=1.0, source_file="a_2026-08-01_2026-08-10.csv")])
        db.upsert_batch([make_row(tokens=50, cost=0.5, source_file="b_2026-08-11_2026-08-20.csv")])

        rows, _ = db.query()
        assert len(rows) == 1
        assert rows[0]["tokens"] == 150
        assert rows[0]["cost"] == pytest.approx(1.5)
        assert set(rows[0]["source_file"].split(",")) == {
            "a_2026-08-01_2026-08-10.csv", "b_2026-08-11_2026-08-20.csv",
        }
        n_src = db.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        assert n_src == 2

    def test_same_file_reread_replaces_contribution(self, db):
        """同文件重复导入: 替换该文件的贡献, 不重复累加"""
        db.upsert_batch([make_row(tokens=100, cost=1.0, source_file="a.csv")])
        db.upsert_batch([make_row(tokens=120, cost=1.2, source_file="a.csv")])

        rows, _ = db.query()
        assert rows[0]["tokens"] == 120
        assert rows[0]["cost"] == pytest.approx(1.2)
        n_src = db.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        assert n_src == 1

    def test_zero_tokens_keeps_unit_price(self, db):
        """无 tokens (纯费用) 记录: 单价沿用贡献值, 不做除法"""
        db.upsert_batch([make_row(tokens=0, cost=5.0, unit_price=3.3)])
        rows, _ = db.query()
        assert rows[0]["unit_price"] == pytest.approx(3.3)


class TestReplaceKeysBatch:
    def test_replace_clears_other_contributions(self, db):
        """强制覆盖: 该 key 的全部贡献被替换为本次记录"""
        db.upsert_batch([
            make_row(tokens=100, cost=1.0, source_file="a.csv"),
            make_row(tokens=50, cost=0.5, source_file="b.csv"),
        ])
        n = db.replace_keys_batch([make_row(tokens=999, cost=9.9, source_file="c.csv")])
        assert n == 1

        rows, _ = db.query()
        assert rows[0]["tokens"] == 999
        assert rows[0]["source_file"] == "c.csv"
        n_src = db.conn.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        assert n_src == 1


# ── 冲突检测 ──────────────────────────────────────────

class TestCheckConflicts:
    def test_empty(self, db):
        assert db.check_conflicts([]) == {"new": [], "same": [], "merges": [], "conflicts": []}

    def test_new(self, db):
        res = db.check_conflicts([make_row()])
        assert len(res["new"]) == 1
        assert not res["same"] and not res["merges"] and not res["conflicts"]

    def test_same_file_same_values(self, db):
        db.upsert_batch([make_row(tokens=100, cost=1.0, source_file="a.csv")])
        res = db.check_conflicts([make_row(tokens=100, cost=1.0, source_file="a.csv")])
        assert len(res["same"]) == 1

    def test_same_file_changed_values(self, db):
        """同文件再导入但数值变了 → 冲突 (需用户裁决)"""
        db.upsert_batch([make_row(tokens=100, cost=1.0, source_file="a.csv")])
        res = db.check_conflicts([make_row(tokens=200, cost=2.0, source_file="a.csv")])
        assert len(res["conflicts"]) == 1
        c = res["conflicts"][0]
        assert c["row"]["tokens"] == 200
        assert c["existing"]["tokens"] == 100
        assert c["old_file"] == "a.csv" and c["new_file"] == "a.csv"

    def test_disjoint_file_merges(self, db):
        """其他文件且文件名区段不重叠 → 互补合并"""
        db.upsert_batch([make_row(source_file="a_2026-08-01_2026-08-10.csv")])
        res = db.check_conflicts([make_row(source_file="b_2026-08-11_2026-08-20.csv")])
        assert len(res["merges"]) == 1

    def test_overlapping_file_conflicts(self, db):
        """其他文件且文件名区段重叠 → 冲突"""
        db.upsert_batch([make_row(source_file="a_2026-08-01_2026-08-31.csv")])
        res = db.check_conflicts([make_row(source_file="b_2026-08-15_2026-08-31.csv")])
        assert len(res["conflicts"]) == 1

    def test_unknown_filename_conflicts(self, db):
        """文件名无日期 → 保守判冲突"""
        db.upsert_batch([make_row(source_file="a.csv")])
        res = db.check_conflicts([make_row(source_file="b.csv")])
        assert len(res["conflicts"]) == 1

    def test_conflict_existing_is_aggregate(self, db):
        """冲突条目的 existing 应为聚合行数值 (两个文件之和)"""
        db.upsert_batch([
            make_row(tokens=100, cost=1.0, source_file="a_2026-08-01_2026-08-10.csv"),
            make_row(tokens=50, cost=0.5, source_file="b_2026-08-11_2026-08-20.csv"),
        ])
        res = db.check_conflicts([make_row(tokens=999, cost=9.9, source_file="c_2026-08-01_2026-08-31.csv")])
        assert len(res["conflicts"]) == 1
        assert res["conflicts"][0]["existing"]["tokens"] == 150

    def test_different_keys_independent(self, db):
        """不同 key 互不影响"""
        db.upsert_batch([make_row(model="GLM-5", source_file="a.csv")])
        res = db.check_conflicts([make_row(model="DeepSeek-V4-Pro", source_file="b.csv")])
        assert len(res["new"]) == 1


# ── 查询与聚合 ────────────────────────────────────────

class TestQuery:
    @pytest.fixture(autouse=True)
    def _seed(self, db):
        db.upsert_batch([
            make_row(date="2026-08-01", platform="Paratera", project="vscode",
                     model="GLM-5", type_="输入", tokens=100, cost=1.0),
            make_row(date="2026-08-02", platform="Paratera", project="vscode",
                     model="GLM-5", type_="输出", tokens=200, cost=2.0),
            make_row(date="2026-08-03", platform="DeepSeek", project="prod",
                     model="DeepSeek-V3", type_="输入", tokens=300, cost=3.0),
        ])

    def test_get_all(self, db):
        rows, total = db.query()
        assert total == 3 and len(rows) == 3

    def test_filter_date_range(self, db):
        rows, total = db.query(bill_start="2026-08-02", bill_end="2026-08-03")
        assert total == 2 and {r["date"] for r in rows} == {"2026-08-02", "2026-08-03"}

    def test_filter_field(self, db):
        rows, total = db.query(platform="DeepSeek")
        assert total == 1 and rows[0]["model"] == "DeepSeek-V3"

    def test_filter_multiple(self, db):
        rows, total = db.query(platform="Paratera", type_="输出")
        assert total == 1 and rows[0]["date"] == "2026-08-02"

    def test_keyword(self, db):
        rows, total = db.query(keyword="V3")
        assert total == 1 and rows[0]["model"] == "DeepSeek-V3"

    def test_pagination_and_order(self, db):
        rows, total = db.query(order_by="date ASC", limit=2, offset=1)
        assert total == 3 and len(rows) == 2
        assert rows[0]["date"] == "2026-08-02"


class TestAggregates:
    @pytest.fixture(autouse=True)
    def _seed(self, db):
        db.upsert_batch([
            make_row(date="2026-08-01", platform="A", tokens=100, cost=1.0),
            make_row(date="2026-08-01", platform="B", tokens=200, cost=2.0),
            make_row(date="2026-09-01", platform="A", tokens=400, cost=4.0),
        ])

    def test_aggregate_by_date(self, db):
        rows = db.aggregate_by_date(value_field="cost")
        by_period = {r["period"]: r["total"] for r in rows}
        assert by_period["2026-08-01"] == pytest.approx(3.0)
        assert by_period["2026-09-01"] == pytest.approx(4.0)

    def test_aggregate_by_month(self, db):
        rows = db.aggregate_by_date(value_field="tokens",
                                    group_by="strftime('%Y-%m', date)")
        by_period = {r["period"]: r["total"] for r in rows}
        assert by_period["2026-08"] == 300
        assert by_period["2026-09"] == 400

    def test_aggregate_by_field(self, db):
        rows = db.aggregate_by_field(value_field="cost", group_field="platform")
        assert rows[0]["name"] == "A" and rows[0]["total"] == pytest.approx(5.0)

    def test_aggregate_with_filters(self, db):
        rows = db.aggregate_by_date(value_field="cost",
                                    filters={"platform": "B"})
        assert len(rows) == 1 and rows[0]["total"] == pytest.approx(2.0)

    def test_get_distinct(self, db):
        assert db.get_distinct("platform") == ["A", "B"]
        assert db.get_distinct("date") == []  # 不在允许列表

    def test_get_date_range(self, db):
        assert db.get_date_range() == ("2026-08-01", "2026-09-01")
