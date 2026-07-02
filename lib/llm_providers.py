"""
llm_providers.py — 多 LLM 提供方注册表 (local / deepseek / minimax / openai / custom)

设计:
  - 每个 provider 有独立 api_base / api_key / model 列表
  - LLM 类通过 provider 名字解析连接配置
  - 支持 per-book 覆盖 (config.json 里 llm_provider + llm_model)
  - 向后兼容: 旧 llm.api_base / llm.default_model 仍可用 (兼容 "local" provider)

用法:
    from lib.llm_providers import get_provider_config, list_providers, resolve_model
    
    cfg = get_provider_config("deepseek")    # dict with api_base/api_key/models
    cfg = resolve_model("deepseek", "deepseek-chat")  # 给定 provider+model
    
    # Per-book override:
    cfg = resolve_for_book("mybook")  # reads storage.read_json + global config
"""
from __future__ import annotations

import os
import logging
from typing import Any, Optional

from .config_loader import get_config

log = logging.getLogger(__name__)


# ── 内置 provider 列表 (向后兼容 + 常用云端) ────────────────────────
BUILTIN_PROVIDERS = {
    "local": {
        "type": "openai-compat",
        "api_base": "http://127.0.0.1:60443/v1",
        "api_key": "no-key-needed",
        "default_model": "Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf",
        "models": [
            "Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf",
            "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        ],
        "description": "本地 llama-server (port 60443, 多模态 Qwythos-9B + MoE 35B)",
        "needs_key": False,
    },
    "deepseek": {
        "type": "openai-compat",
        "api_base": "https://api.deepseek.com/v1",
        "api_key": "${DEEPSEEK_API_KEY}",
        "default_model": "deepseek-chat",
        "models": [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-reasoner",
        ],
        "description": "DeepSeek 云端 (deepseek-chat = V3, coder = V2.5 Coder)",
        "needs_key": True,
        "env_key": "DEEPSEEK_API_KEY",
    },
    "minimax": {
        "type": "openai-compat",
        "api_base": "https://api.minimax.chat/v1",
        "api_key": "${MINIMAX_API_KEY}",
        "default_model": "MiniMax-M3",
        "models": [
            "MiniMax-M3",
            "MiniMax-M2.7",
            "MiniMax-M2.5",
        ],
        "description": "MiniMax (MiniMax-M3 / M2.7 / M2.5 系列)",
        "needs_key": True,
        "env_key": "MINIMAX_API_KEY",
    },
    "openai": {
        "type": "openai-compat",
        "api_base": "https://api.openai.com/v1",
        "api_key": "${OPENAI_API_KEY}",
        "default_model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-3.5-turbo",
        ],
        "description": "OpenAI 官方 (gpt-4o-mini 便宜, gpt-4o 强)",
        "needs_key": True,
        "env_key": "OPENAI_API_KEY",
    },
}


def _expand_env_str(s: str) -> str:
    """Expand ${VAR} or ${VAR:-default} in a single string."""
    import re
    if not isinstance(s, str):
        return s
    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")

    def repl(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2)
        return os.environ.get(var, default if default is not None else "")
    return pattern.sub(repl, s)


def _merge_user_providers() -> dict[str, Any]:
    """
    从 config.yaml 读取 user-defined providers, 覆盖/扩展 BUILTIN_PROVIDERS.
    config 格式:
        llm:
          providers:
            custom_openai:
              api_base: https://my-llm.example.com/v1
              api_key: ${MY_LLM_KEY}
              default_model: my-model
              models: [my-model, my-model-fast]
              description: ...
    """
    cfg = get_config()
    user_providers = cfg.get("llm", {}).get("providers", {}) or {}
    merged = dict(BUILTIN_PROVIDERS)
    for name, p in user_providers.items():
        if name in merged:
            # Merge: user can override any field
            merged[name] = {**merged[name], **p}
        else:
            # New provider
            p.setdefault("type", "openai-compat")
            p.setdefault("models", [p.get("default_model", "default")])
            merged[name] = p
    return merged


def list_providers() -> dict[str, dict[str, Any]]:
    """Return all known providers (built-in + user-defined from config.yaml)."""
    return _merge_user_providers()


def get_provider_config(provider: str) -> dict[str, Any]:
    """
    Return a copy of the provider config with ${VAR} placeholders expanded.
    Raises KeyError if provider is unknown.
    """
    providers = _merge_user_providers()
    if provider not in providers:
        raise KeyError(
            f"Unknown provider: {provider!r}. "
            f"Available: {', '.join(sorted(providers.keys()))}"
        )
    p = dict(providers[provider])
    p["api_key"] = _expand_env_str(p.get("api_key", ""))
    p["api_base"] = _expand_env_str(p.get("api_base", ""))
    return p


def resolve_model(provider: str, model: Optional[str] = None) -> dict[str, Any]:
    """
    Given a provider name (and optional model), return a dict with:
        {provider, model, api_base, api_key, type}
    
    If model is None, uses provider's default_model.
    Raises KeyError if provider/model invalid.
    """
    p = get_provider_config(provider)
    model = model or p.get("default_model")
    if model and model not in p.get("models", []):
        # Allow but warn
        log.warning(
            "Model %r not in provider %r's known list (%s). Using anyway.",
            model, provider, p.get("models", [])
        )
    return {
        "provider": provider,
        "model": model,
        "api_base": p["api_base"],
        "api_key": p["api_key"],
        "type": p.get("type", "openai-compat"),
    }


def resolve_for_book(book: str, fallback_provider: str = "local") -> dict[str, Any]:
    """
    Resolve LLM config for a specific book.
    
    Order:
    1. Book's config.json llm_provider + llm_model (per-book override)
    2. Global config.yaml llm.default_model + llm.api_base (legacy: maps to "local" provider)
    3. fallback_provider argument
    """
    try:
        from . import storage
        book_cfg = storage.read_json(book, "config.json") or {}
    except Exception:
        book_cfg = {}

    # Per-book override
    book_provider = book_cfg.get("llm_provider")
    book_model = book_cfg.get("llm_model")
    
    if book_provider:
        return resolve_model(book_provider, book_model)
    
    if book_model:
        # Book has model but no provider — assume local for back-compat
        log.info("Book %r has llm_model but no llm_provider; assuming 'local'", book)
        return resolve_model("local", book_model)
    
    # Global config fallback
    cfg = get_config()
    llm_cfg = cfg.get("llm", {})
    global_model = llm_cfg.get("default_model")
    
    # If the global default_model matches a provider's model, use that provider
    if global_model:
        for name, p in _merge_user_providers().items():
            if global_model in p.get("models", []):
                return resolve_model(name, global_model)
        # Otherwise use fallback_provider with that model
        try:
            return resolve_model(fallback_provider, global_model)
        except KeyError:
            pass
    
    return resolve_model(fallback_provider)


def validate_provider(provider: str) -> dict[str, Any]:
    """
    Validate a provider is usable (e.g., API key set if needed).
    Returns dict with: ok (bool), reason (str|None), config (resolved).
    """
    try:
        cfg = get_provider_config(provider)
    except KeyError as e:
        return {"ok": False, "reason": str(e), "config": None}
    
    # Check API key
    if cfg.get("needs_key", False) and not cfg.get("api_key"):
        env_key = BUILTIN_PROVIDERS.get(provider, {}).get("env_key", f"{provider.upper()}_API_KEY")
        return {
            "ok": False,
            "reason": f"Provider {provider!r} needs API key. Set env {env_key} or config llm.providers.{provider}.api_key",
            "config": None,
        }
    
    return {"ok": True, "reason": None, "config": cfg}


def health_check(provider: str, model: Optional[str] = None, timeout: float = 10.0) -> dict[str, Any]:
    """
    Test connectivity to a provider by listing models.
    Returns: {ok, status, models, latency_ms, error}
    """
    import time
    t0 = time.time()
    try:
        cfg = resolve_model(provider, model)
    except KeyError as e:
        return {"ok": False, "status": "config_error", "error": str(e), "latency_ms": 0}
    
    if not cfg["api_key"]:
        return {
            "ok": False, "status": "no_api_key", 
            "error": f"Provider {provider!r} has empty api_key",
            "latency_ms": 0,
        }
    
    try:
        from openai import OpenAI
        client = OpenAI(base_url=cfg["api_base"], api_key=cfg["api_key"], timeout=timeout)
        models = client.models.list()
        latency_ms = int((time.time() - t0) * 1000)
        model_list = [m.id for m in models.data] if hasattr(models, "data") else []
        return {
            "ok": True, "status": "ok", 
            "models": model_list[:20],  # cap
            "latency_ms": latency_ms,
            "endpoint": cfg["api_base"],
        }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False, "status": "request_error",
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": latency_ms,
            "endpoint": cfg["api_base"],
        }


def format_providers_table() -> str:
    """Return a formatted table of all providers (for CLI display)."""
    lines = []
    lines.append(f"{'Name':<14} {'Type':<14} {'Endpoint':<40} {'Models':<3} {'Status'}")
    lines.append("-" * 100)
    for name, p in _merge_user_providers().items():
        endpoint = _expand_env_str(p.get("api_base", ""))
        # Mask endpoint if too long
        if len(endpoint) > 38:
            endpoint = endpoint[:35] + "..."
        n_models = len(p.get("models", []))
        validation = validate_provider(name)
        status = "OK" if validation["ok"] else "NO KEY" if "key" in (validation["reason"] or "").lower() else "?"
        lines.append(
            f"{name:<14} {p.get('type','?'):<14} {endpoint:<40} {n_models:<3} {status}"
        )
    return "\n".join(lines)