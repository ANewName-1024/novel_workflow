"""
test_pipeline.py - lib/pipeline.PipelineRunner (v1.1)

8 cases:
  1. start 启动子进程, 写 .pipeline_state.json
  2. start 重复调用抛 NovelError
  3. start 项目不存在抛 NovelError(NOT_FOUND)
  4. status 读 state, PID 还活着
  5. status PID 死了自动标 failed
  6. cancel 杀子进程 + 标 cancelled
  7. tail_log 读最后 N 行
  8. append_metric + get_metrics 聚合
"""
import json
import time
from pathlib import Path

import pytest

from lib import pipeline
from lib import storage
from lib.errors import ErrorCode, NovelError


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def book(tmp_projects_root):
    """tmp_projects_root 已经建好 test_book, 直接复用."""
    return "test_book"


# ── 1. start 启动 ─────────────────────────────────────────────────────────

def test_start_writes_state_file(tmp_projects_root, book):
    """start() 写 .pipeline_state.json, 包含 pid/status/started_at/chapter."""
    runner = pipeline.PipelineRunner()
    state = runner.start(book, chapter_num=1, auto_rewrite=False)
    try:
        assert state["status"] == "running"
        assert state["current_chapter"] == 1
        assert state["pid"] > 0
        assert state["started_at"]
        # state 文件落盘
        path = pipeline.pipeline_state_path(book)
        assert path.exists()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["status"] == "running"
        assert saved["pid"] == state["pid"]
    finally:
        # 清理
        runner.cancel(book)


def test_start_nonexistent_book_raises(tmp_projects_root):
    """start() 不存在的项目抛 NOT_FOUND."""
    runner = pipeline.PipelineRunner()
    with pytest.raises(NovelError) as exc:
        runner.start("no_such_book", chapter_num=1)
    assert exc.value.code == ErrorCode.NOT_FOUND


def test_start_already_running_raises(tmp_projects_root, book):
    """start() 已有任务在跑抛 GENERIC."""
    runner = pipeline.PipelineRunner()
    runner.start(book, chapter_num=1, auto_rewrite=False)
    try:
        with pytest.raises(NovelError) as exc:
            runner.start(book, chapter_num=2)
        assert exc.value.code == ErrorCode.GENERIC
        assert "已有任务在跑" in exc.value.message
    finally:
        runner.cancel(book)


# ── 4. status 读 + PID 存活 ──────────────────────────────────────────────

def test_status_running_pid_alive(tmp_projects_root, book):
    """start 后 status() 返回 running, PID 存活."""
    runner = pipeline.PipelineRunner()
    runner.start(book, chapter_num=1, auto_rewrite=False)
    try:
        s = runner.status(book)
        assert s is not None
        assert s["status"] == "running"
        assert pipeline._is_pid_alive(s["pid"]) is True
    finally:
        runner.cancel(book)


def test_status_auto_marks_failed_when_pid_dead(tmp_projects_root, book):
    """PID 死了但 state 还是 running → status() 自动标 failed."""
    runner = pipeline.PipelineRunner()
    state = runner.start(book, chapter_num=1, auto_rewrite=False)
    # 杀掉子进程但不动 state
    import os
    try:
        os.kill(state["pid"], 9)  # SIGKILL
    except (OSError, ProcessLookupError):
        pass
    # 0.5s 后再查
    time.sleep(0.5)
    s = runner.status(book)
    assert s["status"] == "failed"
    assert s["exit_code"] == -1


# ── 6. cancel 杀进程 ─────────────────────────────────────────────────────

def test_cancel_kills_subprocess(tmp_projects_root, book):
    """cancel() 杀子进程, state 标 cancelled."""
    runner = pipeline.PipelineRunner()
    state = runner.start(book, chapter_num=1, auto_rewrite=False)
    pid = state["pid"]
    # 1s 内子进程应该还在 (write_chapter 至少 ~30s)
    assert pipeline._is_pid_alive(pid)
    # cancel
    new_state = runner.cancel(book)
    assert new_state["status"] == "cancelled"
    # 1s 内 PID 应该死了
    time.sleep(1.0)
    assert not pipeline._is_pid_alive(pid)


def test_cancel_no_state_raises(tmp_projects_root, book):
    """没有 state 文件时 cancel 抛 NOT_FOUND."""
    runner = pipeline.PipelineRunner()
    with pytest.raises(NovelError) as exc:
        runner.cancel(book)
    assert exc.value.code == ErrorCode.NOT_FOUND


def test_cancel_not_running_raises(tmp_projects_root, book):
    """state 是 idle/done/cancelled 时 cancel 抛 GENERIC."""
    runner = pipeline.PipelineRunner()
    # 写一个 done state
    storage.write_json(book, ".pipeline_state.json", {
        "book": book, "status": "done", "pid": 99999,
    })
    with pytest.raises(NovelError) as exc:
        runner.cancel(book)
    assert exc.value.code == ErrorCode.GENERIC


# ── 7. tail_log ───────────────────────────────────────────────────────────

def test_tail_log_returns_last_n_lines(tmp_projects_root, book):
    """tail_log 读 log 文件最后 N 行."""
    runner = pipeline.PipelineRunner()
    runner.start(book, chapter_num=1, auto_rewrite=False)
    try:
        # 写一些行到 log
        log = pipeline.pipeline_log_path(book)
        log.parent.mkdir(exist_ok=True)
        log.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
        # 读最后 5 行
        lines = runner.tail_log(book, n=5)
        assert len(lines) == 5
        assert lines[-1] == "line 49"
    finally:
        runner.cancel(book)


def test_tail_log_empty_when_no_file(tmp_projects_root, book):
    """log 文件不存在时返回空列表."""
    runner = pipeline.PipelineRunner()
    # 没 start 过, 也没 log 文件
    assert runner.tail_log(book, n=10) == []


# ── 8. metrics 聚合 ──────────────────────────────────────────────────────

def test_append_and_get_metrics(tmp_projects_root, book):
    """append_metric 写 metrics.jsonl, get_metrics 聚合."""
    runner = pipeline.PipelineRunner()
    # 写 3 行 metrics (ch 5, 2 个调用; ch 6, 1 个)
    runner.append_metric(book, stage="writing", ch=5, model="M",
                         input_tokens=1000, output_tokens=200, latency_ms=30000)
    runner.append_metric(book, stage="extract", ch=5, model="M",
                         input_tokens=500, output_tokens=50, latency_ms=10000)
    runner.append_metric(book, stage="writing", ch=6, model="M",
                         input_tokens=1200, output_tokens=300, latency_ms=35000)
    m = runner.get_metrics(book, range_str="all")
    assert m["calls"] == 3
    assert m["total_in"] == 2700
    assert m["total_out"] == 550
    chapters = {c["ch"]: c for c in m["chapters"]}
    assert chapters[5]["input_tokens"] == 1500
    assert chapters[5]["output_tokens"] == 250
    assert chapters[5]["calls"] == 2
    assert chapters[6]["input_tokens"] == 1200
    assert chapters[6]["calls"] == 1


def test_get_metrics_empty(tmp_projects_root, book):
    """metrics.jsonl 不存在时返回 0."""
    runner = pipeline.PipelineRunner()
    m = runner.get_metrics(book, range_str="all")
    assert m["chapters"] == []
    assert m["total_in"] == 0
    assert m["total_out"] == 0
    assert m["calls"] == 0


def test_get_metrics_filters_by_range(tmp_projects_root, book):
    """range=1d 过滤掉超过 1 天的记录."""
    runner = pipeline.PipelineRunner()
    # 写一行, 时间是 now (应该在 1d 范围内)
    runner.append_metric(book, stage="writing", ch=5, model="M",
                         input_tokens=100, output_tokens=10, latency_ms=1000)
    m_all = runner.get_metrics(book, range_str="all")
    m_1d = runner.get_metrics(book, range_str="1d")
    m_1s = runner.get_metrics(book, range_str="1d")
    # now 一定在 1d 范围内
    assert m_1d["calls"] == 1
    assert m_all["calls"] == 1


# ── 9. _parse_current_stage_from_log ──────────────────────────────────────

def test_parse_current_stage_from_log(tmp_projects_root, book):
    """读 log 最后一行 PIPELINE marker, 返回 stage."""
    log = pipeline.pipeline_log_path(book)
    log.parent.mkdir(exist_ok=True)
    log.write_text(
        "[2026-07-01T22:50:00] [PIPELINE] book=测试书籍 ch=8 stage=context status=start\n"
        "[2026-07-01T22:50:01] [PIPELINE] book=测试书籍 ch=8 stage=context status=done\n"
        "[2026-07-01T22:50:01] [PIPELINE] book=测试书籍 ch=8 stage=writing status=start\n"
        "[2026-07-01T22:51:30] some other line\n"
        "[2026-07-01T22:52:00] [PIPELINE] book=测试书籍 ch=8 stage=extract status=start\n",
        encoding="utf-8",
    )
    cur = pipeline._parse_current_stage_from_log(book)
    assert cur == "extract"


def test_parse_current_stage_no_log(tmp_projects_root, book):
    """log 不存在返回 None."""
    assert pipeline._parse_current_stage_from_log(book) is None


def test_parse_current_stage_no_marker(tmp_projects_root, book):
    """log 里没 PIPELINE marker 返回 None."""
    log = pipeline.pipeline_log_path(book)
    log.parent.mkdir(exist_ok=True)
    log.write_text("just some lines\nno markers here\n", encoding="utf-8")
    assert pipeline._parse_current_stage_from_log(book) is None


def test_status_reflects_log_current_stage(tmp_projects_root, book):
    """running 状态下, status() 从 log 读 latest marker 更新 current_stage."""
    runner = pipeline.PipelineRunner()
    state = runner.start(book, chapter_num=1, auto_rewrite=False)
    try:
        # 模拟 chapter.py 写了 1 个 marker
        log = pipeline.pipeline_log_path(book)
        with open(log, "ab") as f:
            f.write(b"[PIPELINE] book=test_book ch=1 stage=writing status=start\n")
        s = runner.status(book)
        assert s["current_stage"] == "writing"
    finally:
        runner.cancel(book)
