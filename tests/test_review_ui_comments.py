"""
tests/test_review_ui_comments.py — v1.2 M2 评论/通知/行级 diff API 测试
"""
import json
import pytest

from review_ui import app as review_app


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    from lib import storage
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book"})
    storage.write_chapter("test_book", "ch_001",
                          "## 第一章\n\n第一行\n第二行\n第三行\n第四行\n第五行\n")
    return proj_root


@pytest.fixture
def client(setup_book):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test"
    with review_app.app.test_client() as c:
        yield c


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setattr(review_app, "_get_auth",
                        lambda: {"enabled": False, "user": "", "password": ""})


# ── 评论 API ─────────────────────────────────────────────────────────

class TestCommentsAPI:
    def test_add_comment(self, client, auth_off, setup_book):
        r = client.post("/api/comments/test_book/ch_001",
                        data=json.dumps({"author": "wei_chao", "text": "hmm 不错"}),
                        content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["comment"]["author"] == "wei_chao"
        assert data["comment"]["id"].startswith("c_")

    def test_add_comment_empty_text_400(self, client, auth_off, setup_book):
        r = client.post("/api/comments/test_book/ch_001",
                        data=json.dumps({"author": "A", "text": "   "}),
                        content_type="application/json")
        assert r.status_code == 400

    def test_add_comment_with_mention_creates_notification(
        self, client, auth_off, setup_book,
    ):
        client.post("/api/comments/test_book/ch_001",
                   data=json.dumps({"author": "A", "text": "@张老师 请看"}),
                   content_type="application/json")
        r = client.get("/api/notifications/test_book?user=张老师&unread=1")
        data = r.get_json()
        assert data["unread_count"] == 1
        assert data["items"][0]["type"] == "mention"

    def test_list_comments_by_chapter(self, client, auth_off, setup_book):
        for i in range(3):
            client.post("/api/comments/test_book/ch_001",
                       data=json.dumps({"author": f"u{i}", "text": f"msg {i}"}),
                       content_type="application/json")
        r = client.get("/api/comments/test_book?chapter=ch_001")
        assert len(r.get_json()) == 3

    def test_list_all_comments(self, client, auth_off, setup_book):
        client.post("/api/comments/test_book/ch_001",
                   data=json.dumps({"author": "A", "text": "1"}),
                   content_type="application/json")
        client.post("/api/comments/test_book/ch_002",
                   data=json.dumps({"author": "A", "text": "2"}),
                   content_type="application/json")
        r = client.get("/api/comments/test_book")
        items = r.get_json()
        assert len(items) == 2
        for it in items:
            assert "chapter_id" in it

    def test_delete_comment(self, client, auth_off, setup_book):
        r = client.post("/api/comments/test_book/ch_001",
                       data=json.dumps({"author": "A", "text": "to del"}),
                       content_type="application/json")
        cid = r.get_json()["comment"]["id"]
        r = client.delete(f"/api/comments/test_book/ch_001/{cid}")
        assert r.status_code == 204
        r = client.get("/api/comments/test_book?chapter=ch_001")
        assert r.get_json() == []

    def test_delete_not_found_404(self, client, auth_off, setup_book):
        r = client.delete("/api/comments/test_book/ch_001/c_xxx")
        assert r.status_code == 404


# ── 通知 API ─────────────────────────────────────────────────────────

class TestNotificationsAPI:
    def test_list_empty(self, client, auth_off, setup_book):
        r = client.get("/api/notifications/test_book?user=wei_chao")
        data = r.get_json()
        assert data["items"] == []
        assert data["unread_count"] == 0

    def test_list_with_mention(self, client, auth_off, setup_book):
        client.post("/api/comments/test_book/ch_001",
                   data=json.dumps({"author": "A", "text": "@wei_chao hi"}),
                   content_type="application/json")
        r = client.get("/api/notifications/test_book?user=wei_chao")
        assert r.get_json()["unread_count"] == 1

    def test_mark_one_read(self, client, auth_off, setup_book):
        client.post("/api/comments/test_book/ch_001",
                   data=json.dumps({"author": "A", "text": "@wei_chao hi"}),
                   content_type="application/json")
        r = client.get("/api/notifications/test_book?user=wei_chao")
        nid = r.get_json()["items"][0]["id"]
        r = client.post(f"/api/notifications/test_book/{nid}/read")
        assert r.status_code == 200
        # unread count is now 0
        r = client.get("/api/notifications/test_book?user=wei_chao&unread=1")
        assert r.get_json()["unread_count"] == 0

    def test_mark_not_found_404(self, client, auth_off, setup_book):
        r = client.post("/api/notifications/test_book/n_xxx/read")
        assert r.status_code == 404

    def test_mark_all_read(self, client, auth_off, setup_book):
        for i in range(3):
            client.post("/api/comments/test_book/ch_001",
                       data=json.dumps({"author": "A", "text": f"@wei_chao msg {i}"}),
                       content_type="application/json")
        r = client.post("/api/notifications/test_book/read-all?user=wei_chao")
        data = r.get_json()
        assert data["marked"] == 3
        # unread should be 0
        r = client.get("/api/notifications/test_book?user=wei_chao&unread=1")
        assert r.get_json()["unread_count"] == 0

    def test_read_all_missing_user_400(self, client, auth_off, setup_book):
        r = client.post("/api/notifications/test_book/read-all")
        assert r.status_code == 400


# ── 行级上下文 ────────────────────────────────────────────────────────

class TestChapterContext:
    def test_context_default_window(self, client, auth_off, setup_book):
        """默认 window=3, 3 行前后."""
        r = client.get("/api/chapter/test_book/ch_001/context?line=3")
        data = r.get_json()
        assert data["line"] == 3
        assert data["target"]["line_no"] == 3
        # 第 3 行: "第一行"
        assert "第一行" in data["target"]["text"]
        # before 应该有 2 行
        assert len(data["before"]) == 2
        # after 应该有 3 行
        assert len(data["after"]) == 3

    def test_context_custom_window(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/context?line=3&window=1")
        data = r.get_json()
        assert len(data["before"]) == 1
        assert len(data["after"]) == 1

    def test_context_line_out_of_range_400(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/context?line=999")
        assert r.status_code == 400

    def test_context_line_zero_400(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/context?line=0")
        assert r.status_code == 400

    def test_context_chapter_not_found_404(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_999/context?line=1")
        assert r.status_code == 404
