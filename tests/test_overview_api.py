"""
test_overview_api.py - v1.3 M1 全项目进度总览 API 测试

覆盖:
- storage.list_projects() - 单/多/无项目
- GET /api/overview - 返回所有书籍 + pipeline 状态
- GET /overview - 页面渲染
"""
from __future__ import annotations

import pytest
from pathlib import Path

from lib import storage
from review_ui import app as review_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """测试用 Flask client, projects_dir 隔离到 tmp_path."""
    # redirect storage.PROJECTS_ROOT to tmp
    monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    storage.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    app = review_app.app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_book(parent: Path, name: str, *, total: int = 10, current: int = 0,
               title: str = "测试书", genre: str = "都市") -> None:
    """Helper: 创建一个最小可用的项目目录."""
    book_dir = parent / name
    book_dir.mkdir()
    cfg = storage.init_project(name, {
        "book_name": title, "genre": genre,
        "target_chapters": total, "words_per_chapter": 2500,
    })
    if current > 0:
        prog = storage.read_json(name, "progress.json") or {}
        prog["current_chapter"] = current
        storage.write_json(name, "progress.json", prog)


# ── 1. storage.list_projects ──────────────────────────────────────────

class TestListProjects:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
        monkeypatch.setattr(storage, "ROOT", tmp_path)
        storage.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
        assert storage.list_projects() == []

    def test_one_book(self, client, tmp_path):
        _make_book(tmp_path, "book-a")
        assert storage.list_projects() == ["book-a"]

    def test_multi_sorted(self, client, tmp_path):
        _make_book(tmp_path, "zeta")
        _make_book(tmp_path, "alpha")
        _make_book(tmp_path, "mu")
        names = storage.list_projects()
        assert names == ["alpha", "mu", "zeta"]

    def test_skip_dirs_without_config(self, client, tmp_path):
        """没有 config.json 的目录应跳过."""
        _make_book(tmp_path, "valid")
        (tmp_path / "garbage").mkdir()
        (tmp_path / "garbage" / "notes.txt").write_text("x")
        assert storage.list_projects() == ["valid"]


# ── 2. GET /api/overview ───────────────────────────────────────────────

class TestOverviewAPI:
    def test_no_projects(self, client):
        r = client.get("/api/overview")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["count"] == 0
        assert data["projects"] == []

    def test_single_book_no_pipeline(self, client, tmp_path):
        _make_book(tmp_path, "book-x", total=20, current=3, title="X书")
        r = client.get("/api/overview")
        data = r.get_json()
        assert data["count"] == 1
        p = data["projects"][0]
        assert p["name"] == "book-x"
        assert p["title"] == "X书"
        assert p["genre"] == "都市"
        assert p["current_chapter"] == 3
        assert p["total_chapters"] == 20
        assert p["pipeline"] is None  # 从未跑过 pipeline

    def test_multiple_books(self, client, tmp_path):
        _make_book(tmp_path, "a", total=10, current=2, title="A")
        _make_book(tmp_path, "b", total=30, current=15, title="B", genre="玄幻")
        r = client.get("/api/overview")
        data = r.get_json()
        assert data["count"] == 2
        by_name = {p["name"]: p for p in data["projects"]}
        assert by_name["a"]["current_chapter"] == 2
        assert by_name["b"]["current_chapter"] == 15
        assert by_name["b"]["genre"] == "玄幻"


# ── 3. GET /overview (page) ────────────────────────────────────────────

class TestOverviewPage:
    def test_renders(self, client):
        r = client.get("/overview")
        assert r.status_code == 200
        body = r.data.decode("utf-8", errors="replace")
        assert "全项目进度总览" in body
        assert "EventSource" in body  # SSE client wired
        assert "/api/overview" in body