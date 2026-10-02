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
import os
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
from . import storage
import logging

log = logging.getLogger(__name__)

# ── 文件 IO ───────────────────────────────────────────────────────────────

def _mem_path(book: str, lib: str) -> Path:
    """获取 memory 文件路径, 从 lib.storage.PROJECTS_ROOT 拼接."""
    return storage.PROJECTS_ROOT / book / "memory" / f"{lib}.json"


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
    # 原子写 (2026-10-02): 直接 write_text 是「截断→写入」, 进程在中间被杀就留下
    # 一个半截的 JSON, 读侧 json.JSONDecodeError 会静默退化成空 dict —— 角色记忆
    # 整库消失且无任何报错。EntityStore 会在读路径上触发迁移写盘, 这个窗口是常态
    # 而不是异常, 所以必须 temp + os.replace。
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)
    # v1.3 M6: sync entities to SQLite
    _sync_entities_to_db(book, lib, data)


def _entity_key(item: Any, field: str, max_len: int = 50) -> str:
    """SQLite 实体键: 优先稳定 id, 回退到旧自然键.

    回退不能省: 迁移前的行没有 id, 而 entities.name 是 NOT NULL, 空串会把所有
    未迁移的行挤到同一条记录上。
    """
    if not isinstance(item, dict):
        return ""
    eid = item.get("id")
    if isinstance(eid, str) and eid:
        return eid
    text = item.get(field, "")
    if isinstance(text, str) and text:
        return text[:max_len]
    return ""


def _sync_entities_to_db(book: str, lib: str, data: Any) -> None:
    """Write-through: after saving entity JSON, sync to SQLite.

    键从"显示名"换成稳定 id —— 显示名会随改名而变, 换了就等于多出一条记录。
    """
    try:
        from . import db as _dbmod
    except Exception:
        return
    if lib == "characters" and isinstance(data, dict):
        for name, d in data.items():
            _dbmod.upsert_entity(storage.ROOT, book, "character",
                                 _entity_key(d, "name") or name, content=d)
    elif lib == "events" and isinstance(data, list):
        for item in data:
            eid = _entity_key(item, "event")
            if eid:
                _dbmod.upsert_entity(storage.ROOT, book, "event", eid,
                                     content=item)
    elif lib == "foreshadowing" and isinstance(data, list):
        for item in data:
            eid = _entity_key(item, "foreshadow")
            if eid:
                _dbmod.upsert_entity(storage.ROOT, book, "foreshadow", eid,
                                     content=item)
    elif lib == "world" and isinstance(data, dict):
        for rid, item in data.get("rules", {}).items():
            if not isinstance(item, dict):
                continue
            eid = _entity_key(item, "name") or str(rid)[:50]
            if eid:
                _dbmod.upsert_entity(storage.ROOT, book, "world_rule", eid,
                                     category=item.get("category"),
                                     status=item.get("status"),
                                     content=item)


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
                log.info("记忆合并 · get_world 第1处兜底步骤失败 (非致命)", exc_info=True)
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
                log.info("记忆合并 · merge_extraction 第1处兜底步骤失败 (非致命)", exc_info=True)
                pass
    # 兼容旧 world_updates (字符串数组)
    for wu in extraction.get("world_updates", []):
        if isinstance(wu, str):
            # 升级为 WorldRule
            try:
                wr = WorldRule(
                    # 原来是 wu[:30] or wu[:30] —— 两侧相同, 短路永不生效,
                    # 应是复制粘贴残留。本意应为 wu[:30] or wu(截断为空时回退全文),
                    # 但非空字符串截断后必非空, 所以两者行为一致, 简化之。
                    name=wu[:30],
                    category="其他",
                    description=wu,
                )
                if wr.id not in world.get("rules", {}):
                    world.setdefault("rules", {})[wr.id] = wr.to_dict()
            except ValueError:
                log.info("记忆合并 · merge_extraction 第2处兜底步骤失败 (非致命)", exc_info=True)
                pass
        elif isinstance(wu, dict) and wu.get("name"):
            try:
                wr = WorldRule.from_dict(wu)
                if wr.id not in world.get("rules", {}):
                    world.setdefault("rules", {})[wr.id] = wr.to_dict()
            except (ValueError, KeyError):
                log.info("记忆合并 · merge_extraction 第3处兜底步骤失败 (非致命)", exc_info=True)
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
# 稳定 id 迁移
# ═════════════════════════════════════════════════════════════════════════

def _has_id(row: object) -> bool:
    """行是否已带合法 id。"""
    return (isinstance(row, dict)
            and isinstance(row.get("id"), str) and bool(row["id"]))


def _migrate_keyed_rows(book: str, lib: str, data: Any, prefix: str) -> int:
    """给 dict 型库补 id (characters 的键仍是显示名, 不动它), 有改动才落盘."""
    if not isinstance(data, dict) or not data:
        return 0
    added = 0
    for row in data.values():
        if isinstance(row, dict) and not _has_id(row):
            row["id"] = gen_id(prefix)
            added += 1
    if added:
        _save_raw(book, lib, data)
    return added


def _migrate_list_rows(book: str, lib: str, rows: Any, prefix: str) -> int:
    """给 list 型库 (events / foreshadowing) 补 id, 有改动才落盘."""
    if not isinstance(rows, list) or not rows:
        return 0
    added = 0
    for row in rows:
        if isinstance(row, dict) and not _has_id(row):
            row["id"] = gen_id(prefix)
            added += 1
    if added:
        _save_raw(book, lib, rows)
    return added


def _migrate_world_rules(book: str) -> int:
    """给缺 id 的 WorldRule 补 id, 并把 rules 的键对齐到 id (键本来就是 id)."""
    data = _load_raw(book, "world")
    if not isinstance(data, dict):
        return 0
    rules = data.get("rules")
    if not isinstance(rules, dict) or not rules:
        return 0
    added = 0
    rekeyed: dict = {}
    for key, row in rules.items():
        if isinstance(row, dict) and not _has_id(row):
            row["id"] = gen_id("rule")
            added += 1
        target = (row.get("id") if isinstance(row, dict) else None) or key
        if target in rekeyed:
            target = key          # 键撞车: 保留原键, 不覆盖已有规则
        rekeyed[target] = row
    if rekeyed != rules:
        data["rules"] = rekeyed
        _save_raw(book, "world", data)
    return added


def migrate_entity_ids(book: str) -> dict[str, int]:
    """给 book 下缺 id 的实体补一个稳定 id 并落盘 (幂等).

    为什么必须落盘: dataclass.from_dict 只在内存里补 id, 不写回文件的话, 下次读
    又是另一个随机值 —— 同一个角色两次读出来身份不同, 重启进程更会丢。这里只给
    "缺 id 的行"补, 已有 id 原样保留, 所以重复调用不改 id、不产生重复条目。

    只写有改动的库: 迁移完成后本函数退化成纯读, 可以安全地放在读路径上。

    返回各库本次新增的 id 数量。
    """
    return {
        "characters": _migrate_keyed_rows(
            book, "characters", _load_raw(book, "characters"), "char"),
        "events": _migrate_list_rows(
            book, "events", _load_raw(book, "events"), "event"),
        "foreshadowing": _migrate_list_rows(
            book, "foreshadowing", _load_raw(book, "foreshadowing"), "fs"),
        "world_rule": _migrate_world_rules(book),
    }


# ═════════════════════════════════════════════════════════════════════════
# EntityStore — v1.2 M1.1 新增统一 CRUD 层
# ═════════════════════════════════════════════════════════════════════════

# ident(调用方传进来的那个 key)的解析顺序, get/update/delete/list_by_type 通用:
#   1. 稳定 id 精确匹配          —— 主路径; 改名后依然指向同一条记录
#   2. 自然键匹配 (向后兼容)     —— 未迁移的旧数据没有 id
#      character:   显示名 (= characters.json 的键) 或行内 name 字段
#      event/fs:    文本前 30 字 (旧行为)
# 第 2 步不能砍: 线上 UI 的 /api/entities/<book>/<type>/<id> 现在传的仍是中文
# 显示名(如 '苏晴'), 砍掉之后所有 GET/PUT/DELETE 会直接 404。
#
# 旧行为"id = 文本前 30 字"原先散在 5 处(get_event / get_foreshadow /
# update_foreshadow / delete_event / delete_foreshadow), 且访问方式还不统一
# (dataclass 属性 vs 原始 dict .get())。改一处漏四处就会让两个实体的行为分裂,
# 所以自然键的规则收在这里。
_ID_PREFIX_LEN = 30


def _id_matches(text: object, ident: str) -> bool:
    """文本前 30 字是否等于 ident。text 非字符串时当作不匹配。"""
    return isinstance(text, str) and text[:_ID_PREFIX_LEN] == ident


def _row_ident_matches(row: object, field: str, ident: str) -> bool:
    """一行是否匹配 ident: 先比 id, 再回退到自然键 (field 文本前 30 字)."""
    if not isinstance(row, dict):
        return False
    if row.get("id") == ident:
        return True
    return _id_matches(row.get(field, ""), ident)


def _find_row_index(rows: list[dict], field: str, ident: str) -> int:
    """ident → 行下标, 先 id 再自然键; 找不到返回 -1.

    分两趟而不是一趟, 是为了让 id 严格优先于自然键: 一个 id 恰好等于另一个实体
    的自然键时, 命中的一定是 id 真正指向的那条。
    """
    for i, r in enumerate(rows):
        if isinstance(r, dict) and r.get("id") == ident:
            return i
    for i, r in enumerate(rows):
        if _id_matches(r.get(field, ""), ident) if isinstance(r, dict) else False:
            return i
    return -1


def _find_character_key(chars: dict, ident: str) -> str | None:
    """ident → characters.json 的存储键: id → 存储键(显示名) → 行内 name.

    存储键保持是显示名(文件格式不变), 解析出来之后所有读写都走这个键。
    """
    for key, d in chars.items():
        if isinstance(d, dict) and d.get("id") == ident:
            return key
    if ident in chars:
        return ident
    for key, d in chars.items():
        if isinstance(d, dict) and d.get("name") == ident:
            return key
    return None


class EntityStore:
    """统一管理 4 类实体的 CRUD.

    实体的主键是稳定的 `id` 字段 (Character.char_xxxx / Event.event_xxxx /
    Foreshadow.fs_xxxx / WorldRule.rule_xxxx), 与显示名解耦 —— 改名不改身份。

    get/update/delete 的参数 `ident` 按"id 优先、自然键兜底"的顺序解析,
    所以旧调用方(以及线上 UI 传的中文显示名)不用改也能继续用。详见本模块
    _ID_PREFIX_LEN 上方的解析顺序说明。

    用法:
        store = EntityStore(book)
        char = store.add_character(Character(name="主角"))
        store.list_characters()
        store.update_character("主角", arc="觉醒")     # 或 update_character(char.id, ...)
        store.delete_character(char.id)
    """

    def __init__(self, book: str):
        self.book = book
        # 稳定 id 的落盘点放这里: 14 处调用点(含 review_ui)都不会主动调迁移,
        # 而迁移完成后是零写入的纯读, 放在读路径上代价可忽略。
        # 迁移失败绝不能让读炸掉 —— 兜住后退回自然键查找, 旧行为不变。
        try:
            migrate_entity_ids(book)
        except Exception:
            log.info("实体 id 迁移失败 (非致命, 退回自然键查找)", exc_info=True)

    # ── Character ─────────────────────────────────────────────────────

    def add_character(self, char: Character) -> Character:
        chars = get_characters(self.book)
        if char.name in chars:
            raise ValueError(f"Character '{char.name}' 已存在")
        char.touch()
        chars[char.name] = char.to_dict()
        update_characters(self.book, chars)
        return char

    def get_character(self, ident: str) -> Character | None:
        """按 id 或显示名取角色 (id 优先)。找不到返回 None。"""
        chars = get_characters(self.book)
        key = _find_character_key(chars, ident)
        if key is None:
            return None
        return Character.from_dict(chars[key])

    def list_characters(self) -> list[Character]:
        chars = get_characters(self.book)
        return [Character.from_dict(c) for c in chars.values()]

    def update_character(self, ident: str, **fields) -> Character:
        """按 id 或显示名更新 (id 优先)。ident 不存在则抛 ValueError。"""
        chars = get_characters(self.book)
        key = _find_character_key(chars, ident)
        if key is None:
            raise ValueError(f"Character '{ident}' 不存在")
        existing = chars[key]
        if not _has_id(existing):
            existing["id"] = gen_id("char")   # 顺手补上, 别让 id 每次读都变
        for k, v in fields.items():
            if k in existing:
                existing[k] = v
        existing["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        chars[key] = existing
        update_characters(self.book, chars)
        return Character.from_dict(existing)

    def delete_character(self, ident: str) -> None:
        """按 id 或显示名删除 (id 优先)。"""
        chars = get_characters(self.book)
        key = _find_character_key(chars, ident)
        if key is None:
            raise ValueError(f"Character '{ident}' 不存在")
        del chars[key]
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
        """按 id 取事件; 旧数据/旧调用方传文本时回退到"文本前 30 字"匹配."""
        events = get_events(self.book)
        idx = _find_row_index(events, "event", event_id)
        return Event.from_dict(events[idx]) if idx >= 0 else None

    def delete_event(self, event_id: str) -> None:
        events = get_events(self.book)
        idx = _find_row_index(events, "event", event_id)
        if idx < 0:
            raise ValueError(f"Event '{event_id}' 不存在")
        del events[idx]
        update_events(self.book, events)

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
        """按 id 取伏笔; 旧数据/旧调用方传文本时回退到"文本前 30 字"匹配."""
        items = get_foreshadowing(self.book)
        idx = _find_row_index(items, "foreshadow", fs_id)
        return Foreshadow.from_dict(items[idx]) if idx >= 0 else None

    def update_foreshadow(self, fs_id: str, **fields) -> Foreshadow:
        items = get_foreshadowing(self.book)
        idx = _find_row_index(items, "foreshadow", fs_id)
        if idx < 0:
            raise ValueError(f"Foreshadow '{fs_id}' 不存在")
        f = items[idx]
        if not _has_id(f):
            f["id"] = gen_id("fs")     # 顺手补上, 别让 id 每次读都变
        for k, v in fields.items():
            if k in f:
                f[k] = v
        f["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        items[idx] = f
        update_foreshadowing(self.book, items)
        return Foreshadow.from_dict(f)

    def delete_foreshadow(self, fs_id: str) -> None:
        items = get_foreshadowing(self.book)
        idx = _find_row_index(items, "foreshadow", fs_id)
        if idx < 0:
            raise ValueError(f"Foreshadow '{fs_id}' 不存在")
        del items[idx]
        update_foreshadowing(self.book, items)

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
        """按 EntityType 返回 Entity 列表 (每条的 id 都是稳定 id, 不是显示名)."""
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