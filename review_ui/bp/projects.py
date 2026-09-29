"""review_ui/bp/projects.py — 项目(书籍)CRUD / 章节列表

从 review_ui/app.py 拆出(Phase 1)。
路由: 5 条   辅助函数: 6 个
"""
from __future__ import annotations

import re as _re
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

from pathlib import Path
from flask import jsonify
from flask import request
from lib import storage

from review_ui.core import _ensure_book

# Phase 1 随函数一同迁来的模块级常量(原本在 app.py 顶层)
_BOOK_SLUG_RE = _re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

bp = Blueprint("projects", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/projects")
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


@bp.route("/api/projects", methods=["POST"])
def api_projects_create():
    """Create a new project."""
    payload = request.get_json(silent=True) or {}
    book = (payload.get("name") or "").strip()
    resp, status = _create_project(book, payload)
    return jsonify(resp), status


@bp.route("/api/projects/<book>", methods=["GET"])
def api_project_get(book):
    """Get a single project's config (for edit form prefill)."""
    if not storage.project_exists(book):
        return jsonify({"ok": False, "error": f"项目 [{book}] 不存在"}), 404
    cfg = storage.read_json(book, "config.json") or {}
    return jsonify({"ok": True, "book": book, "config": cfg})


@bp.route("/api/projects/<book>", methods=["PUT"])
def api_project_update(book):
    """Update a project's config (does not rename)."""
    payload = request.get_json(silent=True) or {}
    resp, status = _update_project(book, payload)
    return jsonify(resp), status


@bp.route("/api/projects/<book>", methods=["DELETE"])
def api_project_delete(book):
    """Delete a project (removes its directory)."""
    resp, status = _delete_project(book)
    return jsonify(resp), status


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


def _validate_book_slug(book: str) -> str | None:
    """Validate book slug. Return error message or None if OK."""
    if not book:
        return "项目名不能为空"
    if not _BOOK_SLUG_RE.match(book):
        return "项目名只能包含字母、数字、下划线、连字符 (1-64 字符)"
    if book in ("__pycache__", ".git"):
        return "非法项目名"
    return None


def _create_project(book: str, payload: dict) -> tuple[dict, int]:
    """Create a new project. Returns (response, status_code)."""
    err = _validate_book_slug(book)
    if err:
        return {"ok": False, "error": err}, 400
    if storage.project_exists(book):
        return {"ok": False, "error": f"项目 [{book}] 已存在"}, 409

    main_plot = (payload.get("main_plot") or "").strip()
    if not main_plot:
        return {"ok": False, "error": "main_plot (主剧情) 是必填项"}, 400

    cfg = {
        "book_name": (payload.get("book_name") or book).strip(),
        "genre": (payload.get("genre") or "都市").strip(),
        "tone": (payload.get("tone") or "轻松日常").strip(),
        "protagonist": (payload.get("protagonist") or "").strip(),
        "antagonist": (payload.get("antagonist") or "").strip(),
        "main_plot": main_plot,
        "style": (payload.get("style") or "简洁流畅").strip(),
        "target_chapters": int(payload.get("target_chapters") or 20),
        "words_per_chapter": int(payload.get("words_per_chapter") or 2500),
        "language": (payload.get("language") or "zh").strip(),
        "llm_model": (payload.get("llm_model") or "").strip(),
        "api_base": (payload.get("api_base") or "").strip(),
        "llm_provider": (payload.get("llm_provider") or "").strip(),
    }
    storage.init_project(book, cfg)
    # 同步到 SQLite (review_ui /api/projects 从 db 读)
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        _dbmod.upsert_project(storage.ROOT, book, cfg["book_name"], cfg)
    except Exception:
        pass  # 文件后備仍然可用
    return {"ok": True, "book": book, "message": f"项目 [{book}] 已创建"}, 201


def _update_project(book: str, payload: dict) -> tuple[dict, int]:
    """Update an existing project's config (no name change)."""
    if not storage.project_exists(book):
        return {"ok": False, "error": f"项目 [{book}] 不存在"}, 404
    cfg = storage.read_json(book, "config.json") or {}

    # Updatable fields (whitelist)
    editable = ("book_name", "genre", "tone", "protagonist", "antagonist",
                "main_plot", "style", "target_chapters", "words_per_chapter",
                "language", "llm_model", "api_base", "llm_provider",
                "self_check", "auto_rewrite_on_critical", "self_check_strict")
    for key in editable:
        if key in payload and payload[key] is not None:
            if key in ("target_chapters", "words_per_chapter"):
                cfg[key] = int(payload[key])
            else:
                cfg[key] = payload[key]

    cfg["updated_at"] = __import__("datetime").datetime.now().isoformat()
    storage.write_json(book, "config.json", cfg)
    # 同步到 SQLite
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        _dbmod.upsert_project(storage.ROOT, book, cfg.get("book_name", book), cfg)
    except Exception:
        pass
    return {"ok": True, "book": book, "message": f"项目 [{book}] 已更新"}, 200


def _delete_project(book: str) -> tuple[dict, int]:
    """Delete a project (removes directory)."""
    if not storage.project_exists(book):
        return {"ok": False, "error": f"项目 [{book}] 不存在"}, 404
    import shutil
    root = storage.project_root(book)
    try:
        shutil.rmtree(root)
    except Exception as exc:
        return {"ok": False, "error": f"删除失败: {exc}"}, 500
    # 同步从 SQLite 删除
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        _dbmod.delete_project(storage.ROOT, book)
    except Exception:
        pass
    return {"ok": True, "book": book, "message": f"项目 [{book}] 已删除"}, 200
