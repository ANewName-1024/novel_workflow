"""
Tests for lib/extract.py — 新增 new_world_rules 字段解析 (v1.2 M1.2)
"""
from __future__ import annotations

import json

import pytest

from lib.extract import parse_extraction


# ── parse_extraction 加 new_world_rules ─────────────────────────────────

class TestParseExtractionWorldRules:
    def test_full_world_rules_parsed(self):
        raw = json.dumps({
            "new_characters": [],
            "new_events": [],
            "new_world_rules": [
                {
                    "name": "灵根等级",
                    "category": "体系",
                    "description": "修士天赋分天/地/人三等",
                    "constraints": ["灵根品级先天决定", "高品压低品"],
                    "examples": ["天灵根百年一遇"],
                    "first_appearance": 3,
                }
            ]
        }, ensure_ascii=False)

        data = parse_extraction(raw)
        assert "new_world_rules" in data
        assert len(data["new_world_rules"]) == 1
        rule = data["new_world_rules"][0]
        assert rule["name"] == "灵根等级"
        assert rule["category"] == "体系"
        assert len(rule["constraints"]) == 2

    def test_missing_world_rules_defaults_empty(self):
        raw = '{"new_characters": []}'
        data = parse_extraction(raw)
        assert data["new_world_rules"] == []

    def test_markdown_fence_stripped(self):
        raw = "```json\n" + json.dumps({
            "new_world_rules": [{"name": "魔法体系", "category": "魔法", "description": "六元素相克"}]
        }, ensure_ascii=False) + "\n```"
        data = parse_extraction(raw)
        assert len(data["new_world_rules"]) == 1
        assert data["new_world_rules"][0]["name"] == "魔法体系"

    def test_malformed_json_returns_empty_world_rules(self):
        data = parse_extraction("not json at all")
        assert data["new_world_rules"] == []
        assert data["new_characters"] == []  # 其他字段也空


# ── merge_extraction 处理 new_world_rules ─────────────────────────────

class TestMergeExtractionWorldRules:
    def test_world_rules_merged_into_store(self, tmp_path, monkeypatch):
        from lib.memory import (
            EntityStore, merge_extraction, _mem_path
        )
        import lib.memory as mem_mod

        book = "test_book"
        (tmp_path / book / "memory").mkdir(parents=True)
        monkeypatch.setattr(
            mem_mod, "_mem_path",
            lambda b, l: tmp_path / b / "memory" / f"{l}.json"
        )

        merge_extraction(book, {
            "new_characters": [],
            "updated_characters": [],
            "new_events": [],
            "new_foreshadowing": [],
            "resolved_foreshadowing": [],
            "world_updates": [],
            "new_world_rules": [
                {
                    "name": "灵根等级",
                    "category": "体系",
                    "description": "修士天赋分天/地/人三等",
                    "constraints": ["灵根品级先天决定"],
                    "examples": ["天灵根百年一遇"],
                },
                {
                    "name": "斗技等级",
                    "category": "体系",
                    "description": "玄阶/地阶/天阶",
                    "constraints": ["等级越高越稀有"],
                }
            ]
        })

        store = EntityStore(book)
        rules = store.list_world_rules()
        names = {r.name for r in rules}
        assert "灵根等级" in names
        assert "斗技等级" in names
        assert len(rules) == 2

        # 验证 WorldRule 字段全部保存
        linggen = next(r for r in rules if r.name == "灵根等级")
        assert linggen.category == "体系"
        assert linggen.constraints == ["灵根品级先天决定"]
        assert linggen.examples == ["天灵根百年一遇"]

    def test_duplicate_world_rules_not_added(self, tmp_path, monkeypatch):
        """同 id 不会重复添加 — EntityStore 按 id 判重."""
        from lib.memory import (
            EntityStore, merge_extraction
        )
        from lib.entity import WorldRule
        import lib.memory as mem_mod

        book = "test_book"
        (tmp_path / book / "memory").mkdir(parents=True)
        monkeypatch.setattr(
            mem_mod, "_mem_path",
            lambda b, l: tmp_path / b / "memory" / f"{l}.json"
        )

        # 预先 add 一条 rule
        store = EntityStore(book)
        store.add_world_rule(WorldRule(id="rule_fixed", name="规则A", category="体系"))

        # 尝试 merge 同 id 的 rule (from_dict 保持原 id)
        merge_extraction(book, {
            "new_characters": [], "updated_characters": [],
            "new_events": [], "new_foreshadowing": [],
            "resolved_foreshadowing": [], "world_updates": [],
            "new_world_rules": [{"id": "rule_fixed", "name": "规则A", "category": "体系", "description": "..."}]
        })

        rules = EntityStore(book).list_world_rules()
        assert len(rules) == 1  # 同 id 不重复

    def test_legacy_world_updates_string_format(self, tmp_path, monkeypatch):
        """旧字符串 world_updates 仍然能升级为 WorldRule."""
        from lib.memory import EntityStore, merge_extraction
        import lib.memory as mem_mod

        book = "test_book"
        (tmp_path / book / "memory").mkdir(parents=True)
        monkeypatch.setattr(
            mem_mod, "_mem_path",
            lambda b, l: tmp_path / b / "memory" / f"{l}.json"
        )

        merge_extraction(book, {
            "new_characters": [], "updated_characters": [],
            "new_events": [], "new_foreshadowing": [],
            "resolved_foreshadowing": [],
            "world_updates": ["公司存在自动化邮件触发机制", "存在精确到秒的裁员流程"],
            "new_world_rules": []
        })

        rules = EntityStore(book).list_world_rules()
        # 字符串字段会被 WorldRule.__post_init__ 拒绝 (name 不能空)
        # 因为 name=wu[:30] 拿前30字符, 是非空的
        assert len(rules) == 2

    def test_invalid_world_rule_skipped(self, tmp_path, monkeypatch):
        """缺少 name 的 world_rule 跳过, 不污染存储."""
        from lib.memory import EntityStore, merge_extraction
        import lib.memory as mem_mod

        book = "test_book"
        (tmp_path / book / "memory").mkdir(parents=True)
        monkeypatch.setattr(
            mem_mod, "_mem_path",
            lambda b, l: tmp_path / b / "memory" / f"{l}.json"
        )

        merge_extraction(book, {
            "new_characters": [], "updated_characters": [],
            "new_events": [], "new_foreshadowing": [],
            "resolved_foreshadowing": [], "world_updates": [],
            "new_world_rules": [
                {"description": "没 name"},  # 缺 name → 跳过
                {"name": "有效规则", "category": "体系", "description": "OK"},
            ]
        })

        rules = EntityStore(book).list_world_rules()
        names = {r.name for r in rules}
        assert "有效规则" in names
        assert len(rules) == 1