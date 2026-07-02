"""
test_chapter_v2_integration.py — chapter.py 写 v2 checkpoint 集成测试 (M5.2)

3 tests:
1. v2 helper 不抛异常 (best-effort)
2. FSM 转换 (PENDING → RUNNING → DONE) 在 chapter.py 模拟下正确写入
3. v2 故障不影响主流程 (mock v2 抛异常)
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lib import pipeline_v2 as pv2


# ── 1. v2 helper 不抛 ─────────────────────────────────────────────────────

def test_v2_mark_helper_does_not_raise(tmp_projects_root, monkeypatch):
    """_v2_mark 在 v2 内部抛异常时也不抛 (包 try/except)."""
    from lib import chapter as chapmod
    # 让 pv2.get_v2() 抛异常
    with patch("lib.pipeline_v2.PipelineV2.transition", side_effect=RuntimeError("boom")):
        # 不应抛
        chapmod._v2_mark("test_book", 1, "context", "RUNNING")
    # 确认 v2 文件没被创建
    assert not pv2.checkpoint_path("test_book").exists()


# ── 2. FSM 转换正确写入 ──────────────────────────────────────────────────

def test_v2_checkpoints_written_on_transition_calls(tmp_projects_root):
    """模拟 chapter.py 完整跑一章, v2 checkpoint 7 个 stage 全 DONE."""
    v2 = pv2.PipelineV2()
    book = "test_book"

    # 模拟 write_chapter + run_post_write_pipeline 全过
    sequence = [
        ("context", "RUNNING"),
        ("context", "DONE"),
        ("writing", "RUNNING"),
        ("writing", "DONE"),
        ("extract", "RUNNING"),
        ("extract", "DONE"),
        ("summary", "RUNNING"),
        ("summary", "DONE"),
        ("state", "RUNNING"),
        ("state", "DONE"),
        ("self_check", "RUNNING"),
        ("self_check", "DONE"),
        ("done", "RUNNING"),
        ("done", "DONE"),
    ]
    for stage, status in sequence:
        v2.transition(book, 1, stage, status)

    # 验证 checkpoint
    ch_doc = v2.get_chapter(book, 1)
    assert ch_doc.is_complete()
    for s in pv2.STAGES:
        assert ch_doc.stages[s].status == "DONE", f"{s} should be DONE"


def test_v2_failure_path_writes_failed_status(tmp_projects_root):
    """extract 失败 → v2 记 FAILED, 下游 stage 不跑."""
    v2 = pv2.PipelineV2()
    book = "test_book"

    # context + writing 成功
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    v2.transition(book, 1, "writing", "RUNNING")
    v2.transition(book, 1, "writing", "DONE")
    # extract 失败
    v2.transition(book, 1, "extract", "RUNNING")
    v2.transition(book, 1, "extract", "FAILED", error="JSON parse error")
    # summary 也没跑
    view = v2.get_pipeline_view(book, 1)
    assert view["failed_stage"] == "extract"
    assert view["stages"][2]["status"] == "FAILED"  # extract
    assert view["stages"][3]["status"] == "PENDING"  # summary


# ── 3. v2 故障不影响主流程 ──────────────────────────────────────────────

def test_v2_failure_does_not_break_main_flow(tmp_projects_root, capsys):
    """v2 写盘抛异常 → stderr 输出, 主流程继续."""
    from lib import chapter as chapmod
    # 第一次 (context RUNNING) 正常, 第二次 (context DONE) 抛
    real_transition = pv2.PipelineV2.transition
    call_count = {"n": 0}
    def flaky_transition(self, book, ch, stage, status, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full")
        return real_transition(self, book, ch, stage, status, **kwargs)

    with patch.object(pv2.PipelineV2, "transition", flaky_transition):
        chapmod._v2_mark("test_book", 1, "context", "RUNNING")
        chapmod._v2_mark("test_book", 1, "context", "DONE")

    # 第二次应被吞掉
    captured = capsys.readouterr()
    assert "v2-checkpoint" in captured.err or "写入失败" in captured.err or "disk full" in captured.err
    # 第一次正常写盘
    v2_real = pv2.PipelineV2()
    ch = v2_real.get_chapter("test_book", 1)
    assert ch.stages["context"].status == "RUNNING"  # 第一次 (RUNNING) 成功, 第二次 (DONE) 失败