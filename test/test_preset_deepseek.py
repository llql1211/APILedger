"""presets/deepseek.py - 日期转换、调用量跳过、COMPUTED 字段"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.presets import apply_computed_fields


@pytest.fixture
def deepseek():
    from presets import deepseek
    return deepseek


class TestParseRow:
    def test_yyyymmdd_converted_to_iso(self, deepseek):
        row = deepseek.parse_row({}, {"bill_start": "20260621", "type": "input"})
        assert row["bill_start"] == "2026-06-21"

    def test_iso_date_unchanged(self, deepseek):
        row = deepseek.parse_row({}, {"bill_start": "2026-06-21", "type": "input"})
        assert row["bill_start"] == "2026-06-21"

    def test_request_count_skipped(self, deepseek):
        assert deepseek.parse_row({}, {"type": "request_count", "bill_start": "20260621"}) is None

    def test_whitespace_tolerated(self, deepseek):
        row = deepseek.parse_row({}, {"bill_start": " 20260621 ", "type": "input"})
        assert row["bill_start"] == "2026-06-21"


class TestComputed:
    def test_cost_then_unit_price_order(self, deepseek):
        """cost = price * tokens 先算, 再把 price 换算为 元/百万tokens"""
        row = {"unit_price": 0.002, "tokens": 5000}
        apply_computed_fields(deepseek.COMPUTED, row)
        assert row["cost"] == pytest.approx(10.0)
        assert row["unit_price"] == pytest.approx(2000.0)


class TestPresetContract:
    def test_type_map_standards(self, deepseek):
        assert set(deepseek.TYPE_MAP.values()) == {"输入", "输出", "缓存输入"}

    def test_defaults(self, deepseek):
        assert deepseek.DEFAULTS["platform"] == "DeepSeek"

    def test_model_map(self, deepseek):
        assert deepseek.MODEL_MAP["deepseek-chat"] == "DeepSeek-V3"
        assert deepseek.MODEL_MAP["deepseek-reasoner"] == "DeepSeek-R1"
