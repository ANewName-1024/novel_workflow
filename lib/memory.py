"""
4-library memory system:
  characters  world  events  foreshadowing
Each library is a JSON file under projects/<book>/memory/.
"""
from __future__ import annotations
import json, datetime, copy
from pathlib import Path
from typing import Any

def _mem_path(book: str, lib: str) -> Path:
    return Path(__file__).parent.parent / "projects" / book / "memory" / f"{lib}.json"

def _load(book: str, lib: str) -> dict[str, Any] | list:
    p = _mem_path(book, lib)
    if not p.exists():
        return {} if lib in ("characters", "world") else []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {} if lib in ("characters", "world") else []

def _save(book: str, lib: str, data: Any) -> None:
    p = _mem_path(book, lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── characters ───────────────────────────────────────────────────────────────

def get_characters(book: str) -> dict:
    return _load(book, "characters")

def update_characters(book: str, characters: dict) -> None:
    _save(book, "characters", characters)

def get_characters_summary(book: str) -> str:
    chars = get_characters(book)
    if not chars:
        return "（暂无角色记忆）"
    lines = []
    for name, info in list(chars.items())[:10]:
        traits = info.get("traits", "")
        role   = info.get("role", "")
        lines.append(f"- {name}（{role}）：{traits}")
    return "\n".join(lines) if lines else "（暂无角色记忆）"

# ── world ────────────────────────────────────────────────────────────────────

def get_world(book: str) -> dict:
    return _load(book, "world")

def update_world(book: str, world: dict) -> None:
    _save(book, "world", world)

def get_world_summary(book: str) -> str:
    w = get_world(book)
    if not w:
        return "（暂无世界观记忆）"
    return json.dumps(w, ensure_ascii=False, indent=2)

# ── events ─────────────────────────────────────────────────────────────────

def get_events(book: str) -> list[dict]:
    data = _load(book, "events")
    return data if isinstance(data, list) else []

def update_events(book: str, events: list[dict]) -> None:
    _save(book, "events", events)

def get_events_summary(book: str, max_events: int = 20) -> str:
    evts = get_events(book)
    if not evts:
        return "（暂无事件记忆）"
    lines = []
    for e in evts[-max_events:]:
        lines.append(f"- {e.get('event','')}（{e.get('significance','')}）")
    return "\n".join(lines) if lines else "（暂无事件记忆）"

# ── foreshadowing ───────────────────────────────────────────────────────────

class ForeshadowStatus:
    PLANTED   = "已埋"
    PROGRESS  = "推进中"
    RESOLVED  = "已回收"
    ABANDONED = "已放弃"

def get_foreshadowing(book: str) -> list[dict]:
    data = _load(book, "foreshadowing")
    return data if isinstance(data, list) else []

def update_foreshadowing(book: str, foreshadowing: list[dict]) -> None:
    _save(book, "foreshadowing", foreshadowing)

def mark_resolved(book: str, text: str) -> None:
    """Mark a foreshadow as resolved by text match."""
    fs = get_foreshadowing(book)
    for item in fs:
        if text in item.get("foreshadow", "") and item.get("status") != ForeshadowStatus.RESOLVED:
            item["status"] = ForeshadowStatus.RESOLVED
            item["resolved_at"] = datetime.datetime.now().isoformat()
    update_foreshadowing(book, fs)

def get_foreshadowing_summary(book: str) -> str:
    fs = get_foreshadowing(book)
    if not fs:
        return "（暂无伏笔记忆）"
    lines = []
    for f in fs[-15:]:
        status = f.get("status", ForeshadowStatus.PLANTED)
        lines.append(f"- [{status}] {f.get('foreshadow','')}")
    return "\n".join(lines) if lines else "（暂无伏笔记忆）"

# ── extraction merger ───────────────────────────────────────────────────────

def merge_extraction(book: str, extraction: dict) -> None:
    """
    Merge the result of extract.py's EXTRACT output into all 4 libraries.
    Called after each chapter is written.
    """
    # Characters
    chars = get_characters(book)
    for nc in extraction.get("new_characters", []):
        if nc["name"] not in chars:
            chars[nc["name"]] = nc
    for uc in extraction.get("updated_characters", []):
        name = uc.get("name", "")
        if name in chars:
            # Merge traits
            existing = chars[name].get("traits", "")
            updated  = uc.get("updated_traits", "")
            if updated and updated not in existing:
                chars[name]["traits"] = existing + "；" + updated
            rel = uc.get("relationship_changes", "")
            if rel:
                chars[name]["relationship"] = rel
    update_characters(book, chars)

    # World
    world = get_world(book)
    for wu in extraction.get("world_updates", []):
        key = wu[:10]
        if key not in world:
            world[key] = wu
    update_world(book, world)

    # Events
    events = get_events(book)
    for ne in extraction.get("new_events", []):
        events.append({**ne, "extracted_at": datetime.datetime.now().isoformat()})
    # Keep last 50 events
    update_events(book, events[-50:])

    # Foreshadowing
    fs = get_foreshadowing(book)
    for nf in extraction.get("new_foreshadowing", []):
        # Avoid exact duplicate
        if not any(nf.get("foreshadow","") == f.get("foreshadow","") for f in fs):
            fs.append({**nf, "status": ForeshadowStatus.PLANTED})
    # Mark resolved
    for rf in extraction.get("resolved_foreshadowing", []):
        for f in fs:
            if rf in f.get("foreshadow","") and f.get("status") != ForeshadowStatus.RESOLVED:
                f["status"] = ForeshadowStatus.RESOLVED
                f["resolved_at"] = datetime.datetime.now().isoformat()
    update_foreshadowing(book, fs)
