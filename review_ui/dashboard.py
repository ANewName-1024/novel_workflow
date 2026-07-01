"""
review_ui/dashboard.py - v1.1 Web 流水线管理面板 (API 蓝图)

5 routes (M2) + 1 SSE (M3):
  POST /api/pipeline/start/<book>     触发写章节
  POST /api/pipeline/cancel/<book>    取消
  GET  /api/pipeline/status/<book>    当前状态 (含 PID/stage/started_at)
  GET  /api/pipeline/logs/<book>      最近 N 行 (默认 100)
  GET  /api/pipeline/logs/<book>/stream  SSE 流 (M3)
  GET  /api/pipeline/metrics/<book>   token 用量聚合

鉴权: 由 review_ui/app.py 的 before_request 统一处理 (Basic Auth + session).
错误码: 复用 lib.errors.NovelError (NOT_FOUND / GENERIC / INVALID_ARGS).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from lib import pipeline, storage
from lib.config_loader import get_config
from lib.errors import ErrorCode, NovelError

# url_prefix 留空, 路由里手写 /api/pipeline/...
dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard/<book>")
def dashboard_page(book):
    """流水线面板页面."""
    if not storage.project_exists(book):
        return f"<h1>项目 [{book}] 不存在</h1>", 404
    # 下一章节: progress.current_chapter + 1 (从 storage 读)
    prog = storage.read_json(book, "progress.json") or {}
    next_ch = (prog.get("current_chapter") or 0) + 1
    return render_template("dashboard.html", book=book, next_chapter=next_ch)


def _dashboard_cfg() -> dict:
    """读 dashboard 配置 (从全局 config.yaml), 失败用 defaults."""
    return get_config().get("dashboard", {
        "log_tail_default": 100,
        "log_max_buffer": 500,
        "metrics_retention_days": 30,
        "cancel_grace_seconds": 5,
        "stream_poll_interval": 1.0,
    })


def _err_response(e: NovelError) -> tuple[Response, int]:
    """统一 NovelError → JSON 响应."""
    return jsonify({
        "error": e.message,
        "code": int(e.code),
        "code_name": e.code.name,
        "detail": e.detail,
    }), int(e.code) if int(e.code) >= 400 else 400


# ── 1. start ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/start/<book>", methods=["POST"])
def api_pipeline_start(book):
    """触发写 1 个章节.

    Form params:
      chapters: int (required) - 要写的章节号
      auto_rewrite: bool - 是否自动重写 (默认 true)
    """
    try:
        ch_str = request.form.get("chapters") or request.json.get("chapters") if request.is_json else request.form.get("chapters")
        if ch_str is None:
            raise NovelError(ErrorCode.INVALID_ARGS, "缺少 'chapters' 参数 (要写的章节号)")
        try:
            chapter_num = int(ch_str)
        except ValueError:
            raise NovelError(ErrorCode.INVALID_ARGS, f"chapters 必须是整数, 收到: {ch_str!r}")
        if chapter_num < 1:
            raise NovelError(ErrorCode.INVALID_ARGS, f"chapters 必须 >= 1, 收到: {chapter_num}")

        auto_rw_raw = request.form.get("auto_rewrite", "true") if not request.is_json else request.json.get("auto_rewrite", True)
        auto_rewrite = str(auto_rw_raw).lower() in ("1", "true", "yes", "on")

        runner = pipeline.get_runner()
        state = runner.start(book, chapter_num=chapter_num, auto_rewrite=auto_rewrite)
        return jsonify({"ok": True, "state": state}), 200
    except NovelError as e:
        return _err_response(e)


# ── 2. cancel ─────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/cancel/<book>", methods=["POST"])
def api_pipeline_cancel(book):
    """取消运行中的子进程."""
    try:
        runner = pipeline.get_runner()
        state = runner.cancel(book)
        return jsonify({"ok": True, "state": state}), 200
    except NovelError as e:
        return _err_response(e)


# ── 3. status ─────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/status/<book>")
def api_pipeline_status(book):
    """读 .pipeline_state.json + 校准 PID 状态."""
    runner = pipeline.get_runner()
    state = runner.status(book)
    if state is None:
        return jsonify({
            "ok": True,
            "state": None,
            "message": "没有流水线记录 (从未启动或已清理)",
        }), 200
    return jsonify({"ok": True, "state": state}), 200


# ── 4. logs (最近 N 行) ─────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/logs/<book>")
def api_pipeline_logs(book):
    """返回 log 文件最后 N 行 (默认 100, 上限 500)."""
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


# ── 5. metrics ───────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/metrics/<book>")
def api_pipeline_metrics(book):
    """聚合 metrics.jsonl.

    Query: range=all|1d|7d
    """
    range_str = request.args.get("range", "all")
    if range_str not in ("all", "1d", "7d"):
        range_str = "all"
    runner = pipeline.get_runner()
    data = runner.get_metrics(book, range_str=range_str)
    return jsonify({"ok": True, "range": range_str, **data}), 200


# ── 6. SSE log stream (M3) ──────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/logs/<book>/stream")
def api_pipeline_logs_stream(book):
    """SSE 流: 持续推 log 新行.

    行为:
    - 客户端连上后, 立即从 log 文件末尾开始推 (不重复历史)
    - 进程跑完 (status=done/failed/cancelled) 后 flush 残留 log, 关闭流
    - 客户端断开 → generator 自然退出, 不泄漏
    """
    cfg = _dashboard_cfg()
    poll = float(cfg.get("stream_poll_interval", 1.0))
    runner = pipeline.get_runner()

    def generate():
        try:
            for line in runner.stream_log(book, poll_interval=poll):
                # SSE 协议: data: <line>\n\n
                # 注意 line 已经带 \n, 再加一个 \n 终止 event
                yield f"data: {line.rstrip()}\n\n"
                # 每行 flush 一次 (time.sleep 0, 让 yield 立即返回)
            # 关闭事件
            yield "event: end\ndata: {}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: 禁用缓冲
            "Connection": "keep-alive",
        },
    )
