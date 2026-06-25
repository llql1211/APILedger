"""
APILedger - CustomTkinter 主窗口
    工具栏 → Tab 页 (仪表盘 / 数据表格 / 图表分析) → 状态栏
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

import customtkinter as ctk
from tkinter import ttk

from core.db import Database
from core.importer import (
    scan_input_files,
    parse_records_from_file,
    process_single_file,
    commit_import,
    INPUT_DIR,
)
from ui.theme import (
    setup_appearance,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    FONT_SIZES,
    CHART_COLORS,
)
from ui.panels.filter_panel import FilterPanel
from ui.panels.table_panel import TablePanel
from ui.panels.chart_panel import ChartPanel
from ui.conflict_dialog import ConflictDialog

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


class _SummaryCard(ctk.CTkFrame):
    """汇总卡片: 图标 + 数值 + 标签"""

    def __init__(self, master, label: str, value: str, icon: str = "", **kwargs):
        super().__init__(master, corner_radius=8, border_width=1, **kwargs)
        self.configure(height=90)
        self.pack_propagate(False)

        ctk.CTkLabel(self, text=icon, font=("Microsoft YaHei", 24)).pack(pady=(8, 0))
        self.value_label = ctk.CTkLabel(
            self, text=value,
            font=("Microsoft YaHei", 22, "bold"),
        )
        self.value_label.pack(pady=(0, 0))
        ctk.CTkLabel(
            self, text=label,
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            text_color="gray60",
        ).pack(pady=(0, 6))

    def set(self, value: str):
        self.value_label.configure(text=value)


class _DashboardTab(ctk.CTkFrame):
    """仪表盘: 汇总卡片 + 迷你图表 + 近期记录表格"""

    def __init__(self, master, db: Database, **kwargs):
        super().__init__(master, **kwargs)
        self.db = db
        self._data: List[Dict[str, Any]] = []

        # ── 汇总卡片行 ───────────────────────
        card_frame = ctk.CTkFrame(self, fg_color="transparent")
        card_frame.pack(fill="x", padx=16, pady=(12, 8))

        card_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.card_records = _SummaryCard(card_frame, "总记录数", "0", "📋")
        self.card_records.grid(row=0, column=0, padx=4, sticky="ew")

        self.card_tokens = _SummaryCard(card_frame, "总 Tokens", "0", "🔤")
        self.card_tokens.grid(row=0, column=1, padx=4, sticky="ew")

        self.card_cost = _SummaryCard(card_frame, "总费用", "¥0.00", "💰")
        self.card_cost.grid(row=0, column=2, padx=4, sticky="ew")

        self.card_apis = _SummaryCard(card_frame, "API 种类", "0", "🔌")
        self.card_apis.grid(row=0, column=3, padx=4, sticky="ew")

        # ── 迷你图表区 ───────────────────────
        chart_row = ctk.CTkFrame(self, fg_color="transparent")
        chart_row.pack(fill="both", expand=True, padx=16, pady=(4, 8))
        chart_row.grid_columnconfigure((0, 1), weight=1)
        chart_row.grid_rowconfigure(0, weight=1)

        # 左侧: 项目分布 (饼图)
        pie_frame = ctk.CTkFrame(chart_row, corner_radius=8, border_width=1)
        pie_frame.grid(row=0, column=0, padx=(0, 4), sticky="nsew")
        ctk.CTkLabel(
            pie_frame, text="项目费用分布",
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
            anchor="w",
        ).pack(padx=10, pady=(6, 0), fill="x")

        self.pie_figure = Figure(figsize=(4, 2.6), dpi=100, constrained_layout=True)
        self.pie_ax = self.pie_figure.add_subplot(111)
        self.pie_canvas = FigureCanvasTkAgg(self.pie_figure, master=pie_frame)
        self.pie_canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        # 右侧: 费用趋势 (折线)
        trend_frame = ctk.CTkFrame(chart_row, corner_radius=8, border_width=1)
        trend_frame.grid(row=0, column=1, padx=(4, 0), sticky="nsew")
        ctk.CTkLabel(
            trend_frame, text="费用趋势 (近30天)",
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
            anchor="w",
        ).pack(padx=10, pady=(6, 0), fill="x")

        self.trend_figure = Figure(figsize=(4, 2.6), dpi=100, constrained_layout=True)
        self.trend_ax = self.trend_figure.add_subplot(111)
        self.trend_canvas = FigureCanvasTkAgg(self.trend_figure, master=trend_frame)
        self.trend_canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        # ── 近期记录表格 (缩略) ─────────────
        table_section = ctk.CTkFrame(self, corner_radius=8, border_width=1)
        table_section.pack(fill="both", padx=16, pady=(0, 12), expand=False)

        ctk.CTkLabel(
            table_section, text="近期记录 (最新 20 条)",
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
            anchor="w",
        ).pack(padx=10, pady=(6, 2), fill="x")

        tree_frame = ctk.CTkFrame(table_section, height=180)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        tree_frame.pack_propagate(False)

        self.recent_tree = ttk.Treeview(
            tree_frame,
            columns=("time", "platform", "project", "model", "cost"),
            show="headings",
            height=6,
        )
        self.recent_tree.heading("time", text="时间")
        self.recent_tree.heading("platform", text="平台")
        self.recent_tree.heading("project", text="项目")
        self.recent_tree.heading("model", text="模型")
        self.recent_tree.heading("cost", text="金额")
        self.recent_tree.column("time", width=130, anchor="w")
        self.recent_tree.column("platform", width=80, anchor="w")
        self.recent_tree.column("project", width=100, anchor="w")
        self.recent_tree.column("model", width=150, anchor="w")
        self.recent_tree.column("cost", width=80, anchor="e")

        v_s = ttk.Scrollbar(tree_frame, orient="vertical", command=self.recent_tree.yview)
        self.recent_tree.configure(yscrollcommand=v_s.set)
        self.recent_tree.grid(row=0, column=0, sticky="nsew")
        v_s.grid(row=0, column=1, sticky="ns")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Dashboard.Treeview", font=("Microsoft YaHei", FONT_SIZES["small"]), rowheight=24)

    def refresh(self):
        """刷新仪表盘数据"""
        try:
            records = self.db.get_all(order_by="bill_start DESC")
        except Exception:
            records = []
        self._data = records

        # ── 汇总卡片 ────────────────────────
        total_records = len(records)
        total_tokens = sum(int(r.get("tokens", 0) or 0) for r in records)
        total_cost = sum(float(r.get("cost", 0.0) or 0.0) for r in records)
        api_set = set()
        for r in records:
            m = r.get("model", "")
            if m:
                api_set.add(m)

        self.card_records.set(f"{total_records:,}")
        self.card_tokens.set(f"{total_tokens:,}")
        self.card_cost.set(f"¥{total_cost:,.2f}")
        self.card_apis.set(str(len(api_set)))

        # ── 饼图: 项目费用分布 ──────────────
        self.pie_ax.clear()
        project_costs: Dict[str, float] = {}
        for r in records:
            proj = r.get("project", "") or "(无)"
            project_costs[proj] = project_costs.get(proj, 0.0) + float(r.get("cost", 0.0) or 0.0)
        if project_costs:
            sorted_items = sorted(project_costs.items(), key=lambda x: -x[1])
            names, vals = zip(*sorted_items[:6])
            other = sum(v for _, v in sorted_items[6:])
            if other > 0:
                names = list(names) + ["其他"]
                vals = list(vals) + [other]
            colors = CHART_COLORS[:len(names)]
            self.pie_ax.pie(vals, labels=names, autopct="%1.1f%%",
                            colors=colors, startangle=90,
                            textprops={"fontsize": 8})
            self.pie_ax.set_title("", fontsize=10)
        else:
            self.pie_ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=12)
        self.pie_canvas.draw()

        # ── 折线图: 近30天费用趋势 ──────────
        self.trend_ax.clear()
        daily: Dict[str, float] = {}
        for r in records:
            d = str(r.get("bill_start", ""))[:10]
            if d:
                daily[d] = daily.get(d, 0.0) + float(r.get("cost", 0.0) or 0.0)
        if daily:
            sorted_days = sorted(daily.items())[-30:]
            days, costs = zip(*sorted_days) if sorted_days else ([], [])
            if days:
                self.trend_ax.plot(days, costs, color=CHART_COLORS[0], linewidth=1.5)
                self.trend_ax.fill_between(range(len(days)), costs, alpha=0.12, color=CHART_COLORS[0])
                self.trend_ax.tick_params(axis="x", rotation=45, labelsize=7)
                step = max(1, len(days) // 8)
                for i, label in enumerate(self.trend_ax.get_xticklabels()):
                    label.set_visible(i % step == 0)
                self.trend_ax.set_title("", fontsize=10)
        else:
            self.trend_ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=12)
        self.trend_canvas.draw()

        # ── 近期记录表格 ────────────────────
        self.recent_tree.delete(*self.recent_tree.get_children())
        recent = records[:20]
        for r in recent:
            self.recent_tree.insert("", "end", values=(
                str(r.get("bill_start", ""))[:16],
                r.get("platform", ""),
                r.get("project", ""),
                r.get("model", ""),
                f"¥{float(r.get('cost',0) or 0):.4f}",
            ))


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

        # ── 顶部工具栏 ───────────────────────
        toolbar = ctk.CTkFrame(self.main_container, height=48, corner_radius=0)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        ctk.CTkLabel(
            toolbar, text="  APILedger",
            font=("Microsoft YaHei", FONT_SIZES["subtitle"], "bold"),
        ).pack(side="left", padx=12)

        self.import_btn = ctk.CTkButton(
            toolbar, text="📥 导入账单",
            command=self._on_import_clicked,
            height=32,
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
        )
        self.import_btn.pack(side="left", padx=(0, 6))

        self.import_status = ctk.CTkLabel(
            toolbar, text="",
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            anchor="w",
        )
        self.import_status.pack(side="left", padx=(4, 0))

        # 筛选刷新按钮 (右侧)
        self.refresh_btn = ctk.CTkButton(
            toolbar, text="🔄 刷新", command=self._refresh_all,
            height=32, width=80,
            font=("Microsoft YaHei", FONT_SIZES["small"]),
        )
        self.refresh_btn.pack(side="right", padx=12)

        # ── 左侧筛选面板 ─────────────────────
        self.filter_panel = FilterPanel(
            self.main_container,
            db=self.db,
            on_apply=self._on_filters_applied,
            on_reset=self._on_filters_reset,
        )
        self.filter_panel.pack(side="left", fill="y")

        # ── 右侧内容区 ───────────────────────
        right_area = ctk.CTkFrame(self.main_container, corner_radius=0)
        right_area.pack(side="left", fill="both", expand=True)

        # Tab 页
        self.tab_view = ctk.CTkTabview(right_area, corner_radius=8)
        self.tab_view.pack(fill="both", expand=True, padx=8, pady=8)

        # 仪表盘 Tab
        self.tab_view.add("📊 仪表盘")
        self.dashboard = _DashboardTab(self.tab_view.tab("📊 仪表盘"), db=self.db)
        self.dashboard.pack(fill="both", expand=True)

        # 数据表格 Tab
        self.tab_view.add("📋 数据表格")
        self.table_panel = TablePanel(self.tab_view.tab("📋 数据表格"), db=self.db)
        self.table_panel.pack(fill="both", expand=True)

        # 图表分析 Tab
        self.tab_view.add("📈 图表分析")
        self.chart_panel = ChartPanel(self.tab_view.tab("📈 图表分析"), db=self.db)
        self.chart_panel.pack(fill="both", expand=True)

        # ── 底部状态栏 ───────────────────────
        self.status_bar = ctk.CTkLabel(
            right_area,
            text="就绪",
            font=("Microsoft YaHei", FONT_SIZES["small"]),
            anchor="w",
        )
        self.status_bar.pack(fill="x", padx=12, pady=(0, 6))

        # ── 初始加载 ─────────────────────────
        self.after(100, self._initial_load)

    def _initial_load(self):
        self.filter_panel.refresh_options()
        self._refresh_all()

    def _on_filters_applied(self, filters: Dict[str, Any]):
        self._current_filters = filters
        self._refresh_all()

    def _on_filters_reset(self):
        self._current_filters = {}
        self._refresh_all()

    def _refresh_all(self):
        """刷新仪表盘、表格、图表"""
        filters = self._current_filters.copy() or None
        self.dashboard.refresh()
        self.table_panel.refresh(filters)
        self.chart_panel.refresh(filters)
        self.filter_panel.refresh_options()

        count_str = f" | 筛选已应用" if self._current_filters else ""
        self.status_bar.configure(text=f"数据库: api_ledger.db{count_str}")

    # ═══════════════════════════════════════════════
    # 导入流程
    # ═══════════════════════════════════════════════

    def _on_import_clicked(self):
        """点击导入按钮"""
        os.makedirs(INPUT_DIR, exist_ok=True)
        files = scan_input_files()
        if not files:
            self.import_status.configure(text="input/ 中无待导入文件", text_color="gray60")
            self.after(3000, lambda: self.import_status.configure(text=""))
            return

        self.import_btn.configure(state="disabled", text="⏳ 导入中...")
        self.import_status.configure(text=f"处理 {len(files)} 个文件...", text_color="")

        # 后台线程执行导入 (避免 UI 卡顿)
        t = threading.Thread(target=self._run_import_thread, args=(files,), daemon=True)
        t.start()

    def _run_import_thread(self, files: list):
        """后台线程: 逐文件检测 → 有冲突弹窗 → 写入 → 归档"""
        results: list = []
        any_conflict = False

        total = len(files)
        for i, fpath in enumerate(files):
            # 推送进度到状态栏
            fname = os.path.basename(fpath)
            self.after(0, lambda n=fname, idx=i, t=total: self.import_status.configure(
                text=f"处理中 ({idx+1}/{t}): {n}", text_color=""
            ))

            try:
                res = process_single_file(self.db, fpath)
                results.append((fpath, res))
                if res.get("conflicts"):
                    any_conflict = True
            except Exception as e:
                results.append((fpath, {"filename": os.path.basename(fpath), "error": str(e)}))

        # 在主线程处理冲突和写入
        self.after(0, self._handle_import_results, results, any_conflict)

    def _handle_import_results(self, results: list, any_conflict: bool):
        """在主线程中处理导入结果"""
        total_new = 0
        total_same = 0
        total_conflicts = 0
        error_files = []

        for fpath, res in results:
            if "error" in res:
                error_files.append(f"{res['filename']}: {res['error']}")
                continue

            total_new += res.get("new_count", 0)
            total_same += res.get("same_count", 0)
            conflicts = res.get("conflicts", [])
            total_conflicts += len(conflicts)

        # 全部文件解析失败
        if error_files and total_new == 0 and total_same == 0 and total_conflicts == 0:
            err_str = "; ".join(error_files[:3])
            if len(error_files) > 3:
                err_str += f" ... 等共 {len(error_files)} 个"
            self._finish_import(f"导入失败: {err_str}", "#c0392b", error_files)
            return

        # ── 冲突处理 ────────────────────────────
        skip_all_conflict_files = False

        if total_conflicts > 0:
            # 先处理无冲突的文件 (直接写入)
            for fpath, res in results:
                if "error" in res or res.get("conflicts"):
                    continue
                written = commit_import(self.db, fpath, res, force_overwrite_conflicts=True)

            # 再处理有冲突的文件 (弹窗逐一确认)
            for fpath, res in results:
                if "error" in res or not res.get("conflicts"):
                    continue
                # 弹窗 (主线程)
                dialog = ConflictDialog(self, res["conflicts"])
                decision = dialog.result

                if decision is None:
                    # 用户取消了整个导入
                    self._finish_import("已取消", "gray60", error_files)
                    return
                elif decision is True:
                    # 覆盖
                    written = commit_import(self.db, fpath, res, force_overwrite_conflicts=True)
                    total_new += written  # 这些是覆盖的冲突行
                else:
                    # 跳过冲突行, 只写入 new
                    written = commit_import(self.db, fpath, res, force_overwrite_conflicts=False)
        else:
            # 无冲突, 直接全部写入并归档
            for fpath, res in results:
                if "error" in res:
                    continue
                written = commit_import(self.db, fpath, res, force_overwrite_conflicts=True)

        # ── 完成 ────────────────────────────────
        total_processed = len([r for r in results if "error" not in r])
        msg_parts = []
        if total_new > 0:
            msg_parts.append(f"新增/更新 {total_new} 条")
        if total_same > 0:
            msg_parts.append(f"{total_same} 条无变化已跳过")
        if total_conflicts > 0:
            msg_parts.append(f"{total_conflicts} 条冲突已覆盖")
        msg = f"完成 {total_processed} 个文件 | {' | '.join(msg_parts)}" if msg_parts else f"完成 {total_processed}"

        self._finish_import(msg, "#2fa572", error_files)

    def _finish_import(self, msg: str, color: str, error_files: list = None):
        """导入结束, 恢复按钮状态并刷新"""
        self.import_btn.configure(state="normal", text="📥 导入账单")

        if error_files:
            err_str = "; ".join(error_files[:3])
            if len(error_files) > 3:
                err_str += f" ... 等共 {len(error_files)} 个"
            self.import_status.configure(text=f"{msg} | 失败: {err_str}", text_color="#c0392b")
        else:
            self.import_status.configure(text=msg, text_color=color)

        self._refresh_all()

        # 3秒后清除状态文字
        self.after(3000, lambda: self.import_status.configure(text=""))


def run_ui(db: Database):
    app = App(db)
    app.mainloop()