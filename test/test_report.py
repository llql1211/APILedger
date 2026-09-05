"""core/report.py - HTML 报告生成"""

import json
import os
from unittest import mock

import pytest

from core.db import Database
from core.report import TEMPLATE_PATH, build_html, export_report, unsnapped_price_groups


@pytest.fixture
def seeded_db(tmp_path):
    db = Database(str(tmp_path / "report.db"))
    db.connect()
    db.upsert_batch([
        {"bill_start": "2026-08-01", "platform": "Paratera", "project": "vscode",
         "model": "GLM-5", "type": "输入", "tokens": 1000,
         "cost": 58.73289999999, "unit_price": 0.0, "source_file": "a.csv"},
        {"bill_start": "2026-08-02", "platform": "DeepSeek", "project": "prod",
         "model": "DeepSeek-V3", "type": "输出", "tokens": 2000,
         "cost": 1.41060000001, "unit_price": 0.0, "source_file": "b.csv"},
    ])
    yield db
    db.close()


class TestBuildHtml:
    def test_placeholders_replaced(self, seeded_db):
        html = build_html(seeded_db)
        assert "__DATA_JSON__" not in html
        assert "__CHART_COLORS__" not in html
        assert "__ECHARTS_TAG__" not in html

    def test_template_file_exists(self):
        assert os.path.exists(TEMPLATE_PATH)

    def test_summary_cost_rounded(self, seeded_db):
        data = json.loads(_extract_data(build_html(seeded_db)))
        # 总费用四舍五入到 2 位
        assert data["summary"]["total_cost"] == pytest.approx(60.14)

    def test_records_embedded(self, seeded_db):
        """全量明细内嵌, 字段完整"""
        data = json.loads(_extract_data(build_html(seeded_db)))
        records = data["records"]
        assert len(records) == 2
        by_model = {r["model"]: r for r in records}
        assert by_model["GLM-5"]["project"] == "vscode"
        assert by_model["GLM-5"]["tokens"] == 1000
        assert by_model["GLM-5"]["source_file"] == "a.csv"
        assert by_model["DeepSeek-V3"]["cost"] == pytest.approx(1.41060000001)

    def test_cost_formatting_capped_at_4_decimals(self, seeded_db):
        """金额展示使用 fmtCost (4 位小数) / fmtCost2 (2 位小数), 不出现原始 {c} 占位"""
        html = build_html(seeded_db)
        assert "maximumFractionDigits: 4" in html
        # ECharts 的 {c} 原始值输出已全部替换为 fmtCost
        assert "'{b}: ¥{c} ({d}%)'" not in html
        assert html.count("fmtCost(p.value)") >= 1

    def test_filter_fields_present(self, seeded_db):
        """筛选栏包含平台 / 项目 / 模型 / 类型下拉"""
        html = build_html(seeded_db)
        for fid in ("f-platform", "f-project", "f-model", "f-type", "f-start", "f-end"):
            assert f'id="{fid}"' in html

    def test_chart_containers_present(self, seeded_db):
        """全部图表容器齐全 (趋势/模型双条/构成堆叠)"""
        html = build_html(seeded_db)
        for cid in ("chart-trend", "chart-model", "chart-stack"):
            assert f'id="{cid}"' in html
        # 类型分布饼图、模型分布饼图、平台占比饼图已删除
        assert 'chart-typepie' not in html
        assert 'chart-modelpie' not in html
        assert 'chart-platform' not in html


class TestExportReport:
    def test_export_writes_file(self, seeded_db, tmp_path):
        path = export_report(seeded_db, str(tmp_path / "out.html"))
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert "<!DOCTYPE html>" in f.read()

    def test_export_default_output_dir(self, seeded_db, tmp_path, monkeypatch):
        import core.report as report
        out_dir = tmp_path / "output"
        monkeypatch.setattr(report, "OUTPUT_DIR", str(out_dir))
        path = export_report(seeded_db)
        assert os.path.basename(path) == "report.html"
        assert os.path.exists(path)


class TestOfficialPrices:
    """展示层单价吸附: 预设 PRICING (按平台分组, 含 history 时段价) → 报告内嵌官方价表"""

    def _fake_preset(self, pricing, platform="Paratera"):
        import types
        return types.SimpleNamespace(PRICING=pricing, DEFAULTS={"platform": platform})

    def test_collect_groups_by_platform(self, monkeypatch):
        import core.report as report
        pricing = {"GLM-5": {"history": [
            {"until": "2026-08-31", "input_hit": 1.0, "input_miss": 3.0, "output": 9.0},
            {"until": "2099-12-31", "input_hit": 1.2, "input_miss": 3.5, "output": 10.0},
        ]}}
        monkeypatch.setattr("core.presets.load_all_presets",
                            lambda: [self._fake_preset(pricing, "Paratera")])
        prices = report._collect_official_prices()
        assert prices["GLM-5"]["Paratera"]["history"] == pricing["GLM-5"]["history"]

    def test_collect_old_format_keeps_platform(self, monkeypatch):
        import core.report as report
        pricing = {"Paratera": {"GLM-5": {"input_miss": 3.0, "output": 9.0}}}
        monkeypatch.setattr("core.presets.load_all_presets",
                            lambda: [self._fake_preset(pricing)])
        prices = report._collect_official_prices()
        assert prices["GLM-5"]["Paratera"] == {"input_miss": 3.0, "output": 9.0}

    def test_pricing_embedded_in_html(self, seeded_db, monkeypatch):
        import core.report as report
        monkeypatch.setattr(report, "_collect_official_prices",
                            lambda: {"GLM-5": {"Paratera": {"input_miss": 3.0, "output": 9.0}}})
        data = json.loads(_extract_data(build_html(seeded_db)))
        assert data["pricing"]["GLM-5"]["Paratera"]["output"] == 9.0

    def test_empty_pricing_still_embeds(self, seeded_db, monkeypatch):
        import core.report as report
        monkeypatch.setattr(report, "_collect_official_prices", lambda: {})
        data = json.loads(_extract_data(build_html(seeded_db)))
        assert data["pricing"] == {}


class _FakeDb:
    """只提供 get_all 的假数据库, 用于单测 unsnapped_price_groups 的分组逻辑"""
    def __init__(self, rows):
        self._rows = rows
    def get_all(self, order_by=None):
        return self._rows


class TestUnsnappedPrices:
    """终端未吸附提示: unsnapped_price_groups 与前端 snapUnitPrice 同判定"""

    def _patch(self, monkeypatch, pricing):
        monkeypatch.setattr("core.report._collect_official_prices", lambda: pricing)

    def test_zero_price_rows_ignored(self, monkeypatch):
        # 零单价行不展示单价, 不参与提示
        self._patch(monkeypatch, {"GLM-5": {"Paratera": {"input_miss": 3.0}}})
        rows = [{"date": "2026-08-03", "model": "GLM-5", "type": "输入", "unit_price": 0.0}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_no_pricing_returns_empty(self, monkeypatch):
        # 全库未配置任何官方价表 → 无从比对, 不提示
        self._patch(monkeypatch, {})
        rows = [{"date": "2026-08-03", "model": "GLM-5", "type": "输入", "unit_price": 3.0}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_within_tolerance_snapped(self, monkeypatch):
        # 3.06 vs 官方价 3.0 → 偏差 2% ≤5% → 吸附, 无提示
        self._patch(monkeypatch, {"GLM-5": {"Paratera": {"input_miss": 3.0, "output": 9.0}}})
        rows = [{"date": "2026-08-03", "model": "GLM-5", "type": "输入", "unit_price": 3.06}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_platform_aware_resolution(self, monkeypatch):
        # 同模型两平台价格不同: 6 月 Paratera 0.2 / DeepSeek 0.02。
        # 若不按平台分组, 6 月日期会命中 DeepSeek 的 0.02 → Paratera 行误报未吸附
        pricing = {"M": {
            "DeepSeek": {"history": [{"until": "2026-06-30", "input_hit": 0.02},
                                     {"until": "2099-12-31", "input_hit": 0.02}]},
            "Paratera": {"history": [{"until": "2026-08-01", "input_hit": 0.2},
                                     {"until": "2099-12-31", "input_hit": 0.02}]},
        }}
        self._patch(monkeypatch, pricing)
        rows = [{"date": "2026-06-01", "platform": "Paratera", "model": "M",
                 "type": "缓存输入", "unit_price": 0.2},
                {"date": "2026-06-02", "platform": "DeepSeek", "model": "M",
                 "type": "缓存输入", "unit_price": 0.02}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_platform_fallback_merges_all(self, monkeypatch):
        # 记录平台不在价表中 → 回退合并所有平台的配置
        self._patch(monkeypatch, {"M": {"Paratera": {"input_miss": 3.0}}})
        rows = [{"date": "2026-08-03", "platform": "Other", "model": "M",
                 "type": "输入", "unit_price": 3.06}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_deviation_beyond_tolerance(self, monkeypatch):
        # 3.2 vs 3.0 → 偏差 6.7% >5% → 未吸附, 按模型/类型/原因分组
        self._patch(monkeypatch, {"GLM-5": {"Paratera": {"input_miss": 3.0, "output": 9.0}}})
        rows = [{"date": "2026-08-03", "model": "GLM-5", "type": "输入", "unit_price": 3.2},
                {"date": "2026-08-04", "model": "GLM-5", "type": "输入", "unit_price": 3.25}]
        groups = unsnapped_price_groups(_FakeDb(rows))
        assert len(groups) == 1
        g = groups[0]
        assert (g["model"], g["type"]) == ("GLM-5", "输入")
        assert g["count"] == 2
        assert "偏差超 5%" in g["reason"] and "¥3.0/M" in g["reason"]
        assert g["price_range"] == "¥3.2~¥3.25"

    def test_type_key_mapping(self, monkeypatch):
        # 缓存输入→input_hit, 输出→output, 其余→input_miss
        self._patch(monkeypatch, {"M": {"Paratera": {"input_hit": 1.0, "input_miss": 3.0, "output": 9.0}}})
        rows = [{"date": "2026-08-03", "model": "M", "type": "缓存输入", "unit_price": 1.02},
                {"date": "2026-08-03", "model": "M", "type": "输出", "unit_price": 10.0},
                {"date": "2026-08-03", "model": "M", "type": "输入", "unit_price": 3.5}]
        groups = unsnapped_price_groups(_FakeDb(rows))
        # 1.02 vs 1.0 吸附 (2%); 10.0 vs 9.0 偏差 11.1% 未吸附; 3.5 vs 3.0 偏差 16.7% 未吸附
        by_type = {g["type"]: g for g in groups}
        assert "缓存输入" not in by_type
        assert by_type["输出"]["reason"].startswith("偏差超 5%")
        assert by_type["输入"]["reason"].startswith("偏差超 5%")

    def test_missing_model_and_type_price(self, monkeypatch):
        self._patch(monkeypatch, {"GLM-5": {"Paratera": {"input_miss": 3.0}}})
        rows = [{"date": "2026-08-03", "model": "Unknown", "type": "输出", "unit_price": 5.0},
                {"date": "2026-08-05", "model": "GLM-5", "type": "输出", "unit_price": 9.0}]
        groups = unsnapped_price_groups(_FakeDb(rows))
        reasons = {(g["model"], g["type"]): g["reason"] for g in groups}
        assert reasons[("Unknown", "输出")] == "未配置官方价表"
        assert reasons[("GLM-5", "输出")] == "价表缺少该类型单价"

    def test_history_date_aware(self, monkeypatch):
        # 8 月按旧价 3.0 吸附, 9 月按新价 3.5 吸附 (9 月账单若误用旧价会偏差超差)
        pricing = {"GLM-5": {"Paratera": {"history": [
            {"until": "2026-08-31", "input_miss": 3.0},
            {"until": "2099-12-31", "input_miss": 3.5},
        ]}}}
        self._patch(monkeypatch, pricing)
        rows = [{"date": "2026-08-20", "model": "GLM-5", "type": "输入", "unit_price": 3.06},
                {"date": "2026-09-01", "model": "GLM-5", "type": "输入", "unit_price": 3.58}]
        assert unsnapped_price_groups(_FakeDb(rows)) == []

    def test_history_earliest_until_wins(self, monkeypatch):
        # history 自日期早向日期晚: until ≥ 日期中最小的时段生效, 与书写顺序无关
        pricing = {"M": {"Paratera": {"history": [
            {"until": "2099-12-31", "input_miss": 5.0},   # 书写在前, 但 until 更晚
            {"until": "2026-08-31", "input_miss": 3.0},   # 6 月账单应落在此时段
        ]}}}
        self._patch(monkeypatch, pricing)
        rows = [{"date": "2026-06-01", "model": "M", "type": "输入", "unit_price": 5.06}]
        groups = unsnapped_price_groups(_FakeDb(rows))
        assert len(groups) == 1 and "¥3.0/M" in groups[0]["reason"]


def _extract_data(html: str) -> str:
    """从生成的 HTML 中取回内嵌的 DATA JSON"""
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n  const CHART_COLORS")
    return html[start:end]
