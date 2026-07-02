"""
test_outline_ai.py - v1.3 M2 Outline AI 助手 单元测试

测试:
  - _parse_json: 4 种解析路径
  - suggest_chapters / expand_chapter: 走 fake LLM 验证 prompt 组装 + 返回结构
"""
from __future__ import annotations

import json
import pytest

from lib import outline_ai as oai


# ── fake LLM ──────────────────────────────────────────────────────

class _FakeLLM:
    """记录 complete() 调用参数, 返回预设文本."""
    def __init__(self, canned: str):
        self.canned = canned
        self.calls = []

    def complete(self, prompt: str, system: str, temperature: float, max_tokens: int, stage: str):
        self.calls.append({
            "prompt": prompt, "system": system,
            "temperature": temperature, "max_tokens": max_tokens,
            "stage": stage,
        })
        return self.canned


@pytest.fixture
def fake_llm(monkeypatch):
    """Inject fake LLM into outline_ai module."""
    fake = _FakeLLM(canned="")  # default: empty
    monkeypatch.setattr(oai, "_get_llm", lambda **kw: fake)
    return fake


# ── 1. _parse_json ──────────────────────────────────────────────

class TestParseJSON:
    def test_valid_plain(self):
        raw = '{"chapters": [{"title": "t"}], "reasoning": "ok"}'
        out = oai._parse_json(raw, fallback={})
        assert out["chapters"] == [{"title": "t"}]
        assert out["reasoning"] == "ok"

    def test_markdown_code_block(self):
        raw = '```json\n{"key_events": ["a", "b"]}\n```'
        out = oai._parse_json(raw, fallback={})
        assert out["key_events"] == ["a", "b"]

    def test_partial_with_prefix(self):
        """LLM 偶尔会加前言: '好的, 这里是: {...}'"""
        raw = '好的, 这里是: {"chapters": [{"title": "x"}]}'
        out = oai._parse_json(raw, fallback={})
        assert out["chapters"] == [{"title": "x"}]

    def test_invalid_returns_fallback(self):
        raw = "not a json at all"
        fallback = {"chapters": [], "raw": "not a json at all"}
        out = oai._parse_json(raw, fallback=fallback)
        # 不可解析时返回 fallback
        assert out == fallback


# ── 2. suggest_chapters ──────────────────────────────────────────

class TestSuggestChapters:
    def test_prompt_assembly(self, fake_llm):
        fake_llm.canned = '{"chapters": [{"title": "建议1", "summary": "x"}], "reasoning": "ok"}'
        result = oai.suggest_chapters(
            book_title="书名",
            genre="玄幻",
            existing_count=5,
            outline_text="- [ch_001] 序章: 测试",
            next_num=6,
            count=3,
        )
        assert "chapters" in result
        assert result["chapters"][0]["title"] == "建议1"
        # 验证 LLM 收到正确参数
        assert len(fake_llm.calls) == 1
        call = fake_llm.calls[0]
        assert "书名" in call["prompt"]
        assert "玄幻" in call["prompt"]
        assert "第 6 章" in call["prompt"]
        assert "3" in call["prompt"]  # count
        assert call["stage"] == "outline_ai_suggest"
        assert call["max_tokens"] == 2048
        assert "大纲结构设计师" in call["system"]

    def test_fallback_on_bad_json(self, fake_llm):
        fake_llm.canned = "LLM 抽风了, 输出了乱码"
        result = oai.suggest_chapters(
            book_title="t", genre="g", existing_count=0,
            outline_text="", next_num=1, count=3,
        )
        # fallback 结构
        assert result["chapters"] == []
        assert "LLM 抽风了" in result["reasoning"]


# ── 3. expand_chapter ───────────────────────────────────────────

class TestExpandChapter:
    def test_prompt_assembly(self, fake_llm):
        fake_llm.canned = '{"key_events": ["事件1", "事件2"], "foreshadow": "悬疑"}'
        result = oai.expand_chapter(
            book_title="书", genre="g",
            title="第1章 开篇", summary="主角登场",
        )
        assert result["key_events"] == ["事件1", "事件2"]
        assert result["foreshadow"] == "悬疑"
        call = fake_llm.calls[0]
        assert "第1章 开篇" in call["prompt"]
        assert "主角登场" in call["prompt"]
        assert call["stage"] == "outline_ai_expand"
        assert call["max_tokens"] == 1536
