"""
Narrative state snapshot: single source of truth for "where we are now".

Stored at projects/<book>/state.json, updated after every chapter.

Schema:
{
  "current_chapter": int,
  "current_time": str,           # 故事内当前时间 (e.g. "失业第23天")
  "current_location": str,       # 主角当前位置
  "protagonist": {
    "name": str,
    "emotional": str,            # 当前情感状态
    "physical": str,             # 身体状况
    "occupation": str,           # 职业/身份
    "key_relationships": {       # 关键关系网络
      "角色名": "关系描述"
    }
  },
  "active_foreshadows": list[str],   # 状态 != 已回收 的伏笔
  "world_time_elapsed": str,          # 距开篇经过多久
  "updated_at": str                  # ISO 时间戳
}
"""
from __future__ import annotations
import json, datetime
from pathlib import Path
from .llm import LLM
from . import storage, memory

# ── state.json read/write ──────────────────────────────────────────────────

DEFAULT_STATE = {
    "current_chapter": 0,
    "current_time": "（开篇）",
    "current_location": "（待定）",
    "protagonist": {
        "name": "",
        "emotional": "（待定）",
        "physical": "（待定）",
        "occupation": "（待定）",
        "key_relationships": {}
    },
    "active_foreshadows": [],
    "world_time_elapsed": "（第 0 天）",
    "updated_at": ""
}

def get_state(book: str) -> dict:
    p = storage.state_path(book)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT_STATE))

def save_state(book: str, state: dict) -> None:
    state["updated_at"] = datetime.datetime.now().isoformat()
    storage.state_path(book).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ── state update via LLM ──────────────────────────────────────────────────

STATE_UPDATE_PROMPT = """你是长篇小说设定管理员。

【上一章状态】
{old_state}

【上一章正文】
{chapter_text}

【角色记忆库】
{characters}

【任务】基于上一章发生的事件，更新【当前状态】。只更新有变化的字段；无变化的字段保持原文。

更新规则:
1. current_time: 推进到本章结尾时点（可加"第N天"等相对时间）
2. current_location: 主角本章结尾所在位置
3. protagonist.emotional: 本章情感变化后的状态
4. protagonist.physical: 身体状况变化
5. protagonist.occupation: 职业变化（如有）
6. protagonist.key_relationships: 只列【重要】且本章有变化的或仍活跃的关系
7. active_foreshadows: 本章结束时所有【未回收】的伏笔（来自角色库 + 本章新埋伏笔），按时间顺序，最多10条
8. world_time_elapsed: 距开篇经过多久

【输出严格 JSON】(只输出 JSON, 不要解释):
```json
{{
  "current_chapter": {ch_num},
  "current_time": "...",
  "current_location": "...",
  "protagonist": {{
    "name": "...",
    "emotional": "...",
    "physical": "...",
    "occupation": "...",
    "key_relationships": {{"角色": "关系"}}
  }},
  "active_foreshadows": ["伏笔1", "伏笔2"],
  "world_time_elapsed": "..."
}}
```"""

def update_state_after_chapter(
    book: str,
    chapter_num: int,
    llm: LLM,
) -> dict:
    """
    Read the previous state, the latest chapter, and let LLM produce
    the new state. Always saves back to state.json.
    """
    old = get_state(book)

    # Latest chapter
    ch_id = f"ch_{chapter_num:03d}"
    text = storage.read_chapter(book, ch_id)
    if not text:
        raise FileNotFoundError(f"Chapter {ch_id} not found")

    # Truncate chapter text for state inference (last 3000 chars is enough)
    snippet = text[-3000:] if len(text) > 3000 else text

    chars = memory.get_characters_summary(book)

    prompt = STATE_UPDATE_PROMPT.format(
        old_state=json.dumps(old, ensure_ascii=False, indent=2),
        chapter_text=snippet,
        characters=chars,
        ch_num=chapter_num,
    )

    raw = llm.complete(
        prompt=prompt,
        system="你是精确的长篇小说设定管理员。",
        temperature=0.3,
        max_tokens=1500,
    )

    # Parse JSON
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
    first = raw.find("{")
    last  = raw.rfind("}")
    if first >= 0 and last > first:
        raw = raw[first:last+1]

    try:
        new = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: keep old state, just bump current_chapter
        new = json.loads(json.dumps(old))
        new["current_chapter"] = chapter_num

    # Backfill: keep protagonist.name from config if missing
    cfg = storage.read_json(book, "config.json") or {}
    if not new.get("protagonist", {}).get("name"):
        new.setdefault("protagonist", {})["name"] = cfg.get("protagonist", "")

    save_state(book, new)
    return new

def get_state_text(book: str, max_chars: int = 600) -> str:
    """Format current state as a prompt-ready block (compact)."""
    s = get_state(book)
    if s.get("current_chapter", 0) == 0:
        return "（故事刚开始）"

    p = s.get("protagonist", {})
    rels = p.get("key_relationships", {})
    rel_text = "、".join(f"{k}({v})" for k, v in rels.items()) if rels else "暂无"

    active_fs = s.get("active_foreshadows", [])
    fs_text = "\n  - ".join(active_fs[:8]) if active_fs else "暂无"

    lines = [
        f"故事内时间: {s.get('current_time','?')} (距开篇 {s.get('world_time_elapsed','?')})",
        f"当前位置: {s.get('current_location','?')}",
        f"主角: {p.get('name','?')} | 情感: {p.get('emotional','?')} | 状态: {p.get('physical','?')} | 职业: {p.get('occupation','?')}",
        f"关键关系: {rel_text}",
        f"活跃伏笔:\n  - {fs_text}",
    ]
    text = "\n".join(lines)
    return text[:max_chars] if len(text) > max_chars else text