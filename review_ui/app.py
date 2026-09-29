"""
Novel Workflow Review Web UI
============================
Lightweight Flask service exposing review_service.py via HTTP.

Endpoints:
  GET  /                        index page (project picker + stats)
  GET  /api/projects            list all books
  GET  /api/queue/<book>        pending review queue
  GET  /api/review/<book>/<ch>  one chapter's full review record
  GET  /api/chapter/<book>/<ch> chapter text
  POST /api/approve/<book>/<ch> mark approved
  POST /api/reject/<book>/<ch>  mark needs_rewrite (body: {"reason": "..."})
  POST /api/edit/<book>/<ch>    save human edit (body: {"text": "...", "apply": bool})
  POST /api/false-positive/<book>/<ch>  mark false positive (body: {"notes": "..."})
  GET  /api/history/<book>      full history + audit log
  GET  /api/stats/<book>        counters

Run:
  python review_ui/app.py [--port 21199] [--host 127.0.0.1]
"""
from __future__ import annotations
import sys, os, json, argparse, base64, difflib
from pathlib import Path
from flask import (Flask, jsonify, request, render_template, abort, Response,
                   session, redirect, url_for)

# Add project root + lib to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from lib import storage, review_service as revserv  # noqa
from lib.config_loader import get_config  # noqa

try:
    from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError:  # very old werkzeug
    ProxyFix = None

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)

# v1.1: 注册 dashboard 蓝图 (流水线面板 API).
# 用相对导入 (review_ui.dashboard) — review_ui/ 现在有 __init__.py 是真 package,
# pytest 跟 importlib 加载方式都能解析 (修复 v1.1.2 引入的 namespace package regression).
from .dashboard import dashboard_bp  # noqa: E402
app.register_blueprint(dashboard_bp)

# v1.3 M5: APK 日志 + 远程调试端点
try:
    from .app_log import app_log_bp  # noqa: E402
    app.register_blueprint(app_log_bp)
except Exception as _e:
    import sys
    print(f"[warn] app_log blueprint not registered: {_e}", file=sys.stderr)
# session secret for login cookies. Use env, fallback to stable dev key.
# ── Phase 1: 业务域蓝图(自 review_ui/app.py 拆出)────────────────────
# 用相对导入 (review_ui.bp) —— review_ui/ 与 review_ui/bp/ 都是真 package,
# pytest 的 importlib 加载与常规 import 都能解析。
# endpoint 会带上蓝图名前缀(如 outline.api_outline_get);
# 模板未直接 url_for 这些 endpoint(已核查,仅用 url_for('static')),
# 故重命名不影响模板渲染。
from .bp import (  # noqa: E402
    chapter_bp, comments_bp, entities_bp, llm_config_bp,
    notifications_bp, outline_bp, pipeline_bp, projects_bp, review_bp,
)
for _bp in (outline_bp, chapter_bp, entities_bp, projects_bp, review_bp,
            comments_bp, notifications_bp, llm_config_bp, pipeline_bp):
    app.register_blueprint(_bp)

# ── 向后兼容再导出(Phase 1)────────────────────────────────────────────
# 这些内部辅助从 app.py 搬进了 bp/,但既有测试与外部脚本仍以
# review_ui.app.<name> 引用它们。直接再导出比改调用方更稳,
# 也避免「重构顺手改了契约」这种隐性破坏。
# 只读引用可用;若外部要 monkeypatch,请改从 bp 模块取。
# _ensure_book 跨域共用(含留在本文件的页面路由),从 core 引入
from .core import _ensure_book  # noqa: E402,F401
from .bp.outline import _outline_to_text  # noqa: E402,F401
from .bp.review import _diff_stats         # noqa: E402,F401
from .bp.review import _ensure_review_backfill  # noqa: E402,F401
from .bp.projects import (  # noqa: E402,F401
    _list_books, _list_chapters, _create_project, _update_project,
    _delete_project, _validate_book_slug,
)
from .bp.entities import _parse_entity_type, _entity_to_dict  # noqa: E402,F401

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod")

# ── 统一导航栏 context (v1.3 M3) ─────────────────────────────────────────
# Inject `nav` into all templates for _navbar.html
import re as _re
_NAV_BOOK_ROUTES = {
    'book_page': 'book', 'outline_page': 'outline', 'dashboard_page': 'dashboard',
    'entities_page': 'entities', 'chapter_page': 'chapter',
    'notifications_page': 'notifications',
}
_GLOBAL_ROUTES = {'index': 'home', 'overview_page': 'overview', 'llm_config_page': 'llm'}

@app.context_processor
def _nav_context():
    """Inject nav context for unified navbar."""
    # Phase 1 修复: Blueprint 会给 endpoint 加蓝图名前缀
    # (dashboard_page -> dashboard.dashboard_page), 精确匹配会失效,
    # 导致这两个页面的导航高亮静默消失。统一取末段比较。
    endpoint = (request.endpoint or '').rsplit('.', 1)[-1]
    rule = request.url_rule.rule if request.url_rule else ''

    # 提取 book 名称 (路径参数)
    book = None
    m = _re.search(r'/(?:book|outline|entities|dashboard|notifications|chapter)/([^/?]+)', request.path)
    if m:
        book = m.group(1)

    # 当前激活的章节
    book_active = None
    for ep, sec in _NAV_BOOK_ROUTES.items():
        if endpoint == ep:
            book_active = sec
            break

    # 全局激活
    global_active = None
    if endpoint in _GLOBAL_ROUTES:
        global_active = _GLOBAL_ROUTES[endpoint]

    # 书籍列表 (下拉)
    books = []
    try:
        from lib import storage as _storage
        for b in _storage.list_projects():
            cfg = _storage.read_json(b, "config.json") or {}
            books.append((b, cfg.get('book_name') or b, cfg.get('genre', '')))
    except Exception:
        pass

    # 当前书籍的标题 / genre
    cur_title = book
    cur_genre = ''
    if book:
        cfg_b = storage.read_json(book, "config.json") or {}
        cur_title = cfg_b.get('book_name') or book
        cur_genre = cfg_b.get('genre', '')

    return dict(nav={
        'global_active': global_active,
        'book_active': book_active,
        'books': books,
        'current_book': book,
        'current_book_title': cur_title,
        'current_book_genre': cur_genre,
        'unread_count': 0,  # TODO: 接通知 API
    })


# Honor X-Forwarded-Prefix from nginx (L55 fix 2026-07-01: /novel/ path on VPS)
# so url_for() generates "/novel/book/..." not "/book/...".
if ProxyFix is not None:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

# ── M5: Auth (config-driven Basic Auth + session) ───────────────────────

def _get_auth() -> dict:
    """Read review_ui.auth from config (with env expansion already done)."""
    cfg = get_config().get("review_ui", {}).get("auth", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "user": str(cfg.get("user", "weichao")),
        "password": str(cfg.get("password", "")),
    }


def _is_authed() -> bool:
    return bool(session.get("auth_user"))


def _check_basic_auth_header():
    """如果传 Authorization: Basic ... 且对, 写入 session. 返回 True 表示已认证."""
    auth = _get_auth()
    if not auth["enabled"] or not auth["password"]:
        return False
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(hdr[6:]).decode("utf-8")
        u, _, p = decoded.partition(":")
        if u == auth["user"] and p == auth["password"]:
            session["auth_user"] = u
            return True
    except Exception:
        pass
    return False


@app.before_request
def _auth_gate():
    """统一 auth 检查. auth.enabled=False 时放行, 否则护所有非白名单 endpoint."""
    auth = _get_auth()
    if not auth["enabled"]:
        return None  # 配置不上, 全部放行
    # Safeguard: enabled 但 password 为空 → 视为配置错误, 放行 (跟 _check_basic_auth_header 对称)
    if not auth["password"]:
        return None
    # 白名单
    if request.path.startswith("/static/"):
        return None
    if request.path in ("/login", "/logout"):
        return None
    # Basic Auth header 兼容 (curl 友好)
    if _check_basic_auth_header():
        return None
    # 已登录
    if _is_authed():
        return None
    # 未认证: API → 401 JSON, 页面 → 重定向 /login
    if request.path.startswith("/api/") or request.path.startswith("/novel-api/"):
        return jsonify({"error": "unauthorized",
                        "message": "Auth required. POST /login or send Authorization: Basic header."}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    auth = _get_auth()
    if not auth["enabled"] or not auth["password"]:
        return redirect(url_for("index"))  # 配置不上或密码空 → 跳过登录
    error = None
    if request.method == "POST":
        u = (request.form.get("user") or "").strip()
        p = request.form.get("password") or ""
        if u == auth["user"] and p == auth["password"] and auth["password"]:
            session["auth_user"] = u
            nxt = request.args.get("next") or url_for("index")
            return redirect(nxt)
        error = "用户名或密码错"
    return render_template("login.html", error=error), (401 if error else 200)


@app.route("/logout")
def logout():
    session.pop("auth_user", None)
    return redirect(url_for("login"))


# ── helpers ──────────────────────────────────────────────────────────────

# ── helpers ─────────────────────────────────────────────────────────────────


# ── error handlers ─────────────────────────────────────────────────────────

@app.errorhandler(404)
def err_404(e):
    # For API calls (Accept: application/json OR /api/* OR /novel-api/*), return JSON
    path = request.path
    if path.startswith("/api/") or path.startswith("/novel-api/") or \
       request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"error": "not_found",
                        "message": e.description if hasattr(e, 'description') else str(e),
                        "path": path}), 404
    return render_template("error.html",
                           code=404,
                           title="页面不存在",
                           message=e.description if hasattr(e, 'description') else str(e),
                           detail=f"路径: {path}"), 404

@app.errorhandler(400)
def err_400(e):
    return jsonify({"error": "bad_request",
                    "message": e.description if hasattr(e, 'description') else str(e)}), 400

@app.errorhandler(500)
def err_500(e):
    return render_template("error.html",
                           code=500,
                           title="服务器错误",
                           message="服务异常，请稍后重试",
                           detail=str(e)), 500

# ── pages ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    books = []
    for b in _list_books():
        cfg = storage.read_json(b, "config.json") or {}
        stats = revserv.get_review_stats(b)
        books.append({
            "name": b,
            "title": cfg.get("book_name", b),
            "genre": cfg.get("genre", "?"),
            "protagonist": cfg.get("protagonist", "?"),
            "chapters": len(storage.list_chapters(b)),
            "pending": stats.get("pending_review", 0),
            "needs_rewrite": stats.get("needs_rewrite", 0),
            "approved": stats.get("approved", 0),
            "human_edited": stats.get("human_edited", 0),
            "auto_passed": stats.get("auto_passed", 0),
            "false_positive": stats.get("false_positive", 0),
        })
    return render_template("index.html", books=books)

@app.route("/book/<book>")
def book_page(book):
    _ensure_book(book)
    _ensure_review_backfill(book)
    cfg = storage.read_json(book, "config.json") or {}
    stats = revserv.get_review_stats(book)
    queue = revserv.get_review_queue(book)
    chapters = storage.list_chapters(book)
    # Pre-compute chapter numbers for gap detection (avoids Jinja2 |replace|int)
    import re as _re
    chapters_display = []
    prev_num = None
    for i, ch in enumerate(chapters):
        ch_id = ch.get("id", "")
        m = _re.search(r"ch_(\d+)", ch_id)
        ch_num = int(m.group(1)) if m else 0
        # Gap if previous chapter number exists and jump > 1
        gap_before = prev_num is not None and ch_num - prev_num > 1
        gap_count = ch_num - prev_num - 1 if gap_before else 0
        gap_label = (f"ch_{prev_num + 1:03d}" if prev_num else "") if gap_before else ""
        gap_label_end = (f"ch_{ch_num - 1:03d}" if prev_num else "") if gap_before else ""
        chapters_display.append({
            **ch,
            "ch_num": ch_num,
            "_idx": i,
            "gap_before": gap_before,
            "gap_count": gap_count,
            "gap_label": f"跳过 {gap_label} ~ {gap_label_end} ({gap_count} 章)" if gap_before else "",
        })
        prev_num = ch_num
    return render_template("book.html",
        book=book,
        cfg=cfg,
        stats=stats,
        queue=queue,
        chapters=chapters,
        chapters_display=chapters_display,
    )


# 通知中心页面 (v1.2 M2)
@app.route("/notifications/<book>")
def notifications_page(book):
    """GET /notifications/<book>?user=wei_chao — 通知中心."""
    _ensure_book(book)
    from lib import comments as comm_serv
    user = request.args.get("user", "wei_chao")
    items = comm_serv.list_notifications(book, user=user)
    unread = comm_serv.unread_count(book, user)
    return render_template("notifications.html",
        book=book,
        user=user,
        items=items,
        unread_count=unread,
    )

@app.route("/book/<book>/<ch>")
def chapter_page(book, ch):
    _ensure_book(book)
    _ensure_review_backfill(book)
    record = revserv.get_review(book, ch)
    if not record:
        abort(404, description=f"No review for {ch}")
    text = storage.read_chapter(book, ch) or ""
    v2_path = revserv.edited_path(book, ch)
    v2_text = v2_path.read_text(encoding="utf-8") if v2_path.exists() else None

    # M5: 章节导航 (prev/next) — 按 ch_001, ch_002 ... 字典序
    chapters = storage.list_chapters(book)
    ch_ids = [c["id"] for c in chapters]
    idx = ch_ids.index(ch) if ch in ch_ids else -1
    prev_id = ch_ids[idx - 1] if idx > 0 else None
    next_id = ch_ids[idx + 1] if 0 <= idx < len(ch_ids) - 1 else None

    # M3: 版本列表 + 最新版
    versions = ver_serv.list_versions(book, ch)
    latest_v = versions[-1] if versions else None

    # M5: diff (原版 vs v2), 有 v2 才计算
    diff_lines: list[str] = []
    if v2_text is not None:
        diff_lines = list(difflib.unified_diff(
            text.splitlines(),
            v2_text.splitlines(),
            fromfile="v1 (原版)",
            tofile="v2 (人工)",
            lineterm="",
            n=3,
        ))

    return render_template("chapter.html",
        book=book, chapter_id=ch, record=record,
        text=text, v2_text=v2_text,
        prev_id=prev_id, next_id=next_id,
        diff_lines=diff_lines,
        diff_stats=_diff_stats(text, v2_text) if v2_text is not None else None,
        versions=versions, latest_v=latest_v,
    )


# ── 项目 CRUD helpers ──────────────────────────────────────────────────────

import re as _re

_BOOK_SLUG_RE = _re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


# ── JSON API ────────────────────────────────────────────────────────────────


@app.route("/llm")
def llm_config_page():
    """GET /llm — LLM provider 配置页面 (v1.3 M3)."""
    return render_template("llm_config.html", book_name="")


# ── 评论流 + 通知 + 行级 diff 锚点 (v1.2 M2) ───────────────────────────

from lib import comments as comm_serv  # noqa: E402


# 章节版本控制 (v1.2 M3)
from lib import version as ver_serv  # noqa: E402


# 行级 diff 锚点: 按行号取上下文 ±N 行


# ── 实体管理 API (v1.2 M1.2) ──────────────────────────────────────────

from lib.entity import (
    Character, Event, Foreshadow, WorldRule, EntityType,
)
from lib.memory import EntityStore


# ── 实体管理页面 (v1.2 M1.3) ────────────────────────────────────────

_TYPE_LABELS = {
    "character": "角色",
    "event": "事件",
    "foreshadow": "伏笔",
    "world_rule": "世界规则",
}


@app.route("/entities/<book>")
def entities_page(book):
    """GET /entities/<book>?type=character|event|foreshadow|world_rule"""
    _ensure_book(book)
    type_str = request.args.get("type", "character")

    if type_str not in _TYPE_LABELS:
        abort(400, description=f"Invalid type '{type_str}'")

    entity_type = EntityType(type_str)
    store = EntityStore(book)
    entities = store.list_by_type(entity_type)
    counts = store.counts()
    cfg = storage.read_json(book, "config.json") or {"book_name": book}

    return render_template(
        "entities.html",
        book=book,
        cfg=cfg,
        active_type=type_str,
        active_label=_TYPE_LABELS[type_str],
        entities=entities,
        counts=counts,
    )


# ── 大纲编辑器 API (v1.2 M4) ──────────────────────────────────────────
# REST surface for the outline editor page:
#   GET    /api/outline/<book>                   current outline (synced volumes)
#   PUT    /api/outline/<book>                   replace full outline (validated)
#   POST   /api/outline/<book>/node              add chapter node
#   PUT    /api/outline/<book>/node/<ch_id>      update node fields
#   DELETE /api/outline/<book>/node/<ch_id>      remove node
#   POST   /api/outline/<book>/reorder           batch reorder
#   POST   /api/outline/<book>/volumes           add volume
#   DELETE /api/outline/<book>/volumes/<vol_id>  remove volume (reassign chapters)
#   GET    /api/outline/<book>/diff              structural diff between 2 saved versions

from lib import outline_editor as oe  # noqa: E402


# ── Outline AI 助手 (v1.3 M2) ──────────────────────────────────────────────


@app.route("/outline/<book>")
def outline_page(book):
    """GET /outline/<book> — outline editor page (tree view + edit panel)."""
    _ensure_book(book)
    cfg = storage.read_json(book, "config.json") or {"book_name": book}
    return render_template("outline.html", book=book, cfg=cfg)


# ── 一致性扫描 API (v1.2 M1.4) ────────────────────────────────────────


# ── main ────────────────────────────────────────────────────────────────────

def main():
    # Force UTF-8 stdout so emoji prints don't GBK-encode-fail on PS 5.1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 统一日志 (Phase 3 补): 此前 Web 进程没有任何 handler, lib/ 里的
    # log.warning 只能走 logging.lastResort 打到 stderr, 再被 start_all.ps1
    # 重定向到 %TEMP%\review_ui_flask.log.err —— 与 CLI 的 logs/ 分离,
    # 排查时要在两个地方找。
    #
    # 用独立文件而非共用 novel_workflow.log: CLI 与 Web 同时运行时,
    # 两个 RotatingFileHandler 争抢同一个文件会互相覆盖, 轮转时可能丢数据。
    #
    # 放 main() 而不是模块层 —— 测试 import 本模块时不该产生日志副作用
    # (会建文件 handler, 污染 caplog 断言)。
    try:
        from lib.logging_setup import setup_logging
        setup_logging(log_file="logs/review_ui.log")
    except Exception as e:
        print(f"   [warn] setup_logging skipped: {e}")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=21199)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Auto-init + migrate to SQLite (v1.3 M6)
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        # Auto-scan projects (idempotent)
        for _pid in _list_books():
            _cfg = storage.read_json(_pid, "config.json") or {}
            _book_name = _cfg.get("book_name", _pid)
            _dbmod.upsert_project(storage.ROOT, _pid, _book_name, _cfg)
            # Force chapter meta sync
            storage.list_chapters(_pid)
        print(f"   SQLite: {_dbmod.stats(storage.ROOT)}")
    except Exception as _e:
        print(f"   [warn] SQLite init skipped: {_e}")

    print(f"🟢 Novel Review UI on http://{args.host}:{args.port}")
    print(f"   projects: {len(_list_books())} book(s)")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)

if __name__ == "__main__":
    main()
