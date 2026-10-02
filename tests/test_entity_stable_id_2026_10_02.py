"""
test_entity_stable_id_2026_10_02.py — 实体稳定 id (2026-10-02).

背景: Character / Event / Foreshadow 以前拿"自然键"(显示名 / 文本前 30 字)
当主键, 改名就等于换了一个实体。WorldRule 早就有稳定 id, 这次把另外 3 类
对齐到同一形状。

覆盖:
  - 3 个 dataclass 构造即有非空 id
  - from_dict 对旧数据 (无 id) 补一个, 对已有 id 原样保留
  - Entity.from_dataclass 4 类都返回 id
  - migrate_entity_ids 幂等 + 真落盘 (重启进程也不变)
  - 按名字查仍可用 (向后兼容, 线上 UI 传的是中文显示名)
  - 按 id 查指向同一条
  - characters.json 的键仍是显示名 (文件格式不变)
"""
from __future__ import annotations

import json

import pytest

from lib.entity import Character, Entity, EntityType, Event, Foreshadow, WorldRule
from lib.memory import EntityStore, get_characters, migrate_entity_ids


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def legacy_book(tmp_path, monkeypatch):
    """一个只有旧格式数据(没有 id)的 book, memory 指向 tmp_path."""
    book = "stable_id_book"
    mem_dir = tmp_path / book / "memory"
    mem_dir.mkdir(parents=True)

    import lib.memory as mem_mod
    from lib import storage

    monkeypatch.setattr(mem_mod, "_mem_path",
                        lambda b, lib: mem_dir / f"{lib}.json")
    monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(storage, "ROOT", tmp_path)

    # 旧数据: characters 是 {显示名: {...}}, events/foreshadowing 是 list
    (mem_dir / "characters.json").write_text(json.dumps({
        "苏晴": {"name": "苏晴", "role": "主角", "traits": "冷静"},
        "白色灵兽": {"name": "白色灵兽", "role": "配角", "traits": "忠诚"},
    }, ensure_ascii=False), encoding="utf-8")
    (mem_dir / "events.json").write_text(json.dumps([
        {"event": "苏晴觉醒灵根", "chapter": 1},
    ], ensure_ascii=False), encoding="utf-8")
    (mem_dir / "foreshadowing.json").write_text(json.dumps([
        # merge_extraction 落盘时总会带 status, 这里只缺 id
        {"foreshadow": "神秘玉佩", "status": "已埋", "planted_chapter": 1},
    ], ensure_ascii=False), encoding="utf-8")
    return book


# ── 1. dataclass 构造即有 id ───────────────────────────────────────────────

class TestIdOnConstruction:
    def test_character_gets_id(self):
        c = Character(name="苏晴")
        assert c.id
        assert isinstance(c.id, str)

    def test_event_gets_id(self):
        e = Event(event="苏晴觉醒灵根")
        assert e.id
        assert isinstance(e.id, str)

    def test_foreshadow_gets_id(self):
        f = Foreshadow(foreshadow="神秘玉佩")
        assert f.id
        assert isinstance(f.id, str)

    def test_ids_are_unique_per_instance(self):
        a, b = Character(name="A"), Character(name="B")
        assert a.id != b.id

    def test_required_field_still_positional_compatible(self):
        # id 插在必填字段之后, 第一个位置参数仍然是 name/event/foreshadow
        assert Character("苏晴").name == "苏晴"
        assert Event("觉醒").event == "觉醒"
        assert Foreshadow("玉佩").foreshadow == "玉佩"

    def test_to_dict_carries_id(self):
        assert Character(name="苏晴").to_dict()["id"]


# ── 2. from_dict ───────────────────────────────────────────────────────────

class TestFromDictId:
    def test_character_legacy_gets_id(self):
        c = Character.from_dict({"name": "苏晴", "role": "主角"})
        assert c.id
        assert c.id.startswith("char_")

    def test_event_legacy_gets_id(self):
        e = Event.from_dict({"event": "苏晴觉醒灵根"})
        assert e.id
        assert e.id.startswith("event_")

    def test_foreshadow_legacy_gets_id(self):
        f = Foreshadow.from_dict({"foreshadow": "神秘玉佩"})
        assert f.id
        assert f.id.startswith("fs_")

    def test_existing_id_preserved_character(self):
        c = Character.from_dict({"name": "苏晴", "id": "char_deadbe"})
        assert c.id == "char_deadbe"

    def test_existing_id_preserved_event(self):
        e = Event.from_dict({"event": "E", "id": "event_deadbe"})
        assert e.id == "event_deadbe"

    def test_existing_id_preserved_foreshadow(self):
        f = Foreshadow.from_dict({"foreshadow": "F", "id": "fs_deadbe"})
        assert f.id == "fs_deadbe"

    def test_empty_id_is_replaced(self):
        c = Character.from_dict({"name": "苏晴", "id": ""})
        assert c.id and c.id != ""

    def test_id_roundtrips_through_to_from_dict(self):
        c = Character(name="苏晴", role="主角")
        assert Character.from_dict(c.to_dict()).id == c.id


# ── 3. Entity.from_dataclass ───────────────────────────────────────────────

class TestFromDataclass:
    def test_character_id(self):
        c = Character(name="苏晴")
        assert Entity.from_dataclass(c, EntityType.CHARACTER).id == c.id

    def test_event_id(self):
        e = Event(event="苏晴觉醒灵根")
        assert Entity.from_dataclass(e, EntityType.EVENT).id == e.id

    def test_foreshadow_id(self):
        f = Foreshadow(foreshadow="神秘玉佩")
        assert Entity.from_dataclass(f, EntityType.FORESHADOW).id == f.id

    def test_world_rule_id(self):
        r = WorldRule(name="灵气稀薄")
        assert Entity.from_dataclass(r, EntityType.WORLD_RULE).id == r.id

    def test_id_is_not_the_display_name(self):
        c = Character(name="苏晴")
        assert Entity.from_dataclass(c, EntityType.CHARACTER).id != "苏晴"

    def test_event_id_is_not_text_prefix(self):
        e = Event(event="苏晴觉醒灵根")
        assert Entity.from_dataclass(e, EntityType.EVENT).id != e.event[:30]


# ── 4. migrate_entity_ids 幂等 ─────────────────────────────────────────────

class TestMigrateEntityIds:
    def test_backfills_all_four_libs(self, legacy_book):
        counts = migrate_entity_ids(legacy_book)
        assert counts["characters"] == 2
        assert counts["events"] == 1
        assert counts["foreshadowing"] == 1

        chars = get_characters(legacy_book)
        assert all(row["id"] for row in chars.values())

    def test_persists_to_disk(self, legacy_book, tmp_path):
        migrate_entity_ids(legacy_book)
        on_disk = json.loads(
            (tmp_path / legacy_book / "memory" / "characters.json").read_text(encoding="utf-8"))
        assert on_disk["苏晴"].get("id", "").startswith("char_")
        assert on_disk["白色灵兽"].get("id", "").startswith("char_")
        ev = json.loads(
            (tmp_path / legacy_book / "memory" / "events.json").read_text(encoding="utf-8"))
        assert ev[0].get("id", "").startswith("event_")
        fs = json.loads(
            (tmp_path / legacy_book / "memory" / "foreshadowing.json").read_text(encoding="utf-8"))
        assert fs[0].get("id", "").startswith("fs_")

    def test_ids_survive_process_restart(self, legacy_book):
        """id 真的写进了文件 —— 换一个 store 实例(等价于重启进程)读到同一个值."""
        first = {c.name: c.id for c in EntityStore(legacy_book).list_characters()}
        second = {c.name: c.id for c in EntityStore(legacy_book).list_characters()}
        assert first == second
        assert all(first.values())

    def test_idempotent_second_run_changes_nothing(self, legacy_book, tmp_path):
        migrate_entity_ids(legacy_book)
        path = tmp_path / legacy_book / "memory" / "characters.json"
        first = json.loads(path.read_text(encoding="utf-8"))

        counts = migrate_entity_ids(legacy_book)
        second = json.loads(path.read_text(encoding="utf-8"))

        assert counts["characters"] == 0
        assert first == second

    def test_idempotent_no_duplicates(self, legacy_book):
        migrate_entity_ids(legacy_book)
        migrate_entity_ids(legacy_book)
        chars = get_characters(legacy_book)
        assert len(chars) == 2
        assert len(EntityStore(legacy_book).list_characters()) == 2
        ids = [row["id"] for row in chars.values()]
        assert len(set(ids)) == len(ids)

    def test_idempotent_on_already_migrated_data(self, tmp_path, monkeypatch):
        book = "already_migrated"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        import lib.memory as mem_mod
        monkeypatch.setattr(mem_mod, "_mem_path",
                            lambda b, lib: mem_dir / f"{lib}.json")

        c = Character(name="苏晴")
        (mem_dir / "characters.json").write_text(
            json.dumps({"苏晴": c.to_dict()}, ensure_ascii=False), encoding="utf-8")
        counts = migrate_entity_ids(book)
        assert counts["characters"] == 0
        assert json.loads((mem_dir / "characters.json").read_text(
            encoding="utf-8"))["苏晴"]["id"] == c.id

    def test_migrates_world_rules_too(self, tmp_path, monkeypatch):
        book = "world_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        import lib.memory as mem_mod
        monkeypatch.setattr(mem_mod, "_mem_path",
                            lambda b, lib: mem_dir / f"{lib}.json")
        (mem_dir / "world.json").write_text(json.dumps(
            {"rules": {"stale_key": {"name": "灵气稀薄"}}, "raw_notes": []},
            ensure_ascii=False), encoding="utf-8")

        counts = migrate_entity_ids(book)
        assert counts["world_rule"] == 1
        rules = json.loads((mem_dir / "world.json").read_text(encoding="utf-8"))["rules"]
        rid = list(rules)[0]
        assert rid.startswith("rule_")
        assert rules[rid]["id"] == rid
        assert migrate_entity_ids(book)["world_rule"] == 0


# ── 5. 向后兼容: 名字查找仍然可用 ─────────────────────────────────────────

class TestBackwardCompat:
    def test_name_lookup_after_migration(self, legacy_book):
        store = EntityStore(legacy_book)          # 构造即迁移
        c = store.get_character("苏晴")
        assert c is not None
        assert c.name == "苏晴"

    def test_name_lookup_without_explicit_migration(self, legacy_book, monkeypatch):
        """旧调用方连 migrate 都不调, 直接按名字查也要能用."""
        import lib.memory as mem_mod
        monkeypatch.setattr(
            mem_mod.EntityStore, "__init__",
            lambda self, book: setattr(self, "book", book))
        c = mem_mod.EntityStore(legacy_book).get_character("苏晴")
        assert c is not None and c.name == "苏晴"

    def test_id_lookup_finds_same_entity(self, legacy_book):
        store = EntityStore(legacy_book)
        by_name = store.get_character("苏晴")
        by_id = store.get_character(by_name.id)
        assert by_id is not None
        assert by_id.name == by_name.name == "苏晴"
        assert by_id.id == by_name.id

    def test_update_by_name_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        updated = store.update_character("苏晴", traits="冷静且强韧")
        assert updated.traits == "冷静且强韧"
        assert store.get_character("苏晴").traits == "冷静且强韧"

    def test_update_by_id_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        cid = store.get_character("苏晴").id
        updated = store.update_character(cid, traits="冷静且强韧")
        assert updated.traits == "冷静且强韧"
        assert store.get_character("苏晴").traits == "冷静且强韧"

    def test_delete_by_name_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        store.delete_character("苏晴")
        assert store.get_character("苏晴") is None
        assert len(store.list_characters()) == 1

    def test_delete_by_id_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        cid = store.get_character("苏晴").id
        store.delete_character(cid)
        assert store.get_character(cid) is None
        assert len(store.list_characters()) == 1

    def test_event_text_lookup_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        e = store.get_event("苏晴觉醒灵根")
        assert e is not None and e.chapter == 1

    def test_event_id_lookup_works(self, legacy_book):
        store = EntityStore(legacy_book)
        eid = store.get_event("苏晴觉醒灵根").id
        assert store.get_event(eid).event == "苏晴觉醒灵根"

    def test_foreshadow_text_lookup_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        f = store.get_foreshadow("神秘玉佩")
        assert f is not None

    def test_foreshadow_id_lookup_works(self, legacy_book):
        store = EntityStore(legacy_book)
        fid = store.get_foreshadow("神秘玉佩").id
        assert store.get_foreshadow(fid).foreshadow == "神秘玉佩"

    def test_event_delete_by_text_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        store.delete_event("苏晴觉醒灵根")
        assert store.get_event("苏晴觉醒灵根") is None

    def test_foreshadow_update_by_text_still_works(self, legacy_book):
        store = EntityStore(legacy_book)
        store.update_foreshadow("神秘玉佩", status="已回收", resolved_chapter=9)
        assert store.get_foreshadow("神秘玉佩").status == "已回收"

    def test_unknown_ident_still_raises(self, legacy_book):
        store = EntityStore(legacy_book)
        with pytest.raises(ValueError):
            store.delete_character("不存在的人")
        with pytest.raises(ValueError):
            store.delete_event("不存在的事件")

    def test_list_by_type_returns_stable_ids(self, legacy_book):
        store = EntityStore(legacy_book)
        ids = {e.id for e in store.list_by_type(EntityType.CHARACTER)}
        assert ids and "苏晴" not in ids
        assert all(i.startswith("char_") for i in ids)


# ── 6. 文件格式不变: characters.json 的键仍是显示名 ────────────────────────

class TestFileFormatUnchanged:
    def test_dict_keys_are_still_display_names(self, legacy_book, tmp_path):
        migrate_entity_ids(legacy_book)
        on_disk = json.loads(
            (tmp_path / legacy_book / "memory" / "characters.json").read_text(encoding="utf-8"))
        assert set(on_disk) == {"苏晴", "白色灵兽"}

    def test_store_still_exposes_name_keyed_dict(self, legacy_book):
        migrate_entity_ids(legacy_book)
        chars = get_characters(legacy_book)
        assert list(chars) == ["苏晴", "白色灵兽"]

    def test_id_lives_inside_the_row_not_the_key(self, legacy_book, tmp_path):
        migrate_entity_ids(legacy_book)
        on_disk = json.loads(
            (tmp_path / legacy_book / "memory" / "characters.json").read_text(encoding="utf-8"))
        assert "char_" in on_disk["苏晴"]["id"]
        assert "char_" not in on_disk["苏晴"]        # 键没被换成 id

    def test_events_and_foreshadowing_stay_lists(self, legacy_book, tmp_path):
        migrate_entity_ids(legacy_book)
        mem = tmp_path / legacy_book / "memory"
        assert isinstance(json.loads((mem / "events.json").read_text(encoding="utf-8")), list)
        assert isinstance(json.loads((mem / "foreshadowing.json").read_text(encoding="utf-8")), list)

    def test_rename_keeps_id(self, legacy_book):
        """改显示名不换身份 —— 这正是要稳定 id 的原因."""
        store = EntityStore(legacy_book)
        cid = store.get_character("苏晴").id
        updated = store.update_character(cid, name="苏晴儿")
        assert updated.id == cid
        assert store.get_character(cid).name == "苏晴儿"
