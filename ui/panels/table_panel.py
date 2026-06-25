"""
APILedger - 数据表格面板 (基于 ttk.Treeview 嵌入 CustomTkinter)
"""

from __future__ import annotations

from typing import Any, Dict, List

import customtkinter as ctk
from tkinter import ttk

from core.db import Database
from ui.theme import FONT_SIZES, CHART_COLORS

# 表格显示的列
DISPLAY_COLUMNS = [
    ("bill_start",    "开始时间"),
    ("bill_end",      "截止时间"),
    ("platform",      "平台"),
    ("project",       "项目"),
    ("model",         "模型"),
    ("type",          "类型"),
    ("tokens",        "Tokens"),
    ("call_volume",   "调用量"),
    ("cost",          "金额"),
    ("unit_price",    "单价/M"),
    ("source_file",   "来源文件"),
]

COL_KEYS = [c[0] for c in DISPLAY_COLUMNS]
COL_LABELS = [c[1] for c in DISPLAY_COLUMNS]

# 列宽 (px)
COL_WIDTHS = {
    "bill_start":  140,
    "bill_end":    140,
    "platform":    100,
    "project":     120,
    "model":       180,
    "type":        100,
    "tokens":       90,
    "call_volume":  90,
    "cost":         90,
    "unit_price":   90,
    "source_file": 150,
}


class TablePanel(ctk.CTkFrame):
    """数据表格面板"""

    def __init__(self, master, db: Database, **kwargs):
        super().__init__(master, **kwargs)
        self.db = db
        self._data: List[Dict[str, Any]] = []

        # ── 顶部统计栏 ──────────────────────
        self.stats_label = ctk.CTkLabel(
            self, text="共 0 条记录 | 总费用 ¥0.00",
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            anchor="w",
        )
        self.stats_label.pack(padx=12, pady=(8, 4), fill="x")

        # ── 表格容器 ────────────────────────
        # 用 CTkFrame 包裹 Treeview, 使样式统一
        tree_frame = ctk.CTkFrame(self)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Treeview
        self.tree = ttk.Treeview(
            tree_frame,
            columns=COL_KEYS,
            show="headings",
            selectmode="browse",
            height=20,
        )

        # 设置列
        for key, label in DISPLAY_COLUMNS:
            width = COL_WIDTHS.get(key, 120)
            self.tree.heading(key, text=label, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, minwidth=80, anchor="e" if key in ("tokens", "call_volume", "cost") else "w")

        # 滚动条
        v_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        h_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # ── Treeview 样式 ──────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                        font=("Microsoft YaHei", FONT_SIZES["small"]),
                        rowheight=28)
        style.configure("Treeview.Heading",
                        font=("Microsoft YaHei", FONT_SIZES["body"], "bold"))

    def refresh(self, filters: Dict[str, Any] = None):
        """从数据库加载数据并刷新表格"""
        if filters:
            records, total = self.db.query(**filters)
        else:
            records = self.db.get_all()
            total = len(records)

        self._data = records

        # 清空并重新填充
        self.tree.delete(*self.tree.get_children())
        total_cost = 0.0
        for row in records:
            values = []
            for key in COL_KEYS:
                v = row.get(key, "")
                if key == "cost":
                    v = f"{float(v or 0):.4f}"
                    total_cost += float(row.get("cost", 0) or 0)
                elif key in ("tokens", "call_volume"):
                    v = f"{int(v or 0):,}"
                elif key == "unit_price":
                    up = float(v or 0)
                    v = f"{up:.2f}" if up > 0 else ""
                values.append(v)
            self.tree.insert("", "end", values=values)

        self.stats_label.configure(
            text=f"共 {total} 条记录 | 总费用 ¥{total_cost:.2f}"
        )

    def _sort_by(self, col_key: str):
        """按列排序 (升/降切换)"""
        if not self._data:
            return

        # 排序
        reverse = False
        if hasattr(self, "_sort_col") and self._sort_col == col_key:
            reverse = not self._sort_reverse
        self._sort_col = col_key
        self._sort_reverse = reverse

        def sort_key(row):
            v = row.get(col_key, "")
            if col_key in ("tokens", "call_volume"):
                try:
                    return int(v or 0)
                except ValueError:
                    return 0
            elif col_key in ("cost", "unit_price"):
                try:
                    return float(v or 0)
                except ValueError:
                    return 0.0
            return str(v)

        self._data.sort(key=sort_key, reverse=reverse)

        self.tree.delete(*self.tree.get_children())
        for row in self._data:
            values = []
            for key in COL_KEYS:
                v = row.get(key, "")
                if key == "cost":
                    v = f"{float(v or 0):.4f}"
                elif key in ("tokens", "call_volume"):
                    v = f"{int(v or 0):,}"
                elif key == "unit_price":
                    up = float(v or 0)
                    v = f"{up:.2f}" if up > 0 else ""
                values.append(v)
            self.tree.insert("", "end", values=values)
