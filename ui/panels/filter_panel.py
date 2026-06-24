"""
APILedger - 筛选面板 (日期范围 / 平台 / 项目 / 模型 / 类型 / 搜索)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from core.db import Database
from ui.theme import FILTER_PANEL_WIDTH, FONT_SIZES

# 默认日期格式
_DATE_FMT = "%Y-%m-%d"


class FilterPanel(ctk.CTkFrame):
    """左侧筛选面板"""

    def __init__(
        self,
        master,
        db: Database,
        on_apply: Callable[[Dict[str, Any]], None],
        on_reset: Callable[[], None],
        **kwargs,
    ):
        super().__init__(master, width=FILTER_PANEL_WIDTH, corner_radius=0, **kwargs)
        self.db = db
        self.on_apply = on_apply
        self.on_reset = on_reset

        self.pack_propagate(False)

        # ── 标题 ────────────────────────────
        title = ctk.CTkLabel(
            self, text="🔍 筛选条件",
            font=("Microsoft YaHei", FONT_SIZES["subtitle"], "bold"),
            anchor="w",
        )
        title.pack(padx=16, pady=(16, 8), fill="x")

        # ── 日期范围 ─────────────────────────
        date_frame = ctk.CTkFrame(self, fg_color="transparent")
        date_frame.pack(padx=16, pady=4, fill="x")

        ctk.CTkLabel(date_frame, text="日期范围",
                     font=("Microsoft YaHei", FONT_SIZES["body"])).pack(anchor="w")

        self.start_entry = ctk.CTkEntry(date_frame, placeholder_text="开始 (YYYY-MM-DD)",
                                        height=32)
        self.start_entry.pack(fill="x", pady=(2, 2))

        self.end_entry = ctk.CTkEntry(date_frame, placeholder_text="结束 (YYYY-MM-DD)",
                                      height=32)
        self.end_entry.pack(fill="x", pady=(2, 6))

        # ── 平台 ────────────────────────────
        self.platform_var = ctk.StringVar(value="")
        self.platform_menu = ctk.CTkOptionMenu(
            self, variable=self.platform_var,
            values=["全部"], command=self._on_option_change,
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            dropdown_font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        self._add_labeled_dropdown("平台", self.platform_menu)

        # ── 项目 ────────────────────────────
        self.project_var = ctk.StringVar(value="")
        self.project_menu = ctk.CTkOptionMenu(
            self, variable=self.project_var,
            values=["全部"], command=self._on_option_change,
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            dropdown_font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        self._add_labeled_dropdown("项目名", self.project_menu)

        # ── 模型 ────────────────────────────
        self.model_var = ctk.StringVar(value="")
        self.model_menu = ctk.CTkOptionMenu(
            self, variable=self.model_var,
            values=["全部"], command=self._on_option_change,
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            dropdown_font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        self._add_labeled_dropdown("模型名称", self.model_menu)

        # ── 类型 ────────────────────────────
        self.type_var = ctk.StringVar(value="")
        self.type_menu = ctk.CTkOptionMenu(
            self, variable=self.type_var,
            values=["全部"], command=self._on_option_change,
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            dropdown_font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        self._add_labeled_dropdown("类型", self.type_menu)

        # ── 搜索框 ─────────────────────────
        ctk.CTkLabel(self, text="关键词搜索",
                     font=("Microsoft YaHei", FONT_SIZES["body"]),
                     anchor="w").pack(padx=16, pady=(12, 2), fill="x")
        self.search_entry = ctk.CTkEntry(self, placeholder_text="搜索模型/项目/平台...",
                                         height=34)
        self.search_entry.pack(padx=16, pady=(0, 12), fill="x")
        self.search_entry.bind("<Return>", lambda e: self._apply())

        # ── 按钮 ────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(padx=16, pady=(4, 8), fill="x")

        self.apply_btn = ctk.CTkButton(
            btn_frame, text="应用筛选", command=self._apply,
            height=36,
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
        )
        self.apply_btn.pack(fill="x", pady=(0, 6))

        self.reset_btn = ctk.CTkButton(
            btn_frame, text="重置", command=self._reset,
            height=36, fg_color="gray60", hover_color="gray40",
            font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        self.reset_btn.pack(fill="x")

    def _add_labeled_dropdown(self, label: str, menu: ctk.CTkOptionMenu):
        """添加带标签的下拉框"""
        ctk.CTkLabel(self, text=label,
                     font=("Microsoft YaHei", FONT_SIZES["body"]),
                     anchor="w").pack(padx=16, pady=(10, 2), fill="x")
        menu.pack(padx=16, pady=(0, 2), fill="x")

    def _on_option_change(self, _=None):
        """下拉选中后自动应用筛选"""
        self._apply()

    def _apply(self):
        """收集筛选条件并回调"""
        filters: Dict[str, Any] = {}

        start = self.start_entry.get().strip()
        if start:
            filters["bill_start"] = start

        end = self.end_entry.get().strip()
        if end:
            filters["bill_end"] = end

        # 下拉选 "全部" 时不传
        platform = self.platform_var.get()
        if platform and platform != "全部":
            filters["platform"] = platform

        project = self.project_var.get()
        if project and project != "全部":
            filters["project"] = project

        model = self.model_var.get()
        if model and model != "全部":
            filters["model"] = model

        type_ = self.type_var.get()
        if type_ and type_ != "全部":
            filters["type_"] = type_

        keyword = self.search_entry.get().strip()
        if keyword:
            filters["keyword"] = keyword

        self.on_apply(filters)

    def _reset(self):
        """重置全部筛选条件"""
        self.start_entry.delete(0, "end")
        self.end_entry.delete(0, "end")
        self.platform_var.set("全部")
        self.project_var.set("全部")
        self.model_var.set("全部")
        self.type_var.set("全部")
        self.search_entry.delete(0, "end")
        self.on_reset()

    def refresh_options(self):
        """从数据库刷新下拉选项"""
        try:
            platforms = self.db.get_distinct("platform")
            projects = self.db.get_distinct("project")
            models = self.db.get_distinct("model")
            types = self.db.get_distinct("type")
        except Exception:
            # 数据库可能还未初始化
            platforms = projects = models = types = []

        self._update_menu(self.platform_menu, self.platform_var, platforms)
        self._update_menu(self.project_menu, self.project_var, projects)
        self._update_menu(self.model_menu, self.model_var, models)
        self._update_menu(self.type_menu, self.type_var, types)

    @staticmethod
    def _update_menu(menu: ctk.CTkOptionMenu, var: ctk.StringVar, values: List[str]):
        current = var.get()
        all_values = ["全部"] + values

        # 保持选中项, 若不在新列表中则重置为 "全部"
        if current not in all_values:
            var.set("全部")
        menu.configure(values=all_values)
