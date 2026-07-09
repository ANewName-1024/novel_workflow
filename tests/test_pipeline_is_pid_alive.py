"""test_pipeline_is_pid_alive.py - Regression: zombie PID 不能再判为 alive

Bug 来源 (2026-07-09): 用户在 VPS 点 dashboard 启动流水线, 写到第 1 章时
LLM API 报 'Connection error', 子进程死, 但 state.json 一直显示 'running',
看板永远绿色, 用户不能重跑.

根因:
- _is_pid_alive 用 os.kill(pid, 0) 探活,  zombie 也返回 0
- status() 看到 _is_pid_alive=True, 以为进程在跑, 不会校准 state

修复:
- _is_pid_alive 加 zombie 状态检测 (psutil.STATUS_ZOMBIE 或 /proc/<pid>/status State: Z)
- 加 _reap_zombie() 在 status() 校准时顺便 reap (POSIX)
"""
import os
import sys
import time
import subprocess
from pathlib import Path

import pytest

# 跳过环境检测
if sys.platform == "win32":
    pytest.skip("POSIX-only test (zombie reap)", allow_module_level=True)

from lib import pipeline as pl


def _current_pid_alive() -> bool:
    """self 进程显然 alive, 且不是 zombie."""
    return pl._is_pid_alive(os.getpid())


def test_current_process_is_alive():
    assert _current_pid_alive() is True


def test_definitely_dead_pid():
    """PID 10^7 不可能存在 (alloc PID range 测试)."""
    assert pl._is_pid_alive(99999999) is False


def test_invalid_pid():
    assert pl._is_pid_alive(0) is False
    assert pl._is_pid_alive(-1) is False
    assert pl._is_pid_alive(None) is False


def test_zombie_is_not_alive():
    """fork() 一个 child, child 立即 os._exit, 不被 wait, 形成 zombie.
    然后 _is_pid_alive(zombie_pid) 应该返回 False."""
    pid = os.fork()
    if pid == 0:
        # Child: 立即退出, 不刷 buffer
        os._exit(0)
    # Parent: 等 child 死了, 但不 wait
    time.sleep(0.3)
    # 现在 child 应该是 zombie
    # 验证 /proc/<pid>/status
    state_file = Path(f"/proc/{pid}/status")
    if state_file.exists():
        state_text = state_file.read_text()
        is_zombie = "State:\tZ" in state_text
        if not is_zombie:
            # 可能被 reaper 收走了 (Linux 在 parent 不 wait 时也会清掉)
            pytest.skip("环境未产生 zombie (可能被 init reaper 收走)")
        # 核心断言: _is_pid_alive 对 zombie 返回 False
        result = pl._is_pid_alive(pid)
        # 顺手 reap, 清理测试痕迹
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        assert result is False, f"zombie (pid={pid}) should not be 'alive'"
    else:
        # PID 已被 reaper 收走
        pytest.skip("PID not found, was reaped")


def test_reap_zombie_function():
    """_reap_zombie 调用后, /proc/<pid>/status 消失 (被收走)."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    time.sleep(0.3)
    state_file = Path(f"/proc/{pid}/status")
    if not state_file.exists():
        pytest.skip("zombie was reaped before test started")

    # 调 reap
    reaped = pl._reap_zombie(pid)
    assert reaped is True
    # 现在 /proc/<pid>/status 消失
    assert not state_file.exists(), "zombie should be reaped after _reap_zombie"


def test_reap_nonexistent_pid():
    """对不存在的 PID reap, 应返回 False 不抛异常."""
    assert pl._reap_zombie(99999999) is False
    assert pl._reap_zombie(0) is False
    assert pl._reap_zombie(-1) is False
    assert pl._reap_zombie(None) is False