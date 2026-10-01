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
import logging
import os
from flask import request
from lib import storage

from review_ui.core import _ensure_book
from .projects import _validate_book_slug

log = logging.getLogger("novel.review_ui.llm_config")
if not log.handlers:
    log.addHandler(logging.NullHandler())

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

    # get_provider_config 对未知 provider 是【抛 KeyError】而不是返回 None,
    # 所以下面 `if not pcfg: return 400` 曾经是死代码, 未知 provider 直接 500。
    # 顺手修正: 未知 provider 是调用方的输入错误, 该给 400。
    try:
        pcfg = lp.get_provider_config(provider)
    except KeyError:
        return jsonify({"ok": False, "error": f"未知 provider: {provider}"}), 400
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
        log.warning("LLM 健康检查连接失败 (provider=%s): %s: %s",
                    provider, type(e).__name__, e, exc_info=True)
        return jsonify({"ok": False, "error": "连接失败", "provider": provider}), 502
    except Exception as e:
        # 过去是 str(e) 直接回显。HTTPError 的 str() 带 response text,
        # ConnectionError 的带目标 URL —— 相当于把内网拓扑和上游返回原文
        # 交给未认证的调用方。细节进日志, 对外只给类型与状态码。
        log.error("LLM 健康检查失败 (provider=%s): %s: %s",
                  provider, type(e).__name__, e, exc_info=True)
        return jsonify({"ok": False, "error": type(e).__name__,
                        "provider": provider}), 500


@bp.route("/api/config/<book>")
def api_book_config_read(book):
    """GET /api/config/<book> — 读取 book 配置."""
    err = _validate_book_slug(book)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not storage.project_exists(book):
        return jsonify({"ok": False, "error": f"项目不存在: {book}"}), 404
    cfg = storage.read_json(book, "config.json") or {}
    return jsonify({"ok": True, **cfg})


@bp.route("/api/config/<book>", methods=["POST"])
def api_book_config_write(book):
    """POST /api/config/<book> — 更新 book 配置 (部分更新).

    2026-10-01 修复: 这条路径过去既不校验 slug 也不要求书已存在 ——
    read_json 返回 None → cfg = {} → cfg.update(attacker_body) → write_json,
    于是任意 POST 都能凭空造出一本「书」(projects/<name>/config.json),
    且完全绕开了 bp/projects.py 里唯一的 _validate_book_slug。
    叠加「公网可达 + 鉴权关闭」, 这是一个可被外部无限造项目的接口。
    """
    err = _validate_book_slug(book)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    # 必须已存在。这条路由是「改配置」, 不是「建项目」—— 建项目走 /api/projects。
    if not storage.project_exists(book):
        return jsonify({"ok": False, "error": f"项目不存在: {book}"}), 404

    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"ok": False, "error": "空 body"}), 400
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "body 必须是 JSON 对象"}), 400

    cfg = storage.read_json(book, "config.json") or {}
    cfg.update(body)
    storage.write_json(book, "config.json", cfg)

    # 同步 SQLite 镜像。GET /api/projects 是 SQLite 优先 + 文件兜底,
    # 过去这里只写文件, 于是改完书名在项目列表里不更新, 用户看着旧值毫无提示。
    try:
        from lib import db as _dbmod
        _dbmod.upsert_project(storage.ROOT, book, cfg.get("book_name", book), cfg)
    except Exception as e:
        log.warning("书籍配置镜像写入 SQLite 失败, 读回将走文件兜底 (book=%s): %s: %s",
                    book, type(e).__name__, e, exc_info=True)

    return jsonify({"ok": True, "message": "已保存"})
