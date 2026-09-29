"""review_ui/bp/notifications.py — 通知中心

从 review_ui/app.py 拆出(Phase 1)。
路由: 3 条   辅助函数: 0 个
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

from flask import abort
from lib import comments as comm_serv
from flask import jsonify
from flask import request

from review_ui.core import _ensure_book
bp = Blueprint("notifications", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/notifications/<book>", methods=["GET"])
def api_notifications_list(book):
    """GET /api/notifications/<book>?user=wei_chao&unread=1 — 列出通知."""
    _ensure_book(book)
    user = request.args.get("user")
    unread_only = request.args.get("unread") in ("1", "true", "yes")
    items = comm_serv.list_notifications(book, user=user, unread_only=unread_only)
    return jsonify({
        "items": items,
        "unread_count": comm_serv.unread_count(book, user) if user else None,
    })


@bp.route("/api/notifications/<book>/<nid>/read", methods=["POST"])
def api_notifications_read(book, nid):
    """POST /api/notifications/<book>/<nid>/read — 标记已读."""
    _ensure_book(book)
    if not comm_serv.mark_notification_read(book, nid):
        abort(404, description=f"notification {nid} not found")
    return jsonify({"ok": True})


@bp.route("/api/notifications/<book>/read-all", methods=["POST"])
def api_notifications_read_all(book):
    """POST /api/notifications/<book>/read-all?user=wei_chao — 全部已读."""
    _ensure_book(book)
    user = request.args.get("user")
    if not user:
        abort(400, description="user query param required")
    n = comm_serv.mark_all_read(book, user)
    return jsonify({"ok": True, "marked": n})
