"""
APILedger - HTML 报告生成器

从数据库提取数据，生成一个自包含的 HTML 报告文件：
  - 汇总卡片 (随筛选联动)
  - ECharts 交互图表 (趋势 / 分布, 全部由前端按当前筛选实时重算)
  - 可筛选 / 排序 / 翻页的数据明细表

生成单个 .html 文件，用浏览器打开，无需服务器。
数据以 JSON 内嵌在页面中，所有聚合计算在前端完成。
"""

import json
import os
import urllib.request
from typing import Any, Dict

from core.db import Database

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(CORE_DIR, "report_template.html")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
ECHARTS_PATH = os.path.join(OUTPUT_DIR, "echarts.min.js")
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

# 主题色
CHART_COLORS = [
    "#1f538d", "#2fa572", "#e8a838", "#c0392b",
    "#8e44ad", "#16a085", "#2980b9", "#d35400",
    "#27ae60", "#f39c12", "#7f8c8d", "#2c3e50",
]


# ═══════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════

def _collect_official_prices() -> Dict[str, Dict[str, Any]]:
    """
    从各平台预设的 PRICING 收集官方单价表 → {模型名: 价格配置}。

    价格配置结构与预设一致:
      {"input_hit": .., "input_miss": .., "output": ..,
       "history": [{"until": "时段截止日", ...价格...}, ...]}
    同一模型不同时段价格不同, 由 history 表达 (until 含当日, 语义与
    _apply_price_hint 的 type 反推一致)。旧格式 {platform: {model: ...}} 自动摊平。

    供前端展示层"单价吸附": 计算单价与该日期生效的官方价相对误差 ≤2% 时按官方价显示。
    """
    from core.presets import load_all_presets, get_pricing_dict

    price_keys = ("input_hit", "input_miss", "output")

    def _iter_model_cfgs(pricing: dict):
        """产出 (模型名, 价格配置)。兼容新旧两种 PRICING 结构。"""
        for model, cfg in pricing.items():
            if not isinstance(cfg, dict):
                continue
            if any(k in cfg for k in price_keys + ("history",)):
                yield model, cfg                          # 新格式: model → 价格配置
            else:
                for m2, sub in cfg.items():               # 旧格式: platform → model → 配置
                    if isinstance(sub, dict):
                        yield m2, sub

    def _norm_cfg(cfg) -> dict:
        out: Dict[str, Any] = {}
        for k in price_keys:
            try:
                v = float(cfg.get(k) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                out[k] = v
        hist = [h for h in (cfg.get("history") or []) if isinstance(h, dict)]
        if hist:
            out["history"] = hist
        return out

    merged: Dict[str, Dict[str, Any]] = {}
    for preset in load_all_presets():
        for model, cfg in _iter_model_cfgs(get_pricing_dict(preset)):
            norm = _norm_cfg(cfg)
            if not norm:
                continue
            dst = merged.setdefault(model, {})
            for k, v in norm.items():
                if k == "history":
                    dst.setdefault("history", []).extend(v)
                else:
                    dst[k] = v
    return merged


def _build_report_data(db: Database) -> Dict[str, Any]:
    """
    从数据库提取报告所需的全部数据。

    只输出全量明细 records + 一个全库兜底 summary;
    所有维度聚合 (趋势 / 平台 / 模型 / 类型) 由前端按筛选结果实时计算。
    pricing 为各预设的官方单价表, 供前端单价吸附。
    """
    records = db.get_all(order_by="date DESC")

    summary = {
        "record_count": len(records),
        "total_tokens": sum(int(r.get("tokens", 0) or 0) for r in records),
        "total_cost": round(sum(float(r.get("cost", 0.0) or 0.0) for r in records), 2),
        "model_count": len(set(r.get("model", "") for r in records if r.get("model"))),
        "platform_count": len(set(r.get("platform", "") for r in records if r.get("platform"))),
    }
    if records:
        summary["date_min"] = str(records[-1].get("date", ""))[:10]
        summary["date_max"] = str(records[0].get("date", ""))[:10]
    else:
        summary["date_min"] = summary["date_max"] = ""

    raw_records = []
    for r in records:
        raw_records.append({
            "bill_start": r.get("date", "") or r.get("bill_start", ""),
            "platform": r.get("platform", ""),
            "project": r.get("project", ""),
            "model": r.get("model", ""),
            "type": r.get("type", ""),
            "tokens": int(r.get("tokens", 0) or 0),
            "cost": float(r.get("cost", 0.0) or 0.0),
            "unit_price": float(r.get("unit_price", 0.0) or 0.0),
            "source_file": r.get("source_file", ""),
        })

    return {"summary": summary, "records": raw_records, "pricing": _collect_official_prices()}


# ═══════════════════════════════════════════════════
# ECharts 引入 (内联优先, CDN 兜底)
# ═══════════════════════════════════════════════════

def _echarts_script_tag() -> str:
    """
    返回 echarts 的 <script> 标签。

    策略: 本地已有 echarts.min.js → 内容内联进 HTML (真正单文件自包含);
          否则尝试下载到本地一次; 下载失败 → 改用 CDN 引用。
    """
    if os.path.exists(ECHARTS_PATH):
        with open(ECHARTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            return "<script>\n" + f.read() + "\n</script>"

    # 尝试下载到本地
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print("  [报告] 下载 echarts.min.js (首次使用)...", flush=True)
        urllib.request.urlretrieve(ECHARTS_CDN, ECHARTS_PATH)
        with open(ECHARTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            return "<script>\n" + f.read() + "\n</script>"
    except Exception as e:
        print(f"  [报告] 下载 echarts 失败, 改用 CDN: {e}", flush=True)
        return f'<script src="{ECHARTS_CDN}"></script>'


# ═══════════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════════

def build_html(db: Database) -> str:
    """生成完整 HTML 报告字符串"""
    data = _build_report_data(db)
    echarts_tag = _echarts_script_tag()

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__CHART_COLORS__", json.dumps(CHART_COLORS, ensure_ascii=False))
    html = html.replace("__ECHARTS_TAG__", echarts_tag)
    return html


def export_report(db: Database, output_path: str = "") -> str:
    """
    生成报告文件并写入磁盘, 返回文件绝对路径。
    默认写到 output/report.html (固定文件名, 重新生成即覆盖)。
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = output_path or os.path.join(OUTPUT_DIR, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(db))
    return os.path.abspath(path)
