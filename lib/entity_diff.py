"""
entity_diff.py — Per-chapter entity change tracking (v1.3 M4)

Track how characters / events / foreshadows / world_rules changed
when a chapter was written. Stored at:
  projects/<book>/memory/_changelog/<chapter_id>.json

Each changelog entry records:
  - chapter_id, chapter_num
  - entities: {
      character:  {added: [...], updated: [(name, fields_changed)], removed: [...]},
      event:       {added: [...], updated: [...]},
      foreshadow:  {added: [...], updated: [...], resolved: [...]},
      world_rule:  {added: [...], updated: [...]}
    }
  - generated_at (ISO)
  - source: "extract" | "human_edit" | "review_apply"

This powers the chapter page panel "本章节实体变化" and the
reviewer-driven auto-rewrite (review_actions.py).
"""
from __future__ import annotations

import json
import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field, asdict
from difflib import unified_diff

from . import storage
from . import memory
from .entity import (
    Character, Event, Foreshadow, WorldRule,
    ForeshadowStatus, WorldRuleStatus,
)


CHANGELOG_DIR = "_changelog"


def changelog_path(book: str, chapter_id: str) -> Path:
    """projects/<book>/memory/_changelog/<chapter_id>.json"""
    return storage.project_root(book) / "memory" / CHANGELOG_DIR / f"{chapter_id}.json"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


# ── Diff a single entity record (dict → dict) ────────────────────────────

def _diff_dict(before: dict | None, after: dict | None) -> dict:
    """
    Return dict describing the changes between before/after.
    {
      "status": "added" | "updated" | "removed" | "unchanged",
      "before": <dict|None>,
      "after":  <dict|None>,
      "fields_changed": ["field1", "field2", ...]
    }
    """
    if before is None and after is None:
        return {"status": "unchanged", "before": None, "after": None, "fields_changed": []}

    if before is None:
        return {"status": "added", "before": None, "after": after, "fields_changed": []}

    if after is None:
        return {"status": "removed", "before": before, "after": None, "fields_changed": []}

    # both exist → find changed fields
    fields_changed = []
    all_keys = set(before.keys()) | set(after.keys())
    # Skip noisy fields
    skip = {"updated_at", "extracted_at"}
    for k in all_keys:
        if k in skip:
            continue
        b = before.get(k)
        a = after.get(k)
        if b != a:
            fields_changed.append(k)
    status = "updated" if fields_changed else "unchanged"
    return {
        "status": status,
        "before": before,
        "after": after,
        "fields_changed": fields_changed,
    }


# ── Public diff functions ──────────────────────────────────────────────

def diff_characters(before: dict, after: dict) -> dict:
    """
    Compare character dicts (name → {traits, role, ...}).
    Returns: {
      added:   [{name, ...}],
      updated: [{name, fields_changed: [...]}],
      removed: [{name, ...}],
      unchanged_count: int
    }
    """
    before_names = set(before.keys())
    after_names = set(after.keys())

    added = [{"name": n, **after[n]} for n in sorted(after_names - before_names)]
    removed = [{"name": n, **before[n]} for n in sorted(before_names - after_names)]

    updated = []
    unchanged = 0
    for name in sorted(before_names & after_names):
        d = _diff_dict(before[name], after[name])
        if d["status"] == "updated":
            updated.append({
                "name": name,
                "fields_changed": d["fields_changed"],
                "before": {k: before[name].get(k) for k in d["fields_changed"]},
                "after": {k: after[name].get(k) for k in d["fields_changed"]},
            })
        else:
            unchanged += 1

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "unchanged_count": unchanged,
    }


def diff_list_entities(before: list[dict], after: list[dict], key: str = "event") -> dict:
    """
    Compare list-typed entity arrays (events / foreshadows).
    Match by `event` field for events, `foreshadow` for foreshadows, etc.
    Returns: {
      added:   [...],
      updated: [...],
      resolved: [...]    (for foreshadows whose status went from PLANTED→RESOLVED)
      removed: [...],
      unchanged_count: int
    }
    """
    # Identity by (key) value; events/foreshadows don't have stable IDs in current schema
    before_by_key = {item.get(key, ""): item for item in before if item.get(key)}
    after_by_key = {item.get(key, ""): item for item in after if item.get(key)}

    before_keys = set(before_by_key.keys())
    after_keys = set(after_by_key.keys())

    added = [after_by_key[k] for k in sorted(after_keys - before_keys)]
    removed = [before_by_key[k] for k in sorted(before_keys - after_keys)]

    updated = []
    resolved = []
    unchanged = 0
    for k in sorted(before_keys & after_keys):
        d = _diff_dict(before_by_key[k], after_by_key[k])
        if d["status"] == "updated":
            change = {
                key: k,
                "fields_changed": d["fields_changed"],
                "before": {kk: before_by_key[k].get(kk) for kk in d["fields_changed"]},
                "after": {kk: after_by_key[k].get(kk) for kk in d["fields_changed"]},
            }
            # Special: foreshadow status PLANTED → RESOLVED
            if key == "foreshadow" and "status" in d["fields_changed"]:
                if before_by_key[k].get("status") in (ForeshadowStatus.PLANTED.value, ForeshadowStatus.PROGRESS.value) and \
                   after_by_key[k].get("status") == ForeshadowStatus.RESOLVED.value:
                    resolved.append(change)
                    continue
            updated.append(change)
        else:
            unchanged += 1

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "resolved": resolved,  # foreshadows that got resolved (may be empty)
        "unchanged_count": unchanged,
    }


def diff_world_rules(before: dict, after: dict) -> dict:
    """
    World rules stored as {rules: {id: {...}}, raw_notes: [...]}.
    Returns the same shape as diff_characters but with rule-specific keys.
    """
    before_rules = (before or {}).get("rules", {}) or {}
    after_rules = (after or {}).get("rules", {}) or {}

    before_ids = set(before_rules.keys())
    after_ids = set(after_rules.keys())

    added = [{"id": i, **after_rules[i]} for i in sorted(after_ids - before_ids)]
    removed = [{"id": i, **before_rules[i]} for i in sorted(before_ids - after_ids)]

    updated = []
    unchanged = 0
    for rid in sorted(before_ids & after_ids):
        d = _diff_dict(before_rules[rid], after_rules[rid])
        if d["status"] == "updated":
            updated.append({
                "id": rid,
                "fields_changed": d["fields_changed"],
                "before": {k: before_rules[rid].get(k) for k in d["fields_changed"]},
                "after": {k: after_rules[rid].get(k) for k in d["fields_changed"]},
            })
        else:
            unchanged += 1

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "unchanged_count": unchanged,
    }


# ── Snapshot & restore memory state ────────────────────────────────────

def snapshot_memory(book: str) -> dict:
    """Snapshot all 4 memory libs as plain dicts (for diffing later)."""
    return {
        "characters": json.loads(json.dumps(memory.get_characters(book), ensure_ascii=False)),
        "events":     memory.get_events(book)[:],
        "foreshadowing": memory.get_foreshadowing(book)[:],
        "world":      json.loads(json.dumps(memory.get_world(book), ensure_ascii=False)),
    }


def compute_diff(book: str, before_snapshot: dict | None = None) -> dict:
    """
    Compute diff between `before_snapshot` (or load from disk)
    and the CURRENT memory state. Used by chapter write pipeline.
    """
    if before_snapshot is None:
        # Load most recent snapshot from changelog
        before_snapshot = _load_latest_snapshot(book)

    current = snapshot_memory(book)

    if not before_snapshot:
        # First chapter ever: everything is "added"
        return {
            "character": diff_characters({}, current["characters"]),
            "event":     diff_list_entities([], current["events"], "event"),
            "foreshadow": diff_list_entities([], current["foreshadowing"], "foreshadow"),
            "world_rule": diff_world_rules({}, current["world"]),
        }

    return {
        "character":  diff_characters(before_snapshot.get("characters", {}), current["characters"]),
        "event":      diff_list_entities(before_snapshot.get("events", []), current["events"], "event"),
        "foreshadow": diff_list_entities(before_snapshot.get("foreshadowing", []), current["foreshadowing"], "foreshadow"),
        "world_rule": diff_world_rules(before_snapshot.get("world", {}), current["world"]),
    }


def _load_latest_snapshot(book: str) -> dict | None:
    """Find the most recent snapshot stored in any changelog file."""
    changelog_dir = storage.project_root(book) / "memory" / CHANGELOG_DIR
    if not changelog_dir.exists():
        return None
    files = sorted(changelog_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "snapshot_after" in data:
                return data["snapshot_after"]
        except (json.JSONDecodeError, OSError):
            continue
    return None


# ── Write changelog entry ──────────────────────────────────────────────

def record_chapter_changes(
    book: str,
    chapter_id: str,
    chapter_num: int,
    diff: dict,
    before_snapshot: dict,
    after_snapshot: dict | None = None,
    source: str = "extract",
    note: str = "",
) -> dict:
    """
    Persist per-chapter entity change record.
    Idempotent: replaces existing file for chapter_id.
    """
    if after_snapshot is None:
        after_snapshot = snapshot_memory(book)

    entry = {
        "chapter_id": chapter_id,
        "chapter_num": chapter_num,
        "generated_at": _now(),
        "source": source,
        "note": note,
        "entities": diff,
        # snapshots (for re-diffing later or rollback)
        "snapshot_before": before_snapshot,
        "snapshot_after": after_snapshot,
    }

    path = changelog_path(book, chapter_id)
    _ensure_dir(path)
    path.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return entry


# ── Read changelog ──────────────────────────────────────────────────────

def get_chapter_changes(book: str, chapter_id: str) -> dict | None:
    """Load per-chapter entity diff for display on chapter page."""
    path = changelog_path(book, chapter_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_changes_for_chapter_num(book: str, chapter_num: int) -> dict | None:
    """Find changelog for a chapter number (resolves chapter_id from ch_NNN)."""
    chapter_id = f"ch_{chapter_num:03d}"
    return get_chapter_changes(book, chapter_id)


def summarize_changes(diff_entry: dict) -> dict:
    """
    Compact summary for the chapter page header.
    {
      characters:  {added: 2, updated: 1, removed: 0},
      events:      {added: 3, updated: 0, removed: 0},
      foreshadows: {added: 1, updated: 0, resolved: 1, removed: 0},
      world_rules: {added: 0, updated: 1, removed: 0},
      total_changes: 8
    }
    """
    e = diff_entry.get("entities", {})
    char = e.get("character", {})
    ev = e.get("event", {})
    fs = e.get("foreshadow", {})
    wr = e.get("world_rule", {})

    s = {
        "characters":  {"added": len(char.get("added", [])), "updated": len(char.get("updated", [])), "removed": len(char.get("removed", []))},
        "events":      {"added": len(ev.get("added", [])),   "updated": len(ev.get("updated", [])),   "removed": len(ev.get("removed", []))},
        "foreshadows": {"added": len(fs.get("added", [])),   "updated": len(fs.get("updated", [])),   "resolved": len(fs.get("resolved", [])), "removed": len(fs.get("removed", []))},
        "world_rules": {"added": len(wr.get("added", [])),   "updated": len(wr.get("updated", [])),   "removed": len(wr.get("removed", []))},
    }
    s["total_changes"] = sum(
        s["characters"]["added"] + s["characters"]["updated"] + s["characters"]["removed"] +
        s["events"]["added"]     + s["events"]["updated"]     + s["events"]["removed"] +
        s["foreshadows"]["added"] + s["foreshadows"]["updated"] + s["foreshadows"]["resolved"] + s["foreshadows"]["removed"] +
        s["world_rules"]["added"] + s["world_rules"]["updated"] + s["world_rules"]["removed"]
    )
    return s


# ── Hook for pipeline ──────────────────────────────────────────────────

def run_entity_diff_stage(book: str, chapter_num: int, chapter_id: str, before_snapshot: dict) -> dict:
    """
    Pipeline stage: compute & record entity diff for this chapter.
    Returns the recorded changelog entry (or empty diff if before_snapshot is None).
    """
    diff = compute_diff(book, before_snapshot)
    entry = record_chapter_changes(
        book=book,
        chapter_id=chapter_id,
        chapter_num=chapter_num,
        diff=diff,
        before_snapshot=before_snapshot,
        source="extract",
    )
    return entry