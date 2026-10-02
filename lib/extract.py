"""
Extract events, characters, foreshadowing, world updates from a chapter.
Returns a structured dict (see memory.merge_extraction).
"""
from __future__ import annotations
import json, re
from .llm import LLM
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER


class ExtractionParseError(RuntimeError):
    """LLM 的抽取结果不是合法 JSON。

    以前这里是 `print(...) ; return 全空 dict` —— 2026-10-02 实测踩到的
    正是这条路径: 推理模型把 max_tokens 全花在思维链上、API 正常返回 200
    但正文为空 -> 这里拿到空串 -> 解析失败 -> 返回全空 dict -> 调用方
    照常 merge、阶段照样记 DONE -> 整章报"完成", 而 characters/events/
    foreshadowing 三个库永久缺这一章, 且没有任何一处报错。

    "解析不出来" 和 "抽出来是空的" 是两件完全不同的事, 只有前者该炸。
    缺失被当成功是所有降级里最坏的一种。
    """


def extract_from_chapter(chapter_text: str, llm: LLM = None, book: str = None) -> dict:
    """
    Run extraction LLM call on chapter text.
    Returns dict with new_characters, updated_characters, new_events,
    new_foreshadowing, resolved_foreshadowing, world_updates.

    Raises ExtractionParseError if the model returns something that isn't
    valid JSON. It does NOT degrade to an empty result.
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
        llm = get_llm(book=book)

    raw = llm.complete(
        prompt=user_prompt,
        system=EXTRACT_SYSTEM,
        temperature=0.3,   # Low temp for structured extraction
        max_tokens=4096,
    )

    return parse_extraction(raw)

def parse_extraction(raw: str) -> dict:
    """Parse LLM JSON output, be robust to markdown fences.

    Raises ExtractionParseError on unparseable input — see that class's
    docstring for why the old "return everything empty" path was dangerous.
    """
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
        # 以前是 print + 返回全空 dict, 于是这一章的抽取信息静默蒸发。
        # 现在炸出来: 调用方 (chapter.py) 会把 extract 阶段记 FAILED,
        # 章节不会带着空的记忆库继续往下走。
        raise ExtractionParseError(
            f"抽取结果不是合法 JSON ({e})。返回内容前 200 字符: {raw[:200]!r}"
        ) from e

    # Validate keys
    expected_keys = [
        "new_characters", "updated_characters", "new_events",
        "new_foreshadowing", "resolved_foreshadowing", "world_updates",
        "new_world_rules",  # v1.2 M1.2: structured world rules
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
