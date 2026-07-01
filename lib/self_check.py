"""
Anti-drift self-check: after writing a chapter, let the LLM re-read its own
output against the canonical memory to surface inconsistencies.

Optional. Set `novel.py config <book> self_check=true` to enable.
Doubles LLM calls per chapter (~30-60s extra on 35B).

Output saved to projects/<book>/self_checks/<chapter_id>.json
"""
from __future__ import annotations
import json, re
from pathlib import Path
from .llm import LLM
from . import storage, memory, state

SELF_CHECK_PROMPT = """你是资深长篇小说终审编辑。

请【重读章节正文】,对照【既定记忆】找出 5 类问题。每类如有发现,具体引用章节原文片段。

【既定记忆 - 角色】
{characters}

【既定记忆 - 事件】
{events}

【既定记忆 - 伏笔】
{foreshadow}

【当前状态】
{current_state}

【章节正文】
{chapter_text}

【输出严格 JSON】(不要任何解释):
```json
{{
  "character_inconsistency": [
    {{"issue": "问题描述", "quote": "章节原文引用"}}
  ],
  "timeline_conflict": [
    {{"issue": "时间线矛盾描述", "quote": "原文引用"}}
  ],
  "location_or_item_conflict": [
    {{"issue": "地点/物品矛盾", "quote": "原文引用"}}
  ],
  "foreshadow_problem": [
    {{"issue": "已伏笔未推进 / 已回收重复 / 伏笔位置错", "quote": "原文引用"}}
  ],
  "personality_drift": [
    {{"issue": "角色性格突变", "quote": "原文引用"}}
  ],
  "overall_ok": true/false,
  "severity": "none/minor/moderate/critical"
}}
```

无问题 → 对应字段返回空数组, overall_ok=true, severity="none"。
轻微风格问题 → severity="minor"。
中度矛盾 (如角色称呼错) → severity="moderate"。
严重逻辑错误 → severity="critical"。"""

def self_check_chapter(
    book: str,
    chapter_id: str,
    llm: LLM,
    save: bool = True,
) -> dict:
    """
    Run anti-drift check on a chapter. Returns parsed result dict.
    """
    text = storage.read_chapter(book, chapter_id)
    if not text:
        raise FileNotFoundError(f"Chapter {chapter_id} not found")

    # Truncate chapter if huge (last 5000 chars is enough for cross-ref check)
    snippet = text if len(text) <= 5000 else text[-5000:]

    prompt = SELF_CHECK_PROMPT.format(
        characters=memory.get_characters_summary(book),
        events=memory.get_events_summary(book),
        foreshadow=memory.get_foreshadowing_summary(book),
        current_state=state.get_state_text(book),
        chapter_text=snippet,
    )

    raw = llm.complete(
        prompt=prompt,
        system="你是资深长篇小说终审编辑。",
        temperature=0.2,
        max_tokens=2000,
    )

    # Parse
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
    first = raw.find("{")
    last  = raw.rfind("}")
    if first >= 0 and last > first:
        raw = raw[first:last+1]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "character_inconsistency": [],
            "timeline_conflict": [],
            "location_or_item_conflict": [],
            "foreshadow_problem": [],
            "personality_drift": [],
            "overall_ok": None,
            "severity": "unknown",
            "raw_response": raw,
            "parse_error": True,
        }

    if save:
        storage.selfcheck_path(book, chapter_id).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return result

def has_critical_issues(result: dict, strict: bool = False) -> bool:
    """Decide if the chapter needs human review or auto-regen."""
    if result.get("severity") == "critical":
        return True
    # Treat overall_ok=False AND severity=moderate as worth a flag
    if not result.get("overall_ok") and result.get("severity") in ("moderate", "critical"):
        return True
    return False

def get_self_check(book: str, chapter_id: str) -> dict | None:
    p = storage.selfcheck_path(book, chapter_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

# ── auto-rewrite on critical ───────────────────────────────────────────────

REWRITE_SYSTEM = """你是一位长篇小说作者，正在根据资深编辑的反馈重写一章。"""

REWRITE_USER = """你刚才撰写的章节被资深编辑判定为【严重问题】，需整章重写。

【编辑反馈（必须修复）】
{issues}

【原章节正文】（保留你认为做得好的部分）
{chapter_text}

【本章原约束】（不要偏离）
- 字数：{target_words} 字
- 本章章纲与伏笔要求保持不变

重写原则：
1. 只修正编辑反馈中点出的问题，保留你原文中【叙事风格、人物基调、关键事件】
2. 如果反馈说“角色设定冲突”，请重读角色记忆库后修正
3. 如果反馈说“时间线/地点/物品冲突”，请使用【当前状态】中给出的正确事实
4. 如果反馈说“伏笔未推进”，请明确推动伏笔（如以对话/动作/决策推进）
5. 输出完整新章节，以 Markdown H2 标题开头：## 第{chapter_num}章 {chapter_title}
6. 中文 {target_words} 字左右

请直接输出重写后的完整章节正文。"""

def format_issues_for_rewrite(result: dict) -> str:
    """Convert self-check JSON issues into a readable feedback block."""
    sections = []
    categories = [
        ("character_inconsistency", "🔴 角色一致性问题"),
        ("timeline_conflict",        "⏱️ 时间线冲突"),
        ("location_or_item_conflict","📍 地点/物品冲突"),
        ("foreshadow_problem",       "📌 伏笔问题"),
        ("personality_drift",        "🎭 性格漂移"),
    ]
    for key, label in categories:
        items = result.get(key, [])
        if not items:
            continue
        sections.append(f"\n{label} ({len(items)} 项):")
        for i, item in enumerate(items, 1):
            sections.append(f"  {i}. {item.get('issue', '')}")
            quote = item.get("quote", "")
            if quote:
                # Trim long quotes
                q = quote[:200] + "..." if len(quote) > 200 else quote
                sections.append(f"     原句： 「{q}」")
    return "\n".join(sections) if sections else "（无具体问题描述）"


def rewrite_chapter(
    book: str,
    chapter_id: str,
    llm,
    result: dict,
    target_words: int = 2500,
) -> str:
    """
    Rewrite a chapter using self-check feedback.
    Returns the new chapter text (also saved to disk).
    """
    text = storage.read_chapter(book, chapter_id)
    if not text:
        raise FileNotFoundError(f"Chapter {chapter_id} not found")

    # Parse chapter_num from chapter_id
    try:
        ch_num = int(chapter_id.split("_")[1])
    except (IndexError, ValueError):
        ch_num = 0

    # Parse title from H2
    import re
    m = re.search(r"^##\s+(.+)$", text, re.MULTILINE)
    title = m.group(1).strip() if m else ""

    issues_text = format_issues_for_rewrite(result)

    user_prompt = REWRITE_USER.format(
        issues=issues_text,
        chapter_text=text,
        target_words=target_words,
        chapter_num=ch_num,
        chapter_title=title,
    )

    new_text = llm.complete(
        prompt=user_prompt,
        system=REWRITE_SYSTEM,
        temperature=0.55,  # Slightly lower than original (0.65) → more conservative
        max_tokens=8192,
        # Same stop tokens as chapter.py — avoid `## 第` (matches own H2)
        stop=["<stop>", "<END>", "### ", "---END---"],
    )

    # Clean & save
    from .chapter import clean_chapter_text
    new_text = clean_chapter_text(new_text, title, ch_num)
    storage.write_chapter(book, chapter_id, new_text)
    return new_text