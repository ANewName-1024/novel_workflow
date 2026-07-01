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
import sys, os, json, argparse
from pathlib import Path
from flask import Flask, jsonify, request, render_template, abort, Response

# Add project root + lib to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from lib import storage, review_service as revserv  # noqa

try:
    from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError:  # very old werkzeug
    ProxyFix = None

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)
# Honor X-Forwarded-Prefix from nginx (L55 fix 2026-07-01: /novel/ path on VPS)
# so url_for() generates "/novel/book/..." not "/book/...".
if ProxyFix is not None:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

# ── helpers ─────────────────────────────────────────────────────────────────

def _list_books() -> list[str]:
    """List all novel projects under projects/."""
    proj_dir = ROOT / "projects"
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
    return render_template("chapter.html",
        book=book, chapter_id=ch, record=record, text=text, v2_text=v2_text,
    )

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