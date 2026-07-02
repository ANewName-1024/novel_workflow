"""
tests/test_notifications_page.py — v1.2 M2 通知页面 + 铃铛 badge 测试
"""
import json
import pytest

from review_ui import app as review_app
from lib import storage, comments


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book", "reviewer": "wei_chao"})
    storage.write_chapter("test_book", "ch_001", "## 第一章\n\n内容。")
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


class TestNotificationsPage:
    def test_page_empty(self, client, auth_off, setup_book):
        r = client.get("/notifications/test_book?user=wei_chao")
        assert r.status_code == 200
        assert b"\xe9\x80\x9a\xe7\x9f\xa5\xe4\xb8\xad\xe5\xbf\x83" in r.data  # 通知中心
        assert b"\xe6\x9a\x82\xe6\x97\xa0\xe9\x80\x9a\xe7\x9f\xa5" in r.data  # 暂无通知

    def test_page_shows_notifs(self, client, auth_off, setup_book):
        # 制造 2 条通知
        comments.add_comment("test_book", "ch_001", "A", "@wei_chao 提示1")
        comments.add_comment("test_book", "ch_001", "A", "@wei_chao 提示2")
        r = client.get("/notifications/test_book?user=wei_chao")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "提示1" in body
        assert "提示2" in body

    def test_page_shows_chapter_link(self, client, auth_off, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@wei_chao 来看")
        r = client.get("/notifications/test_book?user=wei_chao")
        body = r.data.decode("utf-8")
        assert "/book/test_book/ch_001" in body

    def test_book_page_has_bell(self, client, auth_off, setup_book):
        r = client.get("/book/test_book")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # 铃铛 + 通知链接
        assert "/notifications/test_book" in body
        # JS 拉未读数
        assert "api/notifications" in body
