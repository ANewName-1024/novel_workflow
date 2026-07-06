"""
One-shot migration: scan projects/<book>/ directories, import all JSON metadata
into SQLite (lib.db). Idempotent (uses ON CONFLICT).

Strategy: scan filesystem FIRST, build SQLite SECOND. Don't delete old files.
Original JSONs remain as a fallback if DB ever gets corrupted.

Run: python3 tools/migrate_to_sqlite.py
     python3 tools/migrate_to_sqlite.py --dry-run
     python3 tools/migrate_to_sqlite.py --project <book>
"""
import argparse
import json
import sys
from pathlib import Path

# Make `lib.*` importable when run from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import db, storage
from lib.review_service import REVIEW_STATUS


def migrate_project(root: Path, project_id: str, dry_run: bool = False) -> dict:
    """Import one project's metadata. Returns stats dict."""
    proj_dir = root / project_id
    if not proj_dir.is_dir():
        return {"project": project_id, "error": "directory not found"}

    stats = {"project": project_id, "config": 0, "chapters": 0,
             "reviews": 0, "entities": 0, "outline": 0, "state": 0}

    # ── config ──
    cfg_path = proj_dir / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  [WARN] {project_id}/config.json invalid: {e}", file=sys.stderr)
            cfg = {}
        book_name = cfg.get("book_name", project_id)
        if not cfg.get("created_at"):
            import datetime
            cfg["created_at"] = datetime.datetime.now().isoformat()
        if not dry_run:
            db.upsert_project(root, project_id, book_name, cfg)
        stats["config"] = 1
        print(f"  [OK]   config: {book_name}")

    # ── chapters (meta only, content stays in .md) ──
    chap_dir = proj_dir / "chapters"
    if chap_dir.is_dir():
        for p in sorted(chap_dir.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                import re
                m = re.search(r"^#+\s+(.+)$", text, re.MULTILINE)
                title = m.group(1).strip() if m else p.stem
                words = len(re.findall(r"[\u4e00-\u9fff]+", text))
                eng = len(re.findall(r"[a-zA-Z]{3,}", text))
                wc = words + eng
                body = text[m.end():] if m else text
                lines = [l.strip() for l in body.splitlines() if l.strip()]
                preview = (lines[0][:50] + "…") if lines and len(lines[0]) > 50 else (lines[0] if lines else "")
                st = p.stat()
                if not dry_run:
                    db.upsert_chapter_meta(
                        root, project_id, p.stem, title, wc, preview,
                        str(p.relative_to(root)), st.st_mtime, st.st_size,
                    )
                stats["chapters"] += 1
            except Exception as e:
                print(f"  [WARN] chapter {p.name}: {e}", file=sys.stderr)
        print(f"  [OK]   chapters: {stats['chapters']} files")

    # ── reviews ──
    rev_dir = proj_dir / "reviews"
    if rev_dir.is_dir():
        for p in sorted(rev_dir.glob("*.review.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                ch_id = rec.get("chapter_id", p.stem.replace(".review", ""))
                if not dry_run:
                    db.upsert_review(
                        root, project_id, ch_id,
                        status=rec.get("status", REVIEW_STATUS["AUTO_PASSED"]),
                        auto_severity=rec.get("auto_severity"),
                        auto_issues_count=rec.get("auto_issues_count", 0),
                        auto_result=rec.get("auto_result"),
                        reviewer=rec.get("reviewer"),
                        reviewer_notes=rec.get("reviewer_notes"),
                        v2_chars=rec.get("v2_chars", 0),
                    )
                stats["reviews"] += 1
            except Exception as e:
                print(f"  [WARN] review {p.name}: {e}", file=sys.stderr)
        print(f"  [OK]   reviews: {stats['reviews']} records")

    # ── entities (memory/{characters,events,foreshadowing,world}.json) ──
    mem_dir = proj_dir / "memory"
    if mem_dir.is_dir():
        # Map filename → entity type
        type_map = {
            "characters.json": "character",
            "events.json": "event",
            "foreshadowing.json": "foreshadow",
            "world.json": "world_rule",
        }
        for fname, etype in type_map.items():
            fpath = mem_dir / fname
            if not fpath.exists():
                continue
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else (data.values() if isinstance(data, dict) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or item.get("id") or item.get("title") or "(unnamed)"
                    if not dry_run:
                        db.upsert_entity(
                            root, project_id, etype, name,
                            category=item.get("category"),
                            status=item.get("status"),
                            content=item,
                        )
                    stats["entities"] += 1
            except Exception as e:
                print(f"  [WARN] {fname}: {e}", file=sys.stderr)
        if stats["entities"]:
            print(f"  [OK]   entities: {stats['entities']} rows")

    # ── outline ──
    outline_path = proj_dir / "outline.json"
    if outline_path.exists():
        try:
            outline = json.loads(outline_path.read_text(encoding="utf-8"))
            if not dry_run:
                db.save_outline(root, project_id, outline)
            stats["outline"] = 1
            print(f"  [OK]   outline: {len(outline.get('chapters', []))} nodes")
        except json.JSONDecodeError as e:
            print(f"  [WARN] outline.json invalid: {e}", file=sys.stderr)

    # ── state / progress ──
    state_path = proj_dir / "state.json"
    progress_path = proj_dir / "progress.json"
    state = {}
    progress = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if state or progress:
        if not dry_run:
            db.save_state(root, project_id, state, progress)
        stats["state"] = 1
        print(f"  [OK]   state/progress")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate projects/<book>/* metadata to SQLite")
    parser.add_argument("--root", default=str(storage.ROOT),
                        help="Project root (default: <repo>/projects)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report, don't write to DB")
    parser.add_argument("--project", help="Migrate only this project")
    args = parser.parse_args()

    root = Path(args.root)

    # Init DB
    if not args.dry_run:
        db_path = db.init_db(root)
        print(f"[DB] initialized at: {db_path}")

    # Discover projects
    if args.project:
        projects = [args.project]
    else:
        projects = [d.name for d in root.iterdir() if d.is_dir()]

    print(f"[MIGRATE] scanning {len(projects)} project(s) in {root}")
    print("=" * 60)

    totals = {"config": 0, "chapters": 0, "reviews": 0,
              "entities": 0, "outline": 0, "state": 0}
    for pid in projects:
        print(f"\n[{pid}]")
        s = migrate_project(root, pid, dry_run=args.dry_run)
        for k in totals:
            totals[k] += s.get(k, 0)

    print("\n" + "=" * 60)
    print(f"[SUMMARY] {'(DRY RUN) ' if args.dry_run else ''}migrated:")
    for k, v in totals.items():
        print(f"  {k:12s} {v}")
    if not args.dry_run:
        print(f"\n[DB STATS] {db.stats(root)}")


if __name__ == "__main__":
    main()