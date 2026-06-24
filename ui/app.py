"""
APILedger - CustomTkinter 主窗口 + Tab 集成
"""

from __future__ import annotations

from typing import Any, Dict

import customtkinter as ctk

from core.db import Database
from ui.theme import (
    setup_appearance,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    FILTER_PANEL_WIDTH,
    FONT_SIZES,
)
from ui.panels.filter_panel import FilterPanel
from ui.panels.table_panel import TablePanel
from ui.panels.chart_panel import ChartPanel


class App(ctk.CTk):
    """APILedger 主应用窗口"""

    def __init__(self, db: Database):
        super().__init__()

        self.db = db
        self._current_filters: Dict[str, Any] = {}

        # ── 窗口配置 ─────────────────────────
        setup_appearance()
        self.title("APILedger - API账单管理")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(1100, 700)

        # 主容器
        self.main_container = ctk.CTkFrame(self, corner_radius=0)
        self.main_container.pack(fill="both", expand=True)

        # ── 左侧筛选面板 ─────────────────────
        self.filter_panel = FilterPanel(
            self.main_container,
            db=self.db,
            on_apply=self._on_filters_applied,
            on_reset=self._on_filters_reset,
        )
        self.filter_panel.pack(side="left", fill="y", padx=(0, 1))

        # ── 右侧内容区 ───────────────────────
        right_area = ctk.CTkFrame(self.main_container, corner_radius=0)
        right_area.pack(side="left", fill="both", expand=True)

        # Tab 页
        self.tab_view = ctk.CTkTabview(right_area, corner_radius=8)
        self.tab_view.pack(fill="both", expand=True, padx=8, pady=8)

        # 数据表格 Tab
        self.tab_view.add("📊 数据表格")
        self.table_panel = TablePanel(self.tab_view.tab("📊 数据表格"), db=self.db)
        self.table_panel.pack(fill="both", expand=True)

        # 图表 Tab (内含 3 个子 Tab)
        self.tab_view.add("📈📊🥧 图表")
        self.chart_panel = ChartPanel(self.tab_view.tab("📈📊🥧 图表"), db=self.db)
        self.chart_panel.pack(fill="both", expand=True)

        # ── 底部状态栏 ───────────────────────
        self.status_bar = ctk.CTkLabel(
            right_area,
            text="就绪 | 数据库: api_ledger.db",
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            anchor="w",
            fg_color="transparent",
        )
        self.status_bar.pack(fill="x", padx=12, pady=(0, 6))

        # ── 初始加载 ─────────────────────────
        self.after(100, self._initial_load)

    def _initial_load(self):
        """启动后自动加载数据并刷新下拉选项"""
        self.filter_panel.refresh_options()
        self._refresh_all()

    def _on_filters_applied(self, filters: Dict[str, Any]):
        """筛选条件变更回调"""
        self._current_filters = filters
        self._refresh_all()

    def _on_filters_reset(self):
        """重置筛选回调"""
        self._current_filters = {}
        self._refresh_all()

    def _refresh_all(self):
        """刷新表格 & 图表"""
        filters = self._current_filters.copy()
        self.table_panel.refresh(filters if filters else None)
        self.chart_panel.refresh(filters if filters else None)

        # 更新状态栏
        count_info = ""
        if filters:
            # 从 table_panel 获取当前总数
            count_info = f" | 筛选已应用"
        self.status_bar.configure(text=f"数据库: api_ledger.db{count_info}")


def run_ui(db: Database):
    """启动 UI (非阻塞调用, 阻塞至窗口关闭)"""
    app = App(db)
    app.mainloop()
