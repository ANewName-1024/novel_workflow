"""tests/test_dual_write_field_divergence.py — 双写后读回, 字段有没有丢。

怀疑来源: tools/audit_dual_write.py

  save_review() 写文件 = 完整 record dict
  save_review() 写 SQLite = 7 个字段的投影
    (status, auto_severity, auto_issues_count, auto_result,
     reviewer, reviewer_notes, v2_chars)
  get_review() 读回 = SQLite 优先, 文件兜底

若 record 里有投影外的字段, 它只存在于文件里; 而读回走 SQLite,
兜底只在 SQLite 读【失败】时触发 —— 正常情况下不会走。
那么那些字段就是静默消失的: 没有异常, 没有日志, 读回来就是没有。

本文件用真实双写 + 真实读回来验证, 不靠读代码推断。
若全部字段都回来了, 说明投影是全的或读回做了合并, 那是好消息,
本文件就是防回归的锁。
"""
from __future__ import annotations

import pytest

from lib import review_service as revserv
from lib import storage

BOOK = "test_book"
CH = "ch_001"


@pytest.fixture
def book(tmp_projects_root):
    d = storage.chapters_dir(BOOK)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{CH}.md").write_text("正文\n", encoding="utf-8")
    return BOOK


def _full_record() -> dict:
    """构造一个「字段尽量铺满」的评审记录。"""
    rec = revserv._empty_record(CH)
    rec.update({
        "status": "pending_review",
        "reviewer": "wei_chao",
        "reviewer_notes": "一些备注",
        "auto_severity": "high",
        "auto_issues_count": 3,
        "auto_result": {"issues": ["a", "b"]},
        "v2_chars": 1234,
    })
    return rec


class TestWhatSurvivesDualWrite:
    def test_readback_returns_a_record(self, book):
        rec = _full_record()
        revserv.save_review(book, rec)
        got = revserv.get_review(book, CH)
        assert got is not None, "双写后读不回记录, 那是另一个问题"

    def test_projected_fields_survive(self, book):
        """投影内的 7 个字段, 走 SQLite 读回必须完好。"""
        rec = _full_record()
        revserv.save_review(book, rec)
        got = revserv.get_review(book, CH)
        for k in ("status", "auto_severity", "auto_issues_count",
                  "auto_result", "reviewer", "reviewer_notes", "v2_chars"):
            assert k in got, f"投影字段 {k} 读回后消失了"
            assert got[k] == rec[k], f"投影字段 {k} 值不一致"

    def test_history_survives_dual_write(self, book):
        """history 是审计轨迹, 丢失等于评审过程不可追溯。"""
        rec = _full_record()
        rec["history"] = [
            {"at": "2026-09-29T10:00:00", "action": "auto_flagged", "by": "AI"},
            {"at": "2026-09-29T11:00:00", "action": "approved", "by": "wei_chao"},
        ]
        revserv.save_review(book, rec)
        got = revserv.get_review(book, CH)
        assert "history" in got, (
            "history 写进了文件但读回没有 —— 双写投影把它丢了。"
            "评审审计轨迹会静默消失。"
        )
        assert len(got["history"]) == 2

    def test_timestamps_survive_dual_write(self, book):
        rec = _full_record()
        rec["reviewed_at"] = "2026-09-29T11:00:00"
        rec["updated_at"] = "2026-09-29T11:30:00"
        revserv.save_review(book, rec)
        got = revserv.get_review(book, CH)
        missing = [k for k in ("reviewed_at", "updated_at") if k not in got]
        assert not missing, f"时间戳读回丢失: {missing}"


class TestFileVsDbDirectly:
    """两侧必须一致 —— 这是双写的全部意义。

    历史: 写这个文件时发现了两个叠加的 bug, 且第二个被第一个掩盖:
      1. db.get_review() 写的是 dict(r) 而上面绑的是 row → 每次读到行就 NameError
      2. save_review() 不传 history 给 upsert_review → DB 侧永远是 []
    修好 1 之后 2 才暴露: test_history_survives_dual_write 报 assert 0 == 2。
    """

    def test_db_row_now_has_history(self, book):
        from lib import db as _db
        rec = _full_record()
        rec["history"] = [{"at": "t", "action": "approved", "by": "x"}]
        revserv.save_review(book, rec)
        row = _db.get_review(storage.ROOT, book, CH)
        assert row is not None, "DB 读路径已修, 应能读到行"
        assert "history" in row, "DB 行应带 history(由 history_json 解出)"
        assert len(row["history"]) == 1, f"DB history 长度应为 1, 实得 {row.get('history')}"

    def test_file_and_db_agree_on_history(self, book):
        from lib import db as _db
        rec = _full_record()
        rec["history"] = [
            {"at": "t1", "action": "auto_flagged", "by": "AI"},
            {"at": "t2", "action": "approved", "by": "wei_chao"},
        ]
        revserv.save_review(book, rec)

        on_file = storage.read_json(book, f"reviews/{CH}.review.json")
        row = _db.get_review(storage.ROOT, book, CH)

        assert on_file["history"] == row["history"], (
            f"两侧 history 不一致:\n"
            f"  文件: {on_file['history']}\n"
            f"  DB  : {row.get('history')}"
        )

    def test_db_row_exposes_chapter_id(self, book):
        """DB 列名是 ch_id, 上层要 chapter_id —— get_review 负责补。"""
        from lib import db as _db
        rec = _full_record()
        revserv.save_review(book, rec)
        row = _db.get_review(storage.ROOT, book, CH)
        assert row.get("chapter_id") == CH, (
            f"DB 行应补上 chapter_id 兼容字段, 实得 {row.get('chapter_id')!r}"
        )

    def test_readback_after_approve_has_full_history(self, book):
        """端到端: 走 approve 这条真实路径, 审计轨迹必须完整。"""
        rec = revserv._empty_record(CH)
        rec["history"] = [{"at": "t1", "action": "auto_flagged", "by": "AI"}]
        revserv.save_review(book, rec)
        revserv.approve(book, CH, "wei_chao", "看过了")
        got = revserv.get_review(book, CH)
        assert got is not None
        actions = [h.get("action") for h in got.get("history", [])]
        assert "approved" in actions, f"批准记录应进 history, 实得 {actions}"
        assert "auto_flagged" in actions, f"先前记录应保留, 实得 {actions}"

    def test_divergence_is_reproducible(self, book):
        """同一个记录, 连续两次读回结果必须一致(排除偶发)。"""
        rec = _full_record()
        rec["history"] = [{"at": "t", "action": "a", "by": "b"}]
        revserv.save_review(book, rec)
        first = revserv.get_review(book, CH)
        second = revserv.get_review(book, CH)
        assert (first or {}).keys() == (second or {}).keys()