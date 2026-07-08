"""test_book_llm_settings.py - book.html AI 模型设置卡片 (v1.3 M6 整合)

8 cases:
  - 页面正确渲染 (AI 模型卡片 + 样式)
  - 卡片含 saveLLMSettings / loadLLMProviders / _getLLMProvider 函数
  - 卡片含 provider select / model input / 保存按钮
  - 当前 cfg 在顶部可见
  - 模型更改后 chapter 修订 / outline AI 助手能拿到新 cfg (API 已经支持,仅 smoke test)
  - E2E: book 页 POST llm_provider/llm_model → cfg 立即更新
"""
import json
import pytest


@pytest.fixture
def fresh_book(client, tmp_projects_root):
    """新建一本书,隔离已有数据."""
    from lib import storage
    storage.init_project("llm_test_book", {
        "book_name": "LLM Test Book", "genre": "测试",
        "target_chapters": 5, "words_per_chapter": 1000,
    })
    return "llm_test_book"


# ── UI 渲染 ──────────────────────────────────────────────

class TestBookLLMSettingsCard:
    def test_card_present(self, client, auth_disabled, fresh_book):
        """book.html 必须含 AI 模型设置卡片."""
        r = client.get(f"/book/{fresh_book}")
        assert r.status_code == 200
        body = r.data.decode("utf-8", errors="replace")
        assert "AI 模型设置" in body
        assert 'class="llm-settings-card"' in body

    def test_card_has_required_elements(self, client, auth_disabled, fresh_book):
        """卡片含 select / model input / 保存按钮 + 状态显示."""
        r = client.get(f"/book/{fresh_book}")
        body = r.data.decode("utf-8", errors="replace")
        # provider select
        assert 'id="llm-provider"' in body
        # model input
        assert 'id="llm-model"' in body
        # 保存按钮
        assert 'id="llm-save-btn"' in body or 'onclick="saveLLMSettings()"' in body
        # 当前状态显示
        assert 'id="llm-current"' in body

    def test_card_has_required_functions(self, client, auth_disabled, fresh_book):
        """卡片必须有 JS 函数."""
        r = client.get(f"/book/{fresh_book}")
        body = r.data.decode("utf-8", errors="replace")
        # 必备函数
        assert "async function loadLLMProviders" in body or "function loadLLMProviders" in body
        assert "async function saveLLMSettings" in body or "function saveLLMSettings" in body
        assert "_getLLMProvider" in body
        # 调用 /api/llm/providers 加载 providers
        assert "/api/llm/providers" in body
        # 保存 POST /api/config/<book>
        assert "/api/config/" in body

    def test_card_shows_current_provider(self, client, auth_disabled, fresh_book):
        """当前 cfg.llm_provider + llm_model 应显示在卡片标题."""
        from lib import storage
        # 设 cfg
        cfg = storage.read_json(fresh_book, "config.json") or {}
        cfg["llm_provider"] = "deepseek"
        cfg["llm_model"] = "deepseek-chat"
        storage.write_json(fresh_book, "config.json", cfg)

        r = client.get(f"/book/{fresh_book}")
        body = r.data.decode("utf-8", errors="replace")
        # 当前显示应包含 deepseek
        assert "deepseek" in body

    def test_card_impact_text(self, client, auth_disabled, fresh_book):
        """卡片应解释影响: 大纲 AI / 章节 AI 修订 / 自动评审."""
        r = client.get(f"/book/{fresh_book}")
        body = r.data.decode("utf-8", errors="replace")
        assert "大纲" in body
        assert "章节" in body


# ── API 集成 (book 页的 fetch 调用 /api/config/<book> 写 cfg) ──────────────

class TestBookLLMSettingsAPI:
    def test_api_can_save_provider_and_model(self, client, auth_disabled, fresh_book):
        """E2E: POST /api/config/<book> with llm_provider/llm_model 写 cfg."""
        from lib import storage
        # baseline: 没有 model
        cfg = storage.read_json(fresh_book, "config.json") or {}
        assert cfg.get("llm_provider") not in ("local", "deepseek")

        r = client.post(
            f"/api/config/{fresh_book}",
            json={"llm_provider": "local", "llm_model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is True

        cfg = storage.read_json(fresh_book, "config.json")
        assert cfg["llm_provider"] == "local"
        assert cfg["llm_model"] == "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"

    def test_api_save_empty_provider_does_not_overwrite(self, client, auth_disabled, fresh_book):
        """不传 llm_provider 时不覆盖 cfg."""
        from lib import storage
        cfg = storage.read_json(fresh_book, "config.json") or {}
        cfg["llm_provider"] = "preserved"
        cfg["llm_model"] = "preserved_model"
        storage.write_json(fresh_book, "config.json", cfg)

        # 不传 llm_provider
        r = client.post(f"/api/config/{fresh_book}", json={"book_name": "LLM Test Book"})
        assert r.status_code == 200
        cfg = storage.read_json(fresh_book, "config.json")
        assert cfg["llm_provider"] == "preserved"
        assert cfg["llm_model"] == "preserved_model"


# ── JS 加载逻辑 regression (Provider 下拉卡 “加载中…” 修复) ─────────────────────

from pathlib import Path


class TestLoadLLMProvidersJS:
    """book.html 的 loadLLMProviders 必须:
       1. API 返回 {providers: {name: cfg}} (object), 用 Object.entries 不是 .map
       2. 在 DOM 渲染后调用 (DOMContentLoaded), 否则 getElementById 拿到 null
    """

    def test_uses_object_entries_not_array_map(self):
        book_html = (Path(__file__).parent.parent / "review_ui" / "templates" / "book.html").read_text(encoding="utf-8")
        assert "Object.entries(data.providers" in book_html, \
            "loadLLMProviders must use Object.entries for object-typed providers"
        assert "(data.providers || []).map(" not in book_html, \
            "loadLLMProviders must NOT use (data.providers || []).map — API returns dict"

    def test_called_on_dom_content_loaded(self):
        book_html = (Path(__file__).parent.parent / "review_ui" / "templates" / "book.html").read_text(encoding="utf-8")
        assert ('addEventListener("DOMContentLoaded", loadLLMProviders)' in book_html
                or "addEventListener('DOMContentLoaded', loadLLMProviders)" in book_html), \
            "loadLLMProviders must be wrapped in DOMContentLoaded — otherwise select is null"