"""
Tests for lib/entity.py — 4 实体的 dataclass 模型.
"""
from __future__ import annotations

import pytest

from lib.entity import (
    Character,
    Entity,
    EntityType,
    Event,
    Foreshadow,
    ForeshadowStatus,
    WorldRule,
    WorldRuleCategory,
    WorldRuleStatus,
    gen_id,
)


# ── Character ─────────────────────────────────────────────────────────────

class TestCharacter:
    def test_minimal(self):
        c = Character(name="主角")
        assert c.name == "主角"
        assert c.role == "配角"
        assert c.importance == "中"
        assert c.aliases == []
        assert c.created_at  # 自动生成

    def test_full(self):
        c = Character(
            name="萧炎",
            role="主角",
            traits="坚韧不拔",
            appearance="黑袍少年",
            importance="高",
            first_appearance=1,
            arc="废材 → 斗帝",
            aliases=["炎帝", "萧小子"],
        )
        assert c.role == "主角"
        assert c.first_appearance == 1
        assert "炎帝" in c.aliases

    def test_to_from_dict_roundtrip(self):
        c = Character(name="林动", role="主角", traits="草根逆袭")
        d = c.to_dict()
        c2 = Character.from_dict(d)
        assert c2.name == "林动"
        assert c2.traits == "草根逆袭"
        assert c2.role == "主角"

    def test_from_dict_missing_name_raises(self):
        with pytest.raises(ValueError, match="name 不能为空"):
            Character.from_dict({"role": "主角"})

    def test_from_dict_ignores_unknown_keys(self):
        c = Character.from_dict({"name": "X", "unknown_field": "ignore me"})
        assert c.name == "X"
        assert not hasattr(c, "unknown_field")

    def test_from_dict_fills_missing_timestamps(self):
        c = Character.from_dict({"name": "X"})
        assert c.created_at
        assert c.updated_at == c.created_at

    def test_touch_updates_timestamp(self):
        c = Character(name="X")
        old = c.updated_at
        c.touch()
        assert c.updated_at >= old


# ── Event ─────────────────────────────────────────────────────────────────

class TestEvent:
    def test_minimal(self):
        e = Event(event="主角觉醒")
        assert e.event == "主角觉醒"
        assert e.participants == []
        assert e.chapter is None

    def test_full(self):
        e = Event(
            event="主角觉醒",
            significance="命运转折",
            chapter=3,
            participants=["主角", "师父"],
        )
        assert e.chapter == 3
        assert "主角" in e.participants

    def test_to_from_dict_roundtrip(self):
        e = Event(event="测试", significance="关键")
        d = e.to_dict()
        e2 = Event.from_dict(d)
        assert e2.event == e.event
        assert e2.significance == "关键"

    def test_missing_event_raises(self):
        with pytest.raises(ValueError):
            Event.from_dict({})


# ── Foreshadow ────────────────────────────────────────────────────────────

class TestForeshadow:
    def test_minimal(self):
        f = Foreshadow(foreshadow="神秘玉佩")
        assert f.status == ForeshadowStatus.PLANTED.value
        assert f.resolved_at is None

    def test_mark_resolved(self):
        f = Foreshadow(foreshadow="玉佩", planted_chapter=1)
        f.mark_resolved(chapter=10)
        assert f.status == ForeshadowStatus.RESOLVED.value
        assert f.resolved_chapter == 10
        assert f.resolved_at

    def test_legacy_status_string_accepted(self):
        """旧数据 status 字段是字符串, from_dict 要兼容."""
        f = Foreshadow.from_dict({"foreshadow": "X", "status": "已回收"})
        assert f.status == "已回收"

    def test_missing_status_defaults_planted(self):
        f = Foreshadow.from_dict({"foreshadow": "X"})
        assert f.status == ForeshadowStatus.PLANTED.value


# ── WorldRule (NEW!) ──────────────────────────────────────────────────────

class TestWorldRule:
    def test_minimal(self):
        r = WorldRule(name="灵根等级")
        assert r.name == "灵根等级"
        assert r.category == WorldRuleCategory.SYSTEM.value
        assert r.status == WorldRuleStatus.ESTABLISHED.value
        assert r.id.startswith("rule_")
        assert len(r.id) == len("rule_") + 6

    def test_full(self):
        r = WorldRule(
            name="灵根等级",
            category="体系",
            description="修士天赋分天/地/人三等, 每等三品, 共九品.",
            constraints=[
                "灵根品级先天决定, 不可后天改变",
                "高品修士对低品有灵压优势",
            ],
            examples=["天灵根百年一遇", "人灵根最常见"],
            first_appearance=3,
            related_entities=["char_xiao_yan"],
            status="已确立",
            notes="参考《斗破苍穹》设定",
        )
        assert r.category == "体系"
        assert len(r.constraints) == 2
        assert "char_xiao_yan" in r.related_entities

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name 不能为空"):
            WorldRule(name="")

    def test_whitespace_name_raises(self):
        with pytest.raises(ValueError, match="name 不能为空"):
            WorldRule(name="   ")

    def test_invalid_category_defaults_to_other(self):
        r = WorldRule(name="X", category="invalid_category")
        assert r.category == WorldRuleCategory.OTHER.value

    def test_invalid_status_defaults_to_draft(self):
        r = WorldRule(name="X", status="invalid")
        assert r.status == WorldRuleStatus.DRAFT.value

    def test_id_auto_generated(self):
        r = WorldRule(name="X")
        assert r.id.startswith("rule_")
        # 不重复
        r2 = WorldRule(name="Y")
        assert r.id != r2.id

    def test_custom_id_preserved(self):
        r = WorldRule(id="rule_custom", name="X")
        assert r.id == "rule_custom"

    def test_to_from_dict_roundtrip(self):
        r = WorldRule(
            name="修仙境界",
            category="体系",
            description="练气→筑基→金丹→元婴→化神...",
            constraints=["不可越阶"],
        )
        d = r.to_dict()
        r2 = WorldRule.from_dict(d)
        assert r2.name == r.name
        assert r2.constraints == r.constraints
        assert r2.category == "体系"

    def test_from_dict_generates_id_if_missing(self):
        r = WorldRule.from_dict({"name": "X", "category": "体系"})
        assert r.id.startswith("rule_")

    def test_from_dict_legacy_string_only(self):
        """兼容旧 world.json: {key: text} 直接升级."""
        r = WorldRule.from_dict({"description": "公司存在自动化邮件触发机制"})
        assert "公司存在自动化" in r.name or "公司存在自动化" in r.description
        assert r.id.startswith("rule_")

    def test_touch(self):
        r = WorldRule(name="X")
        old = r.updated_at
        r.touch()
        assert r.updated_at >= old


# ── Entity 通用包装 ──────────────────────────────────────────────────────

class TestEntity:
    def test_from_character(self):
        c = Character(name="主角")
        e = Entity.from_dataclass(c, EntityType.CHARACTER)
        assert e.type == "character"
        assert e.id == "主角"
        assert e.data["name"] == "主角"

    def test_from_world_rule(self):
        r = WorldRule(id="rule_abc", name="灵根")
        e = Entity.from_dataclass(r, EntityType.WORLD_RULE)
        assert e.type == "world_rule"
        assert e.id == "rule_abc"
        assert e.data["name"] == "灵根"

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            Entity.from_dataclass("not an entity", EntityType.CHARACTER)

    def test_to_dict_format(self):
        c = Character(name="X")
        e = Entity.from_dataclass(c, EntityType.CHARACTER)
        d = e.to_dict()
        assert d == {"type": "character", "id": "X", "data": c.to_dict()}


# ── 枚举 ─────────────────────────────────────────────────────────────────

class TestEnums:
    def test_entity_types(self):
        assert EntityType.CHARACTER.value == "character"
        assert EntityType.WORLD_RULE.value == "world_rule"

    def test_world_rule_categories(self):
        cats = {c.value for c in WorldRuleCategory}
        assert "体系" in cats
        assert "魔法" in cats
        assert "科技" in cats

    def test_foreshadow_statuses(self):
        statuses = {s.value for s in ForeshadowStatus}
        assert "已埋" in statuses
        assert "已回收" in statuses


# ── helpers ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_gen_id_format(self):
        i = gen_id("rule")
        assert i.startswith("rule_")
        assert len(i) == len("rule_") + 6

    def test_gen_id_unique(self):
        ids = {gen_id("test") for _ in range(100)}
        assert len(ids) == 100