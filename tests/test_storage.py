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


# ── v1.1: list_chapters preview 字段 ───────────────────────────────────────

def test_list_chapters_returns_preview_field(tmp_projects_root):
    """v1.1: list_chapters 字典里必须有 preview 字段."""
    storage.write_chapter("test_book", "ch_001", "## 第1章 测试\n\n这是第一章的第一段。\n")
    chs = storage.list_chapters("test_book")
    assert len(chs) == 1
    assert "preview" in chs[0]
    # preview 取标题后第一段非空行
    assert chs[0]["preview"] == "这是第一章的第一段。"


def test_list_chapters_preview_truncates_long_body(tmp_projects_root):
    """v1.1: 超过 50 字符的 preview 加 … 后缀."""
    # 16 个汉字 * 4 = 64 字符, 明确超 50
    long_line = "这是一段超过五十个字符的测试内容" * 4
    storage.write_chapter("test_book", "ch_001",
                          f"## 第1章 测试\n\n{long_line}\n")
    chs = storage.list_chapters("test_book")
    assert len(chs[0]["preview"]) <= 51   # 50 + …
    assert chs[0]["preview"].endswith("…")


def test_list_chapters_preview_short_body_no_truncate(tmp_projects_root):
    """v1.1: 短正文不截断, 不带 …."""
    storage.write_chapter("test_book", "ch_001",
                          "## 第1章 测试\n\n短预览。\n")
    chs = storage.list_chapters("test_book")
    assert chs[0]["preview"] == "短预览。"
    assert not chs[0]["preview"].endswith("…")


def test_list_chapters_preview_empty_when_no_body(tmp_projects_root):
    """v1.1: 只有标题没正文时, preview 为空字符串."""
    storage.write_chapter("test_book", "ch_001", "## 第1章 测试\n")
    chs = storage.list_chapters("test_book")
    assert chs[0]["preview"] == ""