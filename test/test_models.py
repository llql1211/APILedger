"""core/models.py - 标准字段定义"""

from core.models import STANDARD_FIELDS


def test_standard_fields_complete():
    # 数据库唯一键依赖这五个字段
    for field in ["bill_start", "platform", "project", "model", "type"]:
        assert field in STANDARD_FIELDS
    # 数值字段
    for field in ["tokens", "cost", "unit_price"]:
        assert field in STANDARD_FIELDS
