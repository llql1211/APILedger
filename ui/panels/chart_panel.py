"""
APILedger - 图表面板 (折线 / 柱状 / 饼图)

包含三个子面板, 通过 Tab 切换。所有图表联动筛选条件。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.db import Database
from ui.theme import FONT_SIZES, CHART_COLORS

# matplotlib 中文字体
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


class _BaseChart(ctk.CTkFrame):
    """图表基类"""

    def __init__(self, master, db: Database, title: str, **kwargs):
        super().__init__(master, **kwargs)
        self.db = db
        self._filters: Dict[str, Any] = {}

        # 顶部控件栏
        toolbar_frame = ctk.CTkFrame(self, height=40, fg_color="transparent")
        toolbar_frame.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(toolbar_frame, text=title,
                     font=("Microsoft YaHei", FONT_SIZES["subtitle"], "bold")
                     ).pack(side="left")

        self._build_toolbar(toolbar_frame)

        # matplotlib Figure
        self.figure = Figure(figsize=(8, 4.5), dpi=100, constrained_layout=True)
        self.ax = self.figure.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 简单导航栏 (保存、缩放)
        nav_toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        nav_toolbar.pack(fill="x", padx=8, pady=(0, 4))

    def _build_toolbar(self, frame: ctk.CTkFrame):
        """子类可重写以添加切换控件"""
        pass

    def refresh(self, filters: Dict[str, Any] = None):
        """刷新图表, 子类必须实现"""
        self._filters = filters or {}
        self._plot()

    def _plot(self):
        """子类实现具体绘图逻辑"""
        raise NotImplementedError


class _TrendChart(_BaseChart):
    """折线图: 按时间汇总费用/tokens/调用量趋势, 可切换聚合粒度"""

    def _build_toolbar(self, frame: ctk.CTkFrame):
        # ⚡目前只绘制费用, 提供粒度切换: 日/周/月
        self._granularity = ctk.StringVar(value="日")
        seg = ctk.CTkSegmentedButton(
            frame, values=["日", "周", "月"],
            variable=self._granularity,
            command=lambda v: self._plot(),
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            width=200,
        )
        seg.pack(side="right")

        self._metric = ctk.StringVar(value="费用")
        metric_seg = ctk.CTkSegmentedButton(
            frame, values=["费用", "Tokens", "调用量"],
            variable=self._metric,
            command=lambda v: self._plot(),
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            width=300,
        )
        metric_seg.pack(side="right", padx=(0, 8))

    def _plot(self):
        self.ax.clear()

        # 指标映射
        metric_map = {"费用": "cost", "Tokens": "tokens", "调用量": "call_volume"}
        value_field = metric_map.get(self._metric.get(), "cost")

        # 粒度映射
        gran = self._granularity.get()
        if gran == "日":
            group_by = "date(bill_start)"
        elif gran == "周":
            group_by = "strftime('%Y-%W', bill_start)"
        else:  # 月
            group_by = "strftime('%Y-%m', bill_start)"

        try:
            data = self.db.aggregate_by_date(
                value_field=value_field,
                group_by=group_by,
                filters=self._filters if self._filters else None,
            )
        except Exception:
            data = []

        if not data:
            self.ax.set_title("暂无数据")
            self.canvas.draw()
            return

        labels = [r["period"] for r in data]
        values = [float(r["total"]) for r in data]

        self.ax.plot(labels, values, marker="o", color=CHART_COLORS[0], linewidth=2)
        self.ax.fill_between(range(len(labels)), values, alpha=0.15, color=CHART_COLORS[0])

        # 格式化 Y 轴
        if value_field == "cost":
            self.ax.set_ylabel("费用 (¥)")
        elif value_field == "tokens":
            self.ax.set_ylabel("Tokens")
        else:
            self.ax.set_ylabel("调用量")

        self.ax.set_xlabel("时间")
        self.ax.set_title(f"{self._metric.get()}趋势 ({gran})")
        self.ax.tick_params(axis="x", rotation=45)

        # 仅显示部分 X 轴标签避免拥挤
        step = max(1, len(labels) // 15)
        for i, label in enumerate(self.ax.get_xticklabels()):
            label.set_visible(i % step == 0)

        self.canvas.draw()


class _CompareChart(_BaseChart):
    """柱状图: 各平台/模型/项目费用或tokens Top-N 对比"""

    def _build_toolbar(self, frame: ctk.CTkFrame):
        self._group_field = ctk.StringVar(value="平台")
        seg = ctk.CTkSegmentedButton(
            frame, values=["平台", "模型", "项目", "类型"],
            variable=self._group_field,
            command=lambda v: self._plot(),
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            width=400,
        )
        seg.pack(side="right")

        self._metric = ctk.StringVar(value="费用")
        metric_seg = ctk.CTkSegmentedButton(
            frame, values=["费用", "Tokens", "调用量"],
            variable=self._metric,
            command=lambda v: self._plot(),
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            width=300,
        )
        metric_seg.pack(side="right", padx=(0, 8))

    def _plot(self):
        self.ax.clear()

        field_map = {"平台": "platform", "模型": "model", "项目": "project", "类型": "type"}
        metric_map = {"费用": "cost", "Tokens": "tokens", "调用量": "call_volume"}

        group_field = field_map.get(self._group_field.get(), "platform")
        value_field = metric_map.get(self._metric.get(), "cost")

        try:
            data = self.db.aggregate_by_field(
                value_field=value_field,
                group_field=group_field,
                filters=self._filters if self._filters else None,
                top_n=12,
            )
        except Exception:
            data = []

        if not data:
            self.ax.set_title("暂无数据")
            self.canvas.draw()
            return

        names = [r["name"] or "(空)" for r in data]
        values = [float(r["total"]) for r in data]

        bars = self.ax.barh(names, values, color=CHART_COLORS[:len(names)])
        self.ax.set_xlabel("费用 (¥)" if value_field == "cost" else value_field)
        self.ax.set_title(f"各{self._group_field.get()}{self._metric.get()}对比 (Top-{len(names)})")

        # 数值标注
        for bar, v in zip(bars, values):
            if v > 0:
                self.ax.text(v * 1.01, bar.get_y() + bar.get_height() / 2,
                             f"{v:,.2f}" if value_field == "cost" else f"{v:,.0f}",
                             va="center", fontsize=9)

        self.canvas.draw()


class _PieChart(_BaseChart):
    """饼图: 费用在各平台/模型/类型的占比"""

    def _build_toolbar(self, frame: ctk.CTkFrame):
        self._group_field = ctk.StringVar(value="平台")
        seg = ctk.CTkSegmentedButton(
            frame, values=["平台", "模型", "项目", "类型"],
            variable=self._group_field,
            command=lambda v: self._plot(),
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            width=400,
        )
        seg.pack(side="right")

    def _plot(self):
        self.ax.clear()

        field_map = {"平台": "platform", "模型": "model", "项目": "project", "类型": "type"}
        group_field = field_map.get(self._group_field.get(), "platform")

        try:
            data = self.db.aggregate_by_field(
                value_field="cost",
                group_field=group_field,
                filters=self._filters if self._filters else None,
                top_n=8,
            )
        except Exception:
            data = []

        if not data:
            self.ax.set_title("暂无数据")
            self.canvas.draw()
            return

        # 前 top_n 项 + "其他"
        names = [r["name"] or "(空)" for r in data]
        values = [float(r["total"]) for r in data]

        # 合并小于5%的为"其他"
        total = sum(values)
        main_names, main_values = [], []
        other_val = 0.0
        for n, v in zip(names, values):
            if v / total >= 0.03:
                main_names.append(n)
                main_values.append(v)
            else:
                other_val += v
        if other_val > 0:
            main_names.append("其他")
            main_values.append(other_val)

        colors = CHART_COLORS[:len(main_names)]
        wedges, texts, autotexts = self.ax.pie(
            main_values, labels=main_names, autopct="%1.1f%%",
            colors=colors, startangle=90,
            textprops={"fontsize": 9},
        )

        self.ax.set_title(f"费用分布 — 按{self._group_field.get()}")
        self.canvas.draw()


class ChartPanel(ctk.CTkTabview):
    """Tab 图表面板, 包含三个子图表"""

    def __init__(self, master, db: Database, **kwargs):
        super().__init__(master, **kwargs)

        self._filters: Dict[str, Any] = {}

        # 添加 Tab
        self.add("📈 趋势")
        self.add("📊 对比")
        self.add("🥧 分布")

        # 创建子图表
        self.trend = _TrendChart(self.tab("📈 趋势"), db, "")
        self.trend.pack(fill="both", expand=True)

        self.compare = _CompareChart(self.tab("📊 对比"), db, "")
        self.compare.pack(fill="both", expand=True)

        self.pie = _PieChart(self.tab("🥧 分布"), db, "")
        self.pie.pack(fill="both", expand=True)

    def refresh(self, filters: Dict[str, Any] = None):
        """刷新全部图表"""
        self._filters = filters or {}
        self.trend.refresh(self._filters)
        self.compare.refresh(self._filters)
        self.pie.refresh(self._filters)
