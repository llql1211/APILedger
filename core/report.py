"""
APILedger - HTML 报告生成器

从数据库提取数据，生成一个自包含的 HTML 报告文件：
  - 汇总卡片 (总费用 / 总Tokens / 记录数 / 模型数)
  - ECharts 交互图表 (趋势 / 对比 / 分布)
  - 前端 JS 筛选的数据明细表

生成单个 .html 文件，用浏览器打开，无需服务器。
数据以 JSON 内嵌在页面中，筛选/排序/翻页全部在前端完成。
"""

import json
import os
import urllib.request
from typing import Any, Dict

from core.db import Database

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
ECHARTS_PATH = os.path.join(OUTPUT_DIR, "echarts.min.js")
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

# 主题色 (与 ui/theme.py 的 CHART_COLORS 一致)
CHART_COLORS = [
    "#1f538d", "#2fa572", "#e8a838", "#c0392b",
    "#8e44ad", "#16a085", "#2980b9", "#d35400",
    "#27ae60", "#f39c12", "#7f8c8d", "#2c3e50",
]


# ═══════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════

def _build_report_data(db: Database) -> Dict[str, Any]:
    """从数据库提取报告所需的全部数据"""
    records = db.get_all(order_by="bill_start DESC")

    summary = {
        "record_count": len(records),
        "total_tokens": sum(int(r.get("tokens", 0) or 0) for r in records),
        "total_cost": round(sum(float(r.get("cost", 0.0) or 0.0) for r in records), 2),
        "total_calls": sum(int(r.get("call_volume", 0) or 0) for r in records),
        "model_count": len(set(r.get("model", "") for r in records if r.get("model"))),
        "platform_count": len(set(r.get("platform", "") for r in records if r.get("platform"))),
    }
    if records:
        summary["date_min"] = str(records[-1].get("bill_start", ""))[:10]
        summary["date_max"] = str(records[0].get("bill_start", ""))[:10]
    else:
        summary["date_min"] = summary["date_max"] = ""

    # 按日聚合 (费用/tokens/调用量)
    daily = {
        "cost": db.aggregate_by_date(value_field="cost", group_by="date(bill_start)"),
        "tokens": db.aggregate_by_date(value_field="tokens", group_by="date(bill_start)"),
        "calls": db.aggregate_by_date(value_field="call_volume", group_by="date(bill_start)"),
    }

    # 各维度 Top-N
    by_platform = db.aggregate_by_field("cost", "platform", top_n=15)
    by_model = db.aggregate_by_field("cost", "model", top_n=15)
    by_type = db.aggregate_by_field("cost", "type", top_n=10)

    # 明细 (前端筛选用)
    raw_records = []
    for r in records:
        raw_records.append({
            "bill_start": r.get("bill_start", ""),
            "bill_end": r.get("bill_end", ""),
            "platform": r.get("platform", ""),
            "project": r.get("project", ""),
            "model": r.get("model", ""),
            "type": r.get("type", ""),
            "tokens": int(r.get("tokens", 0) or 0),
            "call_volume": int(r.get("call_volume", 0) or 0),
            "cost": float(r.get("cost", 0.0) or 0.0),
            "unit_price": float(r.get("unit_price", 0.0) or 0.0),
            "source_file": r.get("source_file", ""),
        })

    return {
        "summary": summary,
        "daily": daily,
        "by_platform": by_platform,
        "by_model": by_model,
        "by_type": by_type,
        "records": raw_records,
    }


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


def build_html(db: Database) -> str:
    """生成完整 HTML 报告字符串"""
    data = _build_report_data(db)
    echarts_tag = _echarts_script_tag()

    html = _HTML_TEMPLATE
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__CHART_COLORS__", json.dumps(CHART_COLORS, ensure_ascii=False))
    html = html.replace("__ECHARTS_TAG__", echarts_tag)
    return html


# ═══════════════════════════════════════════════════
# HTML 模板
# ═══════════════════════════════════════════════════

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>APILedger - API 账单报告</title>
<style>
  :root {
    --primary: #1f538d; --secondary: #2fa572; --bg: #f4f6f9;
    --card: #ffffff; --border: #e3e6ea; --text: #1a1a1a; --muted: #6b7280;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card .label { color: var(--muted); font-size: 12px; }
  .card .value { font-size: 26px; font-weight: 700; margin-top: 4px; }
  .card .value.cost { color: var(--primary); }
  .card .value.tokens { color: var(--secondary); }
  .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }
  .chart-box { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
  .chart-box h3 { font-size: 14px; margin-bottom: 8px; }
  .chart { width: 100%; height: 320px; }
  .chart-tabs { display: flex; gap: 8px; margin-bottom: 10px; }
  .chart-tabs button { padding: 4px 14px; border: 1px solid var(--border); background: #fff; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .chart-tabs button.active { background: var(--primary); color: #fff; border-color: var(--primary); }
  .filters { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 16px; }
  .filters h3 { font-size: 14px; margin-bottom: 10px; }
  .filter-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
  .filter-item label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
  .filter-item select, .filter-item input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; }
  .filter-item input[type="date"] { width: 150px; }
  .filter-item input[type="text"] { width: 200px; }
  .filter-actions { display: flex; gap: 8px; }
  .btn { padding: 6px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; background: var(--primary); color: #fff; }
  .btn.secondary { background: #e5e7eb; color: #374151; }
  .table-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead { background: #f9fafb; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { cursor: pointer; user-select: none; font-weight: 600; }
  th:hover { background: #eef1f4; }
  th .arrow { color: var(--muted); font-size: 10px; }
  tr:hover { background: #f8fafc; }
  .table-meta { padding: 10px 14px; font-size: 12px; color: var(--muted); display: flex; justify-content: space-between; align-items: center; }
  .pagination { display: flex; gap: 6px; align-items: center; }
  .pagination button { padding: 4px 12px; border: 1px solid var(--border); background: #fff; border-radius: 5px; cursor: pointer; font-size: 12px; }
  .pagination button:disabled { opacity: 0.4; cursor: default; }
  .pagination span { font-size: 12px; color: var(--muted); }
  .num { text-align: right; }
  @media (max-width: 900px) { .chart-grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <h1>📊 APILedger 账单报告</h1>
  <div class="subtitle" id="subtitle">加载中...</div>

  <!-- 汇总卡片 -->
  <div class="cards">
    <div class="card"><div class="label">总费用</div><div class="value cost" id="c-cost">-</div></div>
    <div class="card"><div class="label">总 Tokens</div><div class="value tokens" id="c-tokens">-</div></div>
    <div class="card"><div class="label">调用量</div><div class="value" id="c-calls">-</div></div>
    <div class="card"><div class="label">记录数</div><div class="value" id="c-records">-</div></div>
    <div class="card"><div class="label">模型数</div><div class="value" id="c-models">-</div></div>
  </div>

  <!-- 图表 -->
  <div class="chart-grid">
    <div class="chart-box">
      <h3>费用趋势</h3>
      <div class="chart-tabs">
        <button data-g="日" class="active">日</button>
        <button data-g="周">周</button>
        <button data-g="月">月</button>
      </div>
      <div id="chart-trend" class="chart"></div>
    </div>
    <div class="chart-box">
      <h3>平台费用占比</h3>
      <div id="chart-platform" class="chart"></div>
    </div>
    <div class="chart-box">
      <h3>模型费用对比 (Top-15)</h3>
      <div id="chart-model" class="chart"></div>
    </div>
    <div class="chart-box">
      <h3>类型费用分布</h3>
      <div id="chart-type" class="chart"></div>
    </div>
  </div>

  <!-- 筛选栏 -->
  <div class="filters">
    <h3>🔍 数据筛选</h3>
    <div class="filter-row">
      <div class="filter-item"><label>开始日期</label><input type="date" id="f-start"></div>
      <div class="filter-item"><label>结束日期</label><input type="date" id="f-end"></div>
      <div class="filter-item"><label>平台</label><select id="f-platform"><option value="">全部</option></select></div>
      <div class="filter-item"><label>模型</label><select id="f-model"><option value="">全部</option></select></div>
      <div class="filter-item"><label>类型</label><select id="f-type"><option value="">全部</option></select></div>
      <div class="filter-item"><label>关键词</label><input type="text" id="f-keyword" placeholder="搜索模型/项目/平台..."></div>
      <div class="filter-actions">
        <button class="btn" onclick="applyFilters()">应用</button>
        <button class="btn secondary" onclick="resetFilters()">重置</button>
      </div>
    </div>
  </div>

  <!-- 明细表 -->
  <div class="table-wrap">
    <div class="table-meta">
      <span id="table-count">共 0 条</span>
      <div class="pagination">
        <button onclick="changePage(-1)" id="btn-prev">‹ 上一页</button>
        <span id="page-info">1 / 1</span>
        <button onclick="changePage(1)" id="btn-next">下一页 ›</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th data-k="bill_start">时间</th>
          <th data-k="platform">平台</th>
          <th data-k="project">项目</th>
          <th data-k="model">模型</th>
          <th data-k="type">类型</th>
          <th data-k="tokens" class="num">Tokens</th>
          <th data-k="cost" class="num">金额(¥)</th>
        </tr>
      </thead>
      <tbody id="table-body"></tbody>
    </table>
  </div>

  __ECHARTS_TAG__

  <script>
  const DATA = __DATA_JSON__;
  const CHART_COLORS = __CHART_COLORS__;

  // ── 格式化工具 ──
  function fmtNum(v) { return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }); }
  function fmtCost(v) { return '¥' + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 }); }

  // ── 汇总卡片 ──
  function renderSummary() {
    const s = DATA.summary;
    document.getElementById('c-cost').textContent = fmtCost(s.total_cost);
    document.getElementById('c-tokens').textContent = fmtNum(s.total_tokens);
    document.getElementById('c-calls').textContent = fmtNum(s.total_calls);
    document.getElementById('c-records').textContent = fmtNum(s.record_count);
    document.getElementById('c-models').textContent = fmtNum(s.model_count);
    document.getElementById('subtitle').textContent =
      (s.date_min ? s.date_min + ' ~ ' + s.date_max : '暂无数据') +
      ` · ${s.platform_count} 个平台 · ${s.model_count} 个模型`;
  }

  // ── 趋势图 (日/周/月切换) ──
  let trendChart = null;
  function renderTrend(gran) {
    if (typeof echarts === 'undefined') return;
    const el = document.getElementById('chart-trend');
    if (!trendChart) trendChart = echarts.init(el);
    const daily = DATA.daily.cost;

    const map = new Map();
    for (const d of daily) {
      const p = d.period;
      let key = p;
      if (gran === '周') {
        const dt = new Date(p + 'T00:00:00');
        const y = dt.getFullYear();
        const week = Math.ceil((dt - new Date(y, 0, 1)) / 86400000 / 7);
        key = y + '-W' + String(week).padStart(2, '0');
      } else if (gran === '月') {
        key = p.slice(0, 7);
      }
      map.set(key, (map.get(key) || 0) + Number(d.total));
    }
    const keys = [...map.keys()].sort();
    const vals = keys.map(k => map.get(k));
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;

    trendChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 30, bottom: 40 },
      xAxis: { type: 'category', data: keys, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', name: '费用(¥)' },
      series: [{
        type: 'line', data: vals, smooth: true, name: '费用',
        itemStyle: { color: '#1f538d' }, areaStyle: { opacity: 0.1 },
        markLine: avg ? { data: [{ yAxis: avg }], silent: true,
          lineStyle: { type: 'dashed', color: '#c0392b' }, label: { formatter: '均值' } } : undefined,
      }],
    });
  }

  // ── 平台饼图 ──
  let platformChart = null;
  function renderPlatform() {
    if (typeof echarts === 'undefined') return;
    const el = document.getElementById('chart-platform');
    if (!platformChart) platformChart = echarts.init(el);
    const data = (DATA.by_platform || []).map(d => ({ name: d.name || '(空)', value: Number(d.total) }));
    platformChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{ type: 'pie', radius: ['35%', '65%'], data, label: { formatter: '{b}\\n{d}%', fontSize: 11 }, color: CHART_COLORS }],
    });
  }

  // ── 模型柱状图 ──
  let modelChart = null;
  function renderModel() {
    if (typeof echarts === 'undefined') return;
    const el = document.getElementById('chart-model');
    if (!modelChart) modelChart = echarts.init(el);
    const data = DATA.by_model || [];
    const names = [...data].reverse().map(d => d.name || '(空)');
    const vals = [...data].reverse().map(d => Number(d.total));
    modelChart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: p => p[0].name + '<br>¥' + fmtNum(p[0].value) },
      grid: { left: 120, right: 40, top: 20, bottom: 30 },
      xAxis: { type: 'value', name: '费用(¥)' },
      yAxis: { type: 'category', data: names },
      series: [{ type: 'bar', data: vals, itemStyle: { color: '#2fa572', borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', formatter: p => fmtNum(p.value), fontSize: 10 } }],
    });
  }

  // ── 类型饼图 ──
  let typeChart = null;
  function renderType() {
    if (typeof echarts === 'undefined') return;
    const el = document.getElementById('chart-type');
    if (!typeChart) typeChart = echarts.init(el);
    const data = (DATA.by_type || []).map(d => ({ name: d.name || '(空)', value: Number(d.total) }));
    typeChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{ type: 'pie', data, label: { formatter: '{b}\\n{d}%', fontSize: 11 }, color: CHART_COLORS }],
    });
  }

  // ── 明细表: 筛选 / 排序 / 翻页 ──
  const ROWS = DATA.records || [];
  const PAGE_SIZE = 30;
  let filtered = ROWS;
  let page = 1;
  let sortKey = 'bill_start';
  let sortDir = -1;

  function buildOptions(selId, key) {
    const set = new Set(ROWS.map(r => r[key]).filter(Boolean));
    const sel = document.getElementById(selId);
    for (const v of [...set].sort()) {
      const opt = document.createElement('option');
      opt.value = v; opt.textContent = v;
      sel.appendChild(opt);
    }
  }

  function applyFilters() {
    const start = document.getElementById('f-start').value;
    const end = document.getElementById('f-end').value;
    const pf = document.getElementById('f-platform').value;
    const mf = document.getElementById('f-model').value;
    const tf = document.getElementById('f-type').value;
    const kw = document.getElementById('f-keyword').value.trim().toLowerCase();

    filtered = ROWS.filter(r => {
      if (start && r.bill_start.slice(0, 10) < start) return false;
      if (end && r.bill_start.slice(0, 10) > end) return false;
      if (pf && r.platform !== pf) return false;
      if (mf && r.model !== mf) return false;
      if (tf && r.type !== tf) return false;
      if (kw && !(r.model + r.project + r.platform + r.type).toLowerCase().includes(kw)) return false;
      return true;
    });
    page = 1;
    renderTable();
  }

  function resetFilters() {
    ['f-start', 'f-end', 'f-keyword'].forEach(id => document.getElementById(id).value = '');
    ['f-platform', 'f-model', 'f-type'].forEach(id => { const s = document.getElementById(id); s.selectedIndex = 0; });
    filtered = ROWS;
    page = 1;
    renderTable();
  }

  function changePage(delta) {
    page += delta;
    renderTable();
  }

  function renderTable() {
    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    page = Math.max(1, Math.min(page, totalPages));
    document.getElementById('table-count').textContent = `共 ${filtered.length} 条`;
    document.getElementById('page-info').textContent = `${page} / ${totalPages}`;
    document.getElementById('btn-prev').disabled = page <= 1;
    document.getElementById('btn-next').disabled = page >= totalPages;

    const sorted = [...filtered].sort((a, b) => {
      const va = a[sortKey], vb = b[sortKey];
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sortDir;
      return String(va || '').localeCompare(String(vb || ''), 'zh-CN') * sortDir;
    });

    const start = (page - 1) * PAGE_SIZE;
    const slice = sorted.slice(start, start + PAGE_SIZE);
    const tbody = document.getElementById('table-body');
    tbody.innerHTML = '';
    for (const r of slice) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${String(r.bill_start).slice(0, 16)}</td>
        <td>${r.platform}</td>
        <td>${r.project}</td>
        <td>${r.model}</td>
        <td>${r.type}</td>
        <td class="num">${fmtNum(r.tokens)}</td>
        <td class="num">${fmtCost(r.cost)}</td>`;
      tbody.appendChild(tr);
    }
  }

  // 表头排序
  document.querySelectorAll('th[data-k]').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if (sortKey === k) sortDir *= -1;
      else { sortKey = k; sortDir = -1; }
      document.querySelectorAll('th .arrow').forEach(a => a.remove());
      const arrow = document.createElement('span');
      arrow.className = 'arrow';
      arrow.textContent = sortDir === -1 ? ' ↓' : ' ↑';
      th.appendChild(arrow);
      page = 1;
      renderTable();
    });
  });

  // 趋势粒度切换
  document.querySelectorAll('.chart-tabs button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.chart-tabs button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderTrend(btn.dataset.g);
    });
  });

  // 初始化
  window.addEventListener('resize', () => {
    [trendChart, platformChart, modelChart, typeChart].forEach(c => c && c.resize());
  });
  renderSummary();
  renderTrend('日');
  renderPlatform();
  renderModel();
  renderType();
  buildOptions('f-platform', 'platform');
  buildOptions('f-model', 'model');
  buildOptions('f-type', 'type');
  renderTable();
  </script>
</body>
</html>
"""
