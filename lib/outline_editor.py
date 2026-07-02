"""
Outline editor: 节点树 CRUD + 重排 + diff.

Schema (outline.json):
    {
        "meta": {...},
        "volumes": [
            {"id": "vol_1", "title": "...", "summary": "...", "chapters": ["第1章 xxx", ...]},
            ...
        ],
        "chapters": [
            {"id": "ch_001", "vol": "vol_1", "title": "...", "summary": "...",
             "pov": "...", "key_events": [...], "foreshadow": [...]},
            ...
        ],
        "generated_at": "ISO8601"
    }

Operations on chapters[] always trigger sync_volumes_chapters() so that
volumes[].chapters (the user-facing "卷内速览") stays consistent.

Save API: save_outline() best-effort auto-snapshots to version store
(reuse lib.version from M3) — disable when restoring from a version.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

from . import storage


# ── Fields a chapter node may carry ─────────────────────────────────────────

NODE_FIELDS = {"id", "vol", "title", "summary", "pov", "key_events", "foreshadow"}
VOLUME_FIELDS = {"id", "title", "summary", "chapters"}


# ── Loader / Save ───────────────────────────────────────────────────────────

def load_outline_or_empty(book: str) -> dict[str, Any]:
    """Load outline.json; return empty skeleton if missing/damaged."""
    o = storage.read_json(book, "outline.json")
    if o is None:
        return {
            "meta": {"title": book, "target_chapters": 0, "summary": ""},
            "volumes": [],
            "chapters": [],
            "generated_at": "",
        }
    o.setdefault("volumes", [])
    o.setdefault("chapters", [])
    o.setdefault("meta", {})
    return o


def save_outline(book: str, outline: dict, *, auto_snapshot: bool = True) -> None:
    """
    Write outline.json + sync volumes[].chapters from chapters[].
    auto_snapshot=True → trigger M3 version snapshot (best-effort, never raises).
    """
    sync_volumes_chapters(outline)
    if not outline.get("generated_at"):
        outline["generated_at"] = datetime.datetime.now().isoformat()
    storage.write_json(book, "outline.json", outline)
    if auto_snapshot:
        try:
            from .version import create_version
            # create_version expects `content: str` (designed for chapter text);
            # outline is a dict so serialize first.
            content_str = json.dumps(outline, ensure_ascii=False, sort_keys=True)
            create_version(book, "outline.json", content_str, trigger="edit")
        except Exception:
            pass  # best-effort; version store may be absent


# ── Volume CRUD ──────────────────────────────────────────────────────────────

def add_volume(outline: dict, title: str, summary: str = "") -> dict:
    """Append a new volume, auto-assign vol_N id (N=existing+1, skip collisions)."""
    existing = {v.get("id") for v in outline["volumes"]}
    n = len(outline["volumes"]) + 1
    while f"vol_{n}" in existing:
        n += 1
    vid = f"vol_{n}"
    vol = {"id": vid, "title": title, "summary": summary, "chapters": []}
    outline["volumes"].append(vol)
    return vol


def remove_volume(outline: dict, vol_id: str) -> int:
    """
    Remove a volume; reassign its chapters to the first *other* volume.
    If no other volume remains, chapters keep a sentinel vol="" (validate flags it).
    Returns count of chapters reassigned.
    """
    vols = outline["volumes"]
    idx = next((i for i, v in enumerate(vols) if v.get("id") == vol_id), None)
    if idx is None:
        raise ValueError(f"volume {vol_id} not found")
    vols.pop(idx)
    target = next((v.get("id") for v in vols if v.get("id") != vol_id), None)
    reassigned = 0
    for ch in outline["chapters"]:
        if ch.get("vol") == vol_id:
            ch["vol"] = target if target is not None else ""
            reassigned += 1
    return reassigned


def update_volume(outline: dict, vol_id: str, **fields) -> dict:
    """Update mutable volume fields (title/summary). id/chapters are managed elsewhere."""
    vol = next((v for v in outline["volumes"] if v.get("id") == vol_id), None)
    if vol is None:
        raise ValueError(f"volume {vol_id} not found")
    for k, v in fields.items():
        if k in VOLUME_FIELDS and k not in ("id", "chapters"):
            vol[k] = v
    return vol


# ── Chapter node helpers ────────────────────────────────────────────────────

def _new_chapter_id(outline: dict, prefix: str = "ch_") -> str:
    """Generate a unique chapter id like ch_001 (3-digit zero-padded)."""
    existing = {ch.get("id") for ch in outline["chapters"]}
    n = len(outline["chapters"]) + 1
    cid = f"{prefix}{n:03d}"
    while cid in existing:
        n += 1
        cid = f"{prefix}{n:03d}"
    return cid


def _insert_at_vol_position(outline: dict, vol: str, position: int, node: dict) -> None:
    """
    Insert node into outline.chapters[] at the given position *within vol*.
    position is 0-based; clamping to [0, len(siblings)]; works on empty vol (append).
    """
    # Build (chapter_idx_in_full_list, sibling_idx_in_vol) pairs by passing through chapters
    siblings_idx = [i for i, ch in enumerate(outline["chapters"]) if ch.get("vol") == vol]
    if not siblings_idx:
        # Empty vol → append
        outline["chapters"].append(node)
        return
    if position <= 0:
        insert_at = siblings_idx[0]
    elif position >= len(siblings_idx):
        # Insert after the last sibling
        insert_at = siblings_idx[-1] + 1
    else:
        insert_at = siblings_idx[position]
    outline["chapters"].insert(insert_at, node)


def add_node(outline: dict, parent_vol: str, position: int, **fields) -> dict:
    """
    Append a new chapter at (parent_vol, position) — auto-assigns id.
    Returns the new node. Caller still needs save_outline() to persist.
    """
    node = {k: v for k, v in fields.items() if k in NODE_FIELDS and k != "id"}
    node["id"] = _new_chapter_id(outline)
    node["vol"] = parent_vol
    node.setdefault("title", "未命名章节")
    node.setdefault("summary", "")
    node.setdefault("pov", "")
    node.setdefault("key_events", [])
    node.setdefault("foreshadow", [])
    _insert_at_vol_position(outline, parent_vol, position, node)
    return node


def remove_node(outline: dict, ch_id: str) -> dict | None:
    """Remove node by id; returns the removed node or None if not found."""
    for i, ch in enumerate(outline["chapters"]):
        if ch.get("id") == ch_id:
            return outline["chapters"].pop(i)
    return None


def update_node(outline: dict, ch_id: str, **fields) -> dict:
    """Update mutable fields on a chapter node. id cannot change here."""
    node = next((ch for ch in outline["chapters"] if ch.get("id") == ch_id), None)
    if node is None:
        raise ValueError(f"chapter {ch_id} not found")
    for k, v in fields.items():
        if k in NODE_FIELDS and k != "id":
            node[k] = v
    return node


def move_node(outline: dict, ch_id: str, new_vol: str, new_position: int) -> dict:
    """
    Move a chapter to (new_vol, new_position) — works within vol and across vols.
    Returns the moved node.
    """
    node = next((ch for ch in outline["chapters"] if ch.get("id") == ch_id), None)
    if node is None:
        raise ValueError(f"chapter {ch_id} not found")
    outline["chapters"].remove(node)
    node["vol"] = new_vol
    _insert_at_vol_position(outline, new_vol, new_position, node)
    return node


def reorder_nodes(outline: dict, moves: list[dict]) -> list[dict]:
    """
    Apply a batch of moves sequentially; later moves see earlier results.
    Each move = {"ch_id": "...", "new_vol": "...", "new_position": N}.
    Returns the final chapters[].
    """
    for m in moves:
        if "ch_id" not in m or "new_vol" not in m or "new_position" not in m:
            raise ValueError(f"move missing required keys: {m}")
        move_node(outline, m["ch_id"], m["new_vol"], int(m["new_position"]))
    return outline["chapters"]


# ── Sync (volumes[].chapters 派生自 chapters[]) ─────────────────────────────

def sync_volumes_chapters(outline: dict) -> dict:
    """
    Rebuild volumes[].chapters from chapters[] — 按 vol 分组,保留 order.
    volumes[].chapters is for "卷内速览" display only; values are strings
    formatted as "第N章 <title>". Idempotent.
    """
    vol_index: dict = {v.get("id"): v for v in outline["volumes"]}
    for v in outline["volumes"]:
        v["chapters"] = []
    for ch in outline["chapters"]:
        vid = ch.get("vol", "")
        if vid in vol_index and vol_index[vid] is not None:
            slot = len(vol_index[vid]["chapters"]) + 1
            label = f"第{slot}章 {ch.get('title', '未命名')}"
            vol_index[vid]["chapters"].append(label)
    return outline


# ── Validation ──────────────────────────────────────────────────────────────

def validate_outline(outline: dict) -> list[str]:
    """Return list of human-readable errors. Empty = OK."""
    errors: list[str] = []
    seen_v: set = set()
    for v in outline.get("volumes", []):
        vid = v.get("id")
        if not vid:
            errors.append("volume missing id")
            continue
        if vid in seen_v:
            errors.append(f"duplicate volume id: {vid}")
        seen_v.add(vid)
    seen_c: set = set()
    for ch in outline.get("chapters", []):
        cid = ch.get("id")
        if not cid:
            errors.append("chapter missing id")
            continue
        if cid in seen_c:
            errors.append(f"duplicate chapter id: {cid}")
        seen_c.add(cid)
        cv = ch.get("vol")
        if cv not in seen_v:
            errors.append(f"chapter {cid} references unknown/missing vol {cv!r}")
    return errors


# ── Diff (用于大纲版本对比) ─────────────────────────────────────────────────

def _dict_field_diff(old: dict, new: dict, ignore: set | None = None) -> list[str]:
    """Top-level field diff — returns sorted list of field names whose values changed."""
    ignore = ignore or set()
    keys = set(old.keys()) | set(new.keys())
    return sorted(k for k in keys if k not in ignore and old.get(k) != new.get(k))


def _vol_position_index(chapters: list) -> dict:
    """
    Build {ch_id: (vol_id, pos_in_vol)} where pos is 0-based position within vol.
    chapters list order = global order, but position must be computed per vol.
    """
    counters: dict = {}
    index: dict = {}
    for ch in chapters:
        vid = ch.get("vol", "")
        idx = counters.get(vid, 0)
        index[ch.get("id")] = (vid, idx)
        counters[vid] = idx + 1
    return index


def diff_outlines(old: dict, new: dict) -> dict:
    """
    Structural diff between two outline dicts.
    Categories:
      volumes_added: [vol_id, ...]
      volumes_removed: [vol_id, ...]
      volumes_edited: [{vol_id, fields_changed}, ...]
      chapters_added: [ch_id, ...]
      chapters_removed: [ch_id, ...]
      chapters_moved: [{ch_id, from_vol, from_pos, to_vol, to_pos}, ...]
      chapters_edited: [{ch_id, fields_changed}, ...]

    'moved' is independent of 'edited' — moving a chapter from vol_1→vol_2 with the
    same fields shows up as moved only (not edited).
    """
    old_vols = {v.get("id"): v for v in old.get("volumes", [])}
    new_vols = {v.get("id"): v for v in new.get("volumes", [])}
    volumes_added = sorted(set(new_vols) - set(old_vols))
    volumes_removed = sorted(set(old_vols) - set(new_vols))
    volumes_edited = []
    for vid in set(old_vols) & set(new_vols):
        changed = _dict_field_diff(old_vols[vid], new_vols[vid], ignore={"chapters"})
        if changed:
            volumes_edited.append({"vol_id": vid, "fields_changed": changed})

    old_chs = {ch.get("id"): ch for ch in old.get("chapters", [])}
    new_chs = {ch.get("id"): ch for ch in new.get("chapters", [])}
    chapters_added = sorted(set(new_chs) - set(old_chs))
    chapters_removed = sorted(set(old_chs) - set(new_chs))

    chapters_edited = []
    for cid in set(old_chs) & set(new_chs):
        changed = _dict_field_diff(old_chs[cid], new_chs[cid], ignore={"vol"})
        # Only treat as edited if some non-vol field changed. vol changes are moves.
        non_vol = [f for f in changed if f != "vol"]
        if non_vol:
            chapters_edited.append({"ch_id": cid, "fields_changed": non_vol})

    old_pos = _vol_position_index(old.get("chapters", []))
    new_pos = _vol_position_index(new.get("chapters", []))
    chapters_moved = []
    for cid in set(old_chs) & set(new_chs):
        o = old_pos.get(cid, ("", -1))
        n = new_pos.get(cid, ("", -1))
        if o != n:
            chapters_moved.append({
                "ch_id": cid,
                "from_vol": o[0],
                "from_pos": o[1],
                "to_vol": n[0],
                "to_pos": n[1],
            })

    return {
        "volumes_added": volumes_added,
        "volumes_removed": volumes_removed,
        "volumes_edited": volumes_edited,
        "chapters_added": chapters_added,
        "chapters_removed": chapters_removed,
        "chapters_moved": chapters_moved,
        "chapters_edited": chapters_edited,
    }
