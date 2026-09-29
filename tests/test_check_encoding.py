"""tests/test_check_encoding.py — 编码守卫的契约

这些测试和 CI 里的 repo-hygiene 步骤是同一个目标的两道锁:CI 用进程级
check_encoding.py 拦阻;pytest 在本地/开发者环境下兜底。

写这些测试本身就是对守卫的元测试 —— 守卫有 bug 时 (例如之前
startswith("utf-8") 把 BOM 也算干净),CI 可能过关但 pytest 会先红。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "tools" / "check_encoding.py"


def run_guard() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK)],
        cwd=REPO, capture_output=True, text=True,
    )


def test_guard_executable_is_present() -> None:
    assert CHECK.is_file(), f"{CHECK} 不存在"


def test_clean_tree_passes() -> None:
    """当前仓库已经归一化,守卫应当直接通过."""
    r = run_guard()
    assert r.returncode == 0, (
        f"干净状态下守卫不该失败\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    )


def test_utf8_bom_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """反向测试: 故意写一个 UTF-8 BOM 文件,守卫必须拦下.

    之前守卫返回 clean 是因为 label.startswith('utf-8') 把
    'utf-8 (BOM)' 也当成干净 (L7 已修复). 这条测试锁住修复.
    """
    poison = REPO / "lib" / "__poison_bom_test__.py"
    poison.write_bytes(b"\xef\xbb\xbf# BOM\nX = 1\n")
    try:
        r = run_guard()
        assert r.returncode != 0, "BOM 文件必须被守卫拦下"
        assert "utf-8 (BOM)" in r.stdout
    finally:
        poison.unlink(missing_ok=True)
    # 守卫删了文件后,下一次应恢复干净
    r2 = run_guard()
    assert r2.returncode == 0, "清理后守卫必须重新通过"


def test_utf16_is_caught() -> None:
    """另一个反向测试: UTF-16 LE 文件被守卫拦下."""
    poison = REPO / "lib" / "__poison_utf16_test__.py"
    poison.write_bytes(b"\xff\xfeX\x00\x3d\x00\x20\x00\x31\x00\n\x00")
    try:
        r = run_guard()
        assert r.returncode != 0, "UTF-16 文件必须被守卫拦下"
        assert "utf-16" in r.stdout.lower()
    finally:
        poison.unlink(missing_ok=True)


def test_normalize_tool_dry_run_is_idempotent_on_clean_tree() -> None:
    """归一工具在干净状态下 dry-run 不应列出任何变更."""
    r = subprocess.run(
        [sys.executable, str(REPO / "tools" / "normalize_encoding.py")],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "Already clean" in r.stdout