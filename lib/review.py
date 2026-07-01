"""
Chapter and full-book review: consistency, AI-voice detection, improvement tips.
"""
from __future__ import annotations
import json
from .llm import LLM
from .prompts import REVIEW_SYSTEM, REVIEW_USER, FULL_REVIEW_SYSTEM, FULL_REVIEW_USER
from . import storage

def review_chapter(book: str, chapter_id: str, llm: LLM) -> str:
    """
    Run review on a single chapter.
    Returns the review text.
    """
    text = storage.read_chapter(book, chapter_id)
    if not text:
        raise FileNotFoundError(f"Chapter {chapter_id} not found")

    user_prompt = REVIEW_USER.format(chapter_text=text[:6000])  # Truncate

    review = llm.complete(
        prompt=user_prompt,
        system=REVIEW_SYSTEM,
        temperature=0.3,
        max_tokens=2048,
    )
    # Save review
    rev_path = storage.project_root(book) / "reviews" / f"{chapter_id}.md"
    rev_path.parent.mkdir(exist_ok=True)
    rev_path.write_text(review, encoding="utf-8")
    return review

def review_all_chapters(book: str, llm: LLM) -> dict[str, str]:
    """
    Run review on all completed chapters.
    Returns {chapter_id: review_text}.
    """
    chapters = storage.list_chapters(book)
    results = {}
    for ch in chapters:
        print(f"  Reviewing {ch['id']}: {ch['title']}")
        try:
            results[ch["id"]] = review_chapter(book, ch["id"], llm)
        except Exception as e:
            print(f"    ✗ Failed: {e}")
            results[ch["id"]] = f"[Review failed: {e}]"
    return results

def full_book_review(book: str, llm: LLM) -> str:
    """
    Run full-book final review.
    Reads chapter list and a combined excerpt.
    """
    chapters = storage.list_chapters(book)
    if not chapters:
        raise ValueError("No chapters found for review")

    # Build chapter list summary
    chapter_list = "\n".join(
        f"{i+1}. [{ch['id']}] {ch['title']} (~{ch['word_count']}字)"
        for i, ch in enumerate(chapters)
    )

    # Combine last 3 chapters for recent context
    recent = []
    for ch in chapters[-3:]:
        text = storage.read_chapter(book, ch["id"]) or ""
        recent.append(f"=== {ch['title']} ===\n" + text[-1000:])
    recent_text = "\n\n".join(recent)

    user_prompt = (FULL_REVIEW_USER.format(chapter_list=chapter_list)
                   + f"\n\n【近3章正文片段】\n{recent_text}")

    review = llm.complete(
        prompt=user_prompt,
        system=FULL_REVIEW_SYSTEM,
        temperature=0.4,
        max_tokens=4096,
    )

    # Save
    rev_path = storage.project_root(book) / "reviews" / "full_book_review.md"
    rev_path.parent.mkdir(exist_ok=True)
    rev_path.write_text(review, encoding="utf-8")
    return review
