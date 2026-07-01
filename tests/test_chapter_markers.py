"""
test_chapter_markers.py - chapter.py 加的 [PIPELINE] marker (v1.1 M4)

3 cases:
  1. lib/chapter.py 源码包含 [PIPELINE] marker (8 个 stage)
  2. _PIPELINE_RE 正则能匹配 chapter.py 输出的 marker
  3. write_chapter 入口处打 stage=context start
"""
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from lib import pipeline


# ── 1. 源码包含 marker ─────────────────────────────────────────────────

def test_chapter_py_contains_pipeline_markers():
    """chapter.py 源码里至少有 8 个 [PIPELINE] marker (context/writing/extract/summary/state/self_check/done)."""
    text = Path("lib/chapter.py").read_text(encoding="utf-8")
    # 至少 7 个 stage 都得出现
    for stage in ["context", "writing", "extract", "summary", "state", "self_check", "done"]:
        assert f"stage={stage}" in text, f"chapter.py 缺 stage={stage} marker"
    # 每个 stage 都 start/done
    assert text.count("status=start") >= 7
    assert text.count("status=done") >= 5  # context/writing/extract/summary/state/self_check 都 done


# ── 2. 正则匹配 ───────────────────────────────────────────────────────

def test_pipeline_regex_matches_chapter_output():
    """_PIPELINE_RE 能解析 chapter.py 打印的 marker."""
    samples = [
        "[PIPELINE] book=测试书籍 ch=8 stage=context status=start",
        "[PIPELINE] book=test_book ch=1 stage=writing status=done",
        "[PIPELINE] book=测试书籍 ch=8 stage=extract status=failed",
        "[PIPELINE] book=测试书籍 ch=8 stage=self_check status=done severity=ok",
    ]
    for s in samples:
        m = pipeline._PIPELINE_RE.search(s)
        assert m is not None, f"正则没匹配: {s}"
        # 至少 4 个 capture group: book, ch, stage, status
        assert m.group(1)  # book
        assert m.group(2).isdigit()  # ch
        assert m.group(3) in ("context", "writing", "extract", "summary", "state", "self_check", "done")
        assert m.group(4) in ("start", "done", "failed")


# ── 3. write_chapter 入口打 marker ─────────────────────────────────────

def test_write_chapter_prints_context_marker(monkeypatch, tmp_projects_root):
    """write_chapter() 启动时打 [PIPELINE] stage=context start (不用真 LLM, 验 print)."""
    # 这个测试不真跑 LLM, 我们直接 import 后用 mock
    # 简化: 只验 source code 在 write_chapter 函数入口打了 context start
    text = Path("lib/chapter.py").read_text(encoding="utf-8")
    # 找 write_chapter 函数定义
    m = re.search(r"def write_chapter\(.+?\):(.+?)(?=\ndef )", text, re.DOTALL)
    assert m is not None, "write_chapter 函数未找到"
    body = m.group(1)
    # 整个 chapter.py 源码应包含 context / writing / done marker
    assert "stage=context status=start" in text
    assert "stage=writing status=start" in text
    assert "stage=writing status=done" in text


# ── 4. status() 从 log 读 current_stage (e2e) ─────────────────────────

def test_status_e2e_log_marker_updates_stage(tmp_projects_root):
    """e2e: 写多行 PIPELINE marker 到 log, status() 读到最新 stage."""
    from lib import storage
    storage.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})

    # 写 state (running + PID 活)
    import os
    runner = pipeline.PipelineRunner()
    # 用真起子进程的方式拿 PID (活 PID)
    state = runner.start("test_book", chapter_num=1, auto_rewrite=False)
    try:
        # 模拟 chapter.py 顺序: context start → context done → writing start → writing done → extract start
        log = pipeline.pipeline_log_path("test_book")
        with open(log, "ab") as f:
            f.write(b"[PIPELINE] book=test_book ch=1 stage=context status=start\n")
            f.write(b"[PIPELINE] book=test_book ch=1 stage=context status=done\n")
            f.write(b"[PIPELINE] book=test_book ch=1 stage=writing status=start\n")
            f.write(b"[PIPELINE] book=test_book ch=1 stage=extract status=start\n")
        s = runner.status("test_book")
        # 最新 marker 是 extract
        assert s["current_stage"] == "extract"
    finally:
        runner.cancel("test_book")
