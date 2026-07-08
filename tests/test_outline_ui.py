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

    def test_ai_suggest_call_includes_provider_and_model(self, client, auth_disabled, two_vol_book):
        """Regression: AI 助手调用必须传 llm_provider/llm_model,才能用当前选择.
        (Bug: webUI 修改模型后调 AI 助手不生效,因为没传 provider/model 给 API.)"""
        r = client.get("/outline/test_book")
        body = r.data.decode("utf-8", errors="replace")
        # ai-suggest 调用必须含 llm_provider/llm_model
        # 找到 ai-suggest 周围代码块
        idx = body.find("/api/outline/${BOOK}/ai-suggest")
        assert idx > 0, "ai-suggest fetch call not found"
        block = body[idx:idx+800]
        assert "llm_provider" in block, "ai-suggest fetch body missing llm_provider"
        assert "llm_model" in block, "ai-suggest fetch body missing llm_model"
        # 同样 ai-expand
        idx2 = body.find("/api/outline/${BOOK}/ai-expand")
        assert idx2 > 0, "ai-expand fetch call not found"
        block2 = body[idx2:idx2+800]
        assert "llm_provider" in block2, "ai-expand fetch body missing llm_provider"
        assert "llm_model" in block2, "ai-expand fetch body missing llm_model"

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
