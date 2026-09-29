"""review_ui/bp/llm_config.py — LLM provider 列表 / 健康检查 / 书籍级配置读写

从 review_ui/app.py 拆出(Phase 1)。
路由: 4 条   辅助函数: 0 个
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request, render_template, abort, Response, \
    session, redirect, url_for

_HERE = Path(__file__).resolve().parent
_UI = _HERE.parent
if str(_UI.parent) not in sys.path:
    sys.path.insert(0, str(_UI.parent))
if str(_UI.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_UI.parent / "lib"))

from flask import jsonify
import os
from flask import request
from lib import storage

from review_ui.core import _ensure_book
bp = Blueprint("llm_config", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/llm/providers")
def api_llm_providers():
    """GET /api/llm/providers — 列所有 provider 配置 (不暴露完整 key)."""
    from lib import llm_providers as lp
    providers = {}
    # Use merged config (BUILTIN + user-defined from config.yaml)
    merged = lp._merge_user_providers()
    for name, cfg in sorted(merged.items()):
        providers[name] = {
            "model": cfg.get("default_model", ""),
            "api_base": cfg.get("api_base", ""),
            "api_key_configured": bool(cfg.get("api_key")),
            "models": cfg.get("models", []),
        }
    return jsonify({
        "ok": True,
        "providers": providers,
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "local"),
        "current_model": os.environ.get("MODEL", ""),
    })


@bp.route("/api/llm/health", methods=["POST"])
def api_llm_health_check():
    """POST /api/llm/health — 测试指定 provider 连通性.

    Body: {"provider": "deepseek", "model": "deepseek-chat"} (可选, 默认测试 book 当前配置)
    """
    body = request.get_json(silent=True) or {}
    provider = body.get("provider", "")
    model = body.get("model", "")
    book = body.get("book", "")

    from lib import llm_providers as lp
    if book:
        cfg = storage.read_json(book, "config.json") or {}
        provider = provider or cfg.get("llm_provider", "local")
    provider = provider or "local"

    pcfg = lp.get_provider_config(provider)
    if not pcfg:
        return jsonify({"ok": False, "error": f"未知 provider: {provider}"}), 400

    model = model or pcfg.get("model") or pcfg.get("default_model", "")
    api_base = pcfg.get("api_base", "")
    api_key = pcfg.get("api_key", "")

    import requests
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "1"}],
        "max_tokens": 2,
        "temperature": 0,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        return jsonify({"ok": True, "http_status": r.status_code,
                        "model": model, "provider": provider})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "超时 (>10s)", "provider": provider}), 504
    except requests.exceptions.ConnectionError as e:
        return jsonify({"ok": False, "error": f"连接失败: {e}", "provider": provider}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "provider": provider}), 500


@bp.route("/api/config/<book>")
def api_book_config_read(book):
    """GET /api/config/<book> — 读取 book 配置."""
    cfg = storage.read_json(book, "config.json") or {}
    return jsonify({"ok": True, **cfg})


@bp.route("/api/config/<book>", methods=["POST"])
def api_book_config_write(book):
    """POST /api/config/<book> — 更新 book 配置 (部分更新)."""
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"ok": False, "error": "空 body"}), 400
    cfg = storage.read_json(book, "config.json") or {}
    cfg.update(body)
    storage.write_json(book, "config.json", cfg)
    return jsonify({"ok": True, "message": "已保存"})
