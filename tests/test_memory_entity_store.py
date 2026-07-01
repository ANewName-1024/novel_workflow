"""
Tests for lib/memory.py — EntityStore 统一 CRUD 层 + 向后兼容.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.entity import (
    Character,
    EntityType,
    Event,
    Foreshadow,
    ForeshadowStatus,
    WorldRule,
    WorldRuleStatus,
)
from lib.memory import (
    EntityStore,
    get_characters,
    get_events,
    get_foreshadowing,
    get_world,
)


# ── Character CRUD ───────────────────────────────────────────────────────

class TestEntityStoreCharacter:
    def test_add_and_get(self, tmp_book):
        store = EntityStore(tmp_book)
        c = Character(name="萧炎", role="主角", importance="高")
        store.add_character(c)

        fetched = store.get_character("萧炎")
        assert fetched is not None
        assert fetched.name == "萧炎"
        assert fetched.role == "主角"

    def test_list(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="A"))
        store.add_character(Character(name="B"))
        chars = store.list_characters()
        names = {c.name for c in chars}
        assert names == {"A", "B"}

    def test_duplicate_raises(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="X"))
        with pytest.raises(ValueError, match="已存在"):
            store.add_character(Character(name="X"))

    def test_update(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="主角"))
        store.update_character("主角", traits="觉醒", importance="高")
        c = store.get_character("主角")
        assert c.traits == "觉醒"
        assert c.importance == "高"

    def test_update_nonexistent_raises(self, tmp_book):
        store = EntityStore(tmp_book)
        with pytest.raises(ValueError, match="不存在"):
            store.update_character("不存在", traits="X")

    def test_delete(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="X"))
        store.delete_character("X")
        assert store.get_character("X") is None

    def test_delete_nonexistent_raises(self, tmp_book):
        store = EntityStore(tmp_book)
        with pytest.raises(ValueError, match="不存在"):
            store.delete_character("不存在")


# ── Event CRUD ───────────────────────────────────────────────────────────

class TestEntityStoreEvent:
    def test_add_and_list(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_event(Event(event="主角觉醒", chapter=3))
        store.add_event(Event(event="获得神功", chapter=4))
        events = store.list_events()
        assert len(events) == 2
        assert events[0].event == "主角觉醒"

    def test_get_event_by_prefix(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_event(Event(event="主角在山洞发现神秘玉佩"))
        e = store.get_event("主角在山洞发现神秘玉佩")
        assert e is not None
        assert "山洞" in e.event

    def test_delete_event(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_event(Event(event="事件A"))
        store.add_event(Event(event="事件B"))
        store.delete_event("事件A")
        assert store.get_event("事件A") is None
        assert store.get_event("事件B") is not None

    def test_delete_nonexistent_raises(self, tmp_book):
        store = EntityStore(tmp_book)
        with pytest.raises(ValueError, match="不存在"):
            store.delete_event("不存在")


# ── Foreshadow CRUD ─────────────────────────────────────────────────────

class TestEntityStoreForeshadow:
    def test_add_and_list(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_foreshadow(Foreshadow(foreshadow="神秘玉佩", planted_chapter=1))
        fs_list = store.list_foreshadows()
        assert len(fs_list) == 1
        assert fs_list[0].status == ForeshadowStatus.PLANTED.value

    def test_update_status(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_foreshadow(Foreshadow(foreshadow="玉佩", planted_chapter=1))
        store.update_foreshadow("玉佩", status="已回收", resolved_chapter=10)
        f = store.get_foreshadow("玉佩")
        assert f.status == "已回收"
        assert f.resolved_chapter == 10

    def test_delete(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_foreshadow(Foreshadow(foreshadow="玉佩"))
        store.delete_foreshadow("玉佩")
        assert store.get_foreshadow("玉佩") is None


# ── WorldRule CRUD (NEW!) ───────────────────────────────────────────────

class TestEntityStoreWorldRule:
    def test_add_and_get(self, tmp_book):
        store = EntityStore(tmp_book)
        r = WorldRule(
            name="灵根等级",
            category="体系",
            description="修士天赋分天/地/人三等",
            constraints=["灵根品级先天决定"],
        )
        store.add_world_rule(r)

        fetched = store.get_world_rule(r.id)
        assert fetched is not None
        assert fetched.name == "灵根等级"
        assert fetched.constraints == ["灵根品级先天决定"]

    def test_list(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_world_rule(WorldRule(name="规则A"))
        store.add_world_rule(WorldRule(name="规则B"))
        rules = store.list_world_rules()
        assert len(rules) == 2

    def test_update(self, tmp_book):
        store = EntityStore(tmp_book)
        r = store.add_world_rule(WorldRule(name="X", category="体系"))
        store.update_world_rule(r.id, description="详细说明", constraints=["硬约束1"])
        fetched = store.get_world_rule(r.id)
        assert fetched.description == "详细说明"
        assert fetched.constraints == ["硬约束1"]

    def test_update_status_to_abandoned(self, tmp_book):
        store = EntityStore(tmp_book)
        r = store.add_world_rule(WorldRule(name="X"))
        store.update_world_rule(r.id, status="已废弃")
        assert store.get_world_rule(r.id).status == "已废弃"

    def test_delete(self, tmp_book):
        store = EntityStore(tmp_book)
        r = store.add_world_rule(WorldRule(name="X"))
        store.delete_world_rule(r.id)
        assert store.get_world_rule(r.id) is None

    def test_delete_nonexistent_raises(self, tmp_book):
        store = EntityStore(tmp_book)
        with pytest.raises(ValueError, match="不存在"):
            store.delete_world_rule("rule_xxx")


# ── 通用 API ─────────────────────────────────────────────────────────────

class TestEntityStoreGeneric:
    def test_list_by_type(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="主角"))
        store.add_event(Event(event="事件"))
        store.add_foreshadow(Foreshadow(foreshadow="伏笔"))
        r = store.add_world_rule(WorldRule(name="规则"))

        chars = store.list_by_type(EntityType.CHARACTER)
        assert len(chars) == 1
        assert chars[0].id == "主角"

        wrs = store.list_by_type(EntityType.WORLD_RULE)
        assert len(wrs) == 1
        assert wrs[0].id == r.id

    def test_counts(self, tmp_book):
        store = EntityStore(tmp_book)
        store.add_character(Character(name="A"))
        store.add_character(Character(name="B"))
        store.add_event(Event(event="E"))
        store.add_world_rule(WorldRule(name="R"))

        counts = store.counts()
        assert counts["character"] == 2
        assert counts["event"] == 1
        assert counts["foreshadow"] == 0
        assert counts["world_rule"] == 1


# ── 向后兼容: world.json 旧格式自动迁移 ─────────────────────────────────

class TestWorldLegacyMigration:
    def test_legacy_dict_format_migrates(self, tmp_path, monkeypatch):
        """旧格式 {key: text} 自动迁移为 WorldRule."""
        book = "legacy_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "world.json").write_text(
            json.dumps({
                "公司存在自动化邮件触发": "公司存在自动化邮件触发机制",
                "二手书店按斤收购": "二手书店按斤收购旧书",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        # patch _mem_path 指向 tmp_path
        import lib.memory as mem_mod
        def patched_mem_path(book_name: str, lib: str) -> Path:
            return tmp_path / book_name / "memory" / f"{lib}.json"
        monkeypatch.setattr(mem_mod, "_mem_path", patched_mem_path)

        # 读取自动迁移
        world = get_world(book)
        assert "rules" in world
        assert len(world["rules"]) == 2
        assert "_legacy" in world
        assert len(world["_legacy"]) == 2

        # EntityStore 读到的 WorldRule
        store = EntityStore(book)
        rules = store.list_world_rules()
        assert len(rules) == 2
        names = {r.name for r in rules}
        # 旧 key 的前 30 字 当 name
        assert any("公司存在自动化" in n for n in names)

    def test_empty_world(self, tmp_book):
        world = get_world(tmp_book)
        assert world == {"rules": {}, "raw_notes": [], "_legacy": {}}

    def test_new_format_preserved(self, tmp_book):
        store = EntityStore(tmp_book)
        r = store.add_world_rule(WorldRule(name="新规则"))
        # 重新读取
        world = get_world(tmp_book)
        assert r.id in world["rules"]
        assert world["rules"][r.id]["name"] == "新规则"


# ── fixture ──────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_book(tmp_path, monkeypatch):
    """每个测试用独立 book 目录, patch lib.memory._mem_path 指向 tmp_path."""
    book = "test_book"
    (tmp_path / book / "memory").mkdir(parents=True)

    import lib.memory as mem_mod

    def patched_mem_path(book_name: str, lib: str) -> Path:
        return tmp_path / book_name / "memory" / f"{lib}.json"

    monkeypatch.setattr(mem_mod, "_mem_path", patched_mem_path)
    return book