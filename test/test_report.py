"""core/report.py - HTML 报告生成"""

import json

import pytest

from core.db import Database
from core.report import build_html


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

    def test_summary_cost_rounded(self, seeded_db):
        data = json.loads(_extract_data(build_html(seeded_db)))
        # 总费用四舍五入到 2 位
        assert data["summary"]["total_cost"] == pytest.approx(60.14)

    def test_cost_formatting_capped_at_4_decimals(self, seeded_db):
        """所有图表 tooltip 均使用 fmtCost (最多 4 位小数), 不出现原始 {c} 占位"""
        html = build_html(seeded_db)
        assert "maximumFractionDigits: 4" in html
        # ECharts 的 {c} 原始值输出已全部替换为 fmtCost
        assert "'{b}: ¥{c} ({d}%)'" not in html
        assert html.count("fmtCost(p.value)") >= 3
        assert "fmtCost(p[0].value)" in html


def _extract_data(html: str) -> str:
    """从生成的 HTML 中取回内嵌的 DATA JSON"""
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n  const CHART_COLORS")
    return html[start:end]
