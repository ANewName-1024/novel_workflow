"""
Chapter review service: state machine + audit trail for manual review.

v1.3 M6: dual-write to SQLite (lib.db). File kept for git diff + fallback.

Workflow:
  1. After each chapter write, an auto-review record is created based on
     self-check severity.
  2. Human (or scheduled job) reviews flagged chapters and marks them:
     approved / needs_rewrite / human_edited / false_positive.
  3. Audit log captures all state transitions chronologically.

Storage layout under projects/<book>/reviews/:
  ├── ch_001.review.json     # per-chapter review record + history
  ├── ch_002.review.json
  ├── ...
  ├── ch_001.v2.md           # optional human-edited version
  └── audit.log              # chronological log (text)

Status states:
  auto_passed       - auto self-check severity=none/minor (no review needed)
  pending_review    - auto-flagged (moderate/critical), awaiting human
  approved          - human approved as-is
  needs_rewrite     - human rejected, marked for re-write
  human_edited      - human provided edited text (v2.md present)
  false_positive    - human disagrees with auto-flag, archived
"""
from __future__ import annotations
import json, datetime
from pathlib import Path
from typing import Optional
from . import storage, self_check as scmod

REVIEW_STATUS = {
    "AUTO_PASSED":    "auto_passed",
    "PENDING_REVIEW": "pending_review",
    "APPROVED":       "approved",
    "NEEDS_REWRITE":  "needs_rewrite",
    "HUMAN_EDITED":   "human_edited",
    "FALSE_POSITIVE": "false_positive",
}

# Severity → initial status mapping
SEVERITY_TO_STATUS = {
    "none":     REVIEW_STATUS["AUTO_PASSED"],
    "minor":    REVIEW_STATUS["AUTO_PASSED"],
    "moderate": REVIEW_STATUS["PENDING_REVIEW"],
    "critical": REVIEW_STATUS["PENDING_REVIEW"],
    "unknown":  REVIEW_STATUS["PENDING_REVIEW"],  # conservative default
}

# ── paths ─────────────────────────────────────────────────────────────────

def review_dir(book: str) -> Path:
    d = storage.project_root(book) / "reviews"
    d.mkdir(exist_ok=True)
    return d

def review_path(book: str, chapter_id: str) -> Path:
    return review_dir(book) / f"{chapter_id}.review.json"

def edited_path(book: str, chapter_id: str) -> Path:
    """Where human-edited versions live (ch_001.v2.md, v3.md, ...)."""
    return review_dir(book) / f"{chapter_id}.v2.md"

def audit_log_path(book: str) -> Path:
    return review_dir(book) / "audit.log"

# ── CRUD on review records (SQLite + file dual-write) ─────────────────────

def _empty_record(chapter_id: str) -> dict:
    return {
        "chapter_id": chapter_id,
        "status": REVIEW_STATUS["AUTO_PASSED"],
        "auto_severity": None,
        "auto_issues_count": 0,
        "auto_result": None,
        "reviewer": None,
        "reviewer_notes": None,
        "reviewed_at": None,
        "history": [],
        "created_at": None,
        "updated_at": None,
    }

def get_review(book: str, chapter_id: str) -> dict | None:
    """Read review. SQLite first, .review.json fallback."""
    try:
        from . import db as _dbmod
        r = _dbmod.get_review(storage.ROOT, book, chapter_id)
        if r:
            return r
    except Exception:
        pass
    p = review_path(book, chapter_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def save_review(book: str, record: dict) -> None:
    """Save review. Dual-write: file (for git) + SQLite (for fast query)."""
    record["updated_at"] = datetime.datetime.now().isoformat()
    chapter_id = record.get("chapter_id", "")
    # File write
    review_path(book, chapter_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # SQLite write
    try:
        from . import db as _dbmod
        _dbmod.upsert_review(
            storage.ROOT, book, chapter_id,
            status=record.get("status", "pending_review"),
            auto_severity=record.get("auto_severity"),
            auto_issues_count=record.get("auto_issues_count", 0),
            auto_result=record.get("auto_result"),
            reviewer=record.get("reviewer"),
            reviewer_notes=record.get("reviewer_notes"),
            v2_chars=record.get("v2_chars", 0),
        )
    except Exception:
        pass

def append_audit(book: str, chapter_id: str, action: str, by: str, notes: str = "") -> None:
    """Append a line to audit.log."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] ch={chapter_id} | action={action} | by={by}"
    if notes:
        notes_short = notes[:200].replace("\n", " ")
        line += f" | notes={notes_short}"
    with audit_log_path(book).open("a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── transitions ────────────────────────────────────────────────────────────

def auto_flag(book: str, chapter_id: str, self_check_result: dict, by: str = "AI") -> dict:
    """Called by chapter.py post-write pipeline after self-check."""
    sev = self_check_result.get("severity", "unknown")
    issues_count = sum(
        len(self_check_result.get(k, []))
        for k in ("character_inconsistency", "timeline_conflict",
                  "location_or_item_conflict", "foreshadow_problem",
                  "personality_drift")
    )
    new_status = SEVERITY_TO_STATUS.get(sev, REVIEW_STATUS["PENDING_REVIEW"])

    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["chapter_id"]      = chapter_id
    record["auto_severity"]   = sev
    record["auto_issues_count"] = issues_count
    record["auto_result"]     = self_check_result
    record["status"]          = new_status
    record.setdefault("created_at", datetime.datetime.now().isoformat())

    record["history"].append({
        "at":    datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "auto_flagged",
        "by":    by,
        "severity": sev,
        "issues_count": issues_count,
        "new_status": new_status,
    })

    save_review(book, record)
    append_audit(book, chapter_id, "auto_flagged", by,
                 notes=f"severity={sev} issues={issues_count} → {new_status}")
    return record

def approve(book: str, chapter_id: str, reviewer: str, notes: str = "") -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["APPROVED"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "approved",
        "by": reviewer,
        "notes": notes,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "approved", reviewer, notes)
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        pass
    return record

def reject(book: str, chapter_id: str, reviewer: str, reason: str) -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["NEEDS_REWRITE"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = reason
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "needs_rewrite",
        "by": reviewer,
        "reason": reason,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "needs_rewrite", reviewer, reason)
    return record

def edit(book: str, chapter_id: str, reviewer: str, new_text: str, notes: str = "") -> dict:
    """Save human-edited version to reviews/<chapter_id>.v2.md."""
    edited_path(book, chapter_id).write_text(new_text, encoding="utf-8")
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["HUMAN_EDITED"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "human_edited",
        "by": reviewer,
        "notes": notes,
        "edited_chars": len(new_text),
    })
    save_review(book, record)
    append_audit(book, chapter_id, "human_edited", reviewer,
                 notes=f"chars={len(new_text)} {notes}")
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        pass
    return record

def mark_false_positive(book: str, chapter_id: str, reviewer: str, notes: str) -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["FALSE_POSITIVE"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "false_positive",
        "by": reviewer,
        "notes": notes,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "false_positive", reviewer, notes)
    # L51 修复: false_positive 也算 review 过, 同步 mark chapter completed
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        pass
    return record

def apply_edit_to_chapter(book: str, chapter_id: str) -> bool:
    """Replace chapters/<chapter_id>.md with reviews/<chapter_id>.v2.md (after approval)."""
    src = edited_path(book, chapter_id)
    if not src.exists():
        return False
    dst = storage.chapters_dir(book) / f"{chapter_id}.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        pass
    return True

# ── queries (SQLite first, file fallback) ──────────────────────────────────

def get_review_queue(book: str) -> list[dict]:
    """Return all chapters with status pending_review or needs_rewrite. SQLite first."""
    try:
        from . import db as _dbmod
        rows = _dbmod.list_reviews(storage.ROOT, book, status="pending_review")
        rows2 = _dbmod.list_reviews(storage.ROOT, book, status="needs_rewrite")
        out = rows + rows2
        if out:
            # 兼容 layer: DB schema 用 ch_id, 上层 API 用 chapter_id
            for r in out:
                if "chapter_id" not in r and "ch_id" in r:
                    r["chapter_id"] = r["ch_id"]
            return sorted(out, key=lambda r: r.get("chapter_id") or r.get("ch_id", ""))
    except Exception:
        pass
    out = []
    for p in sorted(review_dir(book).glob("ch_*.review.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        s = r.get("status")
        if s in ("pending_review", "needs_rewrite"):
            out.append(r)
    return out

def get_review_stats(book: str) -> dict:
    """Aggregate counts. SQLite first, file fallback."""
    try:
        from . import db as _dbmod
        return _dbmod.review_stats(storage.ROOT, book)
    except Exception:
        pass
    counts = {v: 0 for v in REVIEW_STATUS.values()}
    counts["total"] = 0
    for p in review_dir(book).glob("ch_*.review.json"):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        s = r.get("status", "unknown")
        if s in counts:
            counts[s] += 1
        counts["total"] += 1
    return counts


def render_queue(book: str, queue: list[dict]) -> str:
    """Pretty-print the review queue for CLI display."""
    if not queue:
        return "✓ 评审队列为空 (所有章节均已审核或自动通过)"
    lines = [f"\n{'═' * 60}", f"  评审队列 ({len(queue)} 章待审)", f"{'═' * 60}"]
    for r in queue:
        sev = r.get("auto_severity", "?")
        cnt = r.get("auto_issues_count", 0)
        st  = r.get("status", "?")
        rev = r.get("reviewer") or "-"
        lines.append(f"  {r['chapter_id']}  [{st}]  severity={sev}  issues={cnt}  reviewer={rev}")
    lines.append(f"\n查看详情: novel.py review-show <书名> <chapter_id>")
    return "\n".join(lines)

def format_review_record(book: str, record: dict, include_chapter: bool = False,
                          chapter_text_chars: int = 800) -> str:
    """Pretty-print a review record + (optionally) the chapter text."""
    lines = []
    lines.append(f"{'═' * 60}")
    lines.append(f"  {record['chapter_id']} - 评审记录")
    lines.append(f"{'═' * 60}")
    lines.append(f"  状态: {record['status']}")
    if record.get("auto_severity"):
        lines.append(f"  自检严重度: {record['auto_severity']}")
    if record.get("auto_issues_count"):
        lines.append(f"  发现问题: {record['auto_issues_count']} 项")
    if record.get("reviewer"):
        lines.append(f"  审核人: {record['reviewer']}")
    if record.get("reviewed_at"):
        lines.append(f"  审核时间: {record['reviewed_at']}")
    if record.get("reviewer_notes"):
        lines.append(f"  审核备注: {record['reviewer_notes']}")

    lines.append(f"\n  历史 ({len(record.get('history',[]))} 条):")
    for ev in record.get("history", []):
        lines.append(f"    [{ev.get('at','')}] {ev.get('action','?')} by {ev.get('by','?')}")

    ar = record.get("auto_result") or {}
    flagged = []
    for key, label in (
        ("character_inconsistency", "角色一致性"),
        ("timeline_conflict", "时间线"),
        ("location_or_item_conflict", "地点/物品"),
        ("foreshadow_problem", "伏笔"),
        ("personality_drift", "性格漂移"),
    ):
        items = ar.get(key, [])
        if items:
            flagged.append(f"  {label} ({len(items)} 项):")
            for it in items[:3]:
                q = it.get("quote", "")[:80]
                lines_nl = q.replace("\n", " ")
                flagged.append(f"    • {it.get('issue','')[:120]}")
                if lines_nl:
                    flagged.append(f"      「{lines_nl}」")
    if flagged:
        lines.append(f"\n  自检报告摘录:")
        lines.extend(flagged)

    if include_chapter:
        text = storage.read_chapter(book, record["chapter_id"])
        if text:
            lines.append(f"\n  章节正文 ({len(text)} 字):")
            lines.append(f"  {'─' * 50}")
            lines.append(text[:chapter_text_chars])
            if len(text) > chapter_text_chars:
                lines.append(f"  ... (后续省略 {len(text) - chapter_text_chars} 字)")
            lines.append(f"  {'─' * 50}")

    return "\n".join(lines)
