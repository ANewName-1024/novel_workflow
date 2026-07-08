"""
conftest.py — 公共 fixtures: 临时项目根目录
"""
import sys
import pytest
from pathlib import Path

# 让 tests 能 import 上层 lib/
TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_projects_root(tmp_path, monkeypatch):
    """
    临时 projects 根目录 + 临时 test_book 项目.
    用 storage.init_project 创建完整项目 (config.json + progress.json + memory/).
    monkeypatch storage.PROJECTS_ROOT + ROOT 指到 tmp.
    """
    from lib import storage
    from lib import db as _dbmod
    proj_root = tmp_path / "projects"
    proj_root.mkdir()

    # Patch BEFORE init_project so files land in tmp
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)

    storage.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})

    # 同时同步到 SQLite (review_ui /api/projects + review_service 都从 db 读)
    try:
        _dbmod.init_db(proj_root)
        _cfg = storage.read_json("test_book", "config.json") or {}
        _dbmod.upsert_project(proj_root, "test_book", _cfg.get("book_name", "test_book"), _cfg)
    except Exception:
        pass

    return proj_root