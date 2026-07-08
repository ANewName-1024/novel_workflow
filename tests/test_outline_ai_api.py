"""
test_outline_ai_api.py - v1.3 M2 Outline AI API 集成测试

Mock outline_ai module, 验证 HTTP 路由 + 参数透传.
"""
from __future__ import annotations

import json
import pytest

from lib import storage
from review_ui import app as review_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    storage.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    app = review_app.app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def book_with_outline(client, tmp_path):
    """建一本书 + 一个最小 outline."""
    storage.init_project("test-book", {
        "book_name": "测试书", "genre": "玄幻",
        "target_chapters": 10, "words_per_chapter": 2500,
    })
    o = {
        "meta": {"title": "测试书", "genre": "玄幻"},
        "volumes": [
            {"id": "vol_1", "title": "第一卷", "summary": "开篇", "chapters": ["ch_001"]}
        ],
        "chapters": [
            {"id": "ch_001", "vol": "vol_1", "title": "序章", "summary": "主角登场",
             "pov": "林风", "key_events": ["事件1"], "foreshadow": "伏笔1"},
        ],
    }
    storage.write_json("test-book", "outline.json", o)
    return "test-book"


# ── 1. ai-suggest ──────────────────────────────────────────────

class TestAISuggest:
    def test_basic_call(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        monkeypatch.setattr(oai, "suggest_chapters", lambda **kw: {
            "chapters": [
                {"title": "建议1", "summary": "故事1", "pov": "林风",
                 "key_events": ["e1", "e2"], "foreshadow": "f1"},
            ],
            "reasoning": "mocked reasoning",
        })
        r = client.post("/api/outline/test-book/ai-suggest", json={"count": 1})
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert len(data["chapters"]) == 1
        assert data["chapters"][0]["title"] == "建议1"
        assert data["reasoning"] == "mocked reasoning"

    def test_count_clamps_high(self, client, book_with_outline, monkeypatch):
        """count > 5 应当被 clamp 到 5."""
        from lib import outline_ai as oai
        captured = {}
        def fake(**kw):
            captured["count"] = kw["count"]
            return {"chapters": [], "reasoning": ""}
        monkeypatch.setattr(oai, "suggest_chapters", fake)

        r = client.post("/api/outline/test-book/ai-suggest", json={"count": 99})
        assert r.status_code == 200
        assert captured["count"] == 5  # clamped

    def test_count_clamps_low(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        captured = {}
        def fake(**kw):
            captured["count"] = kw["count"]
            return {"chapters": [], "reasoning": ""}
        monkeypatch.setattr(oai, "suggest_chapters", fake)

        r = client.post("/api/outline/test-book/ai-suggest", json={"count": 0})
        assert captured["count"] == 1

    def test_book_not_found(self, client, monkeypatch):
        from lib import outline_ai as oai
        monkeypatch.setattr(oai, "suggest_chapters", lambda **kw: {"chapters": [], "reasoning": ""})
        r = client.post("/api/outline/nonexistent/ai-suggest", json={})
        assert r.status_code == 404

    def test_writes_llm_provider_to_config(self, client, book_with_outline, monkeypatch):
        """Regression: 前端传的 llm_provider/llm_model 必须写入 config.json.
        (Bug: M6 加了 API 支持,但 webUI 调用时未传 — 依赖用户手动点保存.)
        修复后,AI 助手调用会把当前 UI 选择的 model 写入 cfg,
        避免依赖手动保存。"""
        from lib import outline_ai as oai
        from lib import storage
        monkeypatch.setattr(oai, "suggest_chapters", lambda **kw: {"chapters": [], "reasoning": ""})

        # 调用时带 llm_provider/llm_model
        r = client.post("/api/outline/test-book/ai-suggest", json={
            "count": 1, "llm_provider": "deepseek", "llm_model": "deepseek-chat",
        })
        assert r.status_code == 200
        # config.json 已被更新
        cfg = storage.read_json("test-book", "config.json")
        assert cfg["llm_provider"] == "deepseek"
        assert cfg["llm_model"] == "deepseek-chat"

    def test_empty_provider_does_not_overwrite(self, client, book_with_outline, monkeypatch):
        """不带 llm_provider 时不应覆盖已有 config."""
        from lib import outline_ai as oai
        from lib import storage
        monkeypatch.setattr(oai, "suggest_chapters", lambda **kw: {"chapters": [], "reasoning": ""})

        # 预设 cfg
        cfg = storage.read_json("test-book", "config.json")
        cfg["llm_provider"] = "preserved_provider"
        storage.write_json("test-book", "config.json", cfg)

        r = client.post("/api/outline/test-book/ai-suggest", json={"count": 1})
        assert r.status_code == 200
        cfg = storage.read_json("test-book", "config.json")
        assert cfg["llm_provider"] == "preserved_provider"


# ── 2. ai-expand ───────────────────────────────────────────────

class TestAIExpand:
    def test_basic_call(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        monkeypatch.setattr(oai, "expand_chapter", lambda **kw: {
            "key_events": ["事件1", "事件2", "事件3"],
            "foreshadow": "新伏笔",
            "pov_notes": "第三人称",
        })
        r = client.post("/api/outline/test-book/ai-expand", json={
            "title": "第1章", "summary": "主角登场",
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert len(data["key_events"]) == 3
        assert data["foreshadow"] == "新伏笔"

    def test_missing_title_400(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        monkeypatch.setattr(oai, "expand_chapter", lambda **kw: {})
        r = client.post("/api/outline/test-book/ai-expand", json={"summary": "x"})
        assert r.status_code == 400

    def test_missing_summary_400(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        monkeypatch.setattr(oai, "expand_chapter", lambda **kw: {})
        r = client.post("/api/outline/test-book/ai-expand", json={"title": "x"})
        assert r.status_code == 400

    def test_passes_correct_args(self, client, book_with_outline, monkeypatch):
        from lib import outline_ai as oai
        captured = {}
        def fake(**kw):
            captured.update(kw)
            return {"key_events": [], "foreshadow": ""}
        monkeypatch.setattr(oai, "expand_chapter", fake)

        client.post("/api/outline/test-book/ai-expand", json={
            "title": "第1章 开篇", "summary": "测试摘要",
        })
        assert captured["title"] == "第1章 开篇"
        assert captured["summary"] == "测试摘要"
        assert captured["book_title"] == "测试书"
        assert captured["genre"] == "玄幻"

    def test_writes_llm_provider_to_config(self, client, book_with_outline, monkeypatch):
        """Regression: ai-expand 也必须支持前端的 llm_provider/llm_model."""
        from lib import outline_ai as oai
        from lib import storage
        monkeypatch.setattr(oai, "expand_chapter", lambda **kw: {"key_events": [], "foreshadow": "", "pov_notes": ""})

        r = client.post("/api/outline/test-book/ai-expand", json={
            "title": "t", "summary": "s",
            "llm_provider": "local", "llm_model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        })
        assert r.status_code == 200
        cfg = storage.read_json("test-book", "config.json")
        assert cfg["llm_provider"] == "local"
        assert cfg["llm_model"] == "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"


# ── 3. _outline_to_text helper ─────────────────────────────────

class TestOutlineToText:
    def test_basic(self, client):
        from review_ui import app as review_app
        o = {
            "meta": {"title": "测试", "genre": "玄幻"},
            "volumes": [
                {"id": "vol_1", "title": "第一卷", "summary": "开篇",
                 "chapters": ["ch_001|序章|开场"]}
            ],
        }
        text = review_app._outline_to_text(o)
        assert "书名: 测试" in text
        assert "第一卷" in text
        assert "[ch_001]" in text

    def test_empty(self, client):
        from review_ui import app as review_app
        text = review_app._outline_to_text({})
        # 空 outline → 至少不报错
        assert isinstance(text, str)
