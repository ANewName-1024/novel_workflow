"""
tests/test_migrate_world.py — tools/migrate_world.py 迁移脚本测试
"""
import json
import sys
from pathlib import Path

import pytest

# 让脚本可以 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import migrate_world


class TestDetectFormat:
    def test_empty(self):
        assert migrate_world._detect_format({}) == "empty"
        assert migrate_world._detect_format(None) == "empty"

    def test_legacy(self):
        data = {"公司": "公司...", "城市": "城市..."}
        assert migrate_world._detect_format(data) == "legacy"

    def test_new(self):
        data = {"rules": {"rule_1": {}}, "raw_notes": [], "_legacy": {}}
        assert migrate_world._detect_format(data) == "new"

    def test_mixed(self):
        """含非字符串字段但没 'rules' 键 → mixed."""
        data = {"公司": "text", "meta": {"key": "val"}}
        assert migrate_world._detect_format(data) == "mixed"


class TestMigrateBook:
    def test_dry_run_no_write(self, tmp_path, monkeypatch):
        """dry-run 不写文件."""
        # redirect _projects_root 到 tmp
        monkeypatch.setattr(migrate_world, "_projects_root", lambda: tmp_path)

        book = "test_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "world.json").write_text(json.dumps({
            "公司存在自动化邮件触发": "公司存在自动化邮件触发机制",
            "二手书店按斤收购": "二手书店按斤收购旧书",
        }, ensure_ascii=False), encoding="utf-8")

        report = migrate_world._migrate_book(book, apply=False)
        assert report["status"] == "dry-run"
        assert report["rules_count"] == 2

        # 文件没被改
        data = json.loads((mem_dir / "world.json").read_text(encoding="utf-8"))
        assert "rules" not in data  # 仍然 legacy

    def test_apply_writes_new_format(self, tmp_path, monkeypatch):
        """apply 写入新格式."""
        monkeypatch.setattr(migrate_world, "_projects_root", lambda: tmp_path)

        book = "test_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        world_path = mem_dir / "world.json"
        world_path.write_text(json.dumps({
            "魔法元素": "金木水火土五种元素相生相克",
        }, ensure_ascii=False), encoding="utf-8")

        report = migrate_world._migrate_book(book, apply=True)
        assert report["status"] == "migrated"
        assert report["rules_count"] == 1

        # 验证新格式
        new_data = json.loads(world_path.read_text(encoding="utf-8"))
        assert "rules" in new_data
        assert "_legacy" in new_data
        assert len(new_data["rules"]) == 1
        rid = list(new_data["rules"].keys())[0]
        assert rid.startswith("rule_")
        assert new_data["rules"][rid]["category"] == "其他"
        assert new_data["rules"][rid]["status"] == "草案"
        assert new_data["rules"][rid]["name"] == "魔法元素"

    def test_backup_creates_bak_file(self, tmp_path, monkeypatch):
        """--backup 创建备份文件."""
        monkeypatch.setattr(migrate_world, "_projects_root", lambda: tmp_path)

        book = "test_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        world_path = mem_dir / "world.json"
        world_path.write_text(json.dumps({"key": "value"}, ensure_ascii=False), encoding="utf-8")

        report = migrate_world._migrate_book(book, apply=True, backup=True)
        assert report["status"] == "migrated"
        assert "backup" in report

        # 备份文件存在
        backup_path = Path(report["backup"])
        assert backup_path.exists()
        # 备份内容是旧数据
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        assert backup_data == {"key": "value"}

    def test_skip_new_format(self, tmp_path, monkeypatch):
        """已新格式 → skip."""
        monkeypatch.setattr(migrate_world, "_projects_root", lambda: tmp_path)

        book = "test_book"
        mem_dir = tmp_path / book / "memory"
        mem_dir.mkdir(parents=True)
        world_path = mem_dir / "world.json"
        world_path.write_text(json.dumps({
            "rules": {"rule_x": {"name": "X"}},
            "raw_notes": [],
            "_legacy": {},
        }, ensure_ascii=False), encoding="utf-8")

        report = migrate_world._migrate_book(book, apply=True)
        assert report["status"] == "skip"
        assert report["reason"] == "already new format"

    def test_skip_no_world_json(self, tmp_path, monkeypatch):
        """没有 world.json → skip."""
        monkeypatch.setattr(migrate_world, "_projects_root", lambda: tmp_path)
        book = "empty_book"
        (tmp_path / book / "memory").mkdir(parents=True)

        report = migrate_world._migrate_book(book)
        assert report["status"] == "skip"
        assert "no world.json" in report["reason"]