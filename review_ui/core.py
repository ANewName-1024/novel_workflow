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


