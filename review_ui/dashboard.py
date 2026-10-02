"""
review_ui/dashboard.py - v1.1 流水线管理面板 (API 视图)

5 routes (M2) + 1 SSE (M3):
  POST /api/pipeline/start/<book>     发起写章节  POST /api/pipeline/cancel/<book>    取消
  GET  /api/pipeline/status/<book>    当前状态(含 PID/stage/started_at)
  GET  /api/pipeline/logs/<book>      最后 N 行 (默认 100)
  GET  /api/pipeline/logs/<book>/stream  SSE 流 (M3)
  GET  /api/pipeline/metrics/<book>   token 用量聚合

鉴权: 由 review_ui/app.py 的 before_request 统一处理 (Basic Auth + session).
错误码: 复用 lib.errors.NovelError (NOT_FOUND / GENERIC / INVALID_ARGS).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from review_ui.core import _int_arg
from lib import pipeline, storage
from lib.pipeline import state as pv2
from lib.config_loader import get_config
from lib.errors import ErrorCode, NovelError

dashboard_bp = Blueprint("dashboard", __name__)


# ── v1.3 M1: 全项目进度总览 ───────────────────────────────────────────────
@dashboard_bp.route("/overview")
def overview_page():
    """全项目进度总览页面 (WebUI 进度大屏)"""
    return render_template("overview.html")


def _to_iso(val) -> Optional[str]:
    """Convert Unix timestamp (int/float) or ISO string to ISO date string, or None.

    pipeline_state.json stores started_at as Unix timestamp (int),
    but ended_at as ISO string (via _now_iso()). This normalises both to
    ISO strings so the frontend fmtTs() always receives a parseable value.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Unix seconds → ISO string
        import datetime
        return datetime.datetime.utcfromtimestamp(val).strftime("%Y-%m-%dT%H:%M:%S")
    return str(val)  # already ISO or similar


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
                "started_at": _to_iso(state.get("started_at")),
                "ended_at": _to_iso(state.get("ended_at")),
                "pid": state.get("pid"),
            },
        })
    return jsonify({"ok": True, "projects": out, "count": len(out)}), 200


@dashboard_bp.route("/api/overview/stream")
def api_overview_stream():
    """SSE: push overview state every N seconds.

    每 3s poll 所有项目状态, 仅在有变化时推送 data。

    2026-10-02: 过去是裸 `while True` —— 没有心跳、没有 retry、没有寿命上限。
    实测挂 45s 不报错也不出数据(签名没变), 而 nginx 的
    proxy_read_timeout 是 300s, 于是浏览器每 5 分钟被静默掐断再重连,
    同一份配置里另一个 location 用的还是 86400s(取决于先撞到哪个)。
    现在三件事都补齐, 约定与 /api/pipeline/logs/<book>/stream 对齐:
      - `: keepalive` 注释帧: 状态不变也定期说话, 中间层知道连接还活着
      - `retry: <ms>`:       重连间隔显式下发, 不靠浏览器默认值
      - `event: end` + return: 到 max_lifetime 主动收尾, generator 自然退出
    """
    cfg = _dashboard_cfg()
    # lo 是"配错了也别让它变成 0/负数"的地板, 不是业务下限:
    # max_lifetime=0 会让 generator 第一轮就收尾, 页面等于没接上实时。
    poll = _cfg_float(cfg, "stream_poll_interval", 1.0, 0.01) * 3  # 3s for overview
    max_life = _cfg_float(cfg, "stream_max_lifetime", 1800.0, 0.1)
    hb = _cfg_float(cfg, "stream_heartbeat_interval", 15.0, 0.01)
    retry_ms = int(_cfg_float(cfg, "stream_retry_ms", 5000, 100))
    # 计时器每轮才醒一次, 所以心跳的真实间隔是 poll 的整数倍;
    # 再钳一道 max_life, 保证"到点收尾"前至少发过一次心跳。
    hb = min(hb, max_life)
    # cache 上一帧 hash, 只有变化时才推
    last_sig = {"v": None}

    def generate():
        started = time.monotonic()
        last_hb = started
        # 显式下发重连间隔, 而不是让浏览器用自己那个默认值
        yield f"retry: {retry_ms}\n\n"
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
            now = time.monotonic()
            # 先判寿命: 到点就收尾, 不再发心跳。
            # event: end 与 logs/.../stream 同一个约定, 客户端据此重连。
            if now - started >= max_life:
                yield "event: end\ndata: {}\n\n"
                return
            if now - last_hb >= hb:
                yield ": keepalive\n\n"
                last_hb = now
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

# url_prefix 留空, 路由手写 /api/pipeline/...


@dashboard_bp.route("/dashboard/<book>")
def dashboard_page(book):
    """流水线面板页面"""
    if not storage.project_exists(book):
        # 2026-10-01: 过去这里是 f"<h1>项目 [{book}] 不存在</h1>" —— book 直接
        # 来自 URL 且未经任何转义(不走 render_template 就没有 Jinja autoescape),
        # 请求 /dashboard/%3Cimg%20src=x%20onerror=alert(1)%3E 即反射执行。
        # 同一函数的正常分支走 render_template, 说明这是漏改而非有意设计。
        # nginx 裸路径 /dashboard 直连且当时无鉴权, 这条可达。
        return render_template("error.html",
                               code=404,
                               title="页面不存在",
                               message=f"项目 [{book}] 不存在",
                               detail=""), 404
    # 下一章节: progress.current_chapter + 1 (从 storage 读)
    prog = storage.read_json(book, "progress.json") or {}
    next_ch = (prog.get("current_chapter") or 0) + 1
    return render_template("dashboard.html", book=book, next_chapter=next_ch)


def _dashboard_cfg() -> dict:
    """读 dashboard 配置 (来自全局 config.yaml), 失败回落到 defaults."""
    return get_config().get("dashboard", {
        "log_tail_default": 100,
        "log_max_buffer": 500,
        "metrics_retention_days": 30,
        "cancel_grace_seconds": 5,
        "stream_poll_interval": 1.0,
        # 2026-10-02: overview SSE 的一次连接最长活多久(秒), 到点发
        # `event: end` 主动收尾。代理的 proxy_read_timeout 是 300s,
        # 浏览器自己重连, 但服务端那条 generator 会一直占着 worker 线程。
        "stream_max_lifetime": 1800.0,
        "stream_heartbeat_interval": 15.0,
        "stream_retry_ms": 5000,
    })


def _cfg_float(cfg: dict, key: str, default: float, lo: float) -> float:
    """从 dashboard 配置里取一个正数, 非法/越界一律回落到默认值。

    配置是手写的 yaml —— 写成 "15s" / "" / null 都会让 float() 直接冒泡,
    而这些读取发生在响应流开始之前, 抛出去就是一个 500。
    """
    try:
        v = float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if v >= lo else default


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
    """发起写 1 个章节

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


# ── 2. cancel ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/cancel/<book>", methods=["POST"])
def api_pipeline_cancel(book):
    """取消运行中的子进程"""
    try:
        runner = pipeline.get_runner()
        state = runner.cancel(book)
        return jsonify({"ok": True, "state": state}), 200
    except NovelError as e:
        return _err_response(e)


# ── 3. status ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/status/<book>")
def api_pipeline_status(book):
    """读 .pipeline_state.json + 校准 PID 状态"""
    runner = pipeline.get_runner()
    state = runner.status(book)
    if state is None:
        return jsonify({
            "ok": True,
            "state": None,
            "message": "没有流水线记录 (从未启动或已清理)",
        }), 200
    return jsonify({"ok": True, "state": state}), 200


# ── 4. logs ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/logs/<book>")
def api_pipeline_logs(book):
    """返回 log 文件最后 N 行 (默认 100, 上限 500)."""
    cfg = _dashboard_cfg()
    default_n = cfg.get("log_tail_default", 100)
    max_n = cfg.get("log_max_buffer", 500)
    try:
        n = _int_arg("tail", default_n, lo=1, hi=1000)
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


# ── 5. metrics ──────────────────────────────────────────────────────────────

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


# ── 6. SSE log stream (M3) ──────────────────────────────────────────────────────────────

@dashboard_bp.route("/api/pipeline/logs/<book>/stream")
def api_pipeline_logs_stream(book):
    """SSE 流, 持续推 log 新行。

    行为:
    - 客户端连上后, 立即从 log 文件尾部开始推 (不重复历史)
    - 进程跑完 (status=done/failed/cancelled) 后 flush 剩余 log, 关闭流
    - 客户端断开 -> generator 自然退出, 不泄漏
    """
    cfg = _dashboard_cfg()
    poll = float(cfg.get("stream_poll_interval", 1.0))
    runner = pipeline.get_runner()

    def generate():
        try:
            for line in runner.stream_log(book, poll_interval=poll):
                # SSE 协议: data: <line>\n\n
                # 注意 line 已经带了\n, 再追加一个\n 终止 event
                yield f"data: {line.rstrip()}\n\n"
                # 每行 flush 一次 (time.sleep 0), 让 yield 立即返回
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
        ch = _int_arg("ch", 0, lo=0)
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
        ch = _int_arg("ch", 0, lo=0, src=payload)
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
        ch = _int_arg("ch", 0, lo=0, src=payload)
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
        ch = _int_arg("ch", 0, lo=0, src=payload)
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
