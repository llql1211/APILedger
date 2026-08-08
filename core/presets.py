"""
APILedger - 平台账单导入预设引擎

每个 API 平台在 presets/ 下有一个 .py 预设模块，定义该平台的
列名映射、默认值、type 翻译、模型映射、价格表，以及可选的行级解析逻辑。

本模块统一扫描、加载、匹配、应用这些预设，供 importer 调用。
新增平台只需在 presets/ 放一个 .py 文件，无需改代码。
"""

import importlib.util
import os
import re
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_DIR = os.path.join(BASE_DIR, "presets")


# ═══════════════════════════════════════════════════
# 模块加载
# ═══════════════════════════════════════════════════

def load_all_presets() -> List[Any]:
    """
    扫描 presets/ 目录，导入所有 .py 预设模块（__init__.py 除外）。
    返回模块对象列表，模块的全局变量即为预设配置。
    """
    if not os.path.isdir(PRESETS_DIR):
        return []

    modules = []
    for fname in sorted(os.listdir(PRESETS_DIR)):
        if not fname.endswith(".py") or fname.startswith("_") or fname.startswith("."):
            continue
        name = os.path.splitext(fname)[0]
        path = os.path.join(PRESETS_DIR, fname)
        try:
            mod = _import_module(path, name)
            # 基本校验：至少要有 MATCH_FILENAME 和 COLUMN_MAPPING
            if not hasattr(mod, "MATCH_FILENAME") or not hasattr(mod, "COLUMN_MAPPING"):
                print(f"  [预设] {fname} 缺少必要字段 (MATCH_FILENAME / COLUMN_MAPPING), 跳过", flush=True)
                continue
            modules.append(mod)
        except Exception as e:
            print(f"  [预设] 加载失败 {fname}: {e}", flush=True)

    return modules


def _import_module(path: str, name: str):
    """从文件路径导入 Python 模块"""
    spec = importlib.util.spec_from_file_location(f"presets.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════
# 匹配
# ═══════════════════════════════════════════════════

def _norm(s: str) -> str:
    return s.lower().replace(" ", "")


def detect_preset(
    filename: str,
    headers: List[str],
    presets: Optional[List[Any]] = None,
) -> Optional[Any]:
    """
    自动匹配预设模块。

    规则:
      1. 文件名包含 MATCH_FILENAME 中任一关键词 → 候选
      2. 如定义了 MATCH_HEADERS，必须全部出现在表头中
      3. 多个候选时取 MATCH_HEADERS 命中数最多的
    """
    if presets is None:
        presets = load_all_presets()
    if not presets:
        return None

    name_norm = _norm(filename)
    header_norm = [_norm(h) for h in headers]

    best = None
    best_hits = -1

    for mod in presets:
        # 1. 文件名关键词 (任一命中)
        fw = getattr(mod, "MATCH_FILENAME", [])
        if fw and not any(_norm(kw) in name_norm for kw in fw):
            continue

        # 2. 表头关键词 (有定义时全部命中)
        hw = getattr(mod, "MATCH_HEADERS", [])
        if hw:
            hits = sum(
                1 for kw in hw
                if any(_norm(kw) == h or _norm(kw) in h for h in header_norm)
            )
            if hits != len(hw):
                continue
        else:
            hits = 0

        if hits > best_hits:
            best = mod
            best_hits = hits

    return best


# ═══════════════════════════════════════════════════
# 列名映射
# ═══════════════════════════════════════════════════

def apply_column_mapping(
    column_mapping: Dict[str, List[str]],
    headers: List[str],
) -> Dict[str, str]:
    """
    用平台的 COLUMN_MAPPING 匹配表头，返回 {标准字段: 原始列名}。
    关键词按长度降序匹配，每字段只匹配一列，每列只使用一次。
    """
    flat: List[tuple[str, str]] = []
    for field, keywords in column_mapping.items():
        for kw in keywords:
            flat.append((kw, field))
    flat.sort(key=lambda x: len(x[0]), reverse=True)

    matched: Dict[str, str] = {}
    used_headers: set = set()
    used_fields: set = set()

    for kw, field in flat:
        if field in used_fields:
            continue
        kw_norm = _norm(kw)
        for header in headers:
            if header in used_headers:
                continue
            if kw_norm in _norm(header):
                matched[field] = header
                used_headers.add(header)
                used_fields.add(field)
                break

    return matched


# ═══════════════════════════════════════════════════
# 表达式计算 (COMPUTED)
# ═══════════════════════════════════════════════════

_SAFE_RE = re.compile(r"^[a-z_0-9+\-*/().\s]+$", re.IGNORECASE)


def compute_field(expr: str, row: Dict[str, Any]) -> Optional[float]:
    """计算表达式，如 'unit_price * tokens'。失败返回 None。"""
    if not expr or not _SAFE_RE.match(expr):
        return None
    try:
        ns: Dict[str, Any] = {}
        for field in set(re.findall(r"[a-z_][a-z_0-9]*", expr, re.IGNORECASE)):
            val = row.get(field, 0)
            try:
                ns[field] = float(val)
            except (ValueError, TypeError):
                ns[field] = 0.0
        result = eval(expr, {"__builtins__": {}}, ns)  # noqa: S307
        if isinstance(result, (int, float)):
            return round(float(result), 6)
    except Exception:
        return None
    return None


def apply_computed_fields(computed: Dict[str, str], row: Dict[str, Any]):
    """按声明顺序执行 COMPUTED 表达式并写回 row（后计算的可引用先计算的结果）。"""
    for field, expr in computed.items():
        val = compute_field(expr, row)
        if val is not None:
            row[field] = val


# ═══════════════════════════════════════════════════
# 价格匹配（依据 PRICING 和账单日期识别 type）
# ═══════════════════════════════════════════════════

def get_price_for_date(pricing_cfg: dict, model: str, bill_date: str) -> dict | None:
    """
    获取某模型在某日期的价格配置。
    pricing_cfg 是 preset.PRICING，结构:
      { "模型名": { "input_hit": 0.5, "input_miss": 2.0,
                    "output": 8.0,
                    "history": [{ "until": "2025-02-01", ... }] } }
    """
    cfg = pricing_cfg.get(model)
    if not cfg:
        return None

    # 检查历史价格（按 until 日期降序，取第一个匹配的）
    history = cfg.get("history", [])
    if history and bill_date:
        for h in sorted(history, key=lambda x: x.get("until", ""), reverse=True):
            if bill_date <= h["until"]:
                return h  # 返回匹配的历史价格

    return cfg  # 返回当前价格


def match_type_by_price(
    pricing_cfg: dict, entry: Dict[str, Any]
) -> Optional[str]:
    """
    用计算出的 unit_price 匹配预设价格表，返回类型标签。
    已精确区分的 type 不再修改。
    """
    typ = entry.get("type", "")
    if typ in ("输入", "输出", "缓存输入"):
        return None  # 已精确区分

    up = entry.get("unit_price", 0.0)
    if up <= 0:
        return None

    model = entry.get("model", "")
    bill_date = entry.get("bill_start", "")
    if not model:
        return None

    price_cfg = get_price_for_date(pricing_cfg, model, bill_date)
    if not price_cfg:
        return None

    best_label = None
    best_diff = float("inf")

    for key, label in [
        ("input_hit", "输入(缓存命中)"),
        ("input_miss", "输入(缓存未命中)"),
        ("output", "输出"),
    ]:
        price = price_cfg.get(key)
        if price is not None:
            diff = abs(up - price)
            if diff < best_diff:
                best_diff = diff
                best_label = label

    return best_label


# ═══════════════════════════════════════════════════
# 预设模块 → 完整解析
# ═══════════════════════════════════════════════════

def get_preset_name(mod: Any) -> str:
    """获取预设名称（优先用模块的 PRESET_NAME，否则用文件名）"""
    if hasattr(mod, "PRESET_NAME"):
        return mod.PRESET_NAME
    return os.path.splitext(os.path.basename(mod.__file__))[0]


def get_pricing_dict(mod: Any) -> dict:
    """获取预设的价格表"""
    return getattr(mod, "PRICING", {}).copy()


def get_model_map(mod: Any) -> Dict[str, str]:
    """获取预设的模型名映射"""
    return getattr(mod, "MODEL_MAP", {}).copy()
