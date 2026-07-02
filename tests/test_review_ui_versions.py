"""
tests/test_review_ui_versions.py — v1.2 M3 章节版本 API 测试
"""
import json
import pytest

from review_ui import app as review_app
from lib import storage, version


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book"})
    # 写一个初始章节 (auto-snapshot v001)
    storage.write_chapter("test_book", "ch_001", "v1 original")
    # 手动创建 v002, v003
    version.create_version("test_book", "ch_001", "v2 modified", trigger="edit")
    version.create_version("test_book", "ch_001", "v3 final", trigger="edit")
    # 最后 disk 写 "v3 final" (与 v003 同, 跳过 auto-snapshot)
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


class TestListVersions:
    def test_list_3_versions(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/versions")
        assert r.status_code == 200
        items = r.get_json()
        assert len(items) == 3
        ids = [it["version_id"] for it in items]
        assert ids == ["v001", "v002", "v003"]

    def test_list_excludes_content(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/versions")
        for it in r.get_json():
            assert "content" not in it

    def test_list_includes_metadata(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/versions")
        items = r.get_json()
        # 至少有 trigger + char_count + char_diff
        for it in items:
            assert "trigger" in it
            assert "char_count" in it
            assert "char_diff" in it


class TestGetVersion:
    def test_get_version_with_content(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/versions/v002")
        assert r.status_code == 200
        rec = r.get_json()
        assert rec["content"] == "v2 modified"
        assert rec["version_id"] == "v002"

    def test_get_version_not_found_404(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/versions/v999")
        assert r.status_code == 404


class TestRevert:
    def test_revert_to_v001(self, client, auth_off, setup_book):
        r = client.post("/api/chapter/test_book/ch_001/revert/v001",
                       data=json.dumps({"by": "tester"}),
                       content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        # 章节内容恢复
        assert storage.read_chapter("test_book", "ch_001") == "v1 original"
        # 返回的是 revert 步骤创建的新记录 (v004)
        assert data["version"]["trigger"] == "revert"

    def test_revert_invalid_version_400(self, client, auth_off, setup_book):
        r = client.post("/api/chapter/test_book/ch_001/revert/v999",
                       data=json.dumps({}),
                       content_type="application/json")
        assert r.status_code == 400

    def test_revert_creates_new_versions(self, client, auth_off, setup_book):
        r = client.post("/api/chapter/test_book/ch_001/revert/v001",
                       data=json.dumps({}),
                       content_type="application/json")
        # revert 流程:
        #   v001: "v1 original" (初始)
        #   v002: "v2 modified"
        #   v003: "v3 final" (disk 状态)
        #   v004: 跳过 pre_revert (与 v003 同内容)
        #   v004 实际: revert "v1 original" (与 v003 不同, 创建)
        items = version.list_versions("test_book", "ch_001")
        assert len(items) == 4
        assert items[-1]["trigger"] == "revert"
        assert items[-1]["meta"]["reverted_to"] == "v001"


class TestDiffVersions:
    def test_diff_two_versions(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/diff-versions?v1=v001&v2=v003")
        assert r.status_code == 200
        data = r.get_json()
        assert data["v1"] == "v001"
        assert data["v2"] == "v003"
        assert data["has_diff"] is True

    def test_diff_missing_params_400(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/diff-versions?v1=v001")
        assert r.status_code == 400

    def test_diff_invalid_version_404(self, client, auth_off, setup_book):
        r = client.get("/api/chapter/test_book/ch_001/diff-versions?v1=v001&v2=v999")
        assert r.status_code == 404
