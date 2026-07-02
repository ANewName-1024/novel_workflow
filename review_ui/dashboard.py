"""
review_ui/dashboard.py - v1.1 Web 娴佹按绾跨鐞嗛潰鏉?(API 钃濆浘)

5 routes (M2) + 1 SSE (M3):
  POST /api/pipeline/start/<book>     瑙﹀彂鍐欑珷鑺?  POST /api/pipeline/cancel/<book>    鍙栨秷
  GET  /api/pipeline/status/<book>    褰撳墠鐘舵€?(鍚?PID/stage/started_at)
  GET  /api/pipeline/logs/<book>      鏈€杩?N 琛?(榛樿 100)
  GET  /api/pipeline/logs/<book>/stream  SSE 娴?(M3)
  GET  /api/pipeline/metrics/<book>   token 鐢ㄩ噺鑱氬悎

閴存潈: 鐢?review_ui/app.py 鐨?before_request 缁熶竴澶勭悊 (Basic Auth + session).
閿欒鐮? 澶嶇敤 lib.errors.NovelError (NOT_FOUND / GENERIC / INVALID_ARGS).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from lib import pipeline, storage
from lib import pipeline_v2 as pv2
from lib.config_loader import get_config
from lib.errors import ErrorCode, NovelError

dashboard_bp = Blueprint("dashboard", __name__)


# ── v1.3 M1: 全项目进度总览 ───────────────────────────────────────────────
@dashboard_bp.route("/overview")
def overview_page():
    """全项目进度总览页面 (WebUI 进度大屏)"""
    return render_template("overview.html")


@dashboard_bp.route("/api/overview")
def api_overview():
    """Get all projects + their pipeline state for overview page."""
    books = storage.list_projects()
    states = pipeline.get_overview_state(books)
    out = []
    for book in books:
        cfg = storage.read_json(book, "config.json") or {}
        prog = storage.read_json(book, "progress.json") or {}
        state = states.get(book)
        out.append({
            "name": book,
            "title": cfg.get("book_name", book) or book,
            "genre": cfg.get("genre", ""),
            "current_chapter": prog.get("current_chapter", 0),
            "total_chapters": prog.get("total_chapters", 0),
            "pipeline": None if state is None else {
                "status": state.get("status"),
                "ch": state.get("chapter_num"),
                "stage": state.get("current_stage"),
                "started_at": state.get("started_at"),
                "ended_at": state.get("ended_at"),
                "pid": state.get("pid"),
            },
        })
    return jsonify({"ok": True, "projects": out, "count": len(out)}), 200


@dashboard_bp.route("/api/overview/stream")
def api_overview_stream():
    """SSE: push overview state every N seconds.

    每 3s poll 所有项目状态, 仅在有变化时推送.
    """
    import threading as _th
    cfg = _dashboard_cfg()
    poll = float(cfg.get("stream_poll_interval", 1.0)) * 3  # 3s for overview
    # cache 上一帧 hash, 只有变化时才推
    last_sig = {"v": None}

    def generate():
        while True:
            try:
                books = storage.list_projects()
                states = pipeline.get_overview_state(books)
                # 构造轻量签名
                sig_parts = []
                for b in books:
                    s = states.get(b)
                    if s is None:
                        sig_parts.append(f"{b}:null")
                    else:
                        sig_parts.append(f"{b}:{s.get('status')}:{s.get('current_stage')}:{s.get('chapter_num')}")
                sig = "|".join(sig_parts)
                if sig != last_sig["v"]:
                    last_sig["v"] = sig
                    payload = {
                        "ts": int(time.time()),
                        "count": len(books),
                        "states": {b: (None if states.get(b) is None else {
                            "status": states[b].get("status"),
                            "stage": states[b].get("current_stage"),
                            "ch": states[b].get("chapter_num"),
                        }) for b in books},
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(poll)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── v1.3 M1 结束 ───────────────────────────────────────────────

# url_prefix 鐣欑┖, 璺敱閲屾墜鍐?/api/pipeline/...


@dashboard_bp.route("/dashboard/<book>")
def dashboard_page(book):
    """娴佹按绾块潰鏉块〉闈?"""
    if not storage.project_exists(book):
        return f"<h1>椤圭洰 [{book}] 涓嶅瓨鍦?/h1>", 404
    # 涓嬩竴绔犺妭: progress.current_chapter + 1 (浠?storage 璇?
    prog = storage.read_json(book, "progress.json") or {}
    next_ch = (prog.get("current_chapter") or 0) + 1
    return render_template("dashboard.html", book=book, next_chapter=next_ch)


def _dashboard_cfg() -> dict:
    """璇?dashboard 閰嶇疆 (浠庡叏灞€ config.yaml), 澶辫触鐢?defaults."""
    return get_config().get("dashboard", {
        "log_tail_default": 100,
        "log_max_buffer": 500,
        "metrics_retention_days": 30,
        "cancel_grace_seconds": 5,
        "stream_poll_interval": 1.0,
    })


def _err_response(e: NovelError) -> tuple[Response, int]:
    """缁熶竴 NovelError 鈫?JSON 鍝嶅簲."""
    return jsonify({
        "error": e.message,
        "code": int(e.code),
        "code_name": e.code.name,
        "detail": e.detail,
    }), int(e.code) if int(e.code) >= 400 else 400


# 鈹€鈹€ 1. start 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/start/<book>", methods=["POST"])
def api_pipeline_start(book):
    """瑙﹀彂鍐?1 涓珷鑺?

    Form params:
      chapters: int (required) - 瑕佸啓鐨勭珷鑺傚彿
      auto_rewrite: bool - 鏄惁鑷姩閲嶅啓 (榛樿 true)
    """
    try:
        ch_str = request.form.get("chapters") or request.json.get("chapters") if request.is_json else request.form.get("chapters")
        if ch_str is None:
            raise NovelError(ErrorCode.INVALID_ARGS, "缂哄皯 'chapters' 鍙傛暟 (瑕佸啓鐨勭珷鑺傚彿)")
        try:
            chapter_num = int(ch_str)
        except ValueError:
            raise NovelError(ErrorCode.INVALID_ARGS, f"chapters 蹇呴』鏄暣鏁? 鏀跺埌: {ch_str!r}")
        if chapter_num < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"chapters 蹇呴』 >= 1, 鏀跺埌: {chapter_num}")

        auto_rw_raw = request.form.get("auto_rewrite", "true") if not request.is_json else request.json.get("auto_rewrite", True)
        auto_rewrite = str(auto_rw_raw).lower() in ("1", "true", "yes", "on")

        runner = pipeline.get_runner()
        state = runner.start(book, chapter_num=chapter_num, auto_rewrite=auto_rewrite)
        return jsonify({"ok": True, "state": state}), 200
    except NovelError as e:
        return _err_response(e)


# 鈹€鈹€ 2. cancel 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/cancel/<book>", methods=["POST"])
def api_pipeline_cancel(book):
    """鍙栨秷杩愯涓殑瀛愯繘绋?"""
    try:
        runner = pipeline.get_runner()
        state = runner.cancel(book)
        return jsonify({"ok": True, "state": state}), 200
    except NovelError as e:
        return _err_response(e)


# 鈹€鈹€ 3. status 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/status/<book>")
def api_pipeline_status(book):
    """璇?.pipeline_state.json + 鏍″噯 PID 鐘舵€?"""
    runner = pipeline.get_runner()
    state = runner.status(book)
    if state is None:
        return jsonify({
            "ok": True,
            "state": None,
            "message": "娌℃湁娴佹按绾胯褰?(浠庢湭鍚姩鎴栧凡娓呯悊)",
        }), 200
    return jsonify({"ok": True, "state": state}), 200


# 鈹€鈹€ 4. logs (鏈€杩?N 琛? 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/logs/<book>")
def api_pipeline_logs(book):
    """杩斿洖 log 鏂囦欢鏈€鍚?N 琛?(榛樿 100, 涓婇檺 500)."""
    cfg = _dashboard_cfg()
    default_n = cfg.get("log_tail_default", 100)
    max_n = cfg.get("log_max_buffer", 500)
    try:
        n = int(request.args.get("tail", default_n))
    except ValueError:
        n = default_n
    n = max(1, min(n, max_n))
    runner = pipeline.get_runner()
    lines = runner.tail_log(book, n=n)
    return jsonify({
        "ok": True,
        "lines": lines,
        "count": len(lines),
        "tail": n,
    }), 200


# 鈹€鈹€ 5. metrics 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/metrics/<book>")
def api_pipeline_metrics(book):
    """鑱氬悎 metrics.jsonl.

    Query: range=all|1d|7d
    """
    range_str = request.args.get("range", "all")
    if range_str not in ("all", "1d", "7d"):
        range_str = "all"
    runner = pipeline.get_runner()
    data = runner.get_metrics(book, range_str=range_str)
    return jsonify({"ok": True, "range": range_str, **data}), 200


# 鈹€鈹€ 6. SSE log stream (M3) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

@dashboard_bp.route("/api/pipeline/logs/<book>/stream")
def api_pipeline_logs_stream(book):
    """SSE 娴? 鎸佺画鎺?log 鏂拌.

    琛屼负:
    - 瀹㈡埛绔繛涓婂悗, 绔嬪嵆浠?log 鏂囦欢鏈熬寮€濮嬫帹 (涓嶉噸澶嶅巻鍙?
    - 杩涚▼璺戝畬 (status=done/failed/cancelled) 鍚?flush 娈嬬暀 log, 鍏抽棴娴?    - 瀹㈡埛绔柇寮€ 鈫?generator 鑷劧閫€鍑? 涓嶆硠婕?    """
    cfg = _dashboard_cfg()
    poll = float(cfg.get("stream_poll_interval", 1.0))
    runner = pipeline.get_runner()

    def generate():
        try:
            for line in runner.stream_log(book, poll_interval=poll):
                # SSE 鍗忚: data: <line>\n\n
                # 娉ㄦ剰 line 宸茬粡甯?\n, 鍐嶅姞涓€涓?\n 缁堟 event
                yield f"data: {line.rstrip()}\n\n"
                # 姣忚 flush 涓€娆?(time.sleep 0, 璁?yield 绔嬪嵆杩斿洖)
            # 鍏抽棴浜嬩欢
            yield "event: end\ndata: {}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: 绂佺敤缂撳啿
            "Connection": "keep-alive",
        },
    )


# ── 7. checkpoints (M5) ────────────────────────────────────────────────
# Pipeline state machine (v1.2 M5): GET + 3 POST endpoints.
#   GET  /api/pipeline/checkpoints/<book>?ch=N   all stage checkpoints for ch N
#   POST /api/pipeline/skip/<book>               body: {ch, stage, reason?}
#   POST /api/pipeline/rerun/<book>              body: {ch, from_stage}
#   POST /api/pipeline/reset/<book>              body: {ch}
# ────────────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/checkpoints/<book>")
def api_pipeline_checkpoints(book):
    """Return all stage checkpoints for a chapter + summary view."""
    try:
        ch = int(request.args.get("ch", 0))
        if ch < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"ch must be >= 1, got: {ch}")
        if not storage.project_exists(book):
            raise NovelError(ErrorCode.NOT_FOUND, f"project [{book}] not found")
        v2 = pv2.get_v2()
        view = v2.get_pipeline_view(book, ch)
        return jsonify({"ok": True, **view}), 200
    except NovelError as e:
        return _err_response(e)


@dashboard_bp.route("/api/pipeline/skip/<book>", methods=["POST"])
def api_pipeline_skip(book):
    """Skip a stage. body (form or json): {ch, stage, reason?}."""
    try:
        payload = request.get_json(silent=True) or request.form
        ch = int(payload.get("ch", 0))
        stage = str(payload.get("stage", "")).strip()
        reason = payload.get("reason")
        if ch < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"ch must be >= 1, got: {ch}")
        if not stage:
            raise NovelError(ErrorCode.INVALID_ARGS, "missing 'stage' parameter")
        if not storage.project_exists(book):
            raise NovelError(ErrorCode.NOT_FOUND, f"project [{book}] not found")
        v2 = pv2.get_v2()
        sc = v2.skip_stage(book, ch, stage, reason=reason)
        return jsonify({
            "ok": True,
            "book": book,
            "ch": ch,
            "stage": stage,
            "new_state": sc.status,
        }), 200
    except NovelError as e:
        return _err_response(e)


@dashboard_bp.route("/api/pipeline/rerun/<book>", methods=["POST"])
def api_pipeline_rerun(book):
    """Rerun chapter from a given stage. body: {ch, from_stage}."""
    try:
        payload = request.get_json(silent=True) or request.form
        ch = int(payload.get("ch", 0))
        from_stage = str(payload.get("from_stage", "")).strip()
        if ch < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"ch must be >= 1, got: {ch}")
        if not from_stage:
            raise NovelError(ErrorCode.INVALID_ARGS, "missing 'from_stage' parameter")
        if not storage.project_exists(book):
            raise NovelError(ErrorCode.NOT_FOUND, f"project [{book}] not found")

        # 1. reset v2 checkpoint (from_stage onwards becomes PENDING)
        v2 = pv2.get_v2()
        v2.rerun_from(book, ch, from_stage)

        # 2. start v1 subprocess (same path as regular start)
        runner = pipeline.get_runner()
        state = runner.start(book, chapter_num=ch, auto_rewrite=True)

        return jsonify({
            "ok": True,
            "book": book,
            "ch": ch,
            "from_stage": from_stage,
            "state": state,
        }), 200
    except NovelError as e:
        return _err_response(e)


@dashboard_bp.route("/api/pipeline/reset/<book>", methods=["POST"])
def api_pipeline_reset(book):
    """Clear all checkpoints for a chapter (back to PENDING). body: {ch}."""
    try:
        payload = request.get_json(silent=True) or request.form
        ch = int(payload.get("ch", 0))
        if ch < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"ch must be >= 1, got: {ch}")
        if not storage.project_exists(book):
            raise NovelError(ErrorCode.NOT_FOUND, f"project [{book}] not found")
        v2 = pv2.get_v2()
        v2.reset_chapter(book, ch)
        return jsonify({
            "ok": True,
            "book": book,
            "ch": ch,
            "reset": True,
        }), 200
    except NovelError as e:
        return _err_response(e)

# ── 7. checkpoints (M5) ───────────────────────────────────────────────────
