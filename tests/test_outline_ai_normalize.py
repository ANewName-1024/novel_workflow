"""test_outline_ai_normalize.py - Regression: AI 助手 response 必须标准化成 list

Bug 来源 (2026-07-09): 用户点 🤖 生成建议 → "生成失败: ch.foreshadow.map is not a function"
根因: LLM prompt 让 foreshadow 返回 字符串, 但前端模板用 ch.foreshadow.map(...), 抛 TypeError.
   key_events 同理 (LLM 可能返回 list 也可能返回 string)

修复: lib/outline_ai.py 在 _parse_json 后强制 normalize:
  - foreshadow: str → [str], list → 保留 list, missing → []
  - key_events: 同上

测试策略: mock LLM client 返回各种 raw 字符串, verify _parse_json 后的字段类型.
"""
from unittest.mock import MagicMock, patch

import pytest

from lib import outline_ai


class _FakeLLM:
    """Mock LLM client whose .complete() returns a pre-set string."""
    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, **kwargs) -> str:
        return self.raw


def _patch_llm(raw: str):
    """Patch _get_llm to return a fake LLM returning the given raw string."""
    fake = _FakeLLM(raw)
    return patch.object(outline_ai, "_get_llm", return_value=fake)


def test_suggest_normalizes_foreshadow_string():
    """LLM 返回 foreshadow 是字符串, API 必须返回 list."""
    raw = """{
      "chapters": [{
        "title": "X",
        "summary": "Y",
        "pov": "Z",
        "key_events": ["e1", "e2"],
        "foreshadow": "测试中灵光冲天"
      }],
      "reasoning": "..."
    }"""
    with _patch_llm(raw):
        result = outline_ai.suggest_chapters(
            book_title="T", genre="G", existing_count=0,
            outline_text="", next_num=1, count=1, book=None,
        )
    assert len(result["chapters"]) == 1
    ch = result["chapters"][0]
    assert isinstance(ch["foreshadow"], list), \
        f"foreshadow should be normalized to list, got {type(ch['foreshadow']).__name__}"
    assert ch["foreshadow"] == ["测试中灵光冲天"]
    assert isinstance(ch["key_events"], list)
    assert ch["key_events"] == ["e1", "e2"]


def test_suggest_normalizes_key_events_string():
    """LLM 返回 key_events 是字符串 (少见但可能), API 必须返回 list."""
    raw = """{
      "chapters": [{
        "title": "X",
        "summary": "Y",
        "pov": "Z",
        "key_events": "主角去测试灵根",
        "foreshadow": "测试异常"
      }],
      "reasoning": ""
    }"""
    with _patch_llm(raw):
        result = outline_ai.suggest_chapters(
            book_title="T", genre="G", existing_count=0,
            outline_text="", next_num=1, count=1, book=None,
        )
    ch = result["chapters"][0]
    assert ch["key_events"] == ["主角去测试灵根"]
    assert ch["foreshadow"] == ["测试异常"]


def test_suggest_handles_missing_fields():
    raw = """{
      "chapters": [{"title": "X", "summary": "Y", "pov": "Z"}],
      "reasoning": ""
    }"""
    with _patch_llm(raw):
        result = outline_ai.suggest_chapters(
            book_title="T", genre="G", existing_count=0,
            outline_text="", next_num=1, count=1, book=None,
        )
    ch = result["chapters"][0]
    assert ch["key_events"] == []
    assert ch["foreshadow"] == []


def test_suggest_handles_empty_string_fields():
    raw = """{
      "chapters": [{
        "title": "X", "summary": "Y", "pov": "Z",
        "key_events": [], "foreshadow": ""
      }],
      "reasoning": ""
    }"""
    with _patch_llm(raw):
        result = outline_ai.suggest_chapters(
            book_title="T", genre="G", existing_count=0,
            outline_text="", next_num=1, count=1, book=None,
        )
    ch = result["chapters"][0]
    assert ch["key_events"] == []
    assert ch["foreshadow"] == []


def test_expand_normalizes_foreshadow_string():
    """ai-expand endpoint 同理, foreshadow 应是 list."""
    raw = """{
      "key_events": ["e1", "e2", "e3"],
      "foreshadow": "本章埋下玉佩悬念",
      "pov_notes": ""
    }"""
    with _patch_llm(raw):
        result = outline_ai.expand_chapter(
            book_title="T", genre="G", title="X", summary="Y", book=None,
        )
    assert isinstance(result["foreshadow"], list)
    assert result["foreshadow"] == ["本章埋下玉佩悬念"]
    assert result["key_events"] == ["e1", "e2", "e3"]


def test_expand_handles_missing_foreshadow():
    raw = """{"key_events": ["e1"]}"""
    with _patch_llm(raw):
        result = outline_ai.expand_chapter(
            book_title="T", genre="G", title="X", summary="Y", book=None,
        )
    assert result["key_events"] == ["e1"]
    assert result["foreshadow"] == []


def test_suggest_filters_empty_strings():
    """LLM 返回 ['event1', '', '  ', 'event2'] 应该过滤空字符串."""
    raw = """{
      "chapters": [{
        "title": "X", "summary": "Y", "pov": "Z",
        "key_events": ["e1", "", "  ", "e2"],
        "foreshadow": ""
      }],
      "reasoning": ""
    }"""
    with _patch_llm(raw):
        result = outline_ai.suggest_chapters(
            book_title="T", genre="G", existing_count=0,
            outline_text="", next_num=1, count=1, book=None,
        )
    ch = result["chapters"][0]
    assert ch["key_events"] == ["e1", "e2"]
    assert ch["foreshadow"] == []