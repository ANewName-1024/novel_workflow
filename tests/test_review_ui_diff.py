"""test_review_ui_diff.py - review_ui M5 diff 功能 (服务端 difflib + API)."""
import pytest

from review_ui import app as review_app


@pytest.fixture
def auth_disabled(monkeypatch):
    """测试用 auth off, 不用登录就能调 endpoint."""
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
def book_with_v2(tmp_projects_root):
    """创建 test_book, 一个章节 ch_001 原版 + v2."""
    from lib import storage, review_service as revserv
    storage.write_chapter("test_book", "ch_001",
                          "原版第一行\n原版第二行\n原版第三行\n")
    revserv.edit("test_book", "ch_001", "tester",
                 "原版第一行\n人工改了第二行\n原版第三行\n人工加了第四行\n",
                 notes="测试 fixture")
    return "test_book"


class TestDiffApi:
    def test_no_v2_returns_no_diff(self, client, auth_disabled, tmp_projects_root):
        """没有 v2 时 /api/diff 返回 has_diff=False."""
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "只有原版\n")
        r = client.get("/api/diff/test_book/ch_001")
        assert r.status_code == 200
        data = r.get_json()
        assert data["has_diff"] is False
        assert data["diff"] == []
        assert data["stats"] is None

    def test_with_v2_returns_unified_diff(self, client, auth_disabled, book_with_v2):
        r = client.get("/api/diff/test_book/ch_001")
        assert r.status_code == 200
        data = r.get_json()
        assert data["has_diff"] is True
        diff = data["diff"]
        # 应该含 - 原版第二行, + 人工改了第二行, + 人工加了第四行
        joined = "\n".join(diff)
        assert "原版第二行" in joined
        assert "人工改了第二行" in joined
        assert "人工加了第四行" in joined
        # 统计信息存在
        assert data["stats"]["v1_chars"] > 0
        assert data["stats"]["v2_chars"] > 0

    def test_diff_uses_unified_format_with_markers(self, client, auth_disabled, book_with_v2):
        """含 diff header --- / +++ / @@."""
        r = client.get("/api/diff/test_book/ch_001")
        data = r.get_json()
        # 首 2 行通常是 --- 和 +++
        assert any(l.startswith("---") for l in data["diff"])
        assert any(l.startswith("+++") for l in data["diff"])
        # 至少一个 @@ hunk 头
        assert any(l.startswith("@@") for l in data["diff"])


class TestDiffInChapterPage:
    def test_chapter_page_renders_diff_section(self, client, auth_disabled, book_with_v2):
        """章节页有 v2 时, 模板渲染出 diff 区块."""
        r = client.get("/book/test_book/ch_001")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # <pre class="diff-view"> 元素 (HTML 使用)
        assert body.count('<pre class="diff-view"') == 1
        # 含新增行的 class
        assert "diff-add" in body
        # 含删除行 (原版第二行被删除, 但 add 是新行)
        assert "diff-del" in body
        # 章节导航 nav 元素
        assert "chapter-nav" in body

    def test_chapter_page_no_diff_when_no_v2(self, client, auth_disabled, tmp_projects_root):
        """没 v2 时章节页不渲染 diff <pre> 元素 (CSS 类在 <style> 里会出现, 测 HTML body)."""
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "只有原版\n")
        r = client.get("/book/test_book/ch_001")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # diff <pre> 元素不该出现 (CSS class 定义在 <style> 里, 不算)
        assert body.count('<pre class="diff-view"') == 0