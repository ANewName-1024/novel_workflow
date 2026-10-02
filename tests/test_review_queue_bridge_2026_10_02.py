"""tests/test_review_queue_bridge_2026_10_02.py — CLI 审校必须进人工队列。

2026-10-02 实跑抓到的断层: 仓库里有两套审校, 互不相通。

    lib/review.py    自由文本 Markdown -> reviews/<ch>.md   CLI `review` 走这条
    review_service   结构化 + 状态机 + 待审队列 + audit.log  Web UI 队列走这条

实测: `novel.py review probe_run ch_001` 跑完, reviews/ch_001.md 有 4502 字节
正经审校内容, 但 `novel.py review-queue probe_run` 仍打印
「✓ 评审队列为空」。审校做过了, 结论却没进入任何决策流程 —— 人工在队列里
看不到任何待处理的东西。

本文件把这条桥钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import review_service as revserv  # noqa: E402


_REVIEW_TEXT = "## 一致性\n\n1. 裴照的位置存在明确的时间线冲突。"


class TestCliReviewLandsInQueue:
    def test_pending_review_record_is_created(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")

        rec = revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)

        assert rec["status"] == revserv.REVIEW_STATUS["PENDING_REVIEW"], \
            "自由文本审校落 PENDING_REVIEW —— 不是 AUTO_PASSED"
        assert rec["auto_result"]["source"] == "cli_review"
        assert _REVIEW_TEXT in rec["auto_result"]["text"]

    def test_chapter_shows_up_in_queue(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)

        queue = revserv.get_review_queue("test_book")
        ids = [q.get("chapter_id") for q in queue]
        assert "ch_001" in ids, \
            "审校过的章必须出现在 review-queue 里, 否则人工根本看不到"

    def test_persists_to_disk(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)

        p = storage.project_root("test_book") / "reviews" / "ch_001.review.json"
        assert p.exists(), "必须落盘, 不能只活在内存里"
        reread = revserv.get_review("test_book", "ch_001")
        assert reread["status"] == revserv.REVIEW_STATUS["PENDING_REVIEW"]

    def test_audit_trail_written(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)

        log = revserv.audit_log_path("test_book").read_text(encoding="utf-8")
        assert "cli_review_recorded" in log, "入队必须在 audit.log 留痕"


class TestEmptyReviewIsRejected:
    """空审校不得记成"审过了" —— 0 字节的 reviews/*.md 正是今天踩过的。"""

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
    def test_blank_text_raises(self, tmp_projects_root, bad):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")

        with pytest.raises(ValueError):
            revserv.record_cli_review("test_book", "ch_001", bad)

        assert revserv.get_review("test_book", "ch_001") is None, \
            "空审校不得产生任何评审记录"


class TestHumanDecisionIsNotClobbered:
    """CLI 重复跑 review 不该把人工已经定过的章打回待审。"""

    def test_approved_stays_approved(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        rec = revserv._empty_record("ch_001")
        rec["status"] = revserv.REVIEW_STATUS["APPROVED"]
        rec["reviewer"] = "weichao"
        revserv.save_review("test_book", rec)

        out = revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)

        assert out["status"] == revserv.REVIEW_STATUS["APPROVED"], \
            "人工已批准的章, CLI 再审一次不得回退成 pending_review"
        assert out["reviewer"] == "weichao"

    def test_human_edited_stays_human_edited(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        rec = revserv._empty_record("ch_001")
        rec["status"] = revserv.REVIEW_STATUS["HUMAN_EDITED"]
        revserv.save_review("test_book", rec)

        out = revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)
        assert out["status"] == revserv.REVIEW_STATUS["HUMAN_EDITED"]

    def test_repeat_cli_review_keeps_pending(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.record_cli_review("test_book", "ch_001", "第一次审校")
        out = revserv.record_cli_review("test_book", "ch_001", "第二次审校")

        assert out["status"] == revserv.REVIEW_STATUS["PENDING_REVIEW"]
        text = out["auto_result"]["text"]
        assert "第二次审校" in text, "重复审校应更新为最新正文"
        assert "第一次审校" not in text


class TestBridgeIsNotASilentPass:
    """CLI 审校的产物不能被当成"已通过"。"""

    def test_auto_passed_is_never_produced_by_cli_review(self, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        rec = revserv.record_cli_review("test_book", "ch_001", _REVIEW_TEXT)
        assert rec["status"] != revserv.REVIEW_STATUS["AUTO_PASSED"], \
            "自由文本审校没有 severity 字段, 无从判断通过; 不得自动放行"
