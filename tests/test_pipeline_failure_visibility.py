"""tests/test_pipeline_failure_visibility.py — 失败可见性

Phase 3 第二部分:把「静默失败」改成「有记录的失败」。

最危险的一类不是 SWALLOW(刻意兜底),而是 **失败被伪装成成功**:
`get_interrupted_chapters` 读失败返回 [],和「确实没有中断章节」无法区分;
面板会在读取失败时显示「一切正常」。这些测试锁住修复后的行为。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lib.pipeline import state as st


class TestReadJsonSafe:
    def test_missing_file_returns_none_quietly(self, tmp_path, caplog):
        """「不存在」是正常路径(首次运行),不该报警."""
        with caplog.at_level(logging.WARNING):
            assert st._read_json_safe(tmp_path / "nope.json") is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
            "文件不存在是正常路径,不应记 warning"

    def test_corrupt_file_logs_warning(self, tmp_path, caplog):
        """「损坏」意味着数据丢失 —— 必须有 warning 记录."""
        p = tmp_path / "broken.json"
        p.write_text("{ this is not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert st._read_json_safe(p) is None
        recs = [r for r in caplog.records if "损坏" in r.getMessage()]
        assert recs, "JSON 损坏必须记 warning,不能与「文件不存在」同音"


class TestGetInterruptedChapters:
    def test_load_failure_is_logged_as_error(self, monkeypatch, caplog):
        """v2.load 抛异常时返回 []，但必须留下 ERROR 记录.

        不改返回契约(不破坏调用方),改的是「失败是否可见」。
        """
        class Boom:
            def load(self, book):
                raise OSError("状态文件锁住了")

        monkeypatch.setattr(st, "get_v2", lambda: Boom())
        with caplog.at_level(logging.ERROR):
            result = st.get_interrupted_chapters("b1")

        assert result == [], "契约不变:失败仍返回 []"
        recs = [r for r in caplog.records if "无法判断" in r.getMessage()]
        assert recs, "读取失败必须记 ERROR,否则面板会误显示「一切正常」"
        assert "b1" in recs[0].getMessage(), "日志须带 book 上下文"


class TestCheckpointSnapshot:
    def test_failure_carries_error_field(self, monkeypatch, caplog):
        """available=False 时应附带原因,便于前端区分「无数据」与「读失败」."""
        class Boom:
            def get_chapter(self, book, ch):
                raise ValueError("检查点结构不对")

        monkeypatch.setattr(st, "get_v2", lambda: Boom())
        with caplog.at_level(logging.ERROR):
            snap = st.checkpoint_snapshot("b1", 1)

        assert snap["available"] is False
        assert "error" in snap, "读失败必须带 error 字段,不能与「本来就没数据」同形"
        assert "ValueError" in snap["error"]
        recs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert recs, "读失败必须记 ERROR"
