"""review_ui/bp/outline.py — 大纲编辑 / 大纲 AI / 大纲 diff

从 review_ui/app.py 拆出(Phase 1)。
路由: 12 条   辅助函数: 1 个
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
import json
from flask import jsonify
from lib import outline_editor as oe
from flask import request
from lib import storage
from lib import version as ver_serv

from review_ui.core import _ensure_book
bp = Blueprint("outline", __name__,
               template_folder=str(_UI / "templates"),
               static_folder=str(_UI / "static"),
               static_url_path="/static")


@bp.route("/api/outline/<book>")
def api_outline_get(book):
    """GET /api/outline/<book> — returns the current outline with
    volumes[].chapters synced from chapters[]."""
    _ensure_book(book)
    o = oe.load_outline_or_empty(book)
    oe.sync_volumes_chapters(o)  # always resync before serving
    return jsonify(o)


@bp.route("/api/outline/<book>", methods=["PUT"])
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


@bp.route("/api/outline/<book>/node", methods=["POST"])
def api_outline_node_add(book):
    """POST /api/outline/<book>/node
    body: {parent_vol: 'vol_1', position: 0, title: '...', summary: '...',
           pov: '...', key_events: [...], foreshadow: [...]}

    parent_vol is optional; if missing, falls back to:
      1. The first existing volume in the outline.
      2. Auto-create a default 'vol_1' if the outline has no volumes.
    """
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    parent_vol = body.get("parent_vol") or body.get("vol")
    position = int(body.get("position", 0))
    o = oe.load_outline_or_empty(book)
    if not parent_vol:
        # Fallback 1: first existing volume
        if o.get("volumes"):
            parent_vol = o["volumes"][0]["id"]
        else:
            # Fallback 2: auto-create a default volume
            default_vol = oe.add_volume(o, title="默认卷", summary="自动创建")
            parent_vol = default_vol["id"]
    fields = {k: v for k, v in body.items() if k in oe.NODE_FIELDS and k != "id"}
    node = oe.add_node(o, parent_vol, position, **fields)
    oe.save_outline(book, o)
    return jsonify({"ok": True, "node": node, "parent_vol": parent_vol}), 201


@bp.route("/api/outline/<book>/node/<ch_id>", methods=["PUT"])
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


@bp.route("/api/outline/<book>/node/<ch_id>", methods=["DELETE"])
def api_outline_node_delete(book, ch_id):
    """DELETE /api/outline/<book>/node/<ch_id> — remove chapter node."""
    _ensure_book(book)
    o = oe.load_outline_or_empty(book)
    removed = oe.remove_node(o, ch_id)
    if removed is None:
        abort(404, description=f"chapter {ch_id} not found")
    oe.save_outline(book, o)
    return ("", 204)


@bp.route("/api/outline/<book>/reorder", methods=["POST"])
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


@bp.route("/api/outline/<book>/volumes", methods=["POST"])
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


@bp.route("/api/outline/<book>/volumes/<vol_id>", methods=["DELETE"])
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


@bp.route("/api/outline/<book>/versions", methods=["GET"])
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


@bp.route("/api/outline/<book>/diff")
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


@bp.route("/api/outline/<book>/ai-suggest", methods=["POST"])
def api_outline_ai_suggest(book):
    """POST /api/outline/<book>/ai-suggest
    body: {count?: 3, next_num?: N, llm_provider?: str, llm_model?: str}
    Returns: {chapters: [{title, summary, pov, key_events, foreshadow}], reasoning: str}"""
    _ensure_book(book)
    body = request.get_json(silent=True) or {}
    count = int(body.get("count", 3))
    count = max(1, min(count, 5))  # clamp 1-5

    cfg = storage.read_json(book, "config.json") or {}
    book_title = cfg.get("book_name", book)
    genre = cfg.get("genre", "")

    # 前端传的 llm_provider/llm_model 优先，写入 config.json 供后续使用
    llm_provider = body.get("llm_provider", "")
    llm_model = body.get("llm_model", "")
    if llm_provider:
        cfg["llm_provider"] = llm_provider
        if llm_model:
            cfg["llm_model"] = llm_model
        storage.write_json(book, "config.json", cfg)

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
        book=book,
    )
    return jsonify({"ok": True, **result})


@bp.route("/api/outline/<book>/ai-expand", methods=["POST"])
def api_outline_ai_expand(book):
    """POST /api/outline/<book>/ai-expand
    body: {title: str, summary: str, llm_provider?: str, llm_model?: str}
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

    # 前端传的 llm_provider/llm_model 优先
    llm_provider = body.get("llm_provider", "")
    llm_model = body.get("llm_model", "")
    if llm_provider:
        cfg["llm_provider"] = llm_provider
        if llm_model:
            cfg["llm_model"] = llm_model
        storage.write_json(book, "config.json", cfg)

    from lib import outline_ai as oai
    result = oai.expand_chapter(
        book_title=book_title,
        genre=genre,
        title=title,
        summary=summary,
        book=book,
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
