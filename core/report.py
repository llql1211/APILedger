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

def _collect_official_prices() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    从各平台预设的 PRICING 收集官方单价表 → {模型名: {平台名: 价格配置}}。

    价格配置结构与预设一致:
      {"input_hit": .., "input_miss": .., "output": ..,
       "history": [{"until": "时段截止日", ...价格...}, ...]}
    同一模型不同时段价格不同, 由 history 表达 (until 含当日, 语义与
    _apply_price_hint 的 type 反推一致); 不同平台同模型价格也可能不同
    (如 DeepSeek-V4-Flash: DeepSeek 官方 0.02, Paratera 早期 0.20),
    故按预设归属的平台分组。平台名取预设 DEFAULTS.platform, 缺省用预设名。
    旧格式 {platform: {model: ...}} 直接按其外层键归组。

    供前端展示层"单价吸附"与终端未吸附提示: 计算单价与该记录所属平台的
    价表中该日期生效的官方价相对误差 ≤SNAP_TOLERANCE 时按官方价显示;
    记录平台无价表时回退合并所有平台。
    """
    from core.presets import load_all_presets, get_pricing_dict, get_preset_name

    price_keys = ("input_hit", "input_miss", "output")

    def _iter_model_cfgs(pricing: dict):
        """产出 (模型名, 价格配置, 平台覆盖)。兼容新旧两种 PRICING 结构。"""
        for top_key, cfg in pricing.items():
            if not isinstance(cfg, dict):
                continue
            if any(k in cfg for k in price_keys + ("history",)):
                yield top_key, cfg, None                  # 新格式: model → 价格配置
            else:
                for model, sub in cfg.items():            # 旧格式: platform → model → 配置
                    if isinstance(sub, dict):
                        yield model, sub, str(top_key)

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

    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for preset in load_all_presets():
        defaults = getattr(preset, "DEFAULTS", None)
        preset_platform = str(defaults.get("platform", "") or "") \
            if isinstance(defaults, dict) else ""
        if not preset_platform:
            preset_platform = get_preset_name(preset)
        for model, cfg, plat in _iter_model_cfgs(get_pricing_dict(preset)):
            norm = _norm_cfg(cfg)
            if not norm:
                continue
            dst = merged.setdefault(model, {}).setdefault(plat or preset_platform, {})
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
# 未吸附单价检测 (与前端 snapUnitPrice 同一套判定)
# ═══════════════════════════════════════════════════

# 单价吸附容差: 计算单价与官方价相对误差 ≤5% 视为同一价格 (前端保持一致)
SNAP_TOLERANCE = 0.05


def _price_at(cfg: Dict[str, Any], date_str: str) -> Dict[str, float]:
    """解析某账单日期生效的官方价: history 自日期早向日期晚,
    取 until ≥ 日期中最小的 (即日期真正落在的时段; until 含当日,
    语义与导入侧 _apply_price_hint 及前端 officialPrice 一致)"""
    best = None
    for h in cfg.get("history") or []:
        until = str(h.get("until", ""))
        if date_str and date_str <= until and (best is None or until < str(best.get("until", ""))):
            best = h
    cur = best or cfg
    out: Dict[str, float] = {}
    for k in ("input_hit", "input_miss", "output"):
        try:
            v = float(cur.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            out[k] = v
    return out


def _cfg_for_platform(model_prices: Dict[str, Dict[str, Any]], platform: str) -> Dict[str, Any]:
    """取记录所属平台的价表; 该平台未配置时回退合并所有平台 (与前端一致)"""
    cfg = model_prices.get(platform)
    if cfg is not None:
        return cfg
    merged: Dict[str, Any] = {}
    for sub in model_prices.values():
        if not isinstance(sub, dict):
            continue
        for k in ("input_hit", "input_miss", "output"):
            if merged.get(k) is None and sub.get(k) is not None:
                merged[k] = sub[k]
        if sub.get("history"):
            merged.setdefault("history", []).extend(sub["history"])
    return merged


def unsnapped_price_groups(db: Database) -> list:
    """
    找出未吸附到预设官方价的账单, 按 (模型, 类型, 原因) 分组计数。

    判定与前端 snapUnitPrice 一致: 模型 × 平台 × 账单日期 × 类型
    (缓存输入→input_hit, 输出→output, 其余→input_miss), 相对误差
    ≤SNAP_TOLERANCE 视为吸附。零单价行不展示单价, 不参与提示;
    全库未配置任何官方价表时返回空 (无从比对)。

    返回 [{"model", "type", "reason", "count", "price_range"}, ...]
    """
    prices = _collect_official_prices()
    if not prices:
        return []

    groups: Dict[tuple, Dict[str, Any]] = {}
    for r in db.get_all(order_by="date ASC"):
        v = float(r.get("unit_price", 0.0) or 0.0)
        if not v:
            continue
        model = r.get("model", "")
        rtype = r.get("type", "")
        model_prices = prices.get(model)
        if not model_prices:
            reason = "未配置官方价表"
        else:
            cfg = _cfg_for_platform(model_prices, r.get("platform", ""))
            p = _price_at(cfg, str(r.get("date", "") or "")[:10])
            if not p:
                reason = "该日期无生效官方价"
            else:
                key = "input_hit" if "缓存" in rtype else ("output" if "输出" in rtype else "input_miss")
                official = p.get(key)
                if official is None:
                    reason = "价表缺少该类型单价"
                elif abs(v - official) <= official * SNAP_TOLERANCE:
                    continue
                else:
                    reason = f"偏差超 5% (官方价 ¥{official}/M)"
        g = groups.setdefault((model, rtype, reason),
                              {"model": model, "type": rtype, "reason": reason,
                               "count": 0, "prices": set()})
        g["count"] += 1
        g["prices"].add(round(v, 4))

    out = []
    for g in groups.values():
        ps = sorted(g.pop("prices"))
        g["price_range"] = f"¥{ps[0]:g}~¥{ps[-1]:g}" if len(ps) > 1 else f"¥{ps[0]:g}"
        out.append(g)
    return out


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
