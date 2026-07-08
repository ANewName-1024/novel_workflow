"""
test_projects_crud_api.py - review_ui/app.py 项目 CRUD API (v1.3 M7)

5 routes:
  - POST   /api/projects            create new project
  - GET    /api/projects/<book>     get single config
  - PUT    /api/projects/<book>     update config (no rename)
  - DELETE /api/projects/<book>     remove project directory

Tests cover happy path + failure paths:
  - slug validation (空 / 非法字符 / 过长)
  - main_plot 必填
  - 重复创建 409
  - 不存在 404 (GET / PUT / DELETE)
  - 编辑只改白名单字段
  - 删除后无法 GET
"""
import json
import pytest

from review_ui import app as review_app
from lib import storage


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def auth_disabled(monkeypatch):
    review_app._get_auth = lambda: {"enabled": False, "user": "", "password": ""}


@pytest.fixture
def client(auth_disabled):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-projects-crud"
    with review_app.app.test_client() as c:
        yield c


def _valid_payload(name="new_book", **overrides):
    p = {
        "name": name,
        "book_name": "新建书籍",
        "genre": "玄幻",
        "tone": "轻松日常",
        "protagonist": "张三",
        "antagonist": "李四",
        "main_plot": "一个普通的成长故事",
        "style": "简洁流畅",
        "target_chapters": 30,
        "words_per_chapter": 2000,
        "language": "zh",
        "llm_model": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "llm_provider": "deepseek",
    }
    p.update(overrides)
    return p


# ── POST /api/projects ─────────────────────────────────────────────────────

class TestCreateProject:
    def test_create_success(self, client, tmp_projects_root):
        r = client.post("/api/projects", json=_valid_payload("fresh_book"))
        assert r.status_code == 201, r.get_json()
        data = r.get_json()
        assert data["ok"] is True
        assert data["book"] == "fresh_book"
        # 验证文件落地
        assert storage.project_exists("fresh_book")
        cfg = storage.read_json("fresh_book", "config.json")
        assert cfg["book_name"] == "新建书籍"
        assert cfg["genre"] == "玄幻"
        assert cfg["target_chapters"] == 30
        assert cfg["main_plot"] == "一个普通的成长故事"
        # progress 也被初始化
        prog = storage.read_json("fresh_book", "progress.json")
        assert prog is not None
        assert prog["total_chapters"] == 30

    def test_create_duplicate_returns_409(self, client, tmp_projects_root):
        # test_book fixture 已存在
        r = client.post("/api/projects", json=_valid_payload("test_book"))
        assert r.status_code == 409, r.get_json()
        assert "已存在" in r.get_json()["error"]

    def test_create_missing_main_plot_returns_400(self, client, tmp_projects_root):
        p = _valid_payload("no_plot")
        p["main_plot"] = ""
        r = client.post("/api/projects", json=p)
        assert r.status_code == 400
        assert "main_plot" in r.get_json()["error"]

    def test_create_invalid_slug_returns_400(self, client, tmp_projects_root):
        # 含中文/空格/特殊字符
        for bad in ["测试书籍", "has space", "../escape", "x" * 100, ""]:
            r = client.post("/api/projects", json=_valid_payload(bad))
            assert r.status_code == 400, f"bad={bad!r}, resp={r.get_json()}"

    def test_create_only_validates_slug_format(self, client, tmp_projects_root):
        # 合法 slug
        for good in ["a", "abc-123", "test_book_2", "X1"]:
            r = client.post("/api/projects", json=_valid_payload(good))
            assert r.status_code == 201, f"good={good!r}, resp={r.get_json()}"


# ── GET /api/projects/<book> ────────────────────────────────────────────────

class TestGetProject:
    def test_get_success(self, client, tmp_projects_root):
        r = client.get("/api/projects/test_book")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["book"] == "test_book"
        assert "config" in data
        assert data["config"]["genre"] == "玄幻"

    def test_get_not_found_returns_404(self, client, tmp_projects_root):
        r = client.get("/api/projects/ghost_book")
        assert r.status_code == 404
        assert "不存在" in r.get_json()["error"]


# ── PUT /api/projects/<book> ────────────────────────────────────────────────

class TestUpdateProject:
    def test_update_basic_fields(self, client, tmp_projects_root):
        r = client.put("/api/projects/test_book", json={
            "genre": "科幻",
            "protagonist": "王五",
            "target_chapters": 50,
        })
        assert r.status_code == 200, r.get_json()
        cfg = storage.read_json("test_book", "config.json")
        assert cfg["genre"] == "科幻"
        assert cfg["protagonist"] == "王五"
        assert cfg["target_chapters"] == 50

    def test_update_does_not_rename(self, client, tmp_projects_root):
        # 即便传 name,也不应该重命名(目录名 = slug)
        r = client.put("/api/projects/test_book", json={
            "name": "renamed_book",
            "book_name": "新书名",
        })
        assert r.status_code == 200
        # 目录名不变
        assert storage.project_exists("test_book")
        assert not storage.project_exists("renamed_book")
        # 但 book_name(显示名)可以改
        cfg = storage.read_json("test_book", "config.json")
        assert cfg["book_name"] == "新书名"

    def test_update_not_found_returns_404(self, client, tmp_projects_root):
        r = client.put("/api/projects/ghost_book", json={"genre": "x"})
        assert r.status_code == 404

    def test_update_whitelist_only(self, client, tmp_projects_root):
        # created_at 不在白名单 → 不应该被改
        before = storage.read_json("test_book", "config.json") or {}
        original_created = before.get("created_at", "NOT_SET")
        r = client.put("/api/projects/test_book", json={
            "created_at": "FAKE_INJECTION",
            "evil_field": "should_not_be_saved",
        })
        assert r.status_code == 200
        cfg = storage.read_json("test_book", "config.json")
        assert cfg.get("created_at") == original_created
        assert "evil_field" not in cfg

    def test_update_coerces_int_fields(self, client, tmp_projects_root):
        r = client.put("/api/projects/test_book", json={
            "target_chapters": "60",  # 字符串,应该被转 int
        })
        assert r.status_code == 200
        cfg = storage.read_json("test_book", "config.json")
        assert cfg["target_chapters"] == 60
        assert isinstance(cfg["target_chapters"], int)


# ── DELETE /api/projects/<book> ─────────────────────────────────────────────

class TestDeleteProject:
    def test_delete_success(self, client, tmp_projects_root):
        # 创建临时项目
        client.post("/api/projects", json=_valid_payload("to_delete"))
        assert storage.project_exists("to_delete")

        r = client.delete("/api/projects/to_delete")
        assert r.status_code == 200, r.get_json()
        assert not storage.project_exists("to_delete")

    def test_delete_not_found_returns_404(self, client, tmp_projects_root):
        r = client.delete("/api/projects/ghost_book")
        assert r.status_code == 404

    def test_delete_then_get_returns_404(self, client, tmp_projects_root):
        client.post("/api/projects", json=_valid_payload("goner"))
        client.delete("/api/projects/goner")
        r = client.get("/api/projects/goner")
        assert r.status_code == 404


# ── list /api/projects (已有,简单回归) ─────────────────────────────────────

class TestListProjects:
    def test_list_includes_new_project(self, client, tmp_projects_root):
        client.post("/api/projects", json=_valid_payload("list_me"))
        r = client.get("/api/projects")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert "list_me" in data["projects"]