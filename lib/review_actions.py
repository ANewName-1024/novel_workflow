"""
review_actions.py — Apply reviewer feedback to auto-rewrite chapter (v1.3 M4)

Workflow:
  1. Reviewer comments are collected via lib/comments.py
  2. When `apply_feedback_to_chapter(book, ch)` is called:
     - Pull all unresolved comments for the chapter
     - Pull reviewer_notes from review_service (approve/reject reasons)
     - Build a structured feedback prompt for the LLM
     - Ask LLM to rewrite the chapter, preserving good parts and fixing issues
     - Save as ch_NNN.v3.md (or next available version)
     - Record a changelog entry with source="review_apply"
  3. The reviewer can then approve the rewrite (sets status to human_edited)

This integrates with the existing version control (lib/version.py) by
saving under reviews/<chapter_id>.vN.md, matching the human-edit pattern.
"""
from __future__ import annotations

import json
import datetime
import re
from pathlib import Path
from typing import Any

from . import storage, comments, review_service, memory, entity_diff, prompts
from .llm import LLM, get_llm
from .prompts import CHAPTER_SYSTEM


# ── Prompt building ────────────────────────────────────────────────────

REVIEW_FEEDBACK_SYSTEM = """你是资深长篇小说编辑，负责根据评审反馈修改章节。
你的任务：
1. 仔细阅读【原章节正文】和【评审反馈】
2. 保留原文中写得好的部分（情节氛围、人物基线、关键事件）
3. 仅修复评审指出的具体问题
4. 不要偏离大纲（下一章规划见【大纲摘要】）
5. 如果评审要求修改人物行为，参考【人物基线】
6. 如果评审指出与已确立的世界规则冲突，参考【世界规则】保持一致

输出格式：直接输出修改后的完整章节正文（Markdown H2 标题开头），不要任何解释。"""


REVIEW_FEEDBACK_USER = """## 评审反馈

{feedback_block}

## 大纲摘要
{outline_summary}

## 人物基线
{characters_block}

## 世界规则
{world_rules_block}

## 原章节正文
{chapter_text}

## 任务

请根据上方【评审反馈】修改章节正文。输出修改后的完整章节（Markdown H2 标题开头），不附加任何说明。"""


def _build_feedback_block(book: str, chapter_id: str, record: dict | None) -> str:
    """Combine comments + reviewer_notes into a single feedback prompt block."""
    lines = []

    # 1) Comments
    chap_comments = comments.list_comments(book, chapter_id)
    if chap_comments:
        lines.append("### 评论意见")
        for c in chap_comments:
            author = c.get("author", "?")
            text = c.get("text", "")
            line_no = c.get("line")
            line_str = f" (第{line_no}行)" if line_no else ""
            lines.append(f"- [{author}{line_str}]: {text}")

    # 2) Reviewer notes from approve/reject/edit actions
    if record:
        if record.get("reviewer_notes"):
            lines.append(f"\n### 评审备注\n{record['reviewer_notes']}")
        for h in record.get("history", []):
            action = h.get("action", "")
            notes = h.get("notes", "")
            reviewer = h.get("reviewer", "")
            if action in ("reject", "edit", "approve") and notes:
                lines.append(f"\n### [{reviewer}] {action}\n{notes}")

    if not lines:
        return "(无评审反馈)"

    return "\n".join(lines)


def _outline_summary(book: str, chapter_num: int) -> str:
    """Short outline context for the chapter being rewritten."""
    try:
        from . import outline as outmod
        outline = outmod.load_outline_or_empty(book) if hasattr(outmod, "load_outline_or_empty") else {}
    except Exception:
        outline = {}
    info = outline.get("chapters", [])[chapter_num - 1] if chapter_num > 0 and len(outline.get("chapters", [])) >= chapter_num else None
    if not info:
        return "(无大纲)"
    title = info.get("title", "")
    summary = info.get("summary", "")
    pov = info.get("pov", "")
    return f"- 章节: ch_{chapter_num:03d} {title}\n- POV: {pov}\n- 摘要: {summary}"


def _characters_block(book: str) -> str:
    """Compact character summary for prompt."""
    try:
        from .entity import Character
        store = memory.EntityStore(book) if hasattr(memory, "EntityStore") else None
    except Exception:
        store = None

    if store:
        chars = store.list_characters()
        if not chars:
            return "(暂无人物)"
        lines = []
        for c in chars[:8]:
            lines.append(f"- {c.name} ({c.role}): {c.traits}")
        return "\n".join(lines)
    else:
        # Fallback: legacy dict
        chars = memory.get_characters(book)
        if not chars:
            return "(暂无人物)"
        lines = []
        for name, info in list(chars.items())[:8]:
            lines.append(f"- {name} ({info.get('role','?')}): {info.get('traits','')}")
        return "\n".join(lines)


def _world_rules_block(book: str) -> str:
    """Compact world rules for prompt."""
    world = memory.get_world(book)
    rules = world.get("rules", {}) if isinstance(world, dict) else {}
    if not rules:
        return "(暂无已确立的世界规则)"
    lines = []
    for rid, rule in list(rules.items())[:5]:
        status = rule.get("status", "?")
        name = rule.get("name", "?")
        desc = rule.get("description", "")
        if status != "已确立":
            continue
        lines.append(f"- [{name}] ({status}): {desc}")
    return "\n".join(lines) if lines else "(暂无已确立的世界规则)"


# ── Main apply function ────────────────────────────────────────────────

def apply_feedback_to_chapter(
    book: str,
    chapter_id: str,
    chapter_num: int | None = None,
    llm: LLM = None,
    *,
    dry_run: bool = False,
    save: bool = True,
) -> dict:
    """
    Use LLM to rewrite chapter based on collected reviewer feedback.

    Returns:
        {
            ok: bool,
            chapter_id, chapter_num,
            original_text: str,
            new_text: str | None,   # None if dry_run or LLM failed
            feedback_block: str,    # what was sent to LLM
            new_version: int,       # v3, v4, ... (next after existing versions)
            diff_lines: int,        # rough metric: number of changed lines
            error: str | None,
        }

    The rewritten chapter is saved to reviews/<chapter_id>.v3.md (or v4, v5...)
    matching the human-edit version pattern from review_service.
    """
    # Parse chapter_num from chapter_id if not given
    if chapter_num is None:
        m = re.match(r"ch_(\d+)", chapter_id)
        if not m:
            return {"ok": False, "error": f"Invalid chapter_id: {chapter_id}"}
        chapter_num = int(m.group(1))

    original_text = storage.read_chapter(book, chapter_id) or ""
    if not original_text:
        return {"ok": False, "error": f"Chapter {chapter_id} not found"}

    record = review_service.review_path(book, chapter_id)
    record_data = None
    if record.exists():
        try:
            record_data = json.loads(record.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    feedback_block = _build_feedback_block(book, chapter_id, record_data)
    if feedback_block == "(无评审反馈)":
        return {"ok": False, "error": "本章无评审反馈可应用"}

    user_prompt = REVIEW_FEEDBACK_USER.format(
        feedback_block=feedback_block,
        outline_summary=_outline_summary(book, chapter_num),
        characters_block=_characters_block(book),
        world_rules_block=_world_rules_block(book),
        chapter_text=original_text,
    )

    # Resolve LLM (per-book provider)
    if llm is None:
        llm = get_llm(book=book)
    llm.set_stage_context("review_apply", chapter_num)

    try:
        new_text = llm.complete(
            prompt=user_prompt,
            system=REVIEW_FEEDBACK_SYSTEM,
            temperature=0.55,  # slightly lower than original 0.65 → more conservative
            max_tokens=8192,
            stop=["<stop>", "<END>", "### ", "---END---"],
        )
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {e}",
                "feedback_block": feedback_block, "original_text": original_text}

    # Clean up output (strip any leading explanation)
    new_text = _strip_preamble(new_text)
    # Add chapter header if missing
    new_text = _ensure_chapter_header(new_text, chapter_num, original_text)

    # Determine next version number
    new_version = _next_version_number(book, chapter_id)

    # Diff line count (rough metric)
    diff_lines = _count_diff_lines(original_text, new_text)

    result = {
        "ok": True,
        "chapter_id": chapter_id,
        "chapter_num": chapter_num,
        "original_text": original_text,
        "new_text": new_text,
        "feedback_block": feedback_block,
        "new_version": new_version,
        "diff_lines": diff_lines,
        "error": None,
    }

    if save and not dry_run:
        # Save as versioned file under reviews/
        version_path = review_service.edited_path(book, chapter_id).parent / f"{chapter_id}.v{new_version}.md"
        version_path.parent.mkdir(parents=True, exist_ok=True)
        version_path.write_text(new_text, encoding="utf-8")

        # Record changelog entry (entity_diff tracking)
        before_snapshot = entity_diff.snapshot_memory(book)
        diff = entity_diff.compute_diff(book, before_snapshot)
        # We don't expect entities to change here (this is a text rewrite), but
        # still log so we have a record of what feedback was applied
        entity_diff.record_chapter_changes(
            book=book,
            chapter_id=chapter_id,
            chapter_num=chapter_num,
            diff=diff,
            before_snapshot=before_snapshot,
            source="review_apply",
            note=f"基于评审反馈自动修订, 新版本 v{new_version}, diff_lines={diff_lines}",
        )

        # Update review record
        if record_data:
            history = record_data.setdefault("history", [])
            history.append({
                "action": "ai_rewrite",
                "reviewer": "auto",
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "notes": f"AI 应用评审反馈, 新版本 v{new_version}",
                "version_file": f"{chapter_id}.v{new_version}.md",
                "diff_lines": diff_lines,
            })
            record_data["status"] = review_service.REVIEW_STATUS["HUMAN_EDITED"]
            record_data["reviewed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            record_path = review_service.review_path(book, chapter_id)
            record_path.write_text(
                json.dumps(record_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Append to audit log
            audit = review_service.audit_log_path(book)
            with audit.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                        f"[auto] ai_rewrite {chapter_id} → v{new_version} "
                        f"(diff_lines={diff_lines})\n")

    return result


# ── Helpers ─────────────────────────────────────────────────────────────

def _strip_preamble(text: str) -> str:
    """Strip any leading '好的'/'下面是修改后的内容' preambles."""
    # Remove text before the first H1/H2 marker
    m = re.search(r"^#{1,2} ", text, re.MULTILINE)
    if m and m.start() > 0:
        return text[m.start():]
    return text


def _ensure_chapter_header(text: str, chapter_num: int, original: str) -> str:
    """Ensure the output starts with ## 第N章 XXX (use original title if found)."""
    # Extract title from original H2
    m = re.search(r"^##\s+(.+?)$", original, re.MULTILINE)
    title = m.group(1).strip() if m else f"第{chapter_num}章"

    # Check if output already has H2
    if re.match(r"^##\s+", text.strip()):
        return text

    # Prepend the title
    return f"## {title}\n\n{text}"


def _next_version_number(book: str, chapter_id: str) -> int:
    """Find next available version (v2, v3, ...). v1 = original, v2+ = edited."""
    reviews_dir = review_service.review_dir(book)
    existing = list(reviews_dir.glob(f"{chapter_id}.v*.md"))
    if not existing:
        return 2  # first edited version
    versions = []
    for f in existing:
        m = re.match(rf"{chapter_id}\.v(\d+)\.md", f.name)
        if m:
            versions.append(int(m.group(1)))
    return max(versions) + 1 if versions else 2


def _count_diff_lines(a: str, b: str) -> int:
    """Rough line-level diff metric: number of unique lines in unified diff."""
    import difflib
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    diff = list(difflib.unified_diff(a_lines, b_lines, lineterm="", n=2))
    # count +/- lines (exclude file headers)
    changed = 0
    for line in diff:
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed += 1
    return changed