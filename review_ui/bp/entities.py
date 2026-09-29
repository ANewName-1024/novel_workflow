"""review_ui/bp/entities.py — 实体 CRUD / 一致性检查(角色/事件/伏笔/世界规则)

从 review_ui/app.py 拆出(Phase 1)。
路由: 7 条   辅助函数: 2 个
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

from lib.entity import Character
from lib.memory import EntityStore
from lib.entity import EntityType
from lib.entity import Event
from lib.entity import Foreshadow
from lib.entity import WorldRule
from flask import abort
from flask import jsonify
from flask import request
from lib import storage

from review_ui.core import _ensure_book
bp = Blueprint("entities", __name__,
               template_folder=str(_UI / "templates"))


@bp.route("/api/entities/<book>")
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


@bp.route("/api/entities/<book>/counts")
def api_entities_counts(book):
    """GET /api/entities/<book>/counts — 返回 4 类实体数量."""
    _ensure_book(book)
    return jsonify(EntityStore(book).counts())


@bp.route("/api/entities/<book>/<type>/<id>")
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


@bp.route("/api/entities/<book>", methods=["POST"])
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


@bp.route("/api/entities/<book>/<type>/<id>", methods=["PUT"])
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


@bp.route("/api/entities/<book>/<type>/<id>", methods=["DELETE"])
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


@bp.route("/api/entities/<book>/check-consistency", methods=["POST"])
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
