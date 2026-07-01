"""
test_review_service.py — review 服务的核心功能:
approve / reject / edit / mark_false_positive / save_review / get_review
"""
import json
from pathlib import Path
from lib import storage, review_service


def _setup(book: str, chapter_id: str = "ch_001"):
    storage.write_chapter(book, chapter_id, "body " * 100)


def test_save_review_creates_file(tmp_projects_root):
    _setup("test_book", "ch_001")
    record = {
        "chapter_id": "ch_001",
        "status": "pending_review",
        "history": [],
    }
    review_service.save_review("test_book", record)
    p = review_service.review_path("test_book", "ch_001")
    assert p.exists()
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["status"] == "pending_review"


def test_get_review_returns_saved(tmp_projects_root):
    _setup("test_book", "ch_001")
    review_service.save_review("test_book", {
        "chapter_id": "ch_001",
        "status": "needs_rewrite",
        "history": [{"action": "needs_rewrite", "by": "x"}],
    })
    r = review_service.get_review("test_book", "ch_001")
    assert r is not None
    assert r["status"] == "needs_rewrite"


def test_get_review_returns_none_when_missing(tmp_projects_root):
    storage.init_project("test_book", {"book_name": "test_book"})
    assert review_service.get_review("test_book", "nope") is None


def test_approve_sets_status_and_history(tmp_projects_root):
    _setup("test_book", "ch_001")
    rec = review_service.approve("test_book", "ch_001", reviewer="weichao", notes="ok")
    assert rec["status"] == review_service.REVIEW_STATUS["APPROVED"]
    assert rec["reviewer"] == "weichao"
    assert any(h.get("action") == "approved" for h in rec["history"])


def test_reject_sets_needs_rewrite(tmp_projects_root):
    _setup("test_book", "ch_001")
    rec = review_service.reject("test_book", "ch_001", reviewer="weichao",
                                reason="plots inconsistent")
    assert rec["status"] == review_service.REVIEW_STATUS["NEEDS_REWRITE"]
    assert rec["reviewer_notes"] == "plots inconsistent"


def test_edit_writes_v2_file(tmp_projects_root):
    _setup("test_book", "ch_001")
    new_text = "rewritten body " * 100
    rec = review_service.edit("test_book", "ch_001", reviewer="weichao",
                             new_text=new_text, notes="polished")
    assert rec["status"] == review_service.REVIEW_STATUS["HUMAN_EDITED"]
    v2 = review_service.edited_path("test_book", "ch_001")
    assert v2.exists()
    assert v2.read_text(encoding="utf-8") == new_text


def test_mark_false_positive_sets_status(tmp_projects_root):
    _setup("test_book", "ch_001")
    rec = review_service.mark_false_positive("test_book", "ch_001",
                                            reviewer="weichao", notes="ai mistake")
    assert rec["status"] == review_service.REVIEW_STATUS["FALSE_POSITIVE"]


def test_get_review_queue_returns_pending(tmp_projects_root):
    _setup("test_book", "ch_001")
    _setup("test_book", "ch_002")
    review_service.save_review("test_book", {
        "chapter_id": "ch_001", "status": "pending_review", "history": []})
    review_service.save_review("test_book", {
        "chapter_id": "ch_002", "status": "approved", "history": []})
    queue = review_service.get_review_queue("test_book")
    assert len(queue) == 1
    assert queue[0]["chapter_id"] == "ch_001"


def test_audit_log_appended(tmp_projects_root):
    _setup("test_book", "ch_001")
    review_service.approve("test_book", "ch_001", reviewer="weichao", notes="ok")
    log_path = review_service.audit_log_path("test_book")
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "approved" in text
    assert "weichao" in text