"""
test_backup.py — backup.clean_old_backups 边界条件
"""
import time
import pytest
from pathlib import Path
from lib import backup


def _touch(p: Path, days_ago: float) -> None:
    """创建文件并设置 mtime 为 N 天前."""
    p.write_bytes(b"x")
    mtime = time.time() - days_ago * 86400
    import os
    os.utime(p, (mtime, mtime))


def test_list_snapshots_empty(tmp_path):
    assert backup.list_snapshots(tmp_path) == []


def test_list_snapshots_ignores_non_snapshot(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "snapshot-20260701-120000.tar.gz").write_bytes(b"")
    (tmp_path / "README.md").write_bytes(b"")
    (tmp_path / "snapshot-foo.tar.gz").write_bytes(b"")  # bad name
    snaps = backup.list_snapshots(tmp_path)
    assert len(snaps) == 1
    assert snaps[0].name == "snapshot-20260701-120000.tar.gz"


def test_list_snapshots_sorted_by_mtime_desc(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    _touch(tmp_path / "snapshot-20260101-000000.tar.gz", days_ago=30)
    _touch(tmp_path / "snapshot-20260701-000000.tar.gz", days_ago=0)
    _touch(tmp_path / "snapshot-20260201-000000.tar.gz", days_ago=15)
    snaps = backup.list_snapshots(tmp_path)
    names = [s.name for s in snaps]
    # 最新 (days_ago=0) 在最前
    assert names[0].startswith("snapshot-20260701")


def test_clean_old_backups_removes_old(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    _touch(tmp_path / "snapshot-20260101-000000.tar.gz", days_ago=10)
    _touch(tmp_path / "snapshot-20260102-000000.tar.gz", days_ago=20)
    _touch(tmp_path / "snapshot-20260701-000000.tar.gz", days_ago=1)
    removed = backup.clean_old_backups(tmp_path, retention_days=7)
    assert sorted(removed) == [
        "snapshot-20260101-000000.tar.gz",
        "snapshot-20260102-000000.tar.gz",
    ]
    assert (tmp_path / "snapshot-20260701-000000.tar.gz").exists()
    assert not (tmp_path / "snapshot-20260101-000000.tar.gz").exists()


def test_clean_old_backups_no_op_when_all_recent(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    _touch(tmp_path / "snapshot-20260701-120000.tar.gz", days_ago=2)
    removed = backup.clean_old_backups(tmp_path, retention_days=7)
    assert removed == []
    assert (tmp_path / "snapshot-20260701-120000.tar.gz").exists()


def test_clean_old_backups_retention_zero_disables(tmp_path):
    """retention_days < 1 → 不删."""
    tmp_path.mkdir(exist_ok=True)
    _touch(tmp_path / "snapshot-20260101-000000.tar.gz", days_ago=100)
    removed = backup.clean_old_backups(tmp_path, retention_days=0)
    assert removed == []