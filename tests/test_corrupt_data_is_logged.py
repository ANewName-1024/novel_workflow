"""tests/test_corrupt_data_is_logged.py — 损坏数据必须留痕。

背景(tools/audit_ambiguity.py):
95 处 except 里只有 7 处返回的「空」与「合法的空」不可区分。它们原本静默,
UI 层面表现为「本章没有评审 / 流水线没跑过」, 而实际是 JSON 文件损坏。

本次修复保持返回契约(None / [])不变 —— 不破坏调用方 —— 只把失败变可见。
这些测试锁住「损坏要出声」, 且「正常缺失不出声」(否则日志会被淹)。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lib import entity_diff, review_service, self_check
from lib.pipeline import process as pl_process


@pytest.fixture
def broken_json(tmp_path: Path) -> Path:
    p = tmp_path / "broken.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    return p


# ── 正常缺失:静默 ─────────────────────────────────────────────────────────
class TestMissingIsQuiet:
    def test_get_review_missing_is_quiet(self, monkeypatch, caplog):
        monkeypatch.setattr(review_service, "review_path",
                            lambda b, c: Path("nonexistent-dir/x.json"))
        with caplog.at_level(logging.WARNING):
            assert review_service.get_review("b", "ch_001") is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
            "「本来就没有」是正常路径, 不该记 warning"


# ── 文件损坏:必须出声 ─────────────────────────────────────────────────────
class TestCorruptionIsLoud:
    def test_get_review_corrupt_logs(self, monkeypatch, caplog, broken_json):
        monkeypatch.setattr(review_service, "review_path",
                            lambda b, c: broken_json)
        with caplog.at_level(logging.WARNING):
            assert review_service.get_review("b", "ch_001") is None

        recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert recs, "评审文件损坏必须记 warning —— 否则 UI 显示「无评审」"
        assert "损坏" in recs[0].getMessage()
        assert "broken.json" in recs[0].getMessage(), "日志须带文件路径"

    def test_get_self_check_corrupt_logs(self, monkeypatch, caplog, broken_json):
        # 2026-10-01: storage 把 project_root 拆成 project_path(纯计算) 与
        # project_root(建目录) 两族, get_self_check 走读的那一支 selfcheck_file。
        # patch 目标随之更新 —— 断言本身(损坏必须留痕)一个字没放松。
        monkeypatch.setattr(self_check.storage, "selfcheck_file",
                            lambda b, c: broken_json)
        with caplog.at_level(logging.WARNING):
            assert self_check.get_self_check("b", "ch_001") is None
        recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert recs, "自检记录损坏必须留痕"
        assert "损坏" in recs[0].getMessage()

    def test_get_chapter_changes_corrupt_logs(self, monkeypatch, caplog, broken_json):
        monkeypatch.setattr(entity_diff, "changelog_path",
                            lambda b, c: broken_json)
        with caplog.at_level(logging.WARNING):
            assert entity_diff.get_chapter_changes("b", "ch_001") is None
        recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert recs, "实体变更记录损坏必须留痕"
        assert "损坏" in recs[0].getMessage()

    def test_read_state_corrupt_logs(self, monkeypatch, caplog, broken_json):
        """状态文件损坏会被 status() 误读成「流水线没跑过」, 比别处更危险."""
        monkeypatch.setattr(pl_process, "pipeline_state_path",
                            lambda b: broken_json)
        with caplog.at_level(logging.WARNING):
            assert pl_process._read_state("b") is None
        recs = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert recs, "状态文件损坏必须留痕"
        assert "损坏" in recs[0].getMessage()


# ── 正常内容:不受影响 ─────────────────────────────────────────────────────
class TestHappyPathUnchanged:
    def test_get_review_reads_valid_json(self, monkeypatch, tmp_path, caplog):
        p = tmp_path / "ok.json"
        p.write_text(json.dumps({"score": 8}), encoding="utf-8")
        monkeypatch.setattr(review_service, "review_path", lambda b, c: p)
        with caplog.at_level(logging.WARNING):
            got = review_service.get_review("b", "ch_001")
        assert got == {"score": 8}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
            "正常读取不该有任何 warning"

    def test_read_state_reads_valid_json(self, monkeypatch, tmp_path, caplog):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"status": "running", "pid": 1234}),
                     encoding="utf-8")
        monkeypatch.setattr(pl_process, "pipeline_state_path", lambda b: p)
        with caplog.at_level(logging.WARNING):
            got = pl_process._read_state("b")
        assert got == {"status": "running", "pid": 1234}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]