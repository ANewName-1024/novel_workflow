"""review_ui/bp/pipeline.py — 流水线中断记录 / 恢复

从 review_ui/app.py 拆出(Phase 1)。
路由: 2 条   辅助函数: 0 个
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
from flask import jsonify

from review_ui.core import _ensure_book
bp = Blueprint("pipeline", __name__,
               template_folder=str(_UI / "templates"),
               static_folder=str(_UI / "static"),
               static_url_path="/static")


@bp.route("/api/pipeline/<book>/interruptions")
def api_pipeline_interruptions(book):
    """GET /api/pipeline/<book>/interruptions — 列出所有中断的管道 (v1.3 M4)."""
    _ensure_book(book)
    from lib import pipeline_v2 as pv
    interrupted = pv.get_interrupted_chapters(book)
    return jsonify({"ok": True, "chapters": interrupted})


@bp.route("/api/pipeline/<book>/<ch>/resume", methods=["POST"])
def api_pipeline_resume(book, ch):
    """POST /api/pipeline/<book>/<ch>/resume — 恢复中断的管道 (v1.3 M4)."""
    _ensure_book(book)
    m = re.match(r"ch_(\d+)", ch)
    if not m:
        abort(400, description=f"Invalid chapter id: {ch}")
    chapter_num = int(m.group(1))
    from lib import pipeline_v2 as pv
    result = pv.recover_stage(book, chapter_num)
    return jsonify({"ok": result["ok"], "chapter": result["chapter"],
                    "recovered_stage": result["recovered_stage"],
                    "message": result["message"]})
