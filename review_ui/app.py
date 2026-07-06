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
    endpoint = request.endpoint or ''
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

def _list_books() -> list[str]:
    """List all novel projects (folder names)."""
    proj_dir = Path(storage.PROJECTS_ROOT)
    if not proj_dir.exists():
        return []
    out = []
    for p in sorted(proj_dir.iterdir()):
        if p.is_dir() and (p / "config.json").exists():
            out.append(p.name)
    return out


def _list_chapters(book: str) -> list[str]:
    """List chapter files for a book (by folder name)."""
    proj_dir = Path(storage.PROJECTS_ROOT)
    book_dir = proj_dir / book
    if not book_dir.exists():
        return []
    return sorted(
        f.stem for f in book_dir.iterdir()
        if f.suffix in (".txt", ".md") and not f.name.startswith(".")
    )

def _ensure_book(book: str) -> None:
    if not storage.project_exists(book):
        abort(404, description=f"项目 [{book}] 不存在")

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

# ── JSON API ────────────────────────────────────────────────────────────────

@app.route("/api/projects")
def api_projects():
    """返回书项目列表 (SQLite 优先, 文件后备)."""
    try:
        from lib import db as dbmod
        proj_list = dbmod.list_projects_with_stats(storage.ROOT)
        projects = {}
        for p in proj_list:
            projects[p["id"]] = {
                "display_name": p["display_name"],
                "total_chapters": p["total_chapters"],
                "pending_reviews": p["pending_reviews"],
                "approved": p["approved"],
                "rejected": p["rejected"],
            }
        return jsonify({"ok": True, "projects": projects})
    except Exception as exc:
        # Fallback: 传统文件夹扫描
        names = _list_books()
        projects = {}
        for name in names:
            cfg = storage.read_json(name, "config.json") or {}
            projects[name] = {
                "display_name": cfg.get("book_name", name),
                "total_chapters": len(_list_chapters(name)),
            }
        return jsonify({"ok": True, "projects": projects})

@app.route("/api/queue/<book>")
def api_queue(book):
    _ensure_book(book)
    _ensure_review_backfill(book)
    return jsonify(revserv.get_review_queue(book))

@app.route("/api/review/<book>/<ch>")
def api_review(book, ch):
    _ensure_book(book)
    r = revserv.get_review(book, ch)
    if not r:
        abort(404, description=f"No review for {ch}")
    return jsonify(r)

@app.route("/api/chapter/<book>/<ch>")
def api_chapter(book, ch):
    _ensure_book(book)
    text = storage.read_chapter(book, ch)
    if text is None:
        abort(404)
    return Response(text, mimetype="text/plain; charset=utf-8")

@app.route("/api/chapter/<book>/<ch>/entity-diff")
def api_chapter_entity_diff(book, ch):
    """GET /api/chapter/<book>/<ch>/entity-diff — 本章节实体变化记录 (v1.3 M4)."""
    _ensure_book(book)
    from lib import entity_diff as edmod
    diff = edmod.get_chapter_changes(book, ch)
    if diff is None:
        return jsonify({"ok": False, "error": "无实体变化记录"})
    summary = edmod.summarize_changes(diff)
    return jsonify({"ok": True, "diff": diff, "summary": summary})


@app.route("/api/pipeline/<book>/interruptions")
def api_pipeline_interruptions(book):
    """GET /api/pipeline/<book>/interruptions — 列出所有中断的管道 (v1.3 M4)."""
    _ensure_book(book)
    from lib import pipeline_v2 as pv
    interrupted = pv.get_interrupted_chapters(book)
    return jsonify({"ok": True, "chapters": interrupted})


@app.route("/api/pipeline/<book>/<ch>/resume", methods=["POST"])
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


@app.route("/api/llm/providers")
def api_llm_providers():
    """GET /api/llm/providers — 列所有 provider 配置 (不暴露完整 key)."""
    from lib import llm_providers as lp
    providers = {}
    # Use merged config (BUILTIN + user-defined from config.yaml)
    merged = lp._merge_user_providers()
    for name, cfg in sorted(merged.items()):
        providers[name] = {
            "model": cfg.get("default_model", ""),
            "api_base": cfg.get("api_base", ""),
            "api_key_configured": bool(cfg.get("api_key")),
            "models": cfg.get("models", []),
        }
    return jsonify({
        "ok": True,
        "providers": providers,
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "local"),
        "current_model": os.environ.get("MODEL", ""),
    })


@app.route("/api/llm/health", methods=["POST"])
def api_llm_health_check():
    """POST /api/llm/health — 测试指定 provider 连通性.

    Body: {"provider": "deepseek", "model": "deepseek-chat"} (可选, 默认测试 book 当前配置)
    """
    body = request.get_json(silent=True) or {}
    provider = body.get("provider", "")
    model = body.get("model", "")
    book = body.get("book", "")

    from lib import llm_providers as lp
    if book:
        cfg = storage.read_json(book, "config.json") or {}
        provider = provider or cfg.get("llm_provider", "local")
    provider = provider or "local"

    pcfg = lp.get_provider_config(provider)
    if not pcfg:
        return jsonify({"ok": False, "error": f"未知 provider: {provider}"}), 400

    model = model or pcfg.get("model", "")
    api_base = pcfg.get("api_base", "")
    api_key = pcfg.get("api_key", "")

    import requests
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "1"}],
        "max_tokens": 2,
        "temperature": 0,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        return jsonify({"ok": True, "http_status": r.status_code,
                        "model": model, "provider": provider})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "超时 (>10s)", "provider": provider}), 504
    except requests.exceptions.ConnectionError as e:
        return jsonify({"ok": False, "error": f"连接失败: {e}", "provider": provider}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "provider": provider}), 500


@app.route("/llm")
def llm_config_page():
    """GET /llm — LLM provider 配置页面 (v1.3 M3)."""
    return render_template("llm_config.html", book_name="")


@app.route("/api/config/<book>")
def api_book_config_read(book):
    """GET /api/config/<book> — 读取 book 配置."""
    cfg = storage.read_json(book, "config.json") or {}
    return jsonify({"ok": True, **cfg})


@app.route("/api/config/<book>", methods=["POST"])
def api_book_config_write(book):
    """POST /api/config/<book> — 更新 book 配置 (部分更新)."""
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"ok": False, "error": "空 body"}), 400
    cfg = storage.read_json(book, "config.json") or {}
    cfg.update(body)
    storage.write_json(book, "config.json", cfg)
    return jsonify({"ok": True, "message": "已保存"})


@app.route("/api/chapter/<book>/<ch>/apply-feedback", methods=["POST"])
def api_apply_feedback(book, ch):
    """POST /api/chapter/<book>/<ch>/apply-feedback — 根据评审反馈自动修订章节 (v1.3 M4).

    可选 body: {"dry_run": true} — 不保存，只返回 AI 修订内容预览.
    """
    _ensure_book(book)
    from lib import review_actions as ramod
    dry_run = (request.get_json(silent=True) or {}).get("dry_run", False)
    result = ramod.apply_feedback_to_chapter(book, ch, dry_run=dry_run, save=not dry_run)
    if not result.get("ok"):
        abort(400, description=result.get("error", "apply failed"))
    return jsonify(result)


@app.route("/api/stats/<book>")
def api_stats(book):
    _ensure_book(book)
    return jsonify(revserv.get_review_stats(book))

@app.route("/api/history/<book>")
def api_history(book):
    _ensure_book(book)
    log_path = revserv.audit_log_path(book)
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return jsonify({
        "stats": revserv.get_review_stats(book),
        "queue": revserv.get_review_queue(book),
        "audit_log": log.strip().splitlines() if log.strip() else [],
    })

@app.route("/api/approve/<book>/<ch>", methods=["POST"])
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

@app.route("/api/reject/<book>/<ch>", methods=["POST"])
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

@app.route("/api/edit/<book>/<ch>", methods=["POST"])
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

@app.route("/api/false-positive/<book>/<ch>", methods=["POST"])
def api_false_positive(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    if not body.get("notes"):
        abort(400, description="notes required")
    record = revserv.mark_false_positive(book, ch, body.get("reviewer", "wei_chao"), body["notes"])
    return jsonify({"ok": True, "status": record["status"]})


@app.route("/api/batch-approve/<book>", methods=["POST"])
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


@app.route("/api/batch-reject/<book>", methods=["POST"])
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


@app.route("/api/queue/<book>/filtered")
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


@app.route("/api/diff/<book>/<ch>")
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


# ── 评论流 + 通知 + 行级 diff 锚点 (v1.2 M2) ───────────────────────────

from lib import comments as comm_serv  # noqa: E402


@app.route("/api/comments/<book>", methods=["GET"])
def api_comments_list(book):
    """GET /api/comments/<book>?chapter=ch_001 — 列出评论."""
    _ensure_book(book)
    chapter = request.args.get("chapter")
    return jsonify(comm_serv.list_comments(book, chapter))


@app.route("/api/comments/<book>/<ch>", methods=["POST"])
def api_comments_add(book, ch):
    """POST /api/comments/<book>/<ch>
    body: {author, text, line?, reply_to?}
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        abort(400, description="text required")
    author = (body.get("author") or "wei_chao").strip()
    line = body.get("line")
    reply_to = body.get("reply_to")
    try:
        c = comm_serv.add_comment(
            book, ch, author=author, text=text,
            line=line, reply_to=reply_to,
        )
    except ValueError as e:
        abort(400, description=str(e))
    return jsonify({"ok": True, "comment": c})


@app.route("/api/comments/<book>/<ch>/<cid>", methods=["DELETE"])
def api_comments_delete(book, ch, cid):
    """DELETE /api/comments/<book>/<ch>/<cid>"""
    _ensure_book(book)
    if not comm_serv.delete_comment(book, ch, cid):
        abort(404, description=f"comment {cid} not found")
    return ("", 204)


@app.route("/api/notifications/<book>", methods=["GET"])
def api_notifications_list(book):
    """GET /api/notifications/<book>?user=wei_chao&unread=1 — 列出通知."""
    _ensure_book(book)
    user = request.args.get("user")
    unread_only = request.args.get("unread") in ("1", "true", "yes")
    items = comm_serv.list_notifications(book, user=user, unread_only=unread_only)
    return jsonify({
        "items": items,
        "unread_count": comm_serv.unread_count(book, user) if user else None,
    })


@app.route("/api/notifications/<book>/<nid>/read", methods=["POST"])
def api_notifications_read(book, nid):
    """POST /api/notifications/<book>/<nid>/read — 标记已读."""
    _ensure_book(book)
    if not comm_serv.mark_notification_read(book, nid):
        abort(404, description=f"notification {nid} not found")
    return jsonify({"ok": True})


@app.route("/api/notifications/<book>/read-all", methods=["POST"])
def api_notifications_read_all(book):
    """POST /api/notifications/<book>/read-all?user=wei_chao — 全部已读."""
    _ensure_book(book)
    user = request.args.get("user")
    if not user:
        abort(400, description="user query param required")
    n = comm_serv.mark_all_read(book, user)
    return jsonify({"ok": True, "marked": n})


# 章节版本控制 (v1.2 M3)
from lib import version as ver_serv  # noqa: E402


@app.route("/api/chapter/<book>/<ch>/versions", methods=["GET"])
def api_chapter_versions_list(book, ch):
    """GET /api/chapter/<book>/<ch>/versions — 列表(不含content)."""
    _ensure_book(book)
    return jsonify(ver_serv.list_versions(book, ch))


@app.route("/api/chapter/<book>/<ch>/versions/<vid>", methods=["GET"])
def api_chapter_versions_get(book, ch, vid):
    """GET /api/chapter/<book>/<ch>/versions/<vid> — 读完整版本(含content)."""
    _ensure_book(book)
    rec = ver_serv.get_version(book, ch, vid)
    if not rec:
        abort(404, description=f"version {vid} not found")
    return jsonify(rec)


@app.route("/api/chapter/<book>/<ch>/revert/<vid>", methods=["POST"])
def api_chapter_revert(book, ch, vid):
    """POST /api/chapter/<book>/<ch>/revert/<vid> — 回滚到某版本.
    body: {"by": "wei_chao"}
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    by = (body.get("by") or "wei_chao").strip()
    try:
        rec = ver_serv.revert_to(book, ch, vid, by=by)
    except ValueError as e:
        abort(400, description=str(e))
    return jsonify({"ok": True, "version": rec})


@app.route("/api/chapter/<book>/<ch>/diff-versions", methods=["GET"])
def api_chapter_diff_versions(book, ch):
    """GET /api/chapter/<book>/<ch>/diff-versions?v1=v001&v2=v002 — 两版本 diff."""
    _ensure_book(book)
    v1 = request.args.get("v1")
    v2 = request.args.get("v2")
    if not v1 or not v2:
        abort(400, description="v1 and v2 required")
    try:
        return jsonify(ver_serv.diff_versions(book, ch, v1, v2))
    except ValueError as e:
        abort(404, description=str(e))


# 行级 diff 锚点: 按行号取上下文 ±N 行
@app.route("/api/chapter/<book>/<ch>/context")
def api_chapter_context(book, ch):
    """GET /api/chapter/<book>/<ch>/context?line=42&window=3
    返回 {line, target, before: [...], after: [...]} 上下文片段.
    """
    _ensure_book(book)
    text = storage.read_chapter(book, ch)
    if text is None:
        abort(404, description=f"chapter {ch} not found")
    try:
        line = int(request.args.get("line", 0))
    except ValueError:
        abort(400, description="line must be int")
    window = int(request.args.get("window", 3))
    lines = text.splitlines()
    if line < 1 or line > len(lines):
        abort(400, description=f"line {line} out of range [1, {len(lines)}]")
    start = max(0, line - 1 - window)
    end = min(len(lines), line - 1 + window + 1)
    return jsonify({
        "line": line,
        "before": [{"line_no": i + 1, "text": lines[i]} for i in range(start, line - 1)],
        "target": {"line_no": line, "text": lines[line - 1]},
        "after": [{"line_no": i + 1, "text": lines[i]} for i in range(line, end)],
    })


# ── 实体管理 API (v1.2 M1.2) ──────────────────────────────────────────

from lib.entity import (
    Character, Event, Foreshadow, WorldRule, EntityType,
)
from lib.memory import EntityStore


def _parse_entity_type(type_str: str) -> EntityType:
    """解析 type 参数为 EntityType 枚举."""
    try:
        return EntityType(type_str)
    except ValueError:
        valid = [t.value for t in EntityType]
        abort(400, description=f"Invalid type '{type_str}'. Valid: {valid}")


def _entity_to_dict(obj, entity_type: EntityType) -> dict:
    """通用 entity → API dict 转换.
    obj 可以是 Entity (已包装) 或 原始 dataclass."""
    from lib.entity import Entity
    if isinstance(obj, Entity):
        return obj.to_dict()
    return Entity.from_dataclass(obj, entity_type).to_dict()


@app.route("/api/entities/<book>")
def api_entities_list(book):
    """GET /api/entities/<book>?type=character|event|foreshadow|world_rule
    返回指定类型的实体列表."""
    _ensure_book(book)
    type_str = request.args.get("type")
    store = EntityStore(book)

    if type_str:
        # 单类型列表
        entity_type = _parse_entity_type(type_str)
        entities = store.list_by_type(entity_type)
        return jsonify({
            "type": entity_type.value,
            "entities": [_entity_to_dict(e, entity_type) for e in entities],
            "count": len(entities),
        })
    else:
        # 全部 4 类统计
        return jsonify({"counts": store.counts()})


@app.route("/api/entities/<book>/counts")
def api_entities_counts(book):
    """GET /api/entities/<book>/counts — 返回 4 类实体数量."""
    _ensure_book(book)
    return jsonify(EntityStore(book).counts())


@app.route("/api/entities/<book>/<type>/<id>")
def api_entities_get(book, type, id):
    """GET /api/entities/<book>/<type>/<id> — 单个实体详情."""
    _ensure_book(book)
    entity_type = _parse_entity_type(type)
    store = EntityStore(book)

    if entity_type == EntityType.CHARACTER:
        obj = store.get_character(id)
    elif entity_type == EntityType.EVENT:
        obj = store.get_event(id)
    elif entity_type == EntityType.FORESHADOW:
        obj = store.get_foreshadow(id)
    elif entity_type == EntityType.WORLD_RULE:
        obj = store.get_world_rule(id)

    if obj is None:
        abort(404, description=f"{type} '{id}' 不存在")
    return jsonify(_entity_to_dict(obj, entity_type))


@app.route("/api/entities/<book>", methods=["POST"])
def api_entities_create(book):
    """POST /api/entities/<book>
    body: {type: 'character|...', data: {...}}
    返回新建实体 (含 id)."""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    type_str = body.get("type")
    data = body.get("data", {})

    if not type_str:
        abort(400, description="'type' 必填")
    entity_type = _parse_entity_type(type_str)

    try:
        if entity_type == EntityType.CHARACTER:
            obj = Character.from_dict(data)
            EntityStore(book).add_character(obj)
        elif entity_type == EntityType.EVENT:
            obj = Event.from_dict(data)
            EntityStore(book).add_event(obj)
        elif entity_type == EntityType.FORESHADOW:
            obj = Foreshadow.from_dict(data)
            EntityStore(book).add_foreshadow(obj)
        elif entity_type == EntityType.WORLD_RULE:
            obj = WorldRule.from_dict(data)
            EntityStore(book).add_world_rule(obj)
    except ValueError as e:
        abort(400, description=str(e))

    return jsonify({"ok": True, "entity": _entity_to_dict(obj, entity_type)}), 201


@app.route("/api/entities/<book>/<type>/<id>", methods=["PUT"])
def api_entities_update(book, type, id):
    """PUT /api/entities/<book>/<type>/<id>
    body: {fields: {key: value, ...}} — 部分更新."""
    _ensure_book(book)
    entity_type = _parse_entity_type(type)
    body = request.get_json(silent=True) or {}
    fields = body.get("fields", {})

    if not fields:
        abort(400, description="'fields' 必填且非空")

    store = EntityStore(book)
    try:
        if entity_type == EntityType.CHARACTER:
            obj = store.update_character(id, **fields)
        elif entity_type == EntityType.EVENT:
            abort(400, description="Event 不支持更新 (append-only)")
        elif entity_type == EntityType.FORESHADOW:
            obj = store.update_foreshadow(id, **fields)
        elif entity_type == EntityType.WORLD_RULE:
            obj = store.update_world_rule(id, **fields)
        else:
            abort(400, description=f"Unsupported type: {type}")
    except ValueError as e:
        abort(404, description=str(e))

    return jsonify({"ok": True, "entity": _entity_to_dict(obj, entity_type)})


@app.route("/api/entities/<book>/<type>/<id>", methods=["DELETE"])
def api_entities_delete(book, type, id):
    """DELETE /api/entities/<book>/<type>/<id> — 删除实体."""
    _ensure_book(book)
    entity_type = _parse_entity_type(type)
    store = EntityStore(book)

    try:
        if entity_type == EntityType.CHARACTER:
            store.delete_character(id)
        elif entity_type == EntityType.EVENT:
            store.delete_event(id)
        elif entity_type == EntityType.FORESHADOW:
            store.delete_foreshadow(id)
        elif entity_type == EntityType.WORLD_RULE:
            store.delete_world_rule(id)
    except ValueError as e:
        abort(404, description=str(e))

    return ("", 204)


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


@app.route("/api/outline/<book>")
def api_outline_get(book):
    """GET /api/outline/<book> — returns the current outline with
    volumes[].chapters synced from chapters[]."""
    _ensure_book(book)
    o = oe.load_outline_or_empty(book)
    oe.sync_volumes_chapters(o)  # always resync before serving
    return jsonify(o)


@app.route("/api/outline/<book>", methods=["PUT"])
def api_outline_replace(book):
    """PUT /api/outline/<book> — body: full outline dict.
    Validates first (duplicate ids / unknown vol refs) before saving."""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        abort(400, description="body must be JSON object")
    errors = oe.validate_outline(body)
    if errors:
        abort(400, description="; ".join(errors))
    oe.save_outline(book, body)
    return jsonify({"ok": True, "errors": []})


@app.route("/api/outline/<book>/node", methods=["POST"])
def api_outline_node_add(book):
    """POST /api/outline/<book>/node
    body: {parent_vol: 'vol_1', position: 0, title: '...', summary: '...',
           pov: '...', key_events: [...], foreshadow: [...]}"""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    parent_vol = body.get("parent_vol") or body.get("vol")
    position = int(body.get("position", 0))
    if not parent_vol:
        abort(400, description="'parent_vol' 必填")
    o = oe.load_outline_or_empty(book)
    fields = {k: v for k, v in body.items() if k in oe.NODE_FIELDS and k != "id"}
    node = oe.add_node(o, parent_vol, position, **fields)
    oe.save_outline(book, o)
    return jsonify({"ok": True, "node": node}), 201


@app.route("/api/outline/<book>/node/<ch_id>", methods=["PUT"])
def api_outline_node_update(book, ch_id):
    """PUT /api/outline/<book>/node/<ch_id>
    body: {title, summary, pov, key_events, foreshadow, vol}
    Any of NODE_FIELDS except id."""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    fields = {k: v for k, v in body.items() if k in oe.NODE_FIELDS and k != "id"}
    if not fields:
        abort(400, description="no editable fields provided")
    o = oe.load_outline_or_empty(book)
    try:
        node = oe.update_node(o, ch_id, **fields)
    except ValueError as e:
        abort(404, description=str(e))
    oe.save_outline(book, o)
    return jsonify({"ok": True, "node": node})


@app.route("/api/outline/<book>/node/<ch_id>", methods=["DELETE"])
def api_outline_node_delete(book, ch_id):
    """DELETE /api/outline/<book>/node/<ch_id> — remove chapter node."""
    _ensure_book(book)
    o = oe.load_outline_or_empty(book)
    removed = oe.remove_node(o, ch_id)
    if removed is None:
        abort(404, description=f"chapter {ch_id} not found")
    oe.save_outline(book, o)
    return ("", 204)


@app.route("/api/outline/<book>/reorder", methods=["POST"])
def api_outline_reorder(book):
    """POST /api/outline/<book>/reorder
    body: {moves: [{ch_id, new_vol, new_position}, ...]}
    Applied sequentially; later moves see earlier results."""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    moves = body.get("moves")
    if not isinstance(moves, list) or not moves:
        abort(400, description="'moves' must be a non-empty list")
    o = oe.load_outline_or_empty(book)
    try:
        oe.reorder_nodes(o, moves)
    except ValueError as e:
        abort(400, description=str(e))
    oe.save_outline(book, o)
    return jsonify({"ok": True, "chapters": o["chapters"]})


@app.route("/api/outline/<book>/volumes", methods=["POST"])
def api_outline_volume_add(book):
    """POST /api/outline/<book>/volumes
    body: {title: '...', summary: '...'}"""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    if not title:
        abort(400, description="'title' 必填")
    o = oe.load_outline_or_empty(book)
    vol = oe.add_volume(o, title=title, summary=body.get("summary", ""))
    oe.save_outline(book, o)
    return jsonify({"ok": True, "volume": vol}), 201


@app.route("/api/outline/<book>/volumes/<vol_id>", methods=["DELETE"])
def api_outline_volume_delete(book, vol_id):
    """DELETE /api/outline/<book>/volumes/<vol_id>
    Chapters in the volume get reassigned to the first remaining volume.
    Returns the count of reassigned chapters."""
    _ensure_book(book)
    o = oe.load_outline_or_empty(book)
    try:
        reassigned = oe.remove_volume(o, vol_id)
    except ValueError as e:
        abort(404, description=str(e))
    oe.save_outline(book, o)
    return jsonify({"ok": True, "reassigned": reassigned})


@app.route("/api/outline/<book>/versions", methods=["GET"])
def api_outline_versions_list(book):
    """GET /api/outline/<book>/versions — list saved outline snapshots.
    Used by outline.html to populate the version picker."""
    _ensure_book(book)
    from lib import version as ver_serv
    versions = ver_serv.list_versions(book, "outline.json")
    return jsonify([
        {
            "version_id": v["version_id"],
            "ts": v.get("ts"),
            "trigger": v.get("trigger"),
            "char_count": v.get("char_count"),
        }
        for v in versions
    ])


@app.route("/api/outline/<book>/diff")
def api_outline_diff(book):
    """GET /api/outline/<book>/diff?v1=<v_id>&v2=<v_id>
    Structural diff between two saved outline snapshots.
    Versions live at projects/<book>/chapters/.versions/outline.json/<v_id>.json
    (populated automatically by oe.save_outline's best-effort snapshot)."""
    _ensure_book(book)
    v1_id = request.args.get("v1")
    v2_id = request.args.get("v2")
    if not v1_id or not v2_id:
        abort(400, description="'v1' and 'v2' both required")

    book_root = storage.project_root(book)
    versions_root = book_root / "chapters" / ".versions" / "outline.json"
    if not versions_root.exists():
        abort(404, description="no saved outline versions yet")
    v1_path = versions_root / f"{v1_id}.json"
    v2_path = versions_root / f"{v2_id}.json"
    if not v1_path.exists():
        abort(404, description=f"version {v1_id} not found")
    if not v2_path.exists():
        abort(404, description=f"version {v2_id} not found")
    old_raw = json.loads(v1_path.read_text(encoding="utf-8"))
    new_raw = json.loads(v2_path.read_text(encoding="utf-8"))
    # Each version snapshot wraps the outline dict inside {"content": "...json str..."}
    # (lib.version's create_version contract stores content as a string).
    old = json.loads(old_raw["content"])
    new = json.loads(new_raw["content"])
    diff = oe.diff_outlines(old, new)
    return jsonify(diff)


# ── Outline AI 助手 (v1.3 M2) ──────────────────────────────────────────────

@app.route("/api/outline/<book>/ai-suggest", methods=["POST"])
def api_outline_ai_suggest(book):
    """POST /api/outline/<book>/ai-suggest
    body: {count?: 3, next_num?: N}
    Returns: {chapters: [{title, summary, pov, key_events, foreshadow}], reasoning: str}"""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    count = int(body.get("count", 3))
    count = max(1, min(count, 5))  # clamp 1-5

    cfg = storage.read_json(book, "config.json") or {}
    book_title = cfg.get("book_name", book)
    genre = cfg.get("genre", "")

    o = oe.load_outline_or_empty(book)
    existing_count = len(o.get("chapters", []))
    next_num = int(body.get("next_num", existing_count + 1))

    # 构建 outline 文本供 LLM 上下文
    outline_text = _outline_to_text(o)

    from lib import outline_ai as oai
    result = oai.suggest_chapters(
        book_title=book_title,
        genre=genre,
        existing_count=existing_count,
        outline_text=outline_text,
        next_num=next_num,
        count=count,
    )
    return jsonify({"ok": True, **result})


@app.route("/api/outline/<book>/ai-expand", methods=["POST"])
def api_outline_ai_expand(book):
    """POST /api/outline/<book>/ai-expand
    body: {title: str, summary: str}
    Returns: {key_events: [...], foreshadow: str, pov_notes: str}"""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    summary = body.get("summary", "").strip()
    if not title:
        abort(400, description="'title' 必填")
    if not summary:
        abort(400, description="'summary' 必填")

    cfg = storage.read_json(book, "config.json") or {}
    book_title = cfg.get("book_name", book)
    genre = cfg.get("genre", "")

    from lib import outline_ai as oai
    result = oai.expand_chapter(
        book_title=book_title,
        genre=genre,
        title=title,
        summary=summary,
    )
    return jsonify({"ok": True, **result})


def _outline_to_text(o: dict) -> str:
    """把 outline dict 转成可读文本 (供 LLM 上下文)."""
    lines = []
    meta = o.get("meta", {})
    if meta.get("title"):
        lines.append(f"书名: {meta['title']}")
    # 同步 volumes[].chapters (用 chapters[] 完整信息)
    chapters = o.get("chapters", [])
    ch_by_id = {c.get("id"): c for c in chapters if c.get("id")}
    for vol in o.get("volumes", []):
        lines.append(f"\n## {vol.get('title', '卷')}: {vol.get('summary', '')}")
        for ref in vol.get("chapters", []):
            # ref 可能是 "ch_001|标题|摘要" 字符串, 也可能是 dict
            if isinstance(ref, str):
                ch_id = ref.split("|", 1)[0] if "|" in ref else ref
                # 从 chapters[] 查详情
                node = ch_by_id.get(ch_id, {})
                ch_title = node.get("title", "无标题")
                ch_summary = node.get("summary", "")
                ch_pov = node.get("pov", "")
            else:
                ch_id = ref.get("id", "?")
                ch_title = ref.get("title", "无标题")
                ch_summary = ref.get("summary", "")
                ch_pov = ref.get("pov", "")
            lines.append(f"- [{ch_id}] {ch_title}: {ch_summary}")
            if ch_pov:
                lines.append(f"  POV: {ch_pov}")
    return "\n".join(lines)


@app.route("/outline/<book>")
def outline_page(book):
    """GET /outline/<book> — outline editor page (tree view + edit panel)."""
    _ensure_book(book)
    cfg = storage.read_json(book, "config.json") or {"book_name": book}
    return render_template("outline.html", book=book, cfg=cfg)


# ── 一致性扫描 API (v1.2 M1.4) ────────────────────────────────────────

@app.route("/api/entities/<book>/check-consistency", methods=["POST"])
def api_check_consistency(book):
    """POST /api/entities/<book>/check-consistency
    body: {chapter_id: 'ch_001'} 或 {all: true} — 扫描所有章节
    返回 violations 列表 (含 rule_id, constraint, evidence, severity)."""
    _ensure_book(book)
    from lib import self_check

    body = request.get_json(silent=True) or {}
    chapter_id = body.get("chapter_id")
    scan_all = body.get("all", False)

    if not chapter_id and not scan_all:
        abort(400, description="chapter_id 或 all 必填")

    if scan_all:
        chapters = storage.list_chapters(book)
        results = []
        for ch in chapters:
            try:
                r = self_check.world_rule_consistency(book, ch["id"], save=True)
                results.append(r)
            except FileNotFoundError:
                continue
        return jsonify({"results": results, "total": len(results)})
    else:
        try:
            result = self_check.world_rule_consistency(book, chapter_id, save=True)
        except FileNotFoundError:
            abort(404, description=f"Chapter {chapter_id} not found")
        return jsonify(result)

# ── main ────────────────────────────────────────────────────────────────────

def main():
    # Force UTF-8 stdout so emoji prints don't GBK-encode-fail on PS 5.1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
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