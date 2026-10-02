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

from review_ui.core import _ensure_book, pick_config_updates
from .projects import _validate_book_slug

log = logging.getLogger("novel.review_ui.llm_config")
if not log.handlers:
    log.addHandler(logging.NullHandler())

bp = Blueprint("llm_config", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/llm/providers")
def api_llm_providers():
    """GET /api/llm/providers — 列所有 provider 配置 (不暴露完整 key).

    可选 query: ?book=<slug> —— 让 current_model 报那本书真实会用到的模型
    (不传 = 全局解析, 即「没有书籍级覆盖时会用什么」)。
    (book 从 request.args 读, 不用视图参数: 这条路由的 rule 里没有 <book>
     占位符, 本机 Flask 版本不会把 query string 填进视图参数。)

    2026-10-02 (P7): current_model 过去是 os.environ.get("MODEL", ""), 而
    生产环境根本没设这个变量 —— 这个字段恒为空, 页面上「当前模型」那一行
    永远显示成没有。真正生效的模型是 lib/llm_providers.py:resolve_for_book
    按「书籍 config.json 覆盖 > 全局 config.yaml > provider 默认」解析出来的,
    这里直接走同一条路径, 不另造一套解析。
    """
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
    default_provider = os.environ.get("DEFAULT_PROVIDER", "local")
    return jsonify({
        "ok": True,
        "providers": providers,
        "default_provider": default_provider,
        "current_model": _effective_model(lp, request.args.get("book", ""),
                                          default_provider, providers),
    })


def _effective_model(lp, book, default_provider: str, providers: dict) -> str:
    """这本书(或全局)真实会用到的模型名 —— 走 llm_providers.resolve_for_book。

    解析失败(书籍 cfg 里写了个没注册的 provider、config.yaml 坏了等)不让
    整条接口 500: 退回 default_provider 的默认模型, 页面至少有值可显示,
    真实报错由 log 带出去。
    """
    try:
        return lp.resolve_for_book(book or "", fallback_provider=default_provider)["model"] or ""
    except Exception as e:
        log.warning("解析生效模型失败, 回退到 default_provider 默认值 (book=%r): %s: %s",
                    book, type(e).__name__, e, exc_info=True)
        return providers.get(default_provider, {}).get("model", "")


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

    # 2026-10-02 (P1): 过去是 cfg.update(body) —— 调用方发的任何 key 都直接
    # 写进 config.json, 然后回一句「已保存」。实测 POST {"bogus_field": 1}
    # 得到 200 已保存, 之后 GET 把这个 key 原样读回来; 而 llm_model /
    # api_base / llm_provider 是生成流水线要读的字段, 界面上一处拼写错误
    # 就静默落盘, 静默改变后面章节的生成方式。
    # 现在按 review_ui/core.py:EDITABLE_CONFIG_FIELDS 过滤(与 PUT
    # /api/projects/<book> 同一份名单), 并把被丢掉的 key 回报给调用方 ——
    # 拼错了要看得见, 不能只是「不写」。
    try:
        updates = pick_config_updates(body)
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"字段类型非法: {e}"}), 400
    if not updates:
        # 一个可写字段都没有 —— 这是调用方的错(键名拼错/写错了地方),
        # 不是一次成功的保存。一个字节都不写, 连 updated_at 都不盖。
        return jsonify({
            "ok": False,
            "error": "没有可写的字段: 请求里的 key 全都不在可写白名单内",
            "ignored": sorted(body.keys()),
        }), 400

    ignored = sorted(k for k in body if k not in updates)
    cfg = storage.read_json(book, "config.json") or {}
    cfg.update(updates)
    storage.write_json(book, "config.json", cfg)

    # 同步 SQLite 镜像。GET /api/projects 是 SQLite 优先 + 文件兜底,
    # 过去这里只写文件, 于是改完书名在项目列表里不更新, 用户看着旧值毫无提示。
    try:
        from lib import db as _dbmod
        _dbmod.upsert_project(storage.ROOT, book, cfg.get("book_name", book), cfg)
    except Exception as e:
        log.warning("书籍配置镜像写入 SQLite 失败, 读回将走文件兜底 (book=%s): %s: %s",
                    book, type(e).__name__, e, exc_info=True)

    return jsonify({"ok": True, "message": "已保存",
                    "changed": sorted(updates), "ignored": ignored})
