"""
APILedger - 导入冲突确认弹窗

当重复记录 (唯一键匹配) 但数值 (tokens/call_volume/cost) 不一致时,
弹出对话框列出两次文件的信息, 供用户选择覆盖还是跳过。
"""

from typing import Any, Dict, List, Optional

import customtkinter as ctk

from ui.theme import FONT_SIZES


class ConflictDialog(ctk.CTkToplevel):
    """冲突确认对话框, 模态"""

    def __init__(self, parent, conflicts: List[Dict[str, Any]]):
        super().__init__(parent)

        self.result: Optional[bool] = None  # True=覆盖, False=跳过, None=取消
        self.conflicts = conflicts
        total_new = len(conflicts)

        self.title("导入冲突 — 请确认")
        self.geometry("640x480")
        self.minsize(540, 360)
        self.transient(parent)
        self.grab_set()

        # ── 顶部说明 ────────────────────────
        header = ctk.CTkLabel(
            self,
            text=f"发现 {total_new} 条冲突记录",
            font=("Microsoft YaHei", FONT_SIZES["subtitle"], "bold"),
            anchor="w",
        )
        header.pack(padx=20, pady=(16, 4), fill="x")

        desc = ctk.CTkLabel(
            self,
            text="下表中的记录在数据库中已存在 (唯一键一致)，但数值发生变化。\n"
                 "请检查后选择处理方式：",
            font=("Microsoft YaHei", FONT_SIZES["body"]),
            anchor="w",
            justify="left",
        )
        desc.pack(padx=20, pady=(0, 12), fill="x")

        # ── 可滚动的冲突列表 ────────────────
        scroll_frame = ctk.CTkScrollableFrame(self, corner_radius=8)
        scroll_frame.pack(padx=20, pady=(0, 12), fill="both", expand=True)

        for idx, c in enumerate(conflicts):
            row = c["row"]
            old_file = c.get("old_file", "(未知)")
            new_file = c.get("new_file", c.get("filename", "(未知)"))

            card = ctk.CTkFrame(scroll_frame, corner_radius=6, border_width=1)
            card.pack(fill="x", pady=(0, 8), padx=4)

            # 行标题
            title_text = (
                f"#{idx+1}  [{row.get('bill_start','')} ~ {row.get('bill_end','')}]  "
                f"{row.get('platform','')}/{row.get('project','')}/{row.get('model','')}  "
                f"类型: {row.get('type','')}"
            )
            ctk.CTkLabel(
                card, text=title_text,
                font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
                anchor="w",
            ).pack(padx=12, pady=(8, 4), fill="x")

            # 文件 + 数值对比
            detail_frame = ctk.CTkFrame(card, fg_color="transparent")
            detail_frame.pack(padx=12, pady=(0, 8), fill="x")

            # 旧文件
            ctk.CTkLabel(
                detail_frame,
                text=f"📄 已有文件: {old_file}",
                font=("Microsoft YaHei", FONT_SIZES["small"]),
                anchor="w",
                text_color="gray60",
            ).pack(anchor="w")

            # 数值新旧对比
            old = c.get("existing", {})
            numeric_frame = ctk.CTkFrame(detail_frame, fg_color="transparent")
            numeric_frame.pack(fill="x", pady=(2, 0))

            ctk.CTkLabel(
                numeric_frame,
                text=(
                    f"Tokens:  {old.get('tokens',0):,} → {int(row.get('tokens',0) or 0):,}   |   "
                    f"调用量:  {old.get('call_volume',0):,} → {int(row.get('call_volume',0) or 0):,}   |   "
                    f"金额:  ¥{old.get('cost',0):.4f} → ¥{float(row.get('cost',0) or 0):.4f}"
                ),
                font=("Consolas", FONT_SIZES["small"]),
                anchor="w",
                text_color="#c0392b",
            ).pack(anchor="w")

            ctk.CTkLabel(
                detail_frame,
                text=f"📄 新文件: {new_file}",
                font=("Microsoft YaHei", FONT_SIZES["small"]),
                anchor="w",
                text_color="gray60",
            ).pack(anchor="w")

        # ── 底部按钮 ────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(0, 16), fill="x")

        # 左侧勾选
        self.apply_to_all_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            btn_frame, text="对本次导入所有冲突统一处理",
            variable=self.apply_to_all_var,
            font=("Microsoft YaHei", FONT_SIZES["body"]),
        ).pack(side="left")

        # 右侧按钮
        def _overwrite():
            self.result = True
            self.destroy()

        def _skip():
            self.result = False
            self.destroy()

        def _cancel():
            self.result = None
            self.destroy()

        skip_btn = ctk.CTkButton(
            btn_frame, text="跳过此文件", command=_skip,
            fg_color="gray50", hover_color="gray30",
            font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        skip_btn.pack(side="right", padx=(4, 0))

        overwrite_btn = ctk.CTkButton(
            btn_frame, text="覆盖更新", command=_overwrite,
            font=("Microsoft YaHei", FONT_SIZES["body"], "bold"),
        )
        overwrite_btn.pack(side="right", padx=(4, 0))

        cancel_btn = ctk.CTkButton(
            btn_frame, text="取消导入", command=_cancel,
            fg_color="#c0392b", hover_color="#96281b",
            font=("Microsoft YaHei", FONT_SIZES["body"]),
        )
        cancel_btn.pack(side="right")

        # 居中显示
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        x = px + (pw - self.winfo_width()) // 2
        y = py + (ph - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

        self.wait_window()
