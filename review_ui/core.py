"""review_ui/core.py — 跨域共享的内部辅助(Phase 1)

由 _ensure_book 跨 6 个域调用,故置于 core 而非某个 blueprint 内。
注意:本模块不做 auth 判定 —— @app.before_request 的 _auth_gate 仍在 app.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

_UI = Path(__file__).resolve().parent
if str(_UI.parent) not in sys.path:
    sys.path.insert(0, str(_UI.parent))
if str(_UI.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_UI.parent / "lib"))

from flask import jsonify, request

from lib import storage

from flask import abort
from lib import storage

def _ensure_book(book: str) -> None:
    if not storage.project_exists(book):
        abort(404, description=f"项目 [{book}] 不存在")


# ── 请求参数解析 (2026-10-01) ──────────────────────────────────────────────
#
# 之前全仓有 10 处直接 int(request.args.get(...)) / float(...), 脏输入直接
# ValueError 冒泡 → err_500 → 用户看到「服务器错误」而不是「参数非法」。
# 最刺眼的是 bp/chapter.py:137-141 —— 同一个函数里 line 包了
# `except ValueError -> 400`, 紧接着的 window 没包。同一个文件里
# bp/dashboard.py:239 对 tail 做了兜底, 另几个参数没做。
#
# 这类逐个漏改的收敛, 靠 grep 提醒没用, 得有一个所有调用方共用的入口。

def _int_arg(name: str, default: int, lo: int | None = None,
             hi: int | None = None, src=None) -> int:
    """从 query string / JSON body 取整数, 非法或越界一律 400。"""
    raw = _raw_arg(name, default, src)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        abort(400, description=f"参数 {name} 必须是整数, 收到: {raw!r}")
    if lo is not None and v < lo:
        abort(400, description=f"参数 {name} 不能小于 {lo}, 收到 {v}")
    if hi is not None and v > hi:
        abort(400, description=f"参数 {name} 不能大于 {hi}, 收到 {v}")
    return v


def _float_arg(name: str, default: float, lo: float | None = None,
               hi: float | None = None, src=None) -> float:
    raw = _raw_arg(name, default, src)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        abort(400, description=f"参数 {name} 必须是数字, 收到: {raw!r}")
    if lo is not None and v < lo:
        abort(400, description=f"参数 {name} 不能小于 {lo}, 收到 {v}")
    if hi is not None and v > hi:
        abort(400, description=f"参数 {name} 不能大于 {hi}, 收到 {v}")
    return v


def _raw_arg(name: str, default, src=None):
    if src is None:
        src = request.args
    return src.get(name, default)


