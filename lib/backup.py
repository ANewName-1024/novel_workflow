"""
backup.py — 备份清理 helper (供 cmd_backup --clean + 独立脚本调用)
"""
from __future__ import annotations

import re
import time
from pathlib import Path


def list_snapshots(backup_dir: Path) -> list[Path]:
    """列出 backups/ 下的所有 snapshot 文件, 按 mtime 倒序."""
    if not backup_dir.exists():
        return []
    pattern = re.compile(r"snapshot-(\d{8})-(\d{6})\.(tar\.gz|zip)$")
    snaps: list[tuple[float, Path]] = []
    for f in backup_dir.iterdir():
        if pattern.match(f.name):
            snaps.append((f.stat().st_mtime, f))
    snaps.sort(reverse=True)
    return [p for _, p in snaps]


def clean_old_backups(backup_dir: Path, retention_days: int) -> list[str]:
    """
    删除 > retention_days 天的快照 (基于 mtime).
    返回被删除的文件名列表.
    """
    if retention_days < 1:
        return []
    cutoff = time.time() - retention_days * 86400
    snaps = list_snapshots(backup_dir)
    removed: list[str] = []
    for f in snaps:
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                pass
    return removed
