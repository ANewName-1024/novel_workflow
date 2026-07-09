"""test_pipeline_status_recovery.py - Regression: 状态恢复时区分 done vs failed

Bug 来源 (2026-07-09): novel.py write 子进程 写完 ch_001 后正常 exit, 但没更新
.pipeline_state.json, state 仍是 running. 进程死了之后, status() 自动校准只看
_is_pid_alive 判 'failed'. 但用户实际看到 ch_001.md 已写成功, 错误信息误导.

修复: 校准时读 log tail, 如果有 '章节撰写完成' 或 '✓ 完成' marker,
判为 done (exit_code=0), 不是 failed.
"""
import os
import sys
import time
import subprocess
import tempfile
from pathlib import Path

import pytest

from lib import pipeline as pl
from lib import storage


def _setup_book_state(tmp_path, monkeypatch, log_content: str):
    """Mock projects root with a book that has state + log."""
    # 用 tmp_path 替 projects root
    projects = tmp_path / "projects" / "fake_book"
    projects.mkdir(parents=True)
    log_dir = projects / "logs"
    log_dir.mkdir()
    log_path = log_dir / "pipeline.log"
    log_path.write_text(log_content, encoding="utf-8")
    state_path = projects / ".pipeline_state.json"
    state = {
        "book": "fake_book",
        "status": "running",
        "pid": 99999999,  # 肯定死的 PID
        "started_at": "2026-07-09T00:00:00",
        "ended_at": None,
        "current_chapter": 1,
        "current_stage": "writing",
        "stage_started_at": "2026-07-09T00:00:00",
        "exit_code": None,
        "log_path": str(log_path),
        "error": None,
    }
    state_path.write_text(__import__("json").dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # 强制 _read_state/_write_state 用 tmp 路径
    import lib.storage as st
    monkeypatch.setattr(st, "project_root", lambda book: projects)
    return state_path, log_path


def test_dead_pid_with_done_marker_marked_as_done(tmp_path, monkeypatch):
    log = "══════════════════════════════════════════════════\n"
    log += "  ✓ 完成 (2152 字)\n"
    log += "✓ 章节撰写完成！\n"
    state_path, _ = _setup_book_state(tmp_path, monkeypatch, log)
    result = pl.get_runner().status("fake_book")
    assert result["status"] == "done", f"expected done, got {result['status']}"
    assert result["exit_code"] == 0
    assert result["error"] is None
    assert result["current_stage"] == "done"
    assert result["ended_at"] is not None


def test_dead_pid_without_done_marker_marked_as_failed(tmp_path, monkeypatch):
    log = "[PIPELINE] book=fake_book ch=1 stage=writing status=start\n"
    log += "  ✗ 失败: LLM API error after 3 retries: Connection error.\n"
    state_path, _ = _setup_book_state(tmp_path, monkeypatch, log)
    result = pl.get_runner().status("fake_book")
    assert result["status"] == "failed", f"expected failed, got {result['status']}"
    assert result["exit_code"] == -1
    assert "异常退出" in result["error"]


def test_dead_pid_with_empty_log_marked_as_failed(tmp_path, monkeypatch):
    state_path, _ = _setup_book_state(tmp_path, monkeypatch, "")
    result = pl.get_runner().status("fake_book")
    assert result["status"] == "failed"


def test_dead_pid_with_only_check_marker_marked_as_done(tmp_path, monkeypatch):
    """Some versions of log use '✓ 完成' (without 章节撰写完成)."""
    log = "[PIPELINE] book=fake_book ch=1 stage=writing status=start\n"
    log += "  ✓ 完成 (2152 字)\n"
    state_path, _ = _setup_book_state(tmp_path, monkeypatch, log)
    result = pl.get_runner().status("fake_book")
    assert result["status"] == "done"