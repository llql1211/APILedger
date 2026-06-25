"""
APILedger - 配置管理

从 data/api_ledger_config.json 加载配置, 提供:
- 模型名称对照 (各平台 → 统一名称)
- 各平台各模型单价表 (用于辅助识别类型)

配置文件为 JSON 格式, 结构:

{
  "model_mapping": {
    "DeepSeek": { "deepseek-chat": "DeepSeek-V3" },
    "Azure": { "gpt-4o": "GPT-4o" }
  },
  "pricing": {
    "OpenAI": {
      "GPT-4o": { "input": 30.0, "output": 60.0 }
    }
  }
}
"""

import json
import os
from typing import Dict, Optional

# 配置文件路径 (项目根目录 / data / api_ledger_config.json)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "api_ledger_config.json")

# 全局缓存
_config_cache: Optional[Dict] = None


def _load_config() -> dict:
    """加载配置文件, 返回完整 dict (不存在时返回空 dict)"""
    if not os.path.isfile(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_config(force_reload: bool = False) -> dict:
    """获取配置 (带缓存)"""
    global _config_cache
    if _config_cache is None or force_reload:
        _config_cache = _load_config()
    return _config_cache


def save_config(cfg: dict):
    """保存配置到文件"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    global _config_cache
    _config_cache = cfg


def get_model_mapping() -> Dict[str, Dict[str, str]]:
    """
    获取模型名称对照表。
    返回: { "平台名": { "平台原始模型名": "统一模型名" } }
    """
    cfg = get_config()
    return cfg.get("model_mapping", {})


def get_pricing() -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    获取单价表。
    返回: { "平台名": { "模型名": { "input": float, "output": float } } }
    """
    cfg = get_config()
    return cfg.get("pricing", {})


def normalize_model_name(platform: str, raw_model: str) -> str:
    """
    根据配置的模型对照表, 将平台原始模型名转换为统一名称。
    若无映射则返回原名称。
    """
    mapping = get_model_mapping()
    platform_map = mapping.get(platform, {})
    return platform_map.get(raw_model, raw_model)
