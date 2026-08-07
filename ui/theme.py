"""
APILedger - 主题颜色 & 样式管理

提供：
  - 明/暗模式切换（持久化到 data/theme_mode.txt）
  - ttk.Treeview 明暗样式配置
  - matplotlib 明暗样式配置
  - 通用颜色 & 尺寸常量
"""

import os
from typing import Literal

import customtkinter as ctk

# ── 路径 ──────────────────────────────────────────
_THEME_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "theme_mode.txt",
)

# ── 颜色 ──────────────────────────────────────────
PRIMARY = "#1f538d"          # 主色调 (深蓝)
PRIMARY_HOVER = "#14375e"    # 主色调悬停
SECONDARY = "#2fa572"        # 辅色调 (绿色)

# 浅色
BG_LIGHT = "#f0f0f0"
CARD_BG = "#ffffff"
TEXT_PRIMARY = "#1a1a1a"
TEXT_SECONDARY = "#666666"
BORDER = "#d0d0d0"

# 深色
BG_DARK = "#2b2b2b"
CARD_BG_DARK = "#333333"
TEXT_PRIMARY_DARK = "#e0e0e0"
TEXT_SECONDARY_DARK = "#999999"
BORDER_DARK = "#555555"

# ── 图表颜色方案 ──────────────────────────────────
CHART_COLORS = [
    "#1f538d", "#2fa572", "#e8a838", "#c0392b",
    "#8e44ad", "#16a085", "#2980b9", "#d35400",
    "#27ae60", "#f39c12", "#7f8c8d", "#2c3e50",
]

# ── 字体 ──────────────────────────────────────────
FONT_FAMILY = "Microsoft YaHei"
FONT_SIZES = {
    "title": 18,
    "subtitle": 14,
    "body": 12,
    "small": 10,
}

# ── 窗口尺寸 ──────────────────────────────────────
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900
FILTER_PANEL_WIDTH = 260

# ═══════════════════════════════════════════════════
# 主题读写
# ═══════════════════════════════════════════════════

def _read_mode() -> Literal["light", "dark"]:
    try:
        with open(_THEME_FILE, "r") as f:
            mode = f.read().strip().lower()
        return mode if mode in ("light", "dark") else "light"
    except (FileNotFoundError, OSError):
        return "light"


def _write_mode(mode: Literal["light", "dark"]) -> None:
    os.makedirs(os.path.dirname(_THEME_FILE), exist_ok=True)
    with open(_THEME_FILE, "w") as f:
        f.write(mode)


def setup_appearance():
    """全局外观设置（应用启动时调用）"""
    mode = _read_mode()
    ctk.set_appearance_mode(mode)
    ctk.set_default_color_theme("blue")


def get_current_mode() -> Literal["light", "dark"]:
    """获取当前 CTk 外观模式"""
    return ctk.get_appearance_mode().lower()  # type: ignore[return-value]


def toggle_appearance() -> Literal["light", "dark"]:
    """切换明/暗模式并持久化"""
    new = "dark" if get_current_mode() == "light" else "light"
    ctk.set_appearance_mode(new)
    _write_mode(new)
    return new


# ═══════════════════════════════════════════════════
# ttk.Treeview 样式
# ═══════════════════════════════════════════════════

def configure_ttk_tree_style(mode: Literal["light", "dark"] = None):
    """设置 ttk.Treeview 的明暗样式，返回样式名称

    样式名遵循 ttk 点号继承约定 (X.Treeview 继承 Treeview 的 layout),
    同一名称重复配置会就地更新, 已使用该样式的 Treeview 自动生效。
    """
    from tkinter import ttk

    style = ttk.Style()
    style.theme_use("clam")

    if mode is None:
        mode = get_current_mode()

    if mode == "dark":
        name = "LedgerDark.Treeview"
        bg = "#2b2b2b"
        fg = "#e0e0e0"
        heading_bg = "#3c3c3c"
        heading_fg = "#e0e0e0"
        select_bg = "#1f538d"
        select_fg = "#ffffff"
    else:
        name = "LedgerLight.Treeview"
        bg = "#ffffff"
        fg = "#1a1a1a"
        heading_bg = "#f0f0f0"
        heading_fg = "#1a1a1a"
        select_bg = "#1f538d"
        select_fg = "#ffffff"

    style.configure(name,
                    background=bg,
                    foreground=fg,
                    fieldbackground=bg,
                    borderwidth=0,
                    font=(FONT_FAMILY, FONT_SIZES["small"]),
                    rowheight=28)
    style.map(name,
              background=[("selected", select_bg)],
              foreground=[("selected", select_fg)])

    style.configure(f"{name}.Heading",
                    background=heading_bg,
                    foreground=heading_fg,
                    relief="flat",
                    font=(FONT_FAMILY, FONT_SIZES["body"], "bold"))

    return name


# ═══════════════════════════════════════════════════
# matplotlib 暗色模式
# ═══════════════════════════════════════════════════

_MPL_DARK_PARAMS = {
    "figure.facecolor": "#2b2b2b",
    "axes.facecolor": "#333333",
    "axes.edgecolor": "#888888",
    "axes.labelcolor": "#e0e0e0",
    "axes.titlecolor": "#e0e0e0",
    "xtick.color": "#e0e0e0",
    "ytick.color": "#e0e0e0",
    "text.color": "#e0e0e0",
    "grid.color": "#555555",
    "grid.alpha": 0.3,
}

_MPL_LIGHT_PARAMS = {
    "figure.facecolor": "#ffffff",
    "axes.facecolor": "#ffffff",
    "axes.edgecolor": "#333333",
    "axes.labelcolor": "#1a1a1a",
    "axes.titlecolor": "#1a1a1a",
    "xtick.color": "#1a1a1a",
    "ytick.color": "#1a1a1a",
    "text.color": "#1a1a1a",
    "grid.color": "#cccccc",
    "grid.alpha": 0.5,
}


def apply_mpl_theme(mode: Literal["light", "dark"] = None):
    """应用 matplotlib 明暗主题参数，返回 (figure背景色, axes背景色)"""
    import matplotlib as mpl

    if mode is None:
        mode = get_current_mode()

    params = _MPL_DARK_PARAMS if mode == "dark" else _MPL_LIGHT_PARAMS
    mpl.rcParams.update(params)
    return params["figure.facecolor"], params["axes.facecolor"]
