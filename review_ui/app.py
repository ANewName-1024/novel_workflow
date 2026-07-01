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
# session secret for login cookies. Use env, fallback to stable dev key.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod")
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
    """List all novel projects. Use storage.PROJECTS_ROOT so tests can monkeypatch."""
    proj_dir = Path(storage.PROJECTS_ROOT)
    if not proj_dir.exists():
        return []
    out = []
    for p in sorted(proj_dir.iterdir()):
        if p.is_dir() and (p / "config.json").exists():
            out.append(p.name)
    return out

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
    return render_template("book.html",
        book=book,
        cfg=cfg,
        stats=stats,
        queue=queue,
        chapters=chapters,
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
    return jsonify(_list_books())

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
    return jsonify({"ok": True, "status": record["status"]})

@app.route("/api/reject/<book>/<ch>", methods=["POST"])
def api_reject(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    if not body.get("reason"):
        abort(400, description="reason required")
    record = revserv.reject(book, ch, body.get("reviewer", "wei_chao"), body["reason"])
    return jsonify({"ok": True, "status": record["status"]})

@app.route("/api/edit/<book>/<ch>", methods=["POST"])
def api_edit(book, ch):
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    text = body.get("text", "").strip()
    if not text:
        abort(400, description="text required")
    reviewer = body.get("reviewer", "wei_chao")
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
    print(f"🟢 Novel Review UI on http://{args.host}:{args.port}")
    print(f"   projects: {len(_list_books())} book(s)")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)

if __name__ == "__main__":
    main()