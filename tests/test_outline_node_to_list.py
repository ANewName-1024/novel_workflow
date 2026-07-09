"""test_outline_node_to_list.py - Regression: 章节节点 list 字段 (key_events/foreshadow) 必须 list[str]

Bug 来源 (2026-07-09): 用户点开 AI 助手 → 展开章节 → 加入大纲 → 编辑该章节 → 报错
  'Uncaught TypeError: items.forEach is not a function at outline.html:806'

根因:
- acceptAIExpanded 把 AI 返回的 foreshadow (字符串) 直接保存到 outline 节点
- 用户编辑该章节时, renderListItems 收到 string, items.forEach 抛 TypeError
- `x || []` 防御对 truthy string 无效 (空字符串才 fallback 到 [])

修复:
- lib/outline_editor.py: add_node / update_node 强制把 key_events/foreshadow 转 list
- review_ui/static/js/common.js: 加 NW.toList helper
- review_ui/templates/outline.html: renderListItems 用 NW.toList 防御, 提交时也 normalize
"""
import pytest

from lib import outline_editor as oe


def _empty_outline():
    return {
        "meta": {"title": "T", "target_chapters": 1, "summary": ""},
        "volumes": [{"id": "vol_1", "title": "卷1", "summary": "", "chapters": []}],
        "chapters": [],
    }


class TestAddNodeToList:
    def test_string_foreshadow_becomes_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0,
            title="X", foreshadow="本章埋下玉佩悬念")
        assert isinstance(node["foreshadow"], list)
        assert node["foreshadow"] == ["本章埋下玉佩悬念"]

    def test_string_key_events_becomes_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0,
            title="X", key_events="主角去测试灵根")
        assert node["key_events"] == ["主角去测试灵根"]

    def test_list_foreshadow_preserved(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0,
            title="X", foreshadow=["f1", "f2"])
        assert node["foreshadow"] == ["f1", "f2"]

    def test_missing_foreshadow_default_empty_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0, title="X")
        assert node["foreshadow"] == []

    def test_empty_string_foreshadow_becomes_empty_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0,
            title="X", foreshadow="")
        assert node["foreshadow"] == []

    def test_filters_empty_strings_in_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0,
            title="X", foreshadow=["f1", "", "  ", "f2"])
        assert node["foreshadow"] == ["f1", "f2"]


class TestUpdateNodeToList:
    def test_update_string_foreshadow_to_list(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0, title="X")
        ch_id = node["id"]
        oe.update_node(o, ch_id, foreshadow="更新后的伏笔")
        o2 = next(c for c in o["chapters"] if c["id"] == ch_id)
        assert o2["foreshadow"] == ["更新后的伏笔"]

    def test_update_list_foreshadow_preserved(self):
        o = _empty_outline()
        node = oe.add_node(o, "vol_1", 0, title="X")
        ch_id = node["id"]
        oe.update_node(o, ch_id, foreshadow=["new1", "new2"])
        o2 = next(c for c in o["chapters"] if c["id"] == ch_id)
        assert o2["foreshadow"] == ["new1", "new2"]


class TestNormalizeLegacyString:
    """Legacy data with string foreshadow — save through add_node should normalize."""

    def test_legacy_string_saved_as_list(self):
        """Simulate legacy data: a chapter already exists with string foreshadow.
        When add_node is called for a new chapter with same pattern, ensure list form."""
        o = _empty_outline()
        new_ch = oe.add_node(o, "vol_1", 0,
            title="Legacy", foreshadow="旧数据里的字符串伏笔")
        assert isinstance(new_ch["foreshadow"], list)
        assert new_ch["foreshadow"] == ["旧数据里的字符串伏笔"]