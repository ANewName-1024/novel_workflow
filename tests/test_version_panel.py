"""
tests/test_version_panel.py — v1.2 M3 章节页版本历史 panel 渲染测试
"""
import pytest

from review_ui import app as review_app
from lib import storage, review_service as revserv, version


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book", "reviewer": "wei_chao"})
    storage.write_chapter("test_book", "ch_001", "v1 original")
    version.create_version("test_book", "ch_001", "v2 modified", trigger="edit")
    version.create_version("test_book", "ch_001", "v3 final", trigger="edit")
    revserv.auto_flag("test_book", "ch_001", {"severity": "minor", "character_inconsistency": []}, by="AI")
    # disk 同步到 v3
    chapter_path = storage.chapters_dir("test_book") / "ch_001.md"
    chapter_path.write_text("v3 final", encoding="utf-8")
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


class TestVersionPanel:
    def test_panel_renders_with_3_versions(self, client, auth_off, setup_book):
        r = client.get("/book/test_book/ch_001")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "版本历史" in body
        assert "v001" in body
        assert "v002" in body
        assert "v003" in body

    def test_panel_shows_trigger_badges(self, client, auth_off, setup_book):
        r = client.get("/book/test_book/ch_001")
        body = r.data.decode("utf-8")
        # v001=auto, v002=edit, v003=edit
        assert "v-trigger-auto" in body
        assert "v-trigger-edit" in body

    def test_panel_has_action_buttons(self, client, auth_off, setup_book):
        r = client.get("/book/test_book/ch_001")
        body = r.data.decode("utf-8")
        assert "查看" in body
        assert "对比" in body
        assert "回滚" in body

    def test_panel_empty_state(self, client, auth_off, tmp_path, monkeypatch):
        proj_root = tmp_path / "projects2"
        proj_root.mkdir()
        monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
        monkeypatch.setattr(storage, "ROOT", proj_root)
        storage.init_project("test_book2", {"book_name": "test_book2"})
        # 直接写文件, 绕过 auto-snapshot
        chapter_path = storage.chapters_dir("test_book2") / "ch_001.md"
        chapter_path.write_text("only content", encoding="utf-8")
        revserv.auto_flag("test_book2", "ch_001", {"severity": "none"}, by="AI")
        r = client.get("/book/test_book2/ch_001")
        body = r.data.decode("utf-8")
        assert "版本历史" in body
        assert "暂无版本记录" in body

    def test_panel_includes_diff_buttons_for_non_latest(self, client, auth_off, setup_book):
        """v001 和 v002 应有 '对比' 按钮 (不是最新), v003 是最新无."""
        r = client.get("/book/test_book/ch_001")
        body = r.data.decode("utf-8")
        # 用 onclick="diffWith('v001')" 计数
        assert 'diffWith(\'v001\')' in body
        assert 'diffWith(\'v002\')' in body
