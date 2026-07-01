"""
Outline generation and management.
Outputs 3-tier structure: meta → volumes → chapters.
"""
from __future__ import annotations
import json, datetime
from typing import Any
from .llm import LLM
from . import storage, memory

def generate_outline(
    book: str,
    llm: LLM,
    genre: str,
    tone: str,
    main_plot: str,
    style: str,
    protagonist: str,
    antagonist: str,
    target_chapters: int,
    words_per_chapter: int,
    language: str = "zh",
) -> dict[str, Any]:
    """Generate full 3-tier outline from LLM."""
    from .prompts import OUTLINE_SYSTEM, OUTLINE_USER

    user_prompt = OUTLINE_USER.format(
        genre=genre,
        tone=tone,
        main_plot=main_plot,
        style=style,
        protagonist=protagonist,
        antagonist=antagonist,
        target_chapters=target_chapters,
        words_per_chapter=words_per_chapter,
        language=language,
    )

    raw = llm.complete(
        prompt=user_prompt,
        system=OUTLINE_SYSTEM,
        temperature=0.6,
        max_tokens=8192,
    )

    # Strip markdown code fences
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Find first line after opening fence
        start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                start = i + 1
                break
        # Find last line before closing fence
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        raw = "\n".join(lines[start:end]).strip()

    outline = json.loads(raw)
    outline["generated_at"] = datetime.datetime.now().isoformat()

    storage.write_json(book, "outline.json", outline)
    return outline

def save_outline(book: str, outline: dict) -> None:
    """Manually save / update outline."""
    storage.write_json(book, "outline.json", outline)

def load_outline(book: str) -> dict | None:
    return storage.read_json(book, "outline.json")

def get_chapter_info(outline: dict, chapter_num: int) -> dict | None:
    """Get chapter metadata by 1-based number."""
    chapters = outline.get("chapters", [])
    if 0 < chapter_num <= len(chapters):
        return chapters[chapter_num - 1]
    return None

def get_prev_chapter_excerpt(book: str, chapter_num: int, max_chars: int = 600) -> str:
    """Get last ~max_chars of previous chapter for continuity."""
    import re
    if chapter_num <= 1:
        return ""
    prev_id = f"ch_{chapter_num - 1:03d}"
    text = storage.read_chapter(book, prev_id)
    if not text:
        return ""
    # Strip markdown headers
    text = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()
    return text[-max_chars:] if len(text) > max_chars else text

def estimate_context_usage(outline: dict, chapter_num: int) -> dict:
    """
    Estimate token usage for a chapter call.
    Based on show-me-the-story's context budget model.
    """
    chapters_total = outline.get("meta", {}).get("target_chapters", 20)
    # Input context estimate:
    #   prev excerpt  ~600 chars
    #   char/world/events/foreshadow memory ~4K chars
    #   chapter outline ~300 chars
    # Total input ≈ 5K chars ≈ 3.6K tokens (Chinese)
    input_chars_est = 5000
    # Output: words_per_chapter chars → tokens
    wpc = outline.get("meta", {}).get("target_words", 2500)
    output_tokens_est = int(wpc * 1.3)
    return {
        "chapter": chapter_num,
        "total_chapters": chapters_total,
        "input_tokens_est": int(input_chars_est * 0.73),
        "output_tokens_est": output_tokens_est,
        "call_tokens_est": int(input_chars_est * 0.73) + output_tokens_est,
        "ctx_window": 65536,
        "safety_margin": 65536 - (int(input_chars_est * 0.73) + output_tokens_est + 2048),
    }
