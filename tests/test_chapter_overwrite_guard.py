"""tests/test_chapter_overwrite_guard.py — 锁住「不覆盖已有章节」这道保护。

产品问题来源(实测, 非推断):
  projects/测试书籍/ 磁盘有 ch_008(2972 字), 但 progress.chapters_completed
  里没有它, current_chapter=7。novel.py:312 算出 start = 7+1 = 8, 于是
  `novel.py continue` 会从 ch_008 开始写 —— 把已完成的正文整个重写掉。
  该书无 versions/ 快照, 两个 tar 备份(均 2026-07-01)也都不含 ch_008。
  覆盖 = 2972 字不可恢复。

本文件既锁行为, 也是反向验证: 保护若失效, 这些测试必须红。
"""
from __future__ import annotations

import pytest

from lib import storage


@pytest.fixture
def book(tmp_projects_root):
    storage.init_project("guard_book", {"book_name": "guard_book", "genre": "测试"})
    return "guard_book"


class TestRefusesSilentOverwrite:
    def test_writing_over_a_finished_chapter_raises(self, book):
        storage.write_chapter(book, "ch_001", "第一稿正文, 一千字的那种。")
        with pytest.raises(FileExistsError):
            storage.write_chapter(book, "ch_001", "第二稿, 完全不同。")

    def test_original_content_survives_the_attempt(self, book):
        original = "第一稿正文, 绝不能被覆盖掉。"
        storage.write_chapter(book, "ch_001", original)
        with pytest.raises(FileExistsError):
            storage.write_chapter(book, "ch_001", "不该写进来")
        assert storage.read_chapter(book, "ch_001") == original, \
            "拒绝之后原内容必须一字未动"

    def test_error_message_names_the_chapter_and_offers_both_ways_out(self, book):
        storage.write_chapter(book, "ch_001", "已有内容")
        with pytest.raises(FileExistsError) as exc:
            storage.write_chapter(book, "ch_001", "新内容")
        msg = str(exc.value)
        assert "ch_001" in msg, "错误信息要指出是哪一章"
        assert "allow_overwrite" in msg, "要告诉调用方怎么合法覆盖"
        assert "chapters_completed" in msg, "要提示断点错位时的另一个修法"


class TestLegitimatePathsStillWork:
    def test_new_chapter_writes_fine(self, book):
        storage.write_chapter(book, "ch_001", "全新章节")
        assert storage.read_chapter(book, "ch_001") == "全新章节"

    def test_explicit_overwrite_is_allowed(self, book):
        """自检重写走的就是这条路 —— 它必须能用。"""
        storage.write_chapter(book, "ch_001", "原稿")
        storage.write_chapter(book, "ch_001", "改写稿", allow_overwrite=True)
        assert storage.read_chapter(book, "ch_001") == "改写稿"

    def test_rewriting_identical_content_is_not_blocked(self, book):
        """幂等重写(内容相同)不该被拦 —— 那不是覆盖。"""
        text = "同样的内容"
        storage.write_chapter(book, "ch_001", text)
        storage.write_chapter(book, "ch_001", text)
        assert storage.read_chapter(book, "ch_001") == text

    def test_empty_existing_file_can_be_filled(self, book):
        """空文件不算「已有内容」—— 断点写了一半应该能续上。"""
        p = storage.chapters_dir(book) / "ch_001.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("   \n", encoding="utf-8")
        storage.write_chapter(book, "ch_001", "续写的内容")
        assert storage.read_chapter(book, "ch_001") == "续写的内容"


class TestResumeScenarioReproduced:
    """把今天实测到的那个断点错位场景原样复现。"""

    def test_resume_point_landing_on_written_chapter_is_caught(self, book):
        # 磁盘有 ch_008, 记录里没有它 —— 与实测数据同构
        original = "第8章 旧物整理" + "正文。" * 100      # 308 字
        storage.write_chapter(book, "ch_008", original)
        with pytest.raises(FileExistsError):
            # continue 会算出 start=8, 于是往 ch_008 写
            storage.write_chapter(book, "ch_008", "重新生成的第8章")
        after = storage.read_chapter(book, "ch_008")
        assert after == original, "原正文必须一字不差"
        assert len(after) == len(original)
