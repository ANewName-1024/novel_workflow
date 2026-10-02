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


# ── config.json 可写字段白名单 (2026-10-02) ───────────────────────────────
#
# 放这里而不是各 blueprint 里: 写 projects/<book>/config.json 的一共有两条
# 路由 —— PUT /api/projects/<book> (bp/projects.py) 与 POST /api/config/<book>
# (bp/llm_config.py) —— 它们必须认同一份名单。历史上它们各写各的, 结果
# POST /api/config 那条根本没有名单(cfg.update(body)), 调用方拼错一个 key
# 就静默落盘, 之后 llm_model / api_base / llm_provider 被生成流水线读走,
# 章节按错的配置写出来, 而界面上一切正常。同一份名单只能存在一份, 否则
# 两边漂移回去就等于没有名单。
EDITABLE_CONFIG_FIELDS = (
    "book_name", "genre", "tone", "protagonist", "antagonist",
    "main_plot", "style", "target_chapters", "words_per_chapter",
    "language", "llm_model", "api_base", "llm_provider",
    "self_check", "auto_rewrite_on_critical", "self_check_strict",
)

# 白名单里必须落成 int 的字段。config.json 里这两个是数字, 落进字符串
# ("60") 后面读它的进度/统计代码会静默算错。UI 表单就是发字符串, 所以
# 强转不能省。
INT_CONFIG_FIELDS = ("target_chapters", "words_per_chapter")


def pick_config_updates(payload: dict) -> dict:
    """挑出 payload 里可以写进 config.json 的字段, 白名单外的直接丢掉。

    保留 bp/projects.py 原有的两条语义:
      - 值为 None 的键视为「没传」, 不覆盖已有值(前端清空输入框就会发 None);
      - INT_CONFIG_FIELDS 走 int() 强转; 值非法时抛 ValueError, 由调用方
        转成 400 —— 不在这里 abort, 因为这条路径两个调用方的错误约定不同
        (一个返回 (dict, status), 一个返回 jsonify)。
    """
    out: dict = {}
    for key, value in payload.items():
        if key not in EDITABLE_CONFIG_FIELDS or value is None:
            continue
        out[key] = int(value) if key in INT_CONFIG_FIELDS else value
    return out


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


