"""presets/paratera.py - 配置描述列解析与新旧格式兼容"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def paratera():
    from presets import paratera
    return paratera


class TestParseRow:
    @pytest.mark.parametrize("desc,expect_type,expect_tokens", [
        # 旧版 (2026-07 及以前)
        ("输入:52,110tokens", "输入", 52110),
        ("输出:1,234tokens", "输出", 1234),
        ("缓存输入:997,376tokens", "缓存输入", 997376),
        # 新版 (2026-08 起)
        ("文本输入:61,444tokens", "输入", 61444),
        ("文本输出:9,876tokens", "输出", 9876),
    ])
    def test_known_formats(self, paratera, desc, expect_type, expect_tokens):
        row = paratera.parse_row({"配置描述": desc}, {"cost": 1.0})
        assert row is not None
        assert row["type"] == expect_type
        assert row["tokens"] == expect_tokens

    def test_cached_output_maps_to_output(self, paratera):
        """缓存输出收费 (¥9/百万), 必须归入输出而不是跳过"""
        row = paratera.parse_row({"配置描述": "缓存输出:500tokens"}, {"cost": 1.0})
        assert row["type"] == "输出" and row["tokens"] == 500

    def test_cached_storage_skipped(self, paratera):
        """缓存存储免费, 不导入"""
        assert paratera.parse_row({"配置描述": "缓存存储:123tokens"}, {"cost": 0.0}) is None

    def test_settling_status_skipped(self, paratera):
        """结算状态为 结算中 的行不导入 (金额为 0, 避免污染统计)"""
        base = {"配置描述": "输入:1,000tokens", "费用(元)": "0.0", "结算状态": "结算中"}
        assert paratera.parse_row(base, {"cost": 0.0}) is None
        # 已结算正常导入
        settled = dict(base, 结算状态="已结算")
        row = paratera.parse_row(settled, {"cost": 0.5})
        assert row is not None and row["tokens"] == 1000

    def test_unparseable_desc_skipped(self, paratera):
        assert paratera.parse_row({"配置描述": "garbage"}, {"cost": 0.0}) is None
        assert paratera.parse_row({"配置描述": ""}, {"cost": 0.0}) is None
        # 视频类型按 "个" 计费, 非 tokens 单位, 自然跳过
        assert paratera.parse_row({"配置描述": "视频输入:3个"}, {"cost": 0.0}) is None

    def test_image_types_reserved(self, paratera):
        """图片类型与文本同价, 预留映射"""
        assert paratera.TYPE_MAP["图片输入"] == "输入"
        assert paratera.TYPE_MAP["图片输出"] == "输出"


class TestPresetContract:
    def test_type_map_covers_all_three_standards(self, paratera):
        values = set(paratera.TYPE_MAP.values())
        assert values == {"输入", "输出", "缓存输入"}

    def test_skip_types(self, paratera):
        assert "缓存存储" in paratera.SKIP_TYPES

    def test_defaults(self, paratera):
        assert paratera.DEFAULTS["platform"] == "Paratera"

    def test_match_headers(self, paratera):
        assert "配置描述" in paratera.MATCH_HEADERS
        assert "费用(元)" in paratera.MATCH_HEADERS
