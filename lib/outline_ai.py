"""
lib/outline_ai.py - v1.3 M2 Outline AI 助手

LLM-powered outline assistance:
  - suggest_chapters(): given outline + context, suggest next chapter(s)
  - expand_chapter(): given chapter title+summary, fill key_events + foreshadowing

设计原则:
  - 非确定性: 每次调用 LLM, 结果可能不同, 用户可接受或重新生成
  - 幂等展示: 仅生成内容, 不自动写回; 用户点"采纳"才写入
  - 结构化输出: JSON schema 约束, 解析失败 → 降级到原文提取
"""
from __future__ import annotations

import json, re
from typing import Any, Optional

from lib import llm as _llm
from lib.config_loader import get_config


# ── prompt helpers ─────────────────────────────────────────────────────────

_OUTLINE_SUGGEST_SYSTEM = """你是一位资深的小说大纲结构设计师。

你的任务是：给定当前书籍的大纲信息，生成"下一章"的情节发展建议。

输出严格 JSON（不要输出 JSON 之外的内容）：
{
  "chapters": [
    {
      "title": "章节标题（15字内）",
      "summary": "章节一句话简介（50字内）",
      "pov": "POV 视角角色名",
      "key_events": ["事件1", "事件2", "事件3（2-5个）"],
      "foreshadow": "伏笔/悬念（30字内）"
    }
  ],
  "reasoning": "为什么这样设计（说给用户听，不出现在正文中）"
}"""

_OUTLINE_SUGGEST_USER = """当前书籍信息：
- 书名：{book_title}
- 类型：{genre}
- 已有章节数：{existing_count}
- 当前大纲结构：
{outline_text}

请为"第 {next_num} 章"生成 {count} 个候选章节建议，每个候选是独立的章节发展方向。
只输出 JSON。"""

_OUTLINE_EXPAND_SYSTEM = """你是一位资深小说章节策划师。

你的任务是：给出一个章节的标题和简介，展开为完整章节大纲（key_events + foreshadowing）。

输出严格 JSON（不要输出 JSON 之外的内容）：
{
  "key_events": ["事件1（按顺序发生，3-6个）", "事件2", "..."],
  "foreshadow": "本章埋下的伏笔或悬念（30字内）",
  "pov_notes": "POV 视角补充说明（可省略）",
  "notes": "给用户的备注（可省略）"
}"""

_OUTLINE_EXPAND_USER = """章节信息：
- 书名：{book_title}
- 类型：{genre}
- 章节标题：{title}
- 章节简介：{summary}

请展开这个章节，生成具体的场景/事件序列。只输出 JSON。"""

# ── LLM client factory ─────────────────────────────────────────────────────

def _get_llm() -> _llm.LLM:
    cfg = get_config()
    book_cfg = cfg.get("book", {})
    return _llm.LLM(
        model=book_cfg.get("llm_model", "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"),
        api_base=book_cfg.get("api_base", "http://127.0.0.1:60443/v1"),
    )


# ── core functions ─────────────────────────────────────────────────────────

def suggest_chapters(
    book_title: str,
    genre: str,
    existing_count: int,
    outline_text: str,
    *,
    next_num: int,
    count: int = 3,
    temperature: float = 0.8,
) -> dict[str, Any]:
    """为下一章生成 N 个候选章节建议.

    Returns: {"chapters": [...], "reasoning": str}
    Raises: RuntimeError on LLM failure.
    """
    llm_client = _get_llm()
    user_prompt = _OUTLINE_SUGGEST_USER.format(
        book_title=book_title,
        genre=genre,
        existing_count=existing_count,
        outline_text=outline_text or "(暂无大纲)",
        next_num=next_num,
        count=count,
    )
    raw = llm_client.complete(
        prompt=user_prompt,
        system=_OUTLINE_SUGGEST_SYSTEM,
        temperature=temperature,
        max_tokens=2048,
        stage="outline_ai_suggest",
    )
    return _parse_json(raw, fallback={"chapters": [], "reasoning": raw})


def expand_chapter(
    book_title: str,
    genre: str,
    title: str,
    summary: str,
    *,
    temperature: float = 0.7,
) -> dict[str, Any]:
    """展开一个章节的 key_events 和 foreshadowing.

    Returns: {"key_events": [...], "foreshadow": str, ...}
    Raises: RuntimeError on LLM failure.
    """
    llm_client = _get_llm()
    user_prompt = _OUTLINE_EXPAND_USER.format(
        book_title=book_title,
        genre=genre,
        title=title,
        summary=summary,
    )
    raw = llm_client.complete(
        prompt=user_prompt,
        system=_OUTLINE_EXPAND_SYSTEM,
        temperature=temperature,
        max_tokens=1536,
        stage="outline_ai_expand",
    )
    return _parse_json(raw, fallback={"key_events": [], "foreshadow": "", "notes": raw})


# ── JSON parsing helpers ────────────────────────────────────────────────────

def _parse_json(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON, 失败返回 fallback + raw content."""
    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尝试从 markdown code block 中提取
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试找第一个 { 到最后一个 } 之间的内容
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(raw[first:last + 1])
        except json.JSONDecodeError:
            pass
    # 完全失败
    return fallback
