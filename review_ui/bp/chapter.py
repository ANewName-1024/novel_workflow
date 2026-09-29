"""review_ui/bp/chapter.py — 章节正文 / 章节上下文 / 章节 diff / 反馈应用

从 review_ui/app.py 拆出(Phase 1)。
路由: 9 条   辅助函数: 0 个
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

from flask import Response
from flask import abort
from flask import jsonify
from flask import request
from lib import storage
from lib import version as ver_serv

from review_ui.core import _ensure_book
bp = Blueprint("chapter", __name__,
               template_folder=str(_UI / "templates"),
               static_folder=str(_UI / "static"),
               static_url_path="/static")


@bp.route("/api/chapter/<book>/<ch>")
def api_chapter(book, ch):
    _ensure_book(book)
    text = storage.read_chapter(book, ch)
    if text is None:
        abort(404)
    return Response(text, mimetype="text/plain; charset=utf-8")


@bp.route("/api/chapter/<book>/<ch>/entity-diff")
def api_chapter_entity_diff(book, ch):
    """GET /api/chapter/<book>/<ch>/entity-diff — 本章节实体变化记录 (v1.3 M4)."""
    _ensure_book(book)
    from lib import entity_diff as edmod
    diff = edmod.get_chapter_changes(book, ch)
    if diff is None:
        return jsonify({"ok": False, "error": "无实体变化记录"})
    summary = edmod.summarize_changes(diff)
    return jsonify({"ok": True, "diff": diff, "summary": summary})


@bp.route("/api/chapter/<book>/<ch>/apply-feedback", methods=["POST"])
def api_apply_feedback(book, ch):
    """POST /api/chapter/<book>/<ch>/apply-feedback — 根据评审反馈自动修订章节 (v1.3 M4).

    可选 body: {"dry_run": true} — 不保存，只返回 AI 修订内容预览.
    """
    _ensure_book(book)
    from lib import review_actions as ramod
    dry_run = (request.get_json(silent=True) or {}).get("dry_run", False)
    result = ramod.apply_feedback_to_chapter(book, ch, dry_run=dry_run, save=not dry_run)
    if not result.get("ok"):
        abort(400, description=result.get("error", "apply failed"))
    return jsonify(result)


@bp.route("/api/chapter/<book>/<ch>/versions", methods=["GET"])
def api_chapter_versions_list(book, ch):
    """GET /api/chapter/<book>/<ch>/versions — 列表(不含content)."""
    _ensure_book(book)
    return jsonify(ver_serv.list_versions(book, ch))


@bp.route("/api/chapter/<book>/<ch>/versions/<vid>", methods=["GET"])
def api_chapter_versions_get(book, ch, vid):
    """GET /api/chapter/<book>/<ch>/versions/<vid> — 读完整版本(含content)."""
    _ensure_book(book)
    rec = ver_serv.get_version(book, ch, vid)
    if not rec:
        abort(404, description=f"version {vid} not found")
    return jsonify(rec)


@bp.route("/api/chapter/<book>/<ch>/revert/<vid>", methods=["POST"])
def api_chapter_revert(book, ch, vid):
    """POST /api/chapter/<book>/<ch>/revert/<vid> — 回滚到某版本.
    body: {"by": "wei_chao"}
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    by = (body.get("by") or "wei_chao").strip()
    try:
        rec = ver_serv.revert_to(book, ch, vid, by=by)
    except ValueError as e:
        abort(400, description=str(e))
    return jsonify({"ok": True, "version": rec})


@bp.route("/api/chapter/<book>/<ch>/diff-versions", methods=["GET"])
def api_chapter_diff_versions(book, ch):
    """GET /api/chapter/<book>/<ch>/diff-versions?v1=v001&v2=v002 — 两版本 diff."""
    _ensure_book(book)
    v1 = request.args.get("v1")
    v2 = request.args.get("v2")
    if not v1 or not v2:
        abort(400, description="v1 and v2 required")
    try:
        return jsonify(ver_serv.diff_versions(book, ch, v1, v2))
    except ValueError as e:
        abort(404, description=str(e))


@bp.route("/api/chapter/<book>/diff-chapters/<ch1>/<ch2>", methods=["GET"])
def api_chapter_diff_chapters(book, ch1, ch2):
    """GET /api/chapter/<book>/diff-chapters/<ch1>/<ch2> — 跨章节 diff (v1.4).

    对比两章当前内容, 返回 unified diff + 统计.
    """
    _ensure_book(book)
    try:
        return jsonify(ver_serv.diff_chapters(book, ch1, ch2))
    except ValueError as e:
        abort(404, description=str(e))


@bp.route("/api/chapter/<book>/<ch>/context")
def api_chapter_context(book, ch):
    """GET /api/chapter/<book>/<ch>/context?line=42&window=3
    返回 {line, target, before: [...], after: [...]} 上下文片段.
    """
    _ensure_book(book)
    text = storage.read_chapter(book, ch)
    if text is None:
        abort(404, description=f"chapter {ch} not found")
    try:
        line = int(request.args.get("line", 0))
    except ValueError:
        abort(400, description="line must be int")
    window = int(request.args.get("window", 3))
    lines = text.splitlines()
    if line < 1 or line > len(lines):
        abort(400, description=f"line {line} out of range [1, {len(lines)}]")
    start = max(0, line - 1 - window)
    end = min(len(lines), line - 1 + window + 1)
    return jsonify({
        "line": line,
        "before": [{"line_no": i + 1, "text": lines[i]} for i in range(start, line - 1)],
        "target": {"line_no": line, "text": lines[line - 1]},
        "after": [{"line_no": i + 1, "text": lines[i]} for i in range(line, end)],
    })
