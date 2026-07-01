"""
config_loader.py — 加载全局 config.yaml + 解析环境变量

用法:
    from lib.config_loader import get_config
    cfg = get_config()["llm"]["api_base"]

支持 env 替换: ${VAR} 或 ${VAR:-default}
"""
from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # 环境未装 pyyaml
    yaml = None

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config.yaml.example"

_cached: dict[str, Any] | None = None


def _expand_env(value: Any) -> Any:
    """递归替换字符串里的 ${VAR} / ${VAR:-default}."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")
        def repl(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")
        return pattern.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        log.warning("PyYAML 未安装, 跳过 %s", path)
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception as e:
        log.error("加载 %s 失败: %s", path, e)
        return {}


def get_config(reload: bool = False) -> dict[str, Any]:
    """
    加载并缓存全局配置.
    顺序: config.yaml > config.yaml.example > 内置默认值.
    """
    global _cached
    if _cached is not None and not reload:
        return _cached

    if not DEFAULT_CONFIG_PATH.exists() and not EXAMPLE_CONFIG_PATH.exists():
        log.warning("未找到 config.yaml / config.yaml.example, 用默认值")
        cfg: dict[str, Any] = _defaults()
    else:
        example = _load_yaml_file(EXAMPLE_CONFIG_PATH)
        cfg_file = _load_yaml_file(DEFAULT_CONFIG_PATH)
        cfg = {**example, **cfg_file}  # 浅合并

    # env 替换
    cfg = _expand_env(cfg)

    # 缺省补全
    for k, v in _defaults().items():
        cfg.setdefault(k, v)

    _cached = cfg
    return _cached


def _defaults() -> dict[str, Any]:
    return {
        "llm": {
            "api_base": "http://127.0.0.1:60443/v1",
            "default_model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "fallback_models": ["Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf"],
            "timeout_sec": 600,
            "max_retries": 3,
        },
        "review_ui": {
            "host": "127.0.0.1",
            "port": 21199,
            "auth": {"enabled": False, "user": "weichao", "password": ""},
            "proxy_prefix": "/novel",
        },
        "backup": {
            "enabled": True,
            "retention_days": 7,
            "schedule_time": "03:00",
            "compress_tar": True,
        },
        "logging": {
            "level": "INFO",
            "file": "logs/novel_workflow.log",
            "rotation": "10MB x 5",
        },
        "projects": {
            "root": "projects",
            "normalize_ascii_book_name": True,
        },
    }


def reset_cache() -> None:
    """测试时清缓存."""
    global _cached
    _cached = None
