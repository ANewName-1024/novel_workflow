"""
4-library memory system:
  characters  world  events  foreshadowing

Each library is a JSON file under projects/<book>/memory/.

v1.2 M1.1 升级:
  - 引入 lib/entity.py 的 dataclass 模型作为统一序列化层
  - world.json 结构升级: {rules: {id: WorldRule}, raw_notes: [str], _legacy: {key: text}}
  - 提供 EntityStore 统一 CRUD API (Character / Event / Foreshadow / WorldRule)
  - 向后兼容旧数据 (characters 用 name 作为 key, 旧格式兼容)
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from .entity import (
    Character,
    Entity,
    EntityType,
    Event,
    Foreshadow,
    ForeshadowStatus,
    WorldRule,
    gen_id,
)

# ── 文件 IO ───────────────────────────────────────────────────────────────

def _mem_path(book: str, lib: str) -> Path:
    return Path(__file__).parent.parent / "projects" / book / "memory" / f"{lib}.json"


def _load_raw(book: str, lib: str) -> Any:
    """直接读 JSON 文件, 不做语义解析."""
    p = _mem_path(book, lib)
    if not p.exists():
        return {} if lib in ("characters", "world") else []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {} if lib in ("characters", "world") else []


def _save_raw(book: str, lib: str, data: Any) -> None:
    p = _mem_path(book, lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── characters (向后兼容: dict[name, dict]) ──────────────────────────────

def get_characters(book: str) -> dict:
    """返回 {name: dict} 形式, 与旧 API 兼容."""
    return _load_raw(book, "characters")


def update_characters(book: str, characters: dict) -> None:
    _save_raw(book, "characters", characters)


def get_characters_summary(book: str) -> str:
    chars = get_characters(book)
    if not chars:
        return "（暂无角色记忆）"
    lines = []
    for name, info in list(chars.items())[:10]:
        traits = info.get("traits", "")
        role = info.get("role", "")
        lines.append(f"- {name}（{role}）：{traits}")
    return "\n".join(lines) if lines else "（暂无角色记忆）"


# ── world (升级: {rules: {...}, raw_notes: [...], _legacy: {...}}) ──────

def get_world(book: str) -> dict:
    """返回世界数据 dict. 自动迁移旧格式."""
    data = _load_raw(book, "world")
    if not data:
        return {"rules": {}, "raw_notes": [], "_legacy": {}}
    # 旧格式: {key: text} → 新格式
    if "rules" not in data:
        legacy = {k: v for k, v in data.items() if isinstance(v, str)}
        new_data = {"rules": {}, "raw_notes": list(legacy.values()), "_legacy": legacy}
        # 自动迁移旧数据为 WorldRule 草稿
        for k, v in legacy.items():
            try:
                wr = WorldRule(
                    id=gen_id("rule"),
                    name=k[:30],
                    category="其他",
                    description=v,
                    status="已确立",
                )
                new_data["rules"][wr.id] = wr.to_dict()
            except ValueError:
                pass
        return new_data
    return data


def update_world(book: str, world: dict) -> None:
    _save_raw(book, "world", world)


def get_world_summary(book: str) -> str:
    w = get_world(book)
    rules = w.get("rules", {})
    if not rules:
        legacy = w.get("_legacy", {})
        if legacy:
            return json.dumps(legacy, ensure_ascii=False, indent=2)
        return "（暂无世界观记忆）"
    lines = []
    for rid, rd in list(rules.items())[:10]:
        cat = rd.get("category", "")
        desc = rd.get("description", "")[:60]
        lines.append(f"- [{cat}] {rd.get('name', rid)}: {desc}")
    return "\n".join(lines) if lines else "（暂无世界观记忆）"


# ── events ────────────────────────────────────────────────────────────────

def get_events(book: str) -> list[dict]:
    data = _load_raw(book, "events")
    return data if isinstance(data, list) else []


def update_events(book: str, events: list[dict]) -> None:
    _save_raw(book, "events", events)


def get_events_summary(book: str, max_events: int = 20) -> str:
    evts = get_events(book)
    if not evts:
        return "（暂无事件记忆）"
    lines = []
    for e in evts[-max_events:]:
        lines.append(f"- {e.get('event', '')}（{e.get('significance', '')}）")
    return "\n".join(lines) if lines else "（暂无事件记忆）"


# ── foreshadowing ─────────────────────────────────────────────────────────

def get_foreshadowing(book: str) -> list[dict]:
    data = _load_raw(book, "foreshadowing")
    return data if isinstance(data, list) else []


def update_foreshadowing(book: str, foreshadowing: list[dict]) -> None:
    _save_raw(book, "foreshadowing", foreshadowing)


def mark_resolved(book: str, text: str) -> None:
    """按文本匹配标记伏笔已回收."""
    fs = get_foreshadowing(book)
    for item in fs:
        if text in item.get("foreshadow", "") and item.get("status") != ForeshadowStatus.RESOLVED.value:
            item["status"] = ForeshadowStatus.RESOLVED.value
            item["resolved_at"] = datetime.datetime.now().isoformat()
    update_foreshadowing(book, fs)


def get_foreshadowing_summary(book: str) -> str:
    fs = get_foreshadowing(book)
    if not fs:
        return "（暂无伏笔记忆）"
    lines = []
    for f in fs[-15:]:
        status = f.get("status", ForeshadowStatus.PLANTED.value)
        lines.append(f"- [{status}] {f.get('foreshadow', '')}")
    return "\n".join(lines) if lines else "（暂无伏笔记忆）"


# ── 抽取合并 (旧 API, 保留) ─────────────────────────────────────────────

def merge_extraction(book: str, extraction: dict) -> None:
    """把 extract.py 输出合并到 4 个库."""
    # Characters
    chars = get_characters(book)
    for nc in extraction.get("new_characters", []):
        if nc["name"] not in chars:
            chars[nc["name"]] = nc
    for uc in extraction.get("updated_characters", []):
        name = uc.get("name", "")
        if name in chars:
            existing = chars[name].get("traits", "")
            updated = uc.get("updated_traits", "")
            if updated and updated not in existing:
                chars[name]["traits"] = existing + "；" + updated
            rel = uc.get("relationship_changes", "")
            if rel:
                chars[name]["relationship"] = rel
    update_characters(book, chars)

    # World (新格式)
    world = get_world(book)
    # v1.2 M1.2: 处理 new_world_rules (结构化)
    for wr_data in extraction.get("new_world_rules", []):
        if isinstance(wr_data, dict) and wr_data.get("name"):
            try:
                wr = WorldRule.from_dict(wr_data)
                if wr.id not in world.get("rules", {}):
                    world.setdefault("rules", {})[wr.id] = wr.to_dict()
            except (ValueError, KeyError):
                pass
    # 兼容旧 world_updates (字符串数组)
    for wu in extraction.get("world_updates", []):
        if isinstance(wu, str):
            # 升级为 WorldRule
            try:
                wr = WorldRule(
                    name=wu[:30] or wu[:30],
                    category="其他",
                    description=wu,
                )
                if wr.id not in world.get("rules", {}):
                    world.setdefault("rules", {})[wr.id] = wr.to_dict()
            except ValueError:
                pass
        elif isinstance(wu, dict) and wu.get("name"):
            try:
                wr = WorldRule.from_dict(wu)
                if wr.id not in world.get("rules", {}):
                    world.setdefault("rules", {})[wr.id] = wr.to_dict()
            except (ValueError, KeyError):
                pass
    update_world(book, world)

    # Events
    events = get_events(book)
    for ne in extraction.get("new_events", []):
        events.append({**ne, "extracted_at": datetime.datetime.now().isoformat()})
    update_events(book, events[-50:])

    # Foreshadowing
    fs = get_foreshadowing(book)
    for nf in extraction.get("new_foreshadowing", []):
        if not any(nf.get("foreshadow", "") == f.get("foreshadow", "") for f in fs):
            fs.append({**nf, "status": ForeshadowStatus.PLANTED.value})
    for rf in extraction.get("resolved_foreshadowing", []):
        for f in fs:
            if rf in f.get("foreshadow", "") and f.get("status") != ForeshadowStatus.RESOLVED.value:
                f["status"] = ForeshadowStatus.RESOLVED.value
                f["resolved_at"] = datetime.datetime.now().isoformat()
    update_foreshadowing(book, fs)


# ═════════════════════════════════════════════════════════════════════════
# EntityStore — v1.2 M1.1 新增统一 CRUD 层
# ═════════════════════════════════════════════════════════════════════════

class EntityStore:
    """统一管理 4 类实体的 CRUD.

    用法:
        store = EntityStore(book)
        char = store.add_character(Character(name="主角"))
        store.list_characters()
        store.update_character("主角", arc="觉醒")
        store.delete_character("主角")
    """

    def __init__(self, book: str):
        self.book = book

    # ── Character ─────────────────────────────────────────────────────

    def add_character(self, char: Character) -> Character:
        chars = get_characters(self.book)
        if char.name in chars:
            raise ValueError(f"Character '{char.name}' 已存在")
        char.touch()
        chars[char.name] = char.to_dict()
        update_characters(self.book, chars)
        return char

    def get_character(self, name: str) -> Character | None:
        chars = get_characters(self.book)
        if name not in chars:
            return None
        return Character.from_dict(chars[name])

    def list_characters(self) -> list[Character]:
        chars = get_characters(self.book)
        return [Character.from_dict(c) for c in chars.values()]

    def update_character(self, name: str, **fields) -> Character:
        chars = get_characters(self.book)
        if name not in chars:
            raise ValueError(f"Character '{name}' 不存在")
        existing = chars[name]
        for k, v in fields.items():
            if k in existing:
                existing[k] = v
        existing["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        chars[name] = existing
        update_characters(self.book, chars)
        return Character.from_dict(existing)

    def delete_character(self, name: str) -> None:
        chars = get_characters(self.book)
        if name not in chars:
            raise ValueError(f"Character '{name}' 不存在")
        del chars[name]
        update_characters(self.book, chars)

    # ── Event ────────────────────────────────────────────────────────

    def add_event(self, event: Event) -> Event:
        events = get_events(self.book)
        events.append(event.to_dict())
        update_events(self.book, events[-50:])
        return event

    def list_events(self) -> list[Event]:
        events = get_events(self.book)
        return [Event.from_dict(e) for e in events]

    def get_event(self, event_id: str) -> Event | None:
        """按 event 文本前 30 字匹配."""
        for e in self.list_events():
            if e.event[:30] == event_id:
                return e
        return None

    def delete_event(self, event_id: str) -> None:
        events = get_events(self.book)
        new_events = [e for e in events if e.get("event", "")[:30] != event_id]
        if len(new_events) == len(events):
            raise ValueError(f"Event '{event_id}' 不存在")
        update_events(self.book, new_events)

    # ── Foreshadow ───────────────────────────────────────────────────

    def add_foreshadow(self, fs: Foreshadow) -> Foreshadow:
        items = get_foreshadowing(self.book)
        items.append(fs.to_dict())
        update_foreshadowing(self.book, items)
        return fs

    def list_foreshadows(self) -> list[Foreshadow]:
        items = get_foreshadowing(self.book)
        return [Foreshadow.from_dict(f) for f in items]

    def get_foreshadow(self, fs_id: str) -> Foreshadow | None:
        for f in self.list_foreshadows():
            if f.foreshadow[:30] == fs_id:
                return f
        return None

    def update_foreshadow(self, fs_id: str, **fields) -> Foreshadow:
        items = get_foreshadowing(self.book)
        for i, f in enumerate(items):
            if f.get("foreshadow", "")[:30] == fs_id:
                for k, v in fields.items():
                    if k in f:
                        f[k] = v
                f["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                items[i] = f
                update_foreshadowing(self.book, items)
                return Foreshadow.from_dict(f)
        raise ValueError(f"Foreshadow '{fs_id}' 不存在")

    def delete_foreshadow(self, fs_id: str) -> None:
        items = get_foreshadowing(self.book)
        new_items = [f for f in items if f.get("foreshadow", "")[:30] != fs_id]
        if len(new_items) == len(items):
            raise ValueError(f"Foreshadow '{fs_id}' 不存在")
        update_foreshadowing(self.book, new_items)

    # ── WorldRule (v1.2 新!) ─────────────────────────────────────────

    def add_world_rule(self, rule: WorldRule) -> WorldRule:
        world = get_world(self.book)
        if rule.id in world.get("rules", {}):
            raise ValueError(f"WorldRule '{rule.id}' 已存在")
        rule.touch()
        world.setdefault("rules", {})[rule.id] = rule.to_dict()
        update_world(self.book, world)
        return rule

    def get_world_rule(self, rule_id: str) -> WorldRule | None:
        world = get_world(self.book)
        rules = world.get("rules", {})
        if rule_id not in rules:
            return None
        return WorldRule.from_dict(rules[rule_id])

    def list_world_rules(self) -> list[WorldRule]:
        world = get_world(self.book)
        rules = world.get("rules", {})
        return [WorldRule.from_dict(r) for r in rules.values()]

    def update_world_rule(self, rule_id: str, **fields) -> WorldRule:
        world = get_world(self.book)
        rules = world.get("rules", {})
        if rule_id not in rules:
            raise ValueError(f"WorldRule '{rule_id}' 不存在")
        existing = rules[rule_id]
        for k, v in fields.items():
            if k in existing:
                existing[k] = v
        existing["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        rules[rule_id] = existing
        update_world(self.book, world)
        return WorldRule.from_dict(existing)

    def delete_world_rule(self, rule_id: str) -> None:
        world = get_world(self.book)
        rules = world.get("rules", {})
        if rule_id not in rules:
            raise ValueError(f"WorldRule '{rule_id}' 不存在")
        del rules[rule_id]
        update_world(self.book, world)

    # ── 通用 API ────────────────────────────────────────────────────

    def list_by_type(self, entity_type: EntityType) -> list[Entity]:
        """按 EntityType 返回 Entity 列表."""
        if entity_type == EntityType.CHARACTER:
            return [Entity.from_dataclass(c, entity_type) for c in self.list_characters()]
        elif entity_type == EntityType.EVENT:
            return [Entity.from_dataclass(e, entity_type) for e in self.list_events()]
        elif entity_type == EntityType.FORESHADOW:
            return [Entity.from_dataclass(f, entity_type) for f in self.list_foreshadows()]
        elif entity_type == EntityType.WORLD_RULE:
            return [Entity.from_dataclass(r, entity_type) for r in self.list_world_rules()]
        raise ValueError(f"Unknown EntityType: {entity_type}")

    def counts(self) -> dict[str, int]:
        """返回各类实体的数量统计."""
        return {
            "character": len(self.list_characters()),
            "event": len(self.list_events()),
            "foreshadow": len(self.list_foreshadows()),
            "world_rule": len(self.list_world_rules()),
        }