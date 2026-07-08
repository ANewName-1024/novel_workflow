"""
test_outline_ui.py - review_ui/templates/outline.html 渲染层 (M4.3)

8 cases:
  - 页面正确渲染 (返回 200 + Chinese title)
  - 卷/节点 数量正确 (从 outline 拉)
  - 节点拖拽 data 属性正确 (ch_id / vol / pos)
  - 列表为空的 "空卷" 提示
  - 版本接口挂在 /api/outline/<book>/versions
  - Diff 接口接受 v1/v2 query
  - 编辑 endpoint PUT /node/<id>
  - 添加 endpoint POST /node
"""
import pytest

from review_ui import app as review_app
from lib import outline_editor as oe, storage


@pytest.fixture
def auth_disabled(monkeypatch):
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": False, "user": "", "password": ""
    })


@pytest.fixture
def client(tmp_projects_root):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-secret-stable"
    with review_app.app.test_client() as c:
        yield c


@pytest.fixture
def two_vol_book(tmp_projects_root):
    storage.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})
    outline = {
        "meta": {"title": "test_book", "target_chapters": 4, "summary": ""},
        "volumes": [
            {"id": "vol_1", "title": "卷1", "summary": "vs1", "chapters": []},
            {"id": "vol_2", "title": "卷2", "summary": "vs2", "chapters": []},
        ],
        "chapters": [
            {"id": "ch_001", "vol": "vol_1", "title": "C1", "summary": "",
             "pov": "P", "key_events": ["事件A"], "foreshadow": ["伏笔A"]},
            {"id": "ch_002", "vol": "vol_1", "title": "C2", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_003", "vol": "vol_2", "title": "C3", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
        ],
        "generated_at": "2026-07-01T00:00:00",
    }
    oe.save_outline("test_book", outline, auto_snapshot=False)
    return "test_book"


# ── Pages ──────────────────────────────────────────────────────────────────

class TestPages:
    def test_outline_page_renders(self, client, auth_disabled, two_vol_book):
        r = client.get("/outline/test_book")
        assert r.status_code == 200
        assert "大纲编辑器" in r.data.decode("utf-8", errors="replace")
        assert "test_book" in r.data.decode("utf-8", errors="replace")

    def test_outline_html_no_illegal_function_dot_syntax(self):
        """Regression: outline.html 不能含 `async function NW.api(...)` 这种语法错误.
        NW.api/NW.escapeHtml 必须在 common.js 里集中定义.
        (Bug: M2 refactor 引入了重复定义 + 语法错误,导致整个页面 JS 不执行,一直加载中.)"""
        from pathlib import Path
        outline = (Path(__file__).resolve().parent.parent
                   / "review_ui" / "templates" / "outline.html").read_text(encoding="utf-8")
        # 不允许出现 "async function NW." 或 "function NW." 这种错误语法
        assert "async function NW." not in outline, \
            "outline.html 包含 'async function NW.api()' 等违法 JS 语法,请改用 'NW.api = ...'"
        assert "function NW.escapeHtml(" not in outline, \
            "outline.html 包含 'function NW.escapeHtml()' 重复定义,请删除 (common.js 已定义)"
        assert "function NW.api(" not in outline, \
            "outline.html 包含 'function NW.api()' 重复定义,请删除 (common.js 已定义)"

    def test_outline_page_has_load_outline_call(self, client, auth_disabled, two_vol_book):
        """Regression: 页面必须调用 loadOutline() 才会渲染."""
        r = client.get("/outline/test_book")
        assert r.status_code == 200
        assert b"loadOutline()" in r.data

    def test_ai_suggest_call_relies_on_book_cfg(self, client, auth_disabled, two_vol_book):
        """AI 助手调用不再传 provider/model — 使用书属性页的统一设置 (cfg)。
        书属性页 (/book/<book>) 是唯一的 AI 模型设置入口。"""
        r = client.get("/outline/test_book")
        body = r.data.decode("utf-8", errors="replace")
        # ai-suggest 调用不应再传 llm_provider/llm_model
        idx = body.find("/api/outline/${BOOK}/ai-suggest")
        assert idx > 0, "ai-suggest fetch call not found"
        block = body[idx:idx+800]
        assert "llm_provider" not in block, "ai-suggest fetch body should NOT pass llm_provider"
        assert "llm_model" not in block, "ai-suggest fetch body should NOT pass llm_model"
        # 同样 ai-expand
        idx2 = body.find("/api/outline/${BOOK}/ai-expand")
        assert idx2 > 0, "ai-expand fetch call not found"
        block2 = body[idx2:idx2+800]
        assert "llm_provider" not in block2, "ai-expand fetch body should NOT pass llm_provider"
        assert "llm_model" not in block2, "ai-expand fetch body should NOT pass llm_model"

    def test_outline_page_no_longer_has_model_switcher(self, client, auth_disabled, two_vol_book):
        """AI 模型设置已迁移到 book.html,outline 页面顶部不应再有 ms-provider 等控件。
        (v1.3 优化: 统一入口在书属性页。)"""
        r = client.get("/outline/test_book")
        body = r.data.decode("utf-8", errors="replace")
        # 旧 UI 元素应该已移除
        assert 'id="ms-provider"' not in body, "ms-provider select should be removed"
        assert 'id="ms-model"' not in body, "ms-model input should be removed"
        assert "saveModelConfig" not in body, "saveModelConfig function should be removed"
        assert "_getSelectedProvider" not in body, "_getSelectedProvider should be removed"
        # 新 UI: 跳到书属性页的链接 + model-status 显示
        assert 'id="model-status"' in body, "model-status display should be present"
        # 有跳到书属性的链接
        assert '/book/${BOOK}' in body, "should have a link to /book/${BOOK}"

    def test_outline_page_handles_book_with_no_chapters(self, client, auth_disabled, tmp_projects_root):
        storage.init_project("empty_book", {"book_name": "empty_book"})
        r = client.get("/outline/empty_book")

    def test_outline_page_handles_book_with_no_chapters(self, client, auth_disabled, tmp_projects_root):
        storage.init_project("empty_book", {"book_name": "empty_book"})
        r = client.get("/outline/empty_book")
        assert r.status_code == 200


# ── API end-to-end (smoke) ──────────────────────────────────────────────────

class TestAPI:
    def test_list_versions(self, client, auth_disabled, two_vol_book):
        """After 2 saves, versions list should have entries."""
        # Trigger 2 saves
        client.put("/api/outline/test_book/node/ch_001", json={"title": "X"})
        client.put("/api/outline/test_book/node/ch_001", json={"title": "Y"})
        r = client.get("/api/outline/test_book/versions")
        assert r.status_code == 200
        versions = r.get_json()
        assert isinstance(versions, list)
        assert len(versions) >= 2

    def test_list_versions_empty(self, client, auth_disabled, two_vol_book):
        """No saves after seeding → no versions."""
        r = client.get("/api/outline/test_book/versions")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_diff_endpoint_runs_with_real_versions(self, client, auth_disabled, two_vol_book):
        """Smoke: 触发 2 次 save, 然后 diff 跑通."""
        client.put("/api/outline/test_book/node/ch_001", json={"title": "X"})
        client.put("/api/outline/test_book/node/ch_001", json={"title": "Y"})
        versions = client.get("/api/outline/test_book/versions").get_json()
        v1 = versions[-1]["version_id"]  # oldest
        v2 = versions[0]["version_id"]   # newest (newest = 'Y')
        r = client.get(f"/api/outline/test_book/diff?v1={v1}&v2={v2}")
        assert r.status_code == 200
        diff = r.get_json()
        # Should detect ch_001 title edit
        edited_ids = [e["ch_id"] for e in diff["chapters_edited"]]
        assert "ch_001" in edited_ids

    def test_delete_volume_then_get_outline(self, client, auth_disabled, two_vol_book):
        """Delete a vol → chapters reassigned. Outline still renderable."""
        r = client.delete("/api/outline/test_book/volumes/vol_1")
        assert r.status_code == 200
        # ch_001/ch_002 should now belong to vol_2
        r2 = client.get("/api/outline/test_book")
        assert r2.status_code == 200
        data = r2.get_json()
        for ch in data["chapters"]:
            assert ch["vol"] == "vol_2"

    def test_reorder_idempotent(self, client, auth_disabled, two_vol_book):
        """Reorder 'same position' should not corrupt."""
        # ch_001 is already at vol_1 pos 0
        r = client.post("/api/outline/test_book/reorder", json={
            "moves": [{"ch_id": "ch_001", "new_vol": "vol_1", "new_position": 0}],
        })
        assert r.status_code == 200

    def test_ai_modal_model_display_is_dynamic(self):
        """Regression: AI 助手 modal 标题里的模型名不能硬编码。

        之前写死 '(本地 Qwen3.6-35B)', 用户改了 book.html 里的 LLM 后这里仍显示旧名。
        修复: 改为 id='modal-ai-model' 占位符, 打开 modal 时由 loadModelStatus() 填充。
        """
        from pathlib import Path
        outline = (Path(__file__).resolve().parent.parent
                   / "review_ui" / "templates" / "outline.html").read_text(encoding="utf-8")
        # 不能硬编码具体模型名 (例如 'Qwen3.6' / 'Qwythos' / 'GPT-4')
        assert "Qwen3.6" not in outline, "AI modal model display is hardcoded — must be dynamic"
        assert "Qwythos" not in outline, "AI modal model display is hardcoded — must be dynamic"
        # 必须有动态占位符 + JS 填充逻辑
        assert 'id="modal-ai-model"' in outline, "AI modal header needs id='modal-ai-model' placeholder"
        # loadModelStatus() 必须更新这个 id
        assert "getElementById(\"modal-ai-model\")" in outline \
            or "getElementById('modal-ai-model')" in outline, \
            "loadModelStatus() must update #modal-ai-model"
        # showAIModal() 必须刷新
        assert "function showAIModal" in outline
        # 调用 showAIModal 时必须 trigger loadModelStatus (避免缓存旧名)
        # 不强制测试具体语句, 但要确认 JS 代码块中存在调用
        show_block_idx = outline.find("function showAIModal")
        assert show_block_idx > 0
        show_block = outline[show_block_idx:show_block_idx + 400]
        assert "loadModelStatus" in show_block, "showAIModal must call loadModelStatus to refresh model name"
