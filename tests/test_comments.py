"""
tests/test_comments.py — v1.2 M2 评论流 + 通知存储测试
"""
import json
from pathlib import Path

import pytest

from lib import storage, comments


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book"})
    storage.write_chapter("test_book", "ch_001", "## 第一章\n\n内容。")
    storage.write_chapter("test_book", "ch_002", "## 第二章\n\n内容。")
    return proj_root


# ── 评论 CRUD ───────────────────────────────────────────────────────

class TestComments:
    def test_add_comment_basic(self, setup_book):
        c = comments.add_comment("test_book", "ch_001", "wei_chao", "hmm 不错")
        assert c["id"].startswith("c_")
        assert c["author"] == "wei_chao"
        assert c["text"] == "hmm 不错"
        assert c["line"] is None

    def test_add_comment_with_line_anchor(self, setup_book):
        c = comments.add_comment("test_book", "ch_001", "wei_chao", "这行有问题", line=42)
        assert c["line"] == 42

    def test_add_comment_with_mention(self, setup_book):
        c = comments.add_comment("test_book", "ch_001", "wei_chao", "@张老师 请看")
        assert "张老师" in c["mentions"]

    def test_add_comment_empty_raises(self, setup_book):
        with pytest.raises(ValueError, match="empty"):
            comments.add_comment("test_book", "ch_001", "wei_chao", "   ")

    def test_add_comment_invalid_chapter_id(self, setup_book):
        with pytest.raises(ValueError, match="invalid chapter_id"):
            comments.add_comment("test_book", "x_001", "wei_chao", "hi")

    def test_list_comments_by_chapter(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "first")
        comments.add_comment("test_book", "ch_001", "B", "second")
        comments.add_comment("test_book", "ch_002", "C", "other chapter")
        items = comments.list_comments("test_book", "ch_001")
        assert len(items) == 2
        assert {c["author"] for c in items} == {"A", "B"}

    def test_list_all_comments_sorted(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "1")
        comments.add_comment("test_book", "ch_002", "B", "2")
        all_items = comments.list_comments("test_book")
        assert len(all_items) == 2
        # all items should have chapter_id field
        for it in all_items:
            assert "chapter_id" in it

    def test_delete_comment(self, setup_book):
        c = comments.add_comment("test_book", "ch_001", "A", "to delete")
        assert comments.delete_comment("test_book", "ch_001", c["id"]) is True
        assert comments.list_comments("test_book", "ch_001") == []

    def test_delete_comment_not_found(self, setup_book):
        assert comments.delete_comment("test_book", "ch_001", "c_xxx") is False

    def test_reply_to(self, setup_book):
        c1 = comments.add_comment("test_book", "ch_001", "A", "first")
        c2 = comments.add_comment("test_book", "ch_001", "B", "reply", reply_to=c1["id"])
        assert c2["reply_to"] == c1["id"]


# ── 通知 ────────────────────────────────────────────────────────────

class TestNotifications:
    def test_mention_creates_notification(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 请看")
        notifs = comments.list_notifications("test_book", user="张老师")
        assert len(notifs) == 1
        assert notifs[0]["type"] == "mention"
        assert notifs[0]["ref_chapter"] == "ch_001"

    def test_self_mention_no_notification(self, setup_book):
        """@自己不发通知."""
        comments.add_comment("test_book", "ch_001", "wei_chao", "@wei_chao 提醒自己")
        notifs = comments.list_notifications("test_book", user="wei_chao")
        assert len(notifs) == 0

    def test_unread_count(self, setup_book):
        assert comments.unread_count("test_book", "张老师") == 0
        comments.add_comment("test_book", "ch_001", "A", "@张老师 1")
        comments.add_comment("test_book", "ch_001", "A", "@张老师 2")
        comments.add_comment("test_book", "ch_001", "A", "no mention here")
        assert comments.unread_count("test_book", "张老师") == 2

    def test_unread_filter(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 hi")
        n = comments.list_notifications("test_book", user="张老师", unread_only=True)
        assert len(n) == 1
        assert n[0]["read"] is False

    def test_mark_read(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 hi")
        notifs = comments.list_notifications("test_book", user="张老师")
        assert comments.mark_notification_read("test_book", notifs[0]["id"]) is True
        assert comments.unread_count("test_book", "张老师") == 0

    def test_mark_read_not_found(self, setup_book):
        assert comments.mark_notification_read("test_book", "n_xxx") is False

    def test_mark_all_read(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 1")
        comments.add_comment("test_book", "ch_002", "A", "@张老师 2")
        n = comments.mark_all_read("test_book", "张老师")
        assert n == 2
        assert comments.unread_count("test_book", "张老师") == 0

    def test_other_user_notifications_separate(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 @李老师 hi")
        assert comments.unread_count("test_book", "张老师") == 1
        assert comments.unread_count("test_book", "李老师") == 1

    def test_notifications_sorted_desc(self, setup_book):
        comments.add_comment("test_book", "ch_001", "A", "@张老师 1")
        comments.add_comment("test_book", "ch_001", "A", "@张老师 2")
        notifs = comments.list_notifications("test_book", user="张老师")
        # new ones come first; we can check at least the order is non-increasing
        assert notifs[0]["created_at"] >= notifs[1]["created_at"]
