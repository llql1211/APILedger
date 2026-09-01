"""ui/ - 模块导入与纯数据一致性检查 (不启动窗口)"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_ui_modules_importable():
    import ui.theme  # noqa: F401
    import ui.conflict_dialog  # noqa: F401
    import ui.panels.table_panel  # noqa: F401
    import ui.panels.chart_panel  # noqa: F401
    import ui.panels.filter_panel  # noqa: F401
    import ui.app  # noqa: F401


def test_table_panel_column_definitions_consistent():
    from ui.panels.table_panel import (
        COL_KEYS,
        COL_LABELS,
        COL_MINWIDTHS,
        COL_WIDTHS,
        DISPLAY_COLUMNS,
    )

    assert COL_KEYS == [k for k, _ in DISPLAY_COLUMNS]
    assert COL_LABELS == [label for _, label in DISPLAY_COLUMNS]
    # 宽度配置必须覆盖所有显示列, 且最小宽不超过基准宽
    assert set(COL_WIDTHS) == set(COL_KEYS)
    assert set(COL_MINWIDTHS) == set(COL_KEYS)
    for key in COL_KEYS:
        assert COL_MINWIDTHS[key] <= COL_WIDTHS[key]
