"""
Style anchor extraction: lock the writing style early (after ch_001)
so subsequent chapters don't drift in voice, rhythm, or dialogue tone.

Stored at projects/<book>/style.json:
{
  "narrative_excerpt": str,        # 800字 叙事样本
  "dialogue_samples": list[str],   # 5 段对话样本
  "style_keywords": list[str],     # 5 个风格关键词
  "avg_sentence_len": float,       # 平均句长 (字)
  "pov_pattern": str,              # POV 模式 (第一人称/全知等)
  "extracted_at": str
}
"""
from __future__ import annotations
import re, json, datetime
from .llm import LLM
from . import storage

STYLE_ANCHOR_PROMPT = """你是一位文风分析师。

【第 1 章正文】
{ch1_text}

请从中提取【写作风格锚点】,输出 JSON:

```json
{{
  "narrative_excerpt": "最具代表性的 800 字叙事段落（直接引用原文片段）",
  "dialogue_samples": [
    "对话样本1（保留原文，不超 80 字）",
    "对话样本2",
    "对话样本3",
    "对话样本4",
    "对话样本5"
  ],
  "style_keywords": ["风格关键词1", "风格关键词2", "风格关键词3", "风格关键词4", "风格关键词5"],
  "pov_pattern": "第一人称/全知视角/第三人称限制视角"
}}
```

要求:
1. narrative_excerpt 必须直接复制原文,不要改写
2. dialogue_samples 优先选取【对话密度高】且【人物性格鲜明】的片段
3. style_keywords 要具体可操作 (例: "短句为主","白描","对话推动情节")
4. 输出严格 JSON,不要任何解释"""

def extract_style_anchor(book: str, llm: LLM) -> dict:
    """
    Extract style anchor from chapter 1. Only call once per project.
    """
    text = storage.read_chapter(book, "ch_001")
    if not text:
        raise FileNotFoundError("ch_001 not found — style anchor requires chapter 1 first")

    # Strip markdown headers
    clean = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()

    raw = llm.complete(
        prompt=STYLE_ANCHOR_PROMPT.format(ch1_text=clean[:6000]),
        system="你是精确的文风分析师。",
        temperature=0.2,   # Very low temp → stable style reference
        max_tokens=2500,
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
        anchor = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: very basic anchor
        anchor = {
            "narrative_excerpt": clean[:800],
            "dialogue_samples": [],
            "style_keywords": [],
            "pov_pattern": "（解析失败，待人工补充）"
        }

    anchor["extracted_at"] = datetime.datetime.now().isoformat()

    # Compute avg sentence length
    sentences = re.split(r"[。！？\n]+", clean)
    sentences = [s for s in sentences if len(s.strip()) > 4]
    if sentences:
        anchor["avg_sentence_len"] = round(
            sum(len(s) for s in sentences) / len(sentences), 1
        )

    storage.style_path(book).write_text(
        json.dumps(anchor, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return anchor

def get_style_anchor(book: str) -> dict | None:
    p = storage.style_path(book)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def get_style_text(book: str, max_chars: int = 1200) -> str:
    """
    Format style anchor as a prompt-ready block.
    Used in CHAPTER_SYSTEM to lock style.
    """
    anchor = get_style_anchor(book)
    if not anchor:
        return "（暂无风格锚点 — 第 1 章完成后将自动生成）"

    lines = [
        f"【写作风格锚点 - 严格保持】",
        f"POV 模式: {anchor.get('pov_pattern', '?')}",
        f"风格关键词: {'、'.join(anchor.get('style_keywords', []))}",
    ]
    if anchor.get("avg_sentence_len"):
        lines.append(f"平均句长: {anchor['avg_sentence_len']} 字")

    # Include a slice of the exemplar narrative
    excerpt = anchor.get("narrative_excerpt", "")
    if excerpt:
        snippet = excerpt[:500]
        lines.append(f"\n【叙事风格样本（请保持类似的节奏与句式）】\n{snippet}")

    # Dialogue sample
    dlg = anchor.get("dialogue_samples", [])
    if dlg:
        lines.append(f"\n【对话风格样本（请保持类似的人物语气）】")
        for i, d in enumerate(dlg[:3], 1):
            lines.append(f"  {i}. {d[:100]}")

    text = "\n".join(lines)
    return text[:max_chars] if len(text) > max_chars else text