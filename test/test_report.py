"""core/report.py - HTML 报告生成"""

import json
import os

import pytest

from core.db import Database
from core.report import TEMPLATE_PATH, build_html, export_report


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
        """所有金额展示均使用 fmtCost (最多 4 位小数), 不出现原始 {c} 占位"""
        html = build_html(seeded_db)
        assert "maximumFractionDigits: 4" in html
        # ECharts 的 {c} 原始值输出已全部替换为 fmtCost
        assert "'{b}: ¥{c} ({d}%)'" not in html
        assert html.count("fmtCost(p.value)") >= 2
        assert "fmtCost(p[0].value)" in html

    def test_filter_fields_present(self, seeded_db):
        """筛选栏包含平台 / 项目 / 模型 / 类型下拉"""
        html = build_html(seeded_db)
        for fid in ("f-platform", "f-project", "f-model", "f-type", "f-start", "f-end"):
            assert f'id="{fid}"' in html


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


def _extract_data(html: str) -> str:
    """从生成的 HTML 中取回内嵌的 DATA JSON"""
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n  const CHART_COLORS")
    return html[start:end]
