"""
Storage layer: JSON + Markdown, project-scoped.
Each project lives under projects/<book_name>/.
"""
from __future__ import annotations

import json, re, uuid
from pathlib import Path
from typing import Any, Optional

PROJECTS_ROOT = Path(__file__).parent.parent / "projects"
ROOT = PROJECTS_ROOT  # alias; code uses ROOT throughout

# ── path helpers ────────────────────────────────────────────────────────────

def project_root(book: str) -> Path:
    p = ROOT / book
    p.mkdir(parents=True, exist_ok=True)
    return p

def chapters_dir(book: str) -> Path:
    d = project_root(book) / "chapters"
    d.mkdir(exist_ok=True)
    return d

def memory_dir(book: str) -> Path:
    d = project_root(book) / "memory"
    d.mkdir(exist_ok=True)
    return d

def summaries_dir(book: str) -> Path:
    """Per-chapter rolling narrative summaries (~200 chars each)."""
    d = project_root(book) / "summaries"
    d.mkdir(exist_ok=True)
    return d

def state_path(book: str) -> Path:
    return project_root(book) / "state.json"

def style_path(book: str) -> Path:
    return project_root(book) / "style.json"

def selfcheck_path(book: str, chapter_id: str) -> Path:
    d = project_root(book) / "self_checks"
    d.mkdir(exist_ok=True)
    return d / f"{chapter_id}.json"

# ── JSON helpers ────────────────────────────────────────────────────────────

def read_json(book: str, filename: str) -> dict[str, Any] | None:
    path = project_root(book) / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def write_json(book: str, filename: str, data: dict[str, Any], indent: int = 2) -> None:
    path = project_root(book) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")

# ── chapter I/O ─────────────────────────────────────────────────────────────

def read_chapter(book: str, chapter_id: str) -> Optional[str]:
    path = chapters_dir(book) / f"{chapter_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None

def write_chapter(book: str, chapter_id: str, content: str) -> None:
    path = chapters_dir(book) / f"{chapter_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # v1.2 M3: 读老内容, 写完后再 snapshot
    old_content = read_chapter(book, chapter_id)
    path.write_text(content, encoding="utf-8")
    if old_content != content:
        try:
            from . import version as _v
            _v.create_version(
                book, chapter_id, content, trigger="auto",
                meta={"prev_chars": len(old_content) if old_content else 0,
                      "new_chars": len(content)},
            )
        except Exception:
            pass

def list_chapters(book: str) -> list[dict]:
    """Return sorted list of {id, title, word_count, preview} from chapters dir."""
    chapters = []
    for p in sorted(chapters_dir(book).glob("*.md")):
        text = p.read_text(encoding="utf-8")
        # First H1 or H2 is the title
        m = re.search(r"^#+\s+(.+)$", text, re.MULTILINE)
        title = m.group(1).strip() if m else p.stem
        # Count Chinese + English words
        words = len(re.findall(r"[\u4e00-\u9fff]+", text))
        eng   = len(re.findall(r"[a-zA-Z]{3,}", text))
        wc    = words + eng
        # Preview: first non-empty paragraph after the title (max 50 chars)
        body = text[m.end():] if m else text
        body_lines = [l.strip() for l in body.splitlines() if l.strip()]
        preview = body_lines[0][:50] + ("…" if len(body_lines[0]) > 50 else "") if body_lines else ""
        chapters.append({"id": p.stem, "title": title, "word_count": wc, "preview": preview})
    return chapters

# ── config defaults ─────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "book_name": "",
    "genre": "都市",
    "target_chapters": 20,
    "words_per_chapter": 2500,
    "language": "zh",
    "llm_model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
    "api_base": "http://127.0.0.1:60443/v1",
    "created_at": "",
}

DEFAULT_PROGRESS = {
    "phase": "init",          # init | outline | writing | review | done
    "current_chapter": 0,
    "total_chapters": 0,
    "chapters_completed": [],
    "last_updated": "",
}

def mark_chapter_completed(book: str, chapter_id: str, chapter_num: int | None = None) -> None:
    """
    Append chapter_id to progress.chapters_completed (idempotent) and update
    current_chapter if num is provided. Call this from write_chapter, human-edit,
    and review-approve paths so progress doesn't drift behind state.

    Bug fixed 2026-07-01: review_ui human_edited/approved path did not update
    progress, so current_chapter stayed 0 while state.json advanced.
    """
    import datetime as _dt
    prog = read_json(book, "progress.json") or {**DEFAULT_PROGRESS}
    completed = prog.setdefault("chapters_completed", [])
    if chapter_id not in completed:
        completed.append(chapter_id)
    if chapter_num is not None:
        prog["current_chapter"] = max(int(prog.get("current_chapter", 0) or 0), int(chapter_num))
    prog["last_updated"] = _dt.datetime.now().isoformat()
    write_json(book, "progress.json", prog)


def init_project(book: str, cfg: dict[str, Any]) -> None:
    """Create a new project with defaults merged from user config."""
    import datetime
    cfg = {**DEFAULT_CONFIG, **cfg, "created_at": datetime.datetime.now().isoformat()}
    write_json(book, "config.json", cfg)
    write_json(book, "progress.json", {**DEFAULT_PROGRESS, "total_chapters": cfg["target_chapters"]})
    # Memory files
    for fname in ["characters.json", "world.json", "events.json", "foreshadowing.json"]:
        if not (memory_dir(book) / fname).exists():
            write_json(book, f"memory/{fname}", {} if "json" in fname else [], indent=1)
    # Outline placeholders
    write_json(book, "outline.json", {"meta": {}, "volumes": [], "chapters": []})

def project_exists(book: str) -> bool:
    return (project_root(book) / "config.json").exists()

def list_projects() -> list[str]:
    """Return all project names that have a config.json (sorted)."""
    if not PROJECTS_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROJECTS_ROOT.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    ])
