"""review_ui/bp/comments.py — 批注系统

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
bp = Blueprint("comments", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/comments/<book>", methods=["GET"])
def api_comments_list(book):
    """GET /api/comments/<book>?chapter=ch_001 — 列出评论."""
    _ensure_book(book)
    chapter = request.args.get("chapter")
    return jsonify(comm_serv.list_comments(book, chapter))


@bp.route("/api/comments/<book>/<ch>", methods=["POST"])
def api_comments_add(book, ch):
    """POST /api/comments/<book>/<ch>
    body: {author, text, line?, reply_to?}
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        abort(400, description="text required")
    author = (body.get("author") or "wei_chao").strip()
    line = body.get("line")
    reply_to = body.get("reply_to")
    try:
        c = comm_serv.add_comment(
            book, ch, author=author, text=text,
            line=line, reply_to=reply_to,
        )
    except ValueError as e:
        abort(400, description=str(e))
    return jsonify({"ok": True, "comment": c})


@bp.route("/api/comments/<book>/<ch>/<cid>", methods=["DELETE"])
def api_comments_delete(book, ch, cid):
    """DELETE /api/comments/<book>/<ch>/<cid>"""
    _ensure_book(book)
    if not comm_serv.delete_comment(book, ch, cid):
        abort(404, description=f"comment {cid} not found")
    return ("", 204)
