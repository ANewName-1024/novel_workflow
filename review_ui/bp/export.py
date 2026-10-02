"""review_ui/bp/export.py — 全书导出(成稿离开浏览器的唯一出口)

从 novel.py 的 cmd_export 抽出 lib/exporter.py 之后接上 Web。
此前 grep `export|download|导出|下载` 在 review_ui/ 下零命中:
用户可以在浏览器里 init -> outline -> write -> review -> approve 一路走完,
却拿不走成稿, 只能 SSH 上去跑 CLI。导出是纯读 + 一次文件写, 不调 LLM。

路由: 2 条
  GET /api/export/<book>                 JSON: {ok, filename, chapters, words, markdown}
  GET /api/export/<book>/download       直接下载 {book}_全书.md

鉴权: 由 review_ui/app.py 的 @app.before_request _auth_gate 统一兜住,
本模块不做也不重复做 auth 判定(与 core.py 顶部注释同一约定)。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, abort, jsonify

_HERE = Path(__file__).resolve().parent
_UI = _HERE.parent
if str(_UI.parent) not in sys.path:
    sys.path.insert(0, str(_UI.parent))
if str(_UI.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_UI.parent / "lib"))

from lib import exporter
from lib.errors import NovelError

from review_ui.core import _ensure_book

log = logging.getLogger("novel.review_ui.export")
if not log.handlers:
    log.addHandler(logging.NullHandler())

bp = Blueprint("export", __name__,
               template_folder=str(_UI / "templates"))


def _build_or_abort(book: str) -> tuple[str, str, int, int]:
    """统一入口: 校验书存在, 拼 Markdown, 把库的异常翻成 HTTP 错误。

    lib 层抛 NovelError(NOT_FOUND), 这里 abort(404) —— 交给 app.py 的
    err_404 统一转成 {"error": "not_found", ...} JSON, 与其余 /api/ 端点
    同一套契约(前端 NW.api 直接读 .message)。
    """
    _ensure_book(book)
    try:
        markdown, n_chapters, n_words = exporter.build_full_book_markdown(book)
    except NovelError as e:
        abort(404, description=e.message)
    return exporter.default_filename(book), markdown, n_chapters, n_words


@bp.route("/api/export/<book>", methods=["GET"])
def api_export_markdown(book):
    """GET /api/export/<book> — 返回全书 Markdown(前端预览/复制用)."""
    filename, markdown, n_chapters, n_words = _build_or_abort(book)
    return jsonify({
        "ok": True,
        "filename": filename,
        "chapters": n_chapters,
        "words": n_words,
        "markdown": markdown,
    })


@bp.route("/api/export/<book>/download", methods=["GET"])
def api_export_download(book):
    """GET /api/export/<book>/download — 下载 {book}_全书.md.

    Content-Disposition 用 RFC 5987: 文件名含中文, 裸的非 ASCII
    filename= 会被部分浏览器/中间层截成乱码。ASCII 回退名同时给老客户端。
    """
    filename, markdown, _n, _w = _build_or_abort(book)
    # ASCII 回退: 保留扩展名与可打印字符, 其余替换 —— 纯给不认 filename* 的老客户端
    fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    quoted = quote(filename, safe="")
    disposition = f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quoted}'
    return Response(
        markdown.encode("utf-8"),
        # 只给 mimetype: 写 "text/markdown; charset=utf-8" 会被 Werkzeug
        # 再补一次, 实测发出 `; charset=utf-8; charset=utf-8`。
        mimetype="text/markdown",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(markdown.encode("utf-8"))),
        },
    )
