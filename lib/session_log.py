"""
session_log.py — 结构化 session 日志 (v1.3 M4 会话过渡优化)

每个 session 结束时记录 JSONL 到 memory/sessions/ 目录。
自动 hook 在 pipeline 开始/结束、章节完成时写入。

格式:
  {"session_id": "...", "started_at": "...", "ended_at": "...",
   "tasks": [..., ...], "key_decisions": [...], "unfinished": [...],
   "model": "...", "token_usage": {"input": N, "output": M},
   "projects_worked": ["book1", ...]}

禁止手动编辑。所有字段由系统自动写入。
"""
from __future__ import annotations

import json
import os
import datetime
from pathlib import Path
from typing import Any

from . import storage

SESSIONS_LOG = "memory/sessions/sessions.jsonl"
SNAPSHOT_FILE = "memory/sessions/current_session.json"


def session_dir(book: str | None = None) -> Path:
    """Get or create the sessions directory."""
    if book:
        base = storage.project_root(book)
    else:
        base = storage.WORKSPACE_ROOT
    d = base / "memory" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Current session snapshot (跨 turn 累积) ─────────────────────────────

def load_current_snapshot(book: str | None = None) -> dict:
    """Load in-progress session data (idempotent)."""
    path = session_dir(book) / "current_session.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Start fresh
    return {
        "session_id": _generate_session_id(),
        "started_at": _now(),
        "tasks": [],
        "key_decisions": [],
        "unfinished": [],
        "projects_worked": [],
        "model": os.environ.get("MODEL", ""),
    }


def save_current_snapshot(data: dict, book: str | None = None) -> None:
    """Persist in-progress session data."""
    path = session_dir(book) / "current_session.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_session_id() -> str:
    import uuid
    return f"session-{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── Events ────────────────────────────────────────────────────────────────

def log_task(book: str, chapter_id: str, task: str) -> None:
    """Record a task being worked on during this session."""
    data = load_current_snapshot(book)
    if book not in data["projects_worked"]:
        data["projects_worked"].append(book)
    entry = f"{chapter_id}/{task}" if chapter_id else task
    if entry not in data["tasks"]:
        data["tasks"].append(entry)
    save_current_snapshot(data, book)


def log_key_decision(book: str, decision: str) -> None:
    """Record an important decision made in this session."""
    data = load_current_snapshot(book)
    if decision not in data["key_decisions"]:
        data["key_decisions"].append(decision)
    save_current_snapshot(data, book)


def log_unfinished(book: str, item: str) -> None:
    """Record something left unfinished."""
    data = load_current_snapshot(book)
    if item not in data["unfinished"]:
        data["unfinished"].append(item)
    save_current_snapshot(data, book)


def log_token_usage(book: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Accumulate token usage tracking."""
    data = load_current_snapshot(book)
    tu = data.get("token_usage", {"input": 0, "output": 0})
    tu["input"] = tu.get("input", 0) + input_tokens
    tu["output"] = tu.get("output", 0) + output_tokens
    data["token_usage"] = tu
    save_current_snapshot(data, book)


# ── Session flush (session 结束时调用) ────────────────────────────────────

def flush_session(book: str | None = None, *, save_to: str | None = None) -> dict:
    """
    Finalize current session: write JSONL entry, remove snapshot.
    Call this at session end (e.g., via pipeline done, or CLI exit).

    Returns the final session entry dict (for use in daily note).
    """
    data = load_current_snapshot(book)
    data["ended_at"] = _now()
    # Fill in model if still empty
    if not data.get("model"):
        data["model"] = os.environ.get("MODEL", "")

    # Write to global sessions log
    sessions_log = session_dir(None) / "sessions.jsonl"
    sessions_log.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(data, ensure_ascii=False)
    with sessions_log.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    # Also write per-book (for project-specific view)
    if book:
        book_log = session_dir(book) / "sessions.jsonl"
        with book_log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # Remove snapshot
    snap = session_dir(book) / "current_session.json"
    if snap.exists():
        snap.unlink(missing_ok=True)

    return data


# ── Summary formatting ────────────────────────────────────────────────────

def format_session_for_daily_note(entry: dict) -> str:
    """Format a session log entry as a daily note section."""
    lines = []
    lines.append(f"## Session {entry.get('session_id', '?')}")
    lines.append(f"- **时间**: {entry.get('started_at', '?')} → {entry.get('ended_at', '?')}")
    lines.append(f"- **模型**: {entry.get('model', '?')}")
    
    tu = entry.get("token_usage", {})
    if tu.get("input") or tu.get("output"):
        lines.append(f"- **Tokens**: {tu.get('input', 0)} in / {tu.get('output', 0)} out")

    tasks = entry.get("tasks", [])
    if tasks:
        lines.append(f"- **任务**: {', '.join(tasks[:5])}{'...' if len(tasks) > 5 else ''}")

    decisions = entry.get("key_decisions", [])
    if decisions:
        lines.append(f"- **决策**: {', '.join(decisions)}")

    unfinished = entry.get("unfinished", [])
    if unfinished:
        lines.append(f"- **未完成**: {', '.join(unfinished)}")

    projects = entry.get("projects_worked", [])
    if projects:
        lines.append(f"- **项目**: {', '.join(projects)}")

    return "\n".join(lines)


def format_session_summary_by_project(book: str, max_entries: int = 5) -> str | None:
    """Get project-specific session summary for the daily note."""
    log_path = session_dir(book) / "sessions.jsonl"
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None
        # Last N entries
        entries = []
        for l in lines[-max_entries:]:
            try:
                entries.append(json.loads(l))
            except json.JSONDecodeError:
                continue
        if not entries:
            return None
        parts = [f"### 最近 {len(entries)} 次 session 交接"]
        for e in reversed(entries):
            parts.append(format_session_for_daily_note(e))
        return "\n\n".join(parts)
    except Exception:
        return None


# ── Pipeline hooks (从 pipeline_v2 调用) ─────────────────────────────────

def hook_pipeline_start(book: str, chapter_num: int) -> None:
    """Called when a pipeline run starts."""
    log_task(book, f"ch_{chapter_num:03d}", "pipeline")


def hook_pipeline_done(book: str, chapter_num: int) -> None:
    """Called when a pipeline run completes successfully."""
    log_task(book, f"ch_{chapter_num:03d}", "pipeline_done")


def hook_pipeline_failed(book: str, chapter_num: int, error: str) -> None:
    """Called when a pipeline run fails."""
    log_unfinished(book, f"ch_{chapter_num:03d}: {error}")


def hook_review_action(book: str, chapter_id: str, action: str, notes: str = "") -> None:
    """Called when a review action (approve/reject/edit) is taken."""
    log_key_decision(book, f"{chapter_id}: {action}{' - ' + notes if notes else ''}")


def hook_llm_usage(book: str, input_tokens: int, output_tokens: int) -> None:
    """Log token usage from LLM calls."""
    if input_tokens or output_tokens:
        log_token_usage(book, input_tokens, output_tokens)