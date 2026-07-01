"""
Extract events, characters, foreshadowing, world updates from a chapter.
Returns a structured dict (see memory.merge_extraction).
"""
from __future__ import annotations
import json, re
from .llm import LLM
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER

def extract_from_chapter(chapter_text: str, llm: LLM = None) -> dict:
    """
    Run extraction LLM call on chapter text.
    Returns dict with new_characters, updated_characters, new_events,
    new_foreshadowing, resolved_foreshadowing, world_updates.
    """
    # Truncate if too long (chunk last ~3000 chars = ~2K tokens)
    if len(chapter_text) > 3000:
        chunk = chapter_text[-3000:]
    else:
        chunk = chapter_text

    user_prompt = EXTRACT_USER.format(chapter_text=chunk)

    # Use separate LLM instance for extraction if not passed
    if llm is None:
        from .llm import get_llm
        llm = get_llm()

    raw = llm.complete(
        prompt=user_prompt,
        system=EXTRACT_SYSTEM,
        temperature=0.3,   # Low temp for structured extraction
        max_tokens=4096,
    )

    return parse_extraction(raw)

def parse_extraction(raw: str) -> dict:
    """Parse LLM JSON output, be robust to markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        start = next((i+1 for i,l in enumerate(lines) if l.strip().startswith("```") and i==0 or (i>0 and not lines[i].strip().startswith("```"))), 0)
        end   = next((i for i in range(len(lines)-1, -1, -1) if lines[i].strip().startswith("```")), len(lines))
        raw = "\n".join(lines[start:end]).strip()

    # Strip any text before first {
    first_brace = raw.find("{")
    last_brace  = raw.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        raw = raw[first_brace:last_brace+1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"    [extract] JSON parse error: {e}; returning empty")
        return {
            "new_characters": [],
            "updated_characters": [],
            "new_events": [],
            "new_foreshadowing": [],
            "resolved_foreshadowing": [],
            "world_updates": [],
        }

    # Validate keys
    expected_keys = [
        "new_characters", "updated_characters", "new_events",
        "new_foreshadowing", "resolved_foreshadowing", "world_updates",
    ]
    for k in expected_keys:
        if k not in data:
            data[k] = []
    return data

def extract_from_summary(summary_text: str, llm: LLM) -> dict:
    """
    Run extraction on a summary (used for full-book review phase).
    Only extracts high-level events and major foreshadowing.
    """
    from .prompts import EXTRACT_SYSTEM, EXTRACT_USER
    user_prompt = EXTRACT_USER.format(chapter_text=summary_text[:4000])
    raw = llm.complete(
        prompt=user_prompt,
        system=EXTRACT_SYSTEM,
        temperature=0.3,
        max_tokens=2048,
    )
    return parse_extraction(raw)
