"""tests/test_backfill_corrupt_selfcheck.py — 自检记录损坏时不得自动通过。

发现于 tools/audit_novel_py.py, 修在 novel.py 的 _ensure_review_for_existing()。

原逻辑:
    if sc_path.exists():
        try:    sc_result = json.loads(...)
        except Exception: sc_result = None     ← 损坏被当成「没有」
    if sc_result:  auto_flag(...)
    else:          save_review(status=AUTO_PASSED)   ← 写死一个评审结论

自检文件损坏时, 章节被标记成「自动通过」并持久化进评审记录。那不是「跳过这一章」,
那是一个结论 —— 而且一旦落库, 后续再也不会重新自检。与 f34aa40 修的 7 处
同源(失败伪装成成功), 但后果更重: 那 7 处返回 None, 这里返回一个裁决。

修复: 损坏 -> 记 ERROR 并 continue, 不落任何结论。
自检文件不存在 -> 维持原行为(默认通过), 那是既定语义, 不是 bug。
"""
from __future__ import annotations

import json

import pytest

from lib import review_service as revserv
from lib import storage

BOOK = "test_book"          # conftest 的 tmp_projects_root 已建好这个项目
CH = "ch_001"


def _add_chapter(book=BOOK, ch_id=CH):
    """放一个章节文件, 使其可被 list_chapters 枚举。"""
    d = storage.chapters_dir(book)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ch_id}.md").write_text("第一章内容。\n", encoding="utf-8")
    return ch_id


def _self_check_path(book=BOOK, ch_id=CH):
    return storage.project_root(book) / "self_checks" / f"{ch_id}.json"


def _backfill(book=BOOK):
    import novel
    novel._ensure_review_for_existing(book)


class TestCorruptSelfCheck:
    def test_corrupt_file_does_not_mark_auto_passed(self, tmp_projects_root):
        _add_chapter()
        p = _self_check_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ 这不是合法 JSON", encoding="utf-8")

        _backfill()

        rec = revserv.get_review(BOOK, CH)
        assert rec is None, (
            f"自检记录损坏时不应写出任何评审结论, 实得 status="
            f"{rec.get('status') if rec else None!r}"
        )

    def test_corrupt_file_is_logged_as_error(self, tmp_projects_root, caplog):
        import logging
        _add_chapter()
        p = _self_check_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<<<corrupt>>>", encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            _backfill()

        recs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert recs, "损坏必须记 ERROR —— 否则数据损坏完全不可见"
        joined = " ".join(r.getMessage() for r in recs)
        assert "自检记录损坏" in joined
        assert BOOK in joined, "日志须带 book 上下文"


class TestGenuineMissingStillAutoPasses:
    """文件不存在 -> 默认通过是既定语义, 修复不能把它一起改掉。"""

    def test_missing_file_still_auto_passes(self, tmp_projects_root):
        _add_chapter()
        assert not _self_check_path().exists()

        _backfill()

        rec = revserv.get_review(BOOK, CH)
        assert rec is not None, "无自检数据时应照常 backfill"
        assert rec["status"] == revserv.REVIEW_STATUS["AUTO_PASSED"]

    def test_valid_self_check_still_flags(self, tmp_projects_root):
        _add_chapter()
        p = _self_check_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"severity": "high", "issues": ["逻辑漏洞"]}),
                     encoding="utf-8")

        _backfill()

        rec = revserv.get_review(BOOK, CH)
        assert rec is not None
        assert rec["status"] != revserv.REVIEW_STATUS["AUTO_PASSED"], \
            "有自检数据时应走 auto_flag, 不应标成自动通过"


class TestModuleLogger:
    def test_module_level_logger_exists(self):
        """辅助函数在 main() 作用域外, 必须有模块级 logger 可用。"""
        import novel
        assert hasattr(novel, "log"), "novel.py 缺模块级 log, 辅助函数无法记日志"
        recs = [n for n in dir(novel.log) if n in ("warning", "error", "info")]
        assert len(recs) == 3, f"logger 不可用: {recs}"