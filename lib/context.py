"""
Sliding-window context builder for chapter writing.

Strategy (budget: 65K ctx, output: 8K):
  - Chapter 1:     no prev
  - Chapter 2-3:   all prev chapters full text
  - Chapter 4-10:  recent 3 chapters full text + last 3 summaries
  - Chapter 11-20: recent 2 chapters full text + last 5 summaries + all earlier summaries

Always included:
  - Style anchor (after ch_001 exists)
  - Current state snapshot
  - 4-library memory
  - This chapter's outline + foreshadow
  - Hard constraints (must-resolve foreshadows, banned phrases)
"""
from __future__ import annotations
import re
from . import storage, memory, summary, state, style

# ── recent full chapters ──────────────────────────────────────────────────

def get_full_chapters_text(book: str, start_ch: int, end_ch: int) -> list[dict]:
    """Return [{id, num, title, text}, ...] for chapters [start_ch, end_ch]."""
    out = []
    for n in range(start_ch, end_ch + 1):
        ch_id = f"ch_{n:03d}"
        text  = storage.read_chapter(book, ch_id)
        if not text:
            continue
        # Strip markdown headers
        clean = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()
        # Extract title from first H1/H2 if present
        m = re.search(r"^#+\s+(第\s*\d+\s*章[^\n]*)", text, re.MULTILINE)
        title = m.group(1) if m else f"第{n}章"
        out.append({"id": ch_id, "num": n, "title": title, "text": clean})
    return out

def get_prev_chapter_full_text(book: str, current_ch: int) -> str:
    """Backwards-compat helper: return full text of previous chapter."""
    if current_ch <= 1:
        return ""
    chs = get_full_chapters_text(book, current_ch - 1, current_ch - 1)
    return chs[0]["text"] if chs else ""

# ── sliding window logic ──────────────────────────────────────────────────

def _window_strategy(chapter_num: int) -> dict:
    """
    Decide how many prev chapters to include as full text,
    and how many summaries to include.
    """
    if chapter_num <= 1:
        return {"full_prev_count": 0, "summary_count": 0}
    if chapter_num <= 3:
        # Early: 全部全文, 不要摘要
        return {"full_prev_count": chapter_num - 1, "summary_count": 0}
    if chapter_num <= 10:
        # Mid: 最近 3 章全文 + 最近 3 章摘要
        return {"full_prev_count": 3, "summary_count": 3}
    # Late: 最近 2 章全文 + 最近 5 章摘要
    return {"full_prev_count": 2, "summary_count": 5}

# ── main builder ──────────────────────────────────────────────────────────

def build_writing_context(book: str, chapter_num: int) -> dict:
    """
    Build all context blocks for writing chapter_num.
    Returns a dict of named blocks ready for prompt formatting.
    """
    cfg = storage.read_json(book, "config.json") or {}

    # ── sliding window: full prev chapters ──
    win = _window_strategy(chapter_num)
    full_chs = []
    if win["full_prev_count"] > 0:
        start = max(1, chapter_num - win["full_prev_count"])
        full_chs = get_full_chapters_text(book, start, chapter_num - 1)

    full_chs_block = ""
    if full_chs:
        blocks = []
        for c in full_chs:
            blocks.append(f"=== {c['title']} ===\n{c['text']}")
        full_chs_block = "\n\n".join(blocks)
    else:
        full_chs_block = "（首章，无上文）"

    # ── summaries block ──
    summaries_block = summary.get_summaries_text(
        book, before_chapter=chapter_num, n=win["summary_count"]
    )

    # ── memory ──
    mem_chars = memory.get_characters_summary(book)
    mem_world = memory.get_world_summary(book)
    mem_evts  = memory.get_events_summary(book)
    mem_fs    = memory.get_foreshadowing_summary(book)

    # ── state snapshot ──
    state_block = state.get_state_text(book)

    # ── style anchor ──
    style_block = style.get_style_text(book)

    # ── chapter outline (this chapter) ──
    outline = storage.read_json(book, "outline.json") or {}
    chapters = outline.get("chapters", [])
    ch_info = chapters[chapter_num - 1] if 0 < chapter_num <= len(chapters) else {}

    ch_id       = ch_info.get("id", f"ch_{chapter_num:03d}")
    ch_title    = ch_info.get("title", f"第{chapter_num}章")
    pov         = ch_info.get("pov", "全知")
    key_evts    = ch_info.get("key_events", []) or []
    foreshadow  = ch_info.get("foreshadow", []) or []
    ch_outline  = ch_info.get("summary", "")

    # ── hard constraints ──
    # Foreshadows that MUST be addressed (planted, not yet resolved)
    all_fs = memory.get_foreshadowing(book)
    must_plant_or_progress = [
        f.get("foreshadow", "") for f in all_fs
        if f.get("status") in ("已埋", "推进中")
    ][:5]

    # Resolved foreshadows that must NOT be re-used
    resolved = [
        f.get("foreshadow", "") for f in all_fs
        if f.get("status") == "已回收"
    ][:5]

    constraints_block = _build_constraints(must_plant_or_progress, resolved)

    return {
        # Vars used by CHAPTER_SYSTEM template
        "chapter_id":              ch_id,
        "chapter_title":           ch_title,
        "chapter_num":             chapter_num,
        "pov":                     pov,
        "key_events":              "；".join(key_evts) or "推进主线",
        "foreshadow":              "；".join(foreshadow) or "无",
        "memory_characters":       mem_chars,
        "memory_world":            mem_world,
        "memory_events":           mem_evts,
        "memory_foreshadowing":    mem_fs,
        # New blocks
        "recent_full_chapters":    full_chs_block,
        "chapter_summaries":       summaries_block,
        "current_state":           state_block,
        "style_anchor":            style_block,
        "hard_constraints":        constraints_block,
        # For novel.py status output
        "context_window_strategy": win,
    }

def _build_constraints(must_plant_or_progress: list[str], resolved: list[str]) -> str:
    lines = []
    if must_plant_or_progress:
        lines.append("【本章应推进/回收的活跃伏笔】（未完成会被审阅判定为质量问题）:")
        for f in must_plant_or_progress:
            lines.append(f"  ⏳ {f}")
    if resolved:
        lines.append("\n【已回收伏笔 - 禁止重复使用】:")
        for f in resolved:
            lines.append(f"  ✓ {f}")
    if not lines:
        return "（暂无硬性约束）"
    return "\n".join(lines)


def estimate_context_tokens(context: dict, llm=None) -> int:
    """Estimate total input tokens for a context dict (rough)."""
    text_parts = [
        context.get("recent_full_chapters", ""),
        context.get("chapter_summaries", ""),
        context.get("memory_characters", ""),
        context.get("memory_world", ""),
        context.get("memory_events", ""),
        context.get("memory_foreshadowing", ""),
        context.get("current_state", ""),
        context.get("style_anchor", ""),
        context.get("hard_constraints", ""),
        context.get("chapter_outline", "") or context.get("foreshadow", ""),
    ]
    total_chars = sum(len(p) for p in text_parts)
    # Chinese: ~1.4 chars/token average (mixed CJK + ASCII)
    return int(total_chars / 1.4)