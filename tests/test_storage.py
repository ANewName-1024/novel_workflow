"""
test_storage.py — storage 模块核心 idempotent + project_exists
"""
from lib import storage


def _read_progress(book: str) -> dict:
    return storage.read_json(book, "progress.json") or {"chapters_completed": []}


def test_project_exists_true(tmp_projects_root):
    assert storage.project_exists("test_book") is True


def test_project_exists_false(tmp_projects_root):
    assert storage.project_exists("nope") is False


def test_mark_chapter_completed_idempotent(tmp_projects_root):
    """L51 bug 回归: 多次 mark 同一章节不重复."""
    storage.mark_chapter_completed("test_book", "ch001")
    storage.mark_chapter_completed("test_book", "ch001")
    storage.mark_chapter_completed("test_book", "ch001")
    progress = _read_progress("test_book")
    assert progress["chapters_completed"].count("ch001") == 1
    assert len(progress["chapters_completed"]) == 1


def test_mark_chapter_completed_keeps_order(tmp_projects_root):
    """按调用顺序加入, 不会乱序."""
    for ch in ["ch001", "ch002", "ch003"]:
        storage.mark_chapter_completed("test_book", ch)
    progress = _read_progress("test_book")
    assert progress["chapters_completed"] == ["ch001", "ch002", "ch003"]


def test_mark_chapter_completed_different_books(tmp_projects_root):
    """不同书项目互不影响."""
    storage.init_project("other_book", {"book_name": "other_book", "genre": "都市"})
    storage.mark_chapter_completed("test_book", "ch001")
    storage.mark_chapter_completed("other_book", "ch001")
    a = _read_progress("test_book")["chapters_completed"]
    b = _read_progress("other_book")["chapters_completed"]
    assert a == ["ch001"]
    assert b == ["ch001"]
    storage.mark_chapter_completed("test_book", "ch002")
    assert _read_progress("test_book")["chapters_completed"] == ["ch001", "ch002"]
    assert _read_progress("other_book")["chapters_completed"] == ["ch001"]