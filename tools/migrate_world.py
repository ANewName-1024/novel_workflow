"""
tools/migrate_world.py — world.json 旧格式 → 新格式迁移

旧格式: {key: text} (字符串 dict)
新格式: {rules: {id: WorldRule}, raw_notes: [...], _legacy: {...}}

用法:
    python tools/migrate_world.py                    # 扫描所有项目, dry-run 报告
    python tools/migrate_world.py --book 测试书籍    # 迁移单个项目
    python tools/migrate_world.py --all --apply      # 迁移所有项目 (实际写入)
    python tools/migrate_world.py --all --apply --backup  # 备份 + 迁移
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _project_root() -> Path:
    """novel_workflow/ 根目录."""
    return Path(__file__).resolve().parent.parent


def _projects_root() -> Path:
    return _project_root() / "projects"


def _list_books() -> list[str]:
    """返回所有项目名 (有 config.json 的子目录)."""
    proj_root = _projects_root()
    if not proj_root.exists():
        return []
    return sorted([
        p.name for p in proj_root.iterdir()
        if p.is_dir() and (p / "config.json").exists()
    ])


def _detect_format(world_data) -> str:
    """检测 world.json 当前格式: 'legacy' / 'new' / 'empty'."""
    if not world_data:
        return "empty"
    if "rules" in world_data:
        return "new"
    # 旧格式: {key: str}
    if all(isinstance(v, str) for v in world_data.values()):
        return "legacy"
    return "mixed"


def _migrate_book(book: str, apply: bool = False, backup: bool = False) -> dict:
    """迁移单个项目. 返回报告."""
    world_path = _projects_root() / book / "memory" / "world.json"
    if not world_path.exists():
        return {"book": book, "status": "skip", "reason": "no world.json"}

    raw = world_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"book": book, "status": "error", "reason": f"JSON parse: {e}"}

    fmt = _detect_format(data)
    report = {"book": book, "format": fmt, "rules_count": 0}

    if fmt == "new":
        report["status"] = "skip"
        report["reason"] = "already new format"
        report["rules_count"] = len(data.get("rules", {}))
        return report

    if fmt == "empty":
        report["status"] = "skip"
        report["reason"] = "empty"
        return report

    # legacy 或 mixed → 迁移
    legacy = {k: v for k, v in data.items() if isinstance(v, str)}
    new_data = {
        "rules": {},
        "raw_notes": list(legacy.values()),
        "_legacy": legacy,
    }
    report["legacy_count"] = len(legacy)

    # 生成 WorldRule (草案态)
    from lib.entity import WorldRule
    for k, v in legacy.items():
        try:
            wr = WorldRule(
                name=k[:30] or k[:30],
                category="其他",
                description=v,
                status="草案",
                notes="由 migrate_world.py 自动迁移, 请人工确认",
            )
            new_data["rules"][wr.id] = wr.to_dict()
        except ValueError as e:
            report.setdefault("skipped", []).append({"key": k, "reason": str(e)})

    report["rules_count"] = len(new_data["rules"])

    if apply:
        if backup:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = world_path.with_suffix(f".json.bak.{ts}")
            shutil.copy2(world_path, backup_path)
            report["backup"] = str(backup_path)
        world_path.write_text(
            json.dumps(new_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["status"] = "migrated"
    else:
        report["status"] = "dry-run"

    return report


def main():
    parser = argparse.ArgumentParser(description="world.json 旧→新格式迁移")
    parser.add_argument("--book", help="指定项目名")
    parser.add_argument("--all", action="store_true", help="迁移所有项目")
    parser.add_argument("--apply", action="store_true", help="实际写入 (默认 dry-run)")
    parser.add_argument("--backup", action="store_true", help="覆盖前备份到 world.json.bak.<ts>")
    args = parser.parse_args()

    if not args.book and not args.all:
        parser.error("必须指定 --book 或 --all")

    books = [args.book] if args.book else _list_books()
    mode = "APPLY" if args.apply else "DRY-RUN"
    backup = "with backup" if args.backup else "no backup"

    print(f"=== world.json 迁移 ({mode}, {backup}) ===")
    print(f"    扫描 {len(books)} 个项目\n")

    total_migrated = 0
    total_rules = 0
    for book in books:
        report = _migrate_book(book, apply=args.apply, backup=args.backup)
        status = report["status"]
        rules = report.get("rules_count", 0)
        if status == "migrated":
            total_migrated += 1
            total_rules += rules
        print(f"  [{status:>10}] {book}: {rules} rules  {report.get('reason', '')}")

    print()
    print(f"=== 汇总 ===")
    print(f"  迁移: {total_migrated} 项目 / {total_rules} WorldRule")
    if not args.apply:
        print(f"  ⚠️  DRY-RUN 模式, 没写文件. 实际写入加 --apply")


if __name__ == "__main__":
    main()