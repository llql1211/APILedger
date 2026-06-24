"""
APILedger - 主题颜色 & 样式常量
"""

import customtkinter as ctk

# ── 颜色 ──────────────────────────────────────────
PRIMARY = "#1f538d"          # 主色调 (深蓝)
PRIMARY_HOVER = "#14375e"    # 主色调悬停
SECONDARY = "#2fa572"        # 辅色调 (绿色)
BG_LIGHT = "#f0f0f0"        # 浅背景
BG_DARK = "#2b2b2b"         # 深背景
CARD_BG = "#ffffff"          # 卡片背景
TEXT_PRIMARY = "#1a1a1a"    # 主文字
TEXT_SECONDARY = "#666666"  # 次要文字
BORDER = "#d0d0d0"          # 边框

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


def setup_appearance():
    """全局外观设置"""
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")