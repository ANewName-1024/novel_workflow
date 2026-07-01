"""
test_progress_sync.py — L51 bug 回归测试:
review_ui human_edited / approved / false_positive 路径都必须调用
mark_chapter_completed, 才能让 progress.chapters_completed 与 state.json 保持一致.

4 条触发路径 (L51 fix 2026-07-01):
1. review_service.approve
2. review_service.edit
3. review_service.mark_false_positive (假阳性也算 "已经人工 review 过")
4. review_service.apply_edit_to_chapter (edit 后应用到原章节)
"""
from lib import storage, review_service


def _progress(book: str) -> dict:
    return storage.read_json(book, "progress.json") or {}


def _setup_with_chapter(book: str, chapter_id: str = "ch_001"):
    """Create a chapter + reset progress."""
    storage.write_chapter(book, chapter_id, "content body " * 100)
    storage.write_json(book, "progress.json", {
        "chapters_completed": [],
        "phase": "writing",
        "current_chapter": 0,
        "total_chapters": 10,
        "last_updated": "",
    })


def test_approve_marks_completed(tmp_projects_root):
    """L51 关键回归: review_ui approved 必须更新 progress."""
    _setup_with_chapter("test_book", "ch_001")
    review_service.approve("test_book", "ch_001", reviewer="weichao", notes="looks good")
    progress = _progress("test_book")
    assert "ch_001" in progress["chapters_completed"], \
        f"L51 bug regressed! progress={progress}"


def test_edit_marks_completed(tmp_projects_root):
    """L51 关键回归: review_ui human_edited 必须更新 progress."""
    _setup_with_chapter("test_book", "ch_001")
    review_service.edit("test_book", "ch_001", reviewer="weichao",
                        new_text="human-edited body " * 100, notes="polished wording")
    progress = _progress("test_book")
    assert "ch_001" in progress["chapters_completed"], \
        f"L51 bug regressed! progress={progress}"


def test_mark_false_positive_marks_completed(tmp_projects_root):
    """L51 修复: false_positive 也算 review 过, 必须 mark (chapter 是 OK 的)."""
    _setup_with_chapter("test_book", "ch_001")
    review_service.mark_false_positive("test_book", "ch_001",
                                       reviewer="weichao", notes="not a real issue")
    progress = _progress("test_book")
    assert "ch_001" in progress["chapters_completed"], \
        f"L51 bug regressed! progress={progress}"


def test_apply_edit_to_chapter_marks_completed(tmp_projects_root):
    """apply_edit_to_chapter: review 编辑应用回原章节, 也 mark."""
    _setup_with_chapter("test_book", "ch_001")
    # 先编辑一下, 产生 v2.md
    review_service.edit("test_book", "ch_001", reviewer="weichao",
                        new_text="edited body" * 100, notes="x")
    # 清掉 progress (edit 已 mark 一次)
    storage.write_json("test_book", "progress.json", {
        "chapters_completed": [],
        "phase": "writing",
        "current_chapter": 0,
        "total_chapters": 10,
        "last_updated": "",
    })
    # 应用 v2 到原章节
    ok = review_service.apply_edit_to_chapter("test_book", "ch_001")
    assert ok is True
    progress = _progress("test_book")
    assert "ch_001" in progress["chapters_completed"]


def test_all_review_paths_idempotent(tmp_projects_root):
    """多次 approve/edit/false_positive 不重复."""
    _setup_with_chapter("test_book", "ch_001")
    review_service.approve("test_book", "ch_001", reviewer="a", notes="x")
    review_service.edit("test_book", "ch_001", reviewer="b", new_text="x" * 50, notes="y")
    review_service.mark_false_positive("test_book", "ch_001", reviewer="c", notes="z")
    progress = _progress("test_book")
    assert progress["chapters_completed"].count("ch_001") == 1