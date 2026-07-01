"""
Rolling chapter summaries: 200-字 narrative summaries, one per chapter.
Stored under projects/<book>/summaries/<chapter_id>.txt.

These are the single most important tool for long-novel coherence:
they compress the entire novel into per-chapter anchors that fit
in the context window forever.
"""
from __future__ import annotations
from pathlib import Path
from .llm import LLM
from . import storage

SUMMARY_PROMPT = """你是一位精确的小说档案员。

请基于【章节正文】生成 200 字以内的【叙事摘要】。

严格要求:
1. 第三人称,过去时
2. 必须包含:
   - 本章关键情节转折 (1-2 句)
   - 主角状态变化 (情感/位置/关系)
   - 时间推进 (本章距开篇过了多久)
   - 本章结束时留下的悬念或转折点
3. 不评论,不分析,不抒情,只叙事
4. 不要重复章名,直接写内容
5. 输出纯文本,不要标题,不要 Markdown,不要 JSON

【章节正文】
{chapter_text}

【输出】(200 字以内,纯文本):"""

def generate_chapter_summary(
    book: str,
    chapter_id: str,
    llm: LLM,
    max_chars: int = 200,
) -> str:
    """
    Generate (or regenerate) a 200-char narrative summary for one chapter.
    Stored at projects/<book>/summaries/<chapter_id>.txt
    """
    text = storage.read_chapter(book, chapter_id)
    if not text:
        raise FileNotFoundError(f"Chapter {chapter_id} not found")

    # Strip markdown headers before sending
    import re
    clean = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()

    summary = llm.complete(
        prompt=SUMMARY_PROMPT.format(chapter_text=clean[:5000]),
        system="你是一位精确的小说档案员。",
        temperature=0.3,   # Low temp → consistent summaries
        max_tokens=400,
    )

    # Clean up
    summary = summary.strip()
    if summary.startswith("```"):
        lines = summary.splitlines()
        summary = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    # Hard cap at max_chars (Chinese = 1 char, English ~4 chars/word)
    if len(summary) > max_chars * 1.5:
        summary = summary[:max_chars * 2]

    # Save
    p = storage.summaries_dir(book) / f"{chapter_id}.txt"
    p.write_text(summary, encoding="utf-8")
    return summary

def get_chapter_summary(book: str, chapter_id: str) -> str | None:
    p = storage.summaries_dir(book) / f"{chapter_id}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip()

def get_all_chapter_summaries(book: str) -> list[dict]:
    """
    Return [{id, summary, ch_num}, ...] sorted by chapter order.
    Skips chapters whose summary hasn't been generated.
    """
    out = []
    for p in sorted(storage.summaries_dir(book).glob("ch_*.txt")):
        ch_id = p.stem
        # Parse ch_num from "ch_001"
        try:
            ch_num = int(ch_id.split("_")[1])
        except (IndexError, ValueError):
            continue
        out.append({
            "id": ch_id,
            "ch_num": ch_num,
            "summary": p.read_text(encoding="utf-8").strip(),
        })
    return sorted(out, key=lambda x: x["ch_num"])

def get_recent_summaries(book: str, before_chapter: int, n: int = 5) -> list[dict]:
    """
    Return the N most recent summaries BEFORE `before_chapter`.
    Used to feed earlier-chapter narrative into current chapter's context.
    """
    all_sums = get_all_chapter_summaries(book)
    earlier = [s for s in all_sums if s["ch_num"] < before_chapter]
    return earlier[-n:]

def get_summaries_text(book: str, before_chapter: int, n: int = 5) -> str:
    """Format recent summaries as a prompt-ready block."""
    sums = get_recent_summaries(book, before_chapter, n)
    if not sums:
        return "（尚无可用摘要）"
    lines = []
    for s in sums:
        lines.append(f"[第{s['ch_num']:02d}章] {s['summary']}")
    return "\n".join(lines)