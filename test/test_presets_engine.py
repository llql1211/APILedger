"""core/presets.py - 预设引擎: 加载、匹配、列映射、表达式、价格匹配"""

import pytest

from core import presets
from core.presets import (
    apply_column_mapping,
    apply_computed_fields,
    compute_field,
    detect_preset,
    get_model_map,
    get_price_for_date,
    get_preset_name,
    get_pricing_dict,
    load_all_presets,
    match_type_by_price,
)


# ── 模块加载 ──────────────────────────────────────────

class TestLoadPresets:
    def test_loads_real_presets(self):
        mods = load_all_presets()
        names = {get_preset_name(m) for m in mods}
        assert "paratera" in names and "deepseek" in names

    def test_every_preset_has_required_fields(self):
        for mod in load_all_presets():
            assert hasattr(mod, "MATCH_FILENAME")
            assert hasattr(mod, "COLUMN_MAPPING")


# ── 预设匹配 ──────────────────────────────────────────

PARATERA_HEADERS = [
    "资源名称", "资源ID", "计费方式", "资源类型", "模型", "配置描述", "站点",
    "交易类型", "交易时间", "账单开始时间", "账单结束时间", "服务费(元)",
    "费用(元)", "结算状态",
]


class TestDetectPreset:
    def test_paratera_match(self):
        mod = detect_preset("paratera_2026-08-01_2026-08-31.csv", PARATERA_HEADERS)
        assert get_preset_name(mod) == "paratera"

    def test_match_is_case_and_space_insensitive(self):
        mod = detect_preset("PARATERA 2026-08.csv", PARATERA_HEADERS)
        assert get_preset_name(mod) == "paratera"

    def test_no_match_without_header_keywords(self):
        # 文件名命中但表头缺 "配置描述"/"费用(元)"
        mod = detect_preset("paratera_x.csv", ["模型", "金额"])
        assert mod is None

    def test_no_match_wrong_filename(self):
        mod = detect_preset("unknown_platform.csv", PARATERA_HEADERS)
        assert mod is None

    def test_deepseek_match(self):
        headers = ["user_id", "utc_date", "model", "api_key_name", "type", "price", "amount"]
        mod = detect_preset("deepseek_2026-06-01_2026-07-01.csv", headers)
        assert get_preset_name(mod) == "deepseek"


# ── 列名映射 ──────────────────────────────────────────

class TestApplyColumnMapping:
    def test_basic_mapping(self):
        mapping = {"model": ["模型"], "cost": ["费用"]}
        assert apply_column_mapping(mapping, ["模型", "费用(元)"]) == {
            "model": "模型", "cost": "费用(元)",
        }

    def test_longest_keyword_wins(self):
        mapping = {"bill_start": ["时间", "账单开始时间"]}
        matched = apply_column_mapping(mapping, ["账单开始时间", "交易时间"])
        assert matched["bill_start"] == "账单开始时间"

    def test_each_header_used_once(self):
        mapping = {"a": ["模型"], "b": ["模型"]}
        matched = apply_column_mapping(mapping, ["模型"])
        # 只有一个表头, 只能匹配一个字段
        assert len(matched) == 1

    def test_substring_match_normalized(self):
        mapping = {"cost": ["费用(元)"]}
        assert apply_column_mapping(mapping, ["费用 (元)"]) == {"cost": "费用 (元)"}


# ── 表达式计算 ────────────────────────────────────────

class TestComputeField:
    def test_arithmetic(self):
        assert compute_field("unit_price * tokens", {"unit_price": 2, "tokens": 3}) == 6.0
        assert compute_field("a + b", {"a": 1.5, "b": 2}) == 3.5

    def test_missing_field_defaults_zero(self):
        assert compute_field("a + b", {"a": 5}) == 5.0

    def test_non_numeric_value_defaults_zero(self):
        assert compute_field("a * 2", {"a": "abc"}) == 0.0

    def test_dangerous_expr_rejected(self):
        assert compute_field("__import__('os')", {}) is None
        assert compute_field("", {}) is None

    def test_invalid_expr_returns_none(self):
        assert compute_field("a +", {"a": 1}) is None


class TestApplyComputedFields:
    def test_sequential_visibility(self):
        """后计算的字段可引用先计算的结果"""
        computed = {"cost": "unit_price * tokens", "double": "cost * 2"}
        row = {"unit_price": 2, "tokens": 3}
        apply_computed_fields(computed, row)
        assert row["cost"] == 6.0
        assert row["double"] == 12.0

    def test_failed_expr_skipped(self):
        row = {"a": 1}
        apply_computed_fields({"b": "unknown_var + 1/'x'"}, row)
        assert "b" not in row


# ── 价格匹配 ──────────────────────────────────────────

PRICING = {
    "GLM-5": {"input_hit": 1.0, "input_miss": 2.0, "output": 8.0},
    "Old-Model": {
        "input_hit": 99.0, "input_miss": 99.0, "output": 99.0,
        "history": [
            {"until": "2026-01-01", "input_hit": 0.5, "input_miss": 1.0, "output": 4.0},
            {"until": "2025-01-01", "input_hit": 0.25, "input_miss": 0.5, "output": 2.0},
        ],
    },
}


class TestGetPriceForDate:
    def test_unknown_model(self):
        assert get_price_for_date(PRICING, "nope", "2026-08-01") is None

    def test_current_price(self):
        cfg = get_price_for_date(PRICING, "GLM-5", "2026-08-01")
        assert cfg["input_miss"] == 2.0

    def test_history_latest_matching(self):
        cfg = get_price_for_date(PRICING, "Old-Model", "2025-06-01")
        assert cfg["input_miss"] == 1.0  # until 2026-01-01 的历史价

    def test_history_oldest(self):
        # 早于所有 until 的日期 → 取最近的更大 until 档 (代码取降序首个匹配)
        cfg = get_price_for_date(PRICING, "Old-Model", "2024-12-31")
        assert cfg["input_miss"] == 1.0  # until 2026-01-01 的历史价

    def test_history_expired_uses_current(self):
        cfg = get_price_for_date(PRICING, "Old-Model", "2026-06-01")
        assert cfg["input_miss"] == 99.0


class TestMatchTypeByPrice:
    def test_precise_type_untouched(self):
        for typ in ("输入", "输出", "缓存输入"):
            assert match_type_by_price(PRICING, {"type": typ, "unit_price": 2.0, "model": "GLM-5"}) is None

    def test_match_by_nearest_price(self):
        entry = {"type": "", "unit_price": 2.0, "model": "GLM-5", "bill_start": "2026-08-01"}
        assert match_type_by_price(PRICING, entry) == "输入(缓存未命中)"
        entry["unit_price"] = 8.0
        assert match_type_by_price(PRICING, entry) == "输出"

    def test_zero_price_returns_none(self):
        assert match_type_by_price(PRICING, {"type": "", "unit_price": 0, "model": "GLM-5"}) is None

    def test_no_model_returns_none(self):
        assert match_type_by_price(PRICING, {"type": "", "unit_price": 2.0, "model": ""}) is None

    def test_history_respected(self):
        entry = {"type": "", "unit_price": 1.0, "model": "Old-Model", "bill_start": "2025-06-01"}
        assert match_type_by_price(PRICING, entry) == "输入(缓存未命中)"


# ── 取值 helpers ──────────────────────────────────────

def test_get_pricing_and_model_map():
    mod = load_all_presets()[0]
    assert isinstance(get_pricing_dict(mod), dict)
    assert isinstance(get_model_map(mod), dict)
