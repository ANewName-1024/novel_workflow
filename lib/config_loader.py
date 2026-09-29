"""
config_loader.py — 加载全局 config.yaml + 解析环境变量

用法:
    from lib.config_loader import get_config
    cfg = get_config()["llm"]["api_base"]
    get_config(reload=True)          # 显式失效并重载
    from lib.config_loader import reload_config
    reload_config()                  # 同上,更明确的名字

支持 env 替换: ${VAR} 或 ${VAR:-default}

Phase 4 设计
-----------
旧实现用 `global _cached` + `global _DOTENV_LOADED` 做懒加载,带来三个问题:
  1. 状态可变且不可见 —— 何时失效只能靠翻源码
  2. 返回的是缓存本体,任何调用方就地改一下就污染了全局配置
  3. `reload=True` 混在读接口的参数里,读路径和写路径缠在一起

新实现:
  - 内部用 @lru_cache(maxsize=1) 承担缓存,去掉 global
  - 读接口每次返回深拷贝,调用方拿到的是自己的快照,改不坏缓存
  - 失效走显式的 reload_config();get_config(reload=True) 保留为兼容路径
  - reset_cache() 保留为 reload_config() 的别名(测试与旧调用方仍在用)
"""
from __future__ import annotations

import copy
import logging
import os
import re
from functools import lru_cache
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

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


# ── .env 自动加载 ──────────────────────────────────────────────────────────
def _load_dotenv() -> None:
    """把 .env 读进 os.environ(幂等,无三方依赖)。

    只在配置加载时调用一次 —— 缓存失效重载时会再跑一次,这是刻意的:
    重载就应该重新看到当前环境变量。
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # 不覆盖已存在的环境变量
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


# ── 载入(被缓存)───────────────────────────────────────────────────────────
def _expand_env(value: Any) -> Any:
    """递归替换字符串里的 ${VAR} / ${VAR:-default}。"""

    def repl(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2)
        return os.environ.get(var, default if default is not None else "")

    if isinstance(value, str):
        return _ENV_PATTERN.sub(repl, value)
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
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.error("加载 %s 失败: %s", path, e)
        return {}


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
        "dashboard": {
            "enabled": True,
            "log_tail_default": 100,
            "log_max_buffer": 500,
            "metrics_retention_days": 30,
            "cancel_grace_seconds": 5,
            "stream_poll_interval": 1.0,
        },
    }


@lru_cache(maxsize=1)
def _config_snapshot() -> dict[str, Any]:
    """构建一次配置快照。顺序: config.yaml > config.yaml.example > 内置默认。

    缓存命中时不会重复读盘。失效入口是 reload_config()。
    """
    # 先把 .env 灌进 os.environ,${VAR} 占位符才解析得到值
    _load_dotenv()

    if not DEFAULT_CONFIG_PATH.exists() and not EXAMPLE_CONFIG_PATH.exists():
        log.warning("未找到 config.yaml / config.yaml.example, 用默认值")
        cfg: dict[str, Any] = _defaults()
    else:
        example = _load_yaml_file(EXAMPLE_CONFIG_PATH)
        cfg_file = _load_yaml_file(DEFAULT_CONFIG_PATH)
        cfg = {**example, **cfg_file}  # 浅合并

    cfg = _expand_env(cfg)

    for k, v in _defaults().items():
        cfg.setdefault(k, v)

    return cfg


# ── 公开接口 ──────────────────────────────────────────────────────────────
def get_config(reload: bool = False) -> dict[str, Any]:
    """返回当前配置的**深拷贝**。

    每次调用都拿到独立副本 —— 调用方可以放心就地改,不会污染全局。
    代价是一次 deepcopy;配置体量小(几十个键),实测可忽略。
    若确实需要零拷贝,那说明调用方该自己持有一份配置,而不是共享全局。

    reload=True 保留为兼容路径,新代码请显式调用 reload_config()。
    """
    if reload:
        reload_config()
    return copy.deepcopy(_config_snapshot())


def reload_config() -> None:
    """使缓存失效。下一次 get_config() 会重新读盘并重新展开环境变量。"""
    _config_snapshot.cache_clear()


def reset_cache() -> None:
    """reset_cache 的旧名,等价于 reload_config()。保留仅为向后兼容。"""
    reload_config()
