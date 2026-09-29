"""review_ui/bp/review.py — 评审队列 / 通过 / 驳回 / 编辑 / 批量 / 统计 / 历史

从 review_ui/app.py 拆出(Phase 1)。
路由: 12 条   辅助函数: 2 个
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
import difflib
import json
from flask import jsonify
from flask import request
from lib import review_service as revserv
from lib import storage

from review_ui.core import _ensure_book
bp = Blueprint("review", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/queue/<book>")
def api_queue(book):
    _ensure_book(book)
    _ensure_review_backfill(book)
    return jsonify(revserv.get_review_queue(book))


@bp.route("/api/review/<book>/<ch>")
def api_review(book, ch):
    _ensure_book(book)
    r = revserv.get_review(book, ch)
    if not r:
        abort(404, description=f"No review for {ch}")
    return jsonify(r)


@bp.route("/api/stats/<book>")
def api_stats(book):
    _ensure_book(book)
    return jsonify(revserv.get_review_stats(book))


@bp.route("/api/history/<book>")
def api_history(book):
    _ensure_book(book)
    log_path = revserv.audit_log_path(book)
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return jsonify({
        "stats": revserv.get_review_stats(book),
        "queue": revserv.get_review_queue(book),
        "audit_log": log.strip().splitlines() if log.strip() else [],
    })


@bp.route("/api/approve/<book>/<ch>", methods=["POST"])
def api_approve(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    reviewer = body.get("reviewer", "wei_chao")
    notes = body.get("notes", "")
    record = revserv.approve(book, ch, reviewer, notes)
    # v1.3 M4: log
    try:
        from lib import session_log as _slog
        _slog.hook_review_action(book, ch, "approve", notes)
    except Exception:
        pass
    return jsonify({"ok": True, "status": record["status"]})


@bp.route("/api/reject/<book>/<ch>", methods=["POST"])
def api_reject(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    if not body.get("reason"):
        abort(400, description="reason required")
    record = revserv.reject(book, ch, body.get("reviewer", "wei_chao"), body["reason"])
    try:
        from lib import session_log as _slog
        _slog.hook_review_action(book, ch, "reject", body.get("reason", ""))
    except Exception:
        pass
    return jsonify({"ok": True, "status": record["status"]})


@bp.route("/api/edit/<book>/<ch>", methods=["POST"])
def api_edit(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    text = body.get("text", "").strip()
    if not text:
        abort(400, description="text required")
    reviewer = body.get("reviewer", "wei_chao")
    try:
        from lib import session_log as _slog
        _slog.hook_review_action(book, ch, "edit")
    except Exception:
        pass
    notes = body.get("notes", "")
    apply = bool(body.get("apply", False))
    record = revserv.edit(book, ch, reviewer, text, notes)
    applied = False
    if apply:
        applied = revserv.apply_edit_to_chapter(book, ch)
    return jsonify({
        "ok": True,
        "status": record["status"],
        "applied": applied,
        "v2_chars": len(text),
    })


@bp.route("/api/false-positive/<book>/<ch>", methods=["POST"])
def api_false_positive(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    if not body.get("notes"):
        abort(400, description="notes required")
    record = revserv.mark_false_positive(book, ch, body.get("reviewer", "wei_chao"), body["notes"])
    return jsonify({"ok": True, "status": record["status"]})


@bp.route("/api/batch-approve/<book>", methods=["POST"])
def api_batch_approve(book):
    """M5: 批量批准. body: {"chapters": ["ch_001", ...], "reviewer": "...", "notes": "..."}.
    每条独立处理, 部分失败不中断, 返回 ok/失败明细.
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    chapter_ids = body.get("chapters") or []
    if not isinstance(chapter_ids, list) or not chapter_ids:
        abort(400, description="chapters (non-empty list) required")
    reviewer = (body.get("reviewer") or "wei_chao").strip()
    notes = (body.get("notes") or "Web UI 批量批准").strip()
    results = []
    for cid in chapter_ids:
        try:
            if not isinstance(cid, str) or not cid.startswith("ch_"):
                raise ValueError(f"invalid chapter id: {cid!r}")
            rec = revserv.approve(book, cid, reviewer, notes)
            results.append({"id": cid, "ok": True, "status": rec["status"]})
        except Exception as e:
            results.append({"id": cid, "ok": False, "error": str(e)})
    n_ok = sum(1 for r in results if r["ok"])
    n_fail = len(results) - n_ok
    return jsonify({"ok": n_fail == 0,
                    "total": len(results),
                    "approved": n_ok,
                    "failed": n_fail,
                    "results": results})


@bp.route("/api/batch-reject/<book>", methods=["POST"])
def api_batch_reject(book):
    """M2: 批量拒绝. body: {"chapters": [...], "reviewer": "...", "reason": "..."}.
    与 batch-approve 对称的拒绝路径, 需 reason.
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    chapter_ids = body.get("chapters") or []
    if not isinstance(chapter_ids, list) or not chapter_ids:
        abort(400, description="chapters (non-empty list) required")
    reason = (body.get("reason") or "").strip()
    if not reason:
        abort(400, description="reason required")
    reviewer = (body.get("reviewer") or "wei_chao").strip()
    results = []
    for cid in chapter_ids:
        try:
            if not isinstance(cid, str) or not cid.startswith("ch_"):
                raise ValueError(f"invalid chapter id: {cid!r}")
            rec = revserv.reject(book, cid, reviewer, reason)
            results.append({"id": cid, "ok": True, "status": rec["status"]})
        except Exception as e:
            results.append({"id": cid, "ok": False, "error": str(e)})
    n_ok = sum(1 for r in results if r["ok"])
    n_fail = len(results) - n_ok
    return jsonify({"ok": n_fail == 0,
                    "total": len(results),
                    "rejected": n_ok,
                    "failed": n_fail,
                    "results": results})


@bp.route("/api/queue/<book>/filtered")
def api_queue_filtered(book):
    """M2: 评审队列过滤. ?severity=critical|moderate|minor&status=pending_review
    返回过滤后的列表, 在原 list 基础上按严重度/状态过滤.
    """
    _ensure_book(book)
    _ensure_review_backfill(book)
    queue = revserv.get_review_queue(book)
    severity = request.args.get("severity")
    status = request.args.get("status")
    items = queue
    if severity:
        items = [q for q in items if q.get("auto_severity") == severity]
    if status:
        items = [q for q in items if q.get("status") == status]
    return jsonify(items)


@bp.route("/api/diff/<book>/<ch>")
def api_diff(book, ch):
    """M5: 返回 v1 vs v2 unified diff (纯 API, 模板不用)."""
    _ensure_book(book)
    text = storage.read_chapter(book, ch) or ""
    v2_path = revserv.edited_path(book, ch)
    if not v2_path.exists():
        return jsonify({"has_diff": False, "diff": [], "stats": None})
    v2_text = v2_path.read_text(encoding="utf-8")
    diff_lines = list(difflib.unified_diff(
        text.splitlines(),
        v2_text.splitlines(),
        fromfile="v1 (原版)",
        tofile="v2 (人工)",
        lineterm="",
        n=3,
    ))
    return jsonify({"has_diff": True,
                    "diff": diff_lines,
                    "stats": _diff_stats(text, v2_text)})


def _ensure_review_backfill(book: str) -> None:
    """Same backfill as cmd_review_queue."""
    chapters = storage.list_chapters(book)
    for ch in chapters:
        if not revserv.get_review(book, ch["id"]):
            sc_path = storage.project_root(book) / "self_checks" / f"{ch['id']}.json"
            sc_result = None
            if sc_path.exists():
                try:
                    sc_result = json.loads(sc_path.read_text(encoding="utf-8"))
                except Exception:
                    sc_result = None
            if sc_result:
                revserv.auto_flag(book, ch["id"], sc_result, by="AI-backfill")
            else:
                empty = revserv._empty_record(ch["id"])
                empty["status"] = revserv.REVIEW_STATUS["AUTO_PASSED"]
                revserv.save_review(book, empty)


def _diff_stats(text1: str, text2: str) -> dict:
    """原始行数和 v2 行数的快速统计 + 字符级变动统计."""
    a = text1.splitlines()
    b = text2.splitlines()
    # 行级: 加/减/同
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            added += j2 - j1
            removed += i2 - i1
    # 字符级净变动
    char_matcher = difflib.SequenceMatcher(None, text1, text2, autojunk=False)
    char_add = char_del = 0
    for tag, i1, i2, j1, j2 in char_matcher.get_opcodes():
        if tag == "insert":
            char_add += j2 - j1
        elif tag == "delete":
            char_del += i2 - i1
        elif tag == "replace":
            char_add += j2 - j1
            char_del += i2 - i1
    return {"v1_lines": len(a), "v2_lines": len(b),
            "v1_chars": len(text1), "v2_chars": len(text2),
            "lines_added": added, "lines_removed": removed,
            "chars_added": char_add, "chars_removed": char_del,
            "net_change": len(text2) - len(text1)}
