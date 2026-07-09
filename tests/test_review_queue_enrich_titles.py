"""test_review_queue_enrich_titles.py - Regression: 待审章节必须显示标题

Bug 报告 (2026-07-09): "生成待审章节无标题"
- /book/<book> 页面"待审章节"表格只显示 ch_001, ch_002 这种 chapter_id
- 用户看不到章节标题, 必须点进去才知道是哪一章
- 原因: get_review_queue() 只返回 review records, 没有 cross-ref chapters 目录
- 修复: get_review_queue 用 storage.list_chapters(book) 构造 ch_id -> title map, 加到每个 row

本测试验证:
1. review queue items 现在带 chapter_title
2. chapter_title 从 chapter 文件的 H1/H2 解析出来
3. chapter 不存在时 chapter_title 是空字符串 (不报错)
4. 排序保持按 chapter_id
5. 模板 book.html 渲染带 queue-title span
"""
import re
import sys
from pathlib import Path

import pytest


CHAPTERS_DIR = Path(__file__).resolve().parent.parent / "projects" / "test_book" / "chapters"


@pytest.fixture
def fake_book_with_pending_review(tmp_path, monkeypatch):
    """伪造一个 book 有 chapters + reviews (file fallback 路径)."""
    proj = tmp_path / "projects" / "fakebook"
    (proj / "chapters").mkdir(parents=True)
    (proj / "reviews").mkdir(parents=True)
    # 2 chapter 文件 (H1 标题)
    (proj / "chapters" / "ch_001.md").write_text(
        "# 第一章 开端\n\n少年踏入江湖。\n", encoding="utf-8"
    )
    (proj / "chapters" / "ch_002.md").write_text(
        "## 第二章 风云\n\n世事难料。\n", encoding="utf-8"
    )
    (proj / "chapters" / "ch_003.md").write_text(
        "## 第三章 转折\n\n", encoding="utf-8"
    )
    # 2 review (pending_review)
    import json
    (proj / "reviews" / "ch_001.review.json").write_text(json.dumps({
        "chapter_id": "ch_001",
        "status": "pending_review",
        "auto_severity": "minor",
        "auto_issues_count": 1,
        "history": [{"at": "2026-07-09T10:00:00"}],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "reviews" / "ch_002.review.json").write_text(json.dumps({
        "chapter_id": "ch_002",
        "status": "needs_rewrite",
        "auto_severity": "major",
        "auto_issues_count": 5,
        "history": [{"at": "2026-07-09T10:01:00"}],
    }, ensure_ascii=False), encoding="utf-8")
    # ch_003 没有 review, 应该不出现
    # ch_004 有 review 但没 chapter 文件 (chapter_title 应该是空)
    (proj / "reviews" / "ch_004.review.json").write_text(json.dumps({
        "chapter_id": "ch_004",
        "status": "pending_review",
        "auto_severity": "minor",
        "auto_issues_count": 0,
        "history": [],
    }, ensure_ascii=False), encoding="utf-8")
    # 改 storage.ROOT 指向 tmp_path/projects (跟真生产代码一致)
    from lib import storage
    monkeypatch.setattr(storage, "ROOT", tmp_path / "projects")
    return proj


def test_queue_items_have_chapter_title(fake_book_with_pending_review):
    from lib import review_service
    queue = review_service.get_review_queue("fakebook")
    assert len(queue) == 3
    titles = {r["chapter_id"]: r.get("chapter_title", "") for r in queue}
    assert titles["ch_001"] == "第一章 开端"
    assert titles["ch_002"] == "第二章 风云"
    assert titles["ch_004"] == ""  # chapter 文件不存在 → 空


def test_queue_items_also_have_word_count_and_preview(fake_book_with_pending_review):
    from lib import review_service
    queue = review_service.get_review_queue("fakebook")
    for r in queue:
        assert "word_count" in r
        assert "preview" in r
    ch001 = next(r for r in queue if r["chapter_id"] == "ch_001")
    assert ch001["word_count"] > 0
    assert "少年" in ch001["preview"]


def test_queue_sorted_by_chapter_id(fake_book_with_pending_review):
    from lib import review_service
    queue = review_service.get_review_queue("fakebook")
    ids = [r["chapter_id"] for r in queue]
    assert ids == sorted(ids)


def test_queue_empty_when_no_reviews(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "emptybook"
    (proj / "chapters").mkdir(parents=True)
    (proj / "reviews").mkdir(parents=True)
    (proj / "chapters" / "ch_001.md").write_text("# T\n\nbody", encoding="utf-8")
    from lib import storage
    from lib import review_service
    monkeypatch.setattr(storage, "ROOT", tmp_path / "projects")
    assert review_service.get_review_queue("emptybook") == []


def test_template_renders_queue_title_span():
    """book.html 模板必须渲染 r.chapter_title."""
    tmpl = (Path(__file__).resolve().parent.parent / "review_ui" / "templates" / "book.html").read_text(encoding="utf-8")
    # queue-row 必须有 queue-title span
    assert "queue-title" in tmpl
    # 必须读 r.chapter_title
    assert "r.chapter_title" in tmpl
    # 必须用 truncate
    assert "truncate" in tmpl


def test_template_handles_missing_title_with_placeholder():
    """r.chapter_title 为空时显示 '(无标题)' 提示, 不是空白."""
    tmpl = Path(__file__).resolve().parent.parent / "review_ui" / "templates" / "book.html"
    content = tmpl.read_text(encoding="utf-8")
    # 必须有 '无标题' placeholder
    assert "无标题" in content


def test_template_queue_row_preserves_severity_and_meta():
    """添加 title 不能破坏原有的 severity / status / history 显示."""
    tmpl = Path(__file__).resolve().parent.parent / "review_ui" / "templates" / "book.html"
    content = tmpl.read_text(encoding="utf-8")
    # 只看 queue-row 块 (不在 CSS 块), 锁定 {% for r in queue %}...{% endfor %}
    queue_section = re.search(r"\{%\s*for\s+r\s+in\s+queue\s*%\}.*?\{%\s*endfor\s*%\}", content, re.DOTALL)
    assert queue_section, "找不到 queue for-loop"
    section = queue_section.group(0)
    assert "r.auto_severity" in section
    assert "r.auto_issues_count" in section
    assert "r.status" in section
    assert "r.chapter_title" in section