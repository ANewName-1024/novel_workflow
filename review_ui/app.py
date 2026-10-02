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
  GET  /api/export/<book>       full book as markdown (JSON)
  GET  /api/export/<book>/download  full book as a .md file download

Run:
  python review_ui/app.py [--port 21199] [--host 127.0.0.1]
"""
from __future__ import annotations
import sys, os, json, argparse, base64, difflib, hmac, logging
from pathlib import Path
from flask import (Flask, jsonify, request, render_template, abort, Response,
                   session, redirect, url_for)

log = logging.getLogger("novel.review_ui")
if not log.handlers:
    log.addHandler(logging.NullHandler())

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
    # 过去这里只 print 一行到 stderr。后果: 5 个 /api/app-log/* 路由全部消失,
    # 而服务照常启动、UI 照常工作, 没有任何用户可见提示 —— 功能是「静默」的。
    # 根因叠加在 app_log.py: import 期就 mkdir 一个硬编码的 /root/... 目录,
    # 非 root 或换路径部署时直接 PermissionError。
    log.error("app_log 蓝图注册失败, /api/app-log/* 全部 404 —— "
              "崩溃日志与远程调试端点不可用: %s: %s",
              type(_e).__name__, _e, exc_info=True)
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
# 导出蓝图不经过 bp/__init__.py 的再导出: 那个文件是所有域共用的清单,
# 改它等于和别的域抢同一个文件。这里直接从自己的模块取, 注册位置仍在下面。
from .bp.export import bp as export_bp  # noqa: E402
for _bp in (outline_bp, chapter_bp, entities_bp, projects_bp, review_bp,
            comments_bp, notifications_bp, llm_config_bp, pipeline_bp,
            export_bp):
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

# session secret for login cookies.
#
# 2026-10-01: 过去是 os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod"),
# 生产忘了设环境变量就用一个全网公开的常量 —— 攻击者据此可以自己伪造
# session cookie 里的 auth_user, 直接绕过整个登录流程。
# 现在: 生产环境(FLASK_ENV=production 或显式 REVIEW_UI_REQUIRE_SECRET=1)缺 secret
# 直接拒绝启动; 开发环境才允许回落, 且必须够长够随机。
_DEV_SECRET = "dev-secret-change-in-prod"
_secret = os.environ.get("FLASK_SECRET_KEY", "")
_require_secret = (
    os.environ.get("REVIEW_UI_REQUIRE_SECRET", "").lower() in ("1", "true", "yes")
    or os.environ.get("FLASK_ENV", "").lower() == "production"
)
if not _secret:
    if _require_secret:
        raise RuntimeError(
            "FLASK_SECRET_KEY 未设置且处于生产模式 —— 拒绝启动。"
            "用一个足够长且随机的值: python -c \"import secrets;"
            "print(secrets.token_urlsafe(48))\"")
    _secret = _DEV_SECRET
    log.warning("FLASK_SECRET_KEY 未设置, 正在使用【公开的】开发用默认值。"
                "生产部署必须设置, 否则 session cookie 可被伪造。"
                "(设置 REVIEW_UI_REQUIRE_SECRET=1 可让此处直接拒绝启动)")
app.secret_key = _secret

# 显式设置 cookie 安全属性。此前全部走 Flask 默认。
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,     # 防 JS 读走
    SESSION_COOKIE_SAMESITE="Lax",    # 跨站 POST 不带 cookie
    SESSION_COOKIE_SECURE=bool(os.environ.get("REVIEW_UI_COOKIE_SECURE", "").lower()
                               in ("1", "true", "yes")),
    # 401/503 也带同样的头: 之前 err_500 对 /api/ 返回 HTML, 与 404/400 的 JSON 约定不一致
)

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
    books_error = None
    try:
        from lib import storage as _storage
        for b in _storage.list_projects():
            cfg = _storage.read_json(b, "config.json") or {}
            books.append((b, cfg.get('book_name') or b, cfg.get('genre', '')))
    except Exception as e:
        # 过去是 except: pass。后果: projects/ 权限异常或某本书 config.json 损坏时,
        # 整个下拉框静默变空, 页面照常 200 —— 用户只会以为「书没了」。
        # 这是 @app.context_processor, 每一页渲染都跑, 影响面是全站。
        # bp/projects.py:199 的同类镜像写失败已经补了 log.warning, 这里属于同类漏改。
        books_error = str(e)
        log.error("导航栏书籍列表加载失败, 下拉框会显示为空: %s: %s",
                  type(e).__name__, e, exc_info=True)

    # 当前书籍的标题 / genre
    cur_title = book
    cur_genre = ''
    if book:
        cfg_b = storage.read_json(book, "config.json") or {}
        cur_title = cfg_b.get('book_name') or book
        cur_genre = cfg_b.get('genre', '')

    # 通知未读数 (过去硬编码 0, _navbar.html 的铃铛因此永远不亮)。
    # 身份与 book.html 的 `cfg.reviewer|default('wei_chao')` 取同一个来源。
    unread_count = 0
    unread_error = None
    if book:
        try:
            from lib import comments as _comm
            _user = (cfg_b.get('reviewer') or 'wei_chao')
            unread_count = int(_comm.unread_count(book, _user) or 0)
        except Exception as e:
            # 这是 @app.context_processor —— 每一页渲染都跑。这里抛出去
            # 就是整站白屏, 所以与上面的 books_error 同一套防御: 记日志,
            # 退化成 0(铃铛不亮, 但页面照常渲染)。
            unread_error = str(e)
            unread_count = 0
            log.error("导航栏未读通知数加载失败, 铃铛不亮: %s: %s",
                      type(e).__name__, e, exc_info=True)

    return dict(nav={
        'global_active': global_active,
        'book_active': book_active,
        'books': books,
        'books_error': books_error,   # 非 None 时模板应显示「加载失败」而非空列表
        'current_book': book,
        'current_book_title': cur_title,
        'current_book_genre': cur_genre,
        'unread_count': unread_count,
        'unread_error': unread_error,  # 同 books_error: 只作诊断, 不影响渲染
    })


# Honor X-Forwarded-Prefix from nginx (L55 fix 2026-07-01: /novel/ path on VPS)
# so url_for() generates "/novel/book/..." not "/book/...".
if ProxyFix is not None:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

# ── M5: Auth (config-driven Basic Auth + session) ───────────────────────
#
# 失败模式的方向性 (2026-10-01 安全修复)
# ─────────────────────────────────────
# 之前的 _auth_gate 有两条放行分支, 其中一条是:
#     if not auth["password"]: return None      # ← enabled=true 但密码空 → 全放行
# config.yaml 里密码写的是 ${REVIEW_UI_PASSWORD:-}, 环境变量一没设就成空串。
# 于是「配错了」= 「静默关掉鉴权」, 且不打任何日志。实测该部署就是这样:
# 公网可达 + 31 个写端点全部裸奔, 任何人可 DELETE /api/projects/<book>。
#
# 现在的规则:
#   enabled=false        → 显式关闭(本地开发), 放行, 但启动时打醒目 WARNING
#   enabled=true 且密码空 → 配置错误, 【拒绝一切请求】并 log.error, 不再放行
# 空密码永远不等于「不设防」。

def _get_auth() -> dict:
    """Read review_ui.auth from config (with env expansion already done)."""
    cfg = get_config().get("review_ui", {}).get("auth", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "user": str(cfg.get("user", "weichao")),
        "password": str(cfg.get("password", "")),
    }


def _auth_misconfigured(auth: dict) -> bool:
    """enabled=True 但密码为空 —— 这是配置错误, 不是「不设防」。"""
    return bool(auth["enabled"]) and not auth["password"]


def _is_authed() -> bool:
    return bool(session.get("auth_user"))


def _check_basic_auth_header():
    """如果传 Authorization: Basic ... 且对, 写入 session. 返回 True 表示已认证."""
    auth = _get_auth()
    if _auth_misconfigured(auth):
        return False
    if not auth["enabled"]:
        return False
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(hdr[6:]).decode("utf-8")
        u, _, p = decoded.partition(":")
        # 常量时间比较: 逐字符的 == 会在比较失败的前缀长度上泄露时序
        ok = (hmac.compare_digest(u, auth["user"])
              and hmac.compare_digest(p, auth["password"]))
        if ok:
            session["auth_user"] = u
            return True
    except Exception:
        log.warning("Basic Auth 头解析失败", exc_info=True)
    return False


@app.before_request
def _auth_gate():
    """统一 auth 检查.

    - auth.enabled=False  → 放行(本地开发免密的显式选择)
    - auth.enabled=True 但 password 为空 → 配置错误, 拒绝一切请求
    - 其余 → 护住所有非白名单 endpoint
    """
    auth = _get_auth()

    if _auth_misconfigured(auth):
        # 不放行。这里曾经 return None, 等于把配置笔误变成了安全洞。
        log.error(
            "鉴权配置错误: review_ui.auth.enabled=true 但 password 为空 —— "
            "拒绝所有请求。请设置环境变量 REVIEW_UI_PASSWORD。"
            "如果确实要免密, 请显式设 review_ui.auth.enabled=false。")
        if request.path.startswith("/api/") or request.path.startswith("/novel-api/"):
            return jsonify({"error": "auth misconfigured",
                            "message": "服务端鉴权配置错误(review_ui.auth.password 为空), "
                                       "已拒绝全部请求。请管理员设置 REVIEW_UI_PASSWORD。"}), 503
        return "服务端鉴权配置错误(review_ui.auth.password 为空), 已拒绝全部请求。", 503

    if not auth["enabled"]:
        return None  # 显式关闭; 启动时会打 WARNING 提醒这是公网裸奔状态

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
    return redirect(url_for("login", next=_safe_next(request.path)))


def _safe_next(candidate: str) -> str:
    """只允许站内相对路径。

    login() 过去直接 redirect(request.args.get("next")), 攻击者可以发
    /login?next=https://evil.com, 用户登录成功后被弹到站外 —— 开放重定向,
    配合钓鱼页做 credential phishing 很顺手。
    规则: 以单个 / 开头(所以 //evil.com 这种协议相对 URL 也不放行),
    且不含反斜杠与控制字符。

    带前缀部署 (nginx `location /novel/` + `X-Forwarded-Prefix`, 由
    ProxyFix(x_prefix=1) 写进 SCRIPT_NAME) 时, candidate 是不含前缀的
    PATH_INFO。直接 redirect(candidate) 会跳到源站根目录 —— 那是 nginx 上
    另一个应用, 实测登录成功后落到 404 Error 页。url_for() 会自己补前缀,
    这里返回裸字符串所以必须手工补。

    只在已通过站内校验的相对路径前拼接**服务端自己的** script_root, 不放宽
    任何原有拒绝条件。nginx 用 proxy_set_header 覆盖该头, 客户端无法注入。
    """
    if not candidate or not isinstance(candidate, str):
        return url_for("index")
    if not candidate.startswith("/") or candidate.startswith("//"):
        return url_for("index")
    if "\\" in candidate or any(ord(c) < 32 for c in candidate):
        return url_for("index")
    root = (request.script_root or "").rstrip("/")
    if root and candidate != root and not candidate.startswith(root + "/"):
        return root + candidate
    return candidate


@app.route("/login", methods=["GET", "POST"])
def login():
    auth = _get_auth()
    if _auth_misconfigured(auth):
        log.error("login: 鉴权配置错误(password 为空), 拒绝登录")
        return "服务端鉴权配置错误: review_ui.auth.password 为空。", 503
    if not auth["enabled"]:
        return redirect(url_for("index"))  # 显式免密
    error = None
    if request.method == "POST":
        u = (request.form.get("user") or "").strip()
        p = request.form.get("password") or ""
        if auth["password"] and hmac.compare_digest(u, auth["user"]) and hmac.compare_digest(p, auth["password"]):
            session["auth_user"] = u
            nxt = _safe_next(request.args.get("next") or "")
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
    # 404 与 400 都为 /api/ 返回 JSON, 只有 500 返回 HTML —— 前端 fetch
    # 拿到 HTML 却在 res.json() 上炸, 于是真正的异常原因被二次错误盖掉。
    # 补齐同一套约定。
    log.error("未处理异常 500: %s: %s", type(e).__name__, e, exc_info=True)
    path = request.path or ""
    if path.startswith("/api/") or path.startswith("/novel-api/"):
        return jsonify({"error": "internal_error",
                        "message": "服务异常，请稍后重试"}), 500
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
