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
from review_ui.core import _ensure_book, _int_arg, pick_config_updates

import logging

# 本模块过去没有 logger, 导致几处镜像写失败完全静默。
# 语义不变(主写在 try 外正常抛, 镜像写失败不该中断业务), 只是让它可见。
log = logging.getLogger(__name__)

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
        "target_chapters": _int_arg("target_chapters", 20, src=payload),
        "words_per_chapter": _int_arg("words_per_chapter", 2500, src=payload),
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

    # 2026-10-02 (P6): 白名单搬去 review_ui/core.py, 与 POST /api/config
    # 共用同一份 —— 两条路由写的是同一个 config.json, 各留一份就等于没有。
    #
    # 同时不再「什么都没改也报已更新」: 过去 `{}` 和全白名单外的 body
    # (比如 {created_at, evil_field}) 都返回 200「已更新」, 实际只盖了一个
    # updated_at —— 界面上看不出这次编辑没生效, 而用户以为改好了。
    if not isinstance(payload, dict):
        return {"ok": False, "error": "body 必须是 JSON 对象"}, 400
    if not payload:
        return {"ok": False, "error": "空 body: 没有要更新的字段"}, 400
    try:
        updates = pick_config_updates(payload)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"字段类型非法: {exc}"}, 400
    if not updates:
        # 字段全在白名单外: 接受请求但不谎报「已更新」, 并且一个字节都不写
        # (连 updated_at 都不盖, 让「无改动」在磁盘上也可核对)。
        return {"ok": True, "book": book, "changed": [],
                "message": "未识别任何可编辑字段, 未做任何修改"}, 200

    cfg = storage.read_json(book, "config.json") or {}
    cfg.update(updates)

    cfg["updated_at"] = __import__("datetime").datetime.now().isoformat()
    storage.write_json(book, "config.json", cfg)
    # 同步到 SQLite。主写(文件)已在 try 外, 失败照常抛; 这里吞的只是镜像写,
    # 语义正确 —— 但静默会让镜像停写无从发现: 读回走 DB 优先, 拿到的是
    # 过期数据, 而用户以为刚改完。补一条警告, 不改控制流。
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        _dbmod.upsert_project(storage.ROOT, book, cfg.get("book_name", book), cfg)
    except Exception as e:
        log.warning("项目镜像写入 SQLite 失败, 读回将走文件兜底 (book=%s): %s: %s",
                    book, type(e).__name__, e, exc_info=True)
    return {"ok": True, "book": book, "changed": sorted(updates),
            "message": f"项目 [{book}] 已更新"}, 200


def _delete_project(book: str) -> tuple[dict, int]:
    """删除项目 —— 移入可恢复的回收站, 不做不可逆的 rmtree。

    过去这里是 shutil.rmtree(root): 一条请求永久抹掉整本书, 没有备份、没有回收站、
    没有二次确认, 手滑或脚本跑错就找不回来了。项目里本来就有 backups/ 约定
    (测试书籍/backups 就在), 删除路径却没沿用。

    现在改成 move 到 projects/.trash/<book>-<时间戳>/, 需要时手工 mv 回去即可。
    .trash 里没有 config.json, 所以不会被 list_projects() 当成一本书列出来。
    """
    if not storage.project_exists(book):
        return {"ok": False, "error": f"项目 [{book}] 不存在"}, 404
    import shutil
    import datetime as _dt
    root = storage.project_path(book)  # 纯计算, 不 mkdir
    trash = Path(storage.PROJECTS_ROOT) / ".trash"
    try:
        trash.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = trash / f"{book}-{stamp}"
        n = 1
        while dest.exists():          # 同一秒内删两次同名书
            dest = trash / f"{book}-{stamp}-{n}"
            n += 1
        shutil.move(str(root), str(dest))
    except Exception as exc:
        return {"ok": False, "error": f"删除失败: {exc}"}, 500
    # 同步从 SQLite 删除
    try:
        from lib import db as _dbmod
        _dbmod.init_db(storage.ROOT)
        _dbmod.delete_project(storage.ROOT, book)
    except Exception:
        pass
    return {"ok": True, "book": book,
            "trash": str(dest.relative_to(storage.PROJECTS_ROOT)),
            "message": f"项目 [{book}] 已移入回收站 ({dest.name}), 需要恢复可把它移回 projects/"}, 200
