"""
lib/comments.py — 评论流 / 通知 / @提醒 存储层 (v1.2 M2)

数据模型:
- comments: {chapter_id: [{id, author, text, line, created_at, mentions: [...], reply_to?}]}
- notifications: [{id, user, type, ref_chapter, ref_comment, read, created_at, message}]

存储位置: projects/<book>/comments.json + projects/<book>/notifications.json
"""
from __future__ import annotations
import json
import uuid
import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def comments_path(book: str) -> Path:
    from . import storage
    return storage.project_root(book) / "comments.json"


def notifications_path(book: str) -> Path:
    from . import storage
    return storage.project_root(book) / "notifications.json"


# ── 加载 / 保存 ───────────────────────────────────────────────────────

def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 评论 CRUD ────────────────────────────────────────────────────────

def list_comments(book: str, chapter_id: str | None = None) -> list[dict]:
    """List all comments. Filter by chapter if chapter_id given."""
    data = _load_json(comments_path(book), {})
    if chapter_id is not None:
        return data.get(chapter_id, [])
    # flatten all
    out: list[dict] = []
    for cid, items in data.items():
        for it in items:
            out.append({**it, "chapter_id": cid})
    out.sort(key=lambda x: x.get("created_at", ""))
    return out


def add_comment(
    book: str,
    chapter_id: str,
    author: str,
    text: str,
    line: int | None = None,
    reply_to: str | None = None,
    mentions: list[str] | None = None,
) -> dict:
    """Append a comment, return it. Auto-create notification for @mentions."""
    if not text.strip():
        raise ValueError("comment text is empty")
    if not chapter_id.startswith("ch_"):
        raise ValueError(f"invalid chapter_id: {chapter_id!r}")

    # parse @mentions from text if not given
    if mentions is None:
        import re
        mentions = re.findall(r"@(\w+)", text)

    comment = {
        "id": f"c_{uuid.uuid4().hex[:8]}",
        "author": author,
        "text": text.strip(),
        "line": line,
        "mentions": mentions,
        "reply_to": reply_to,
        "created_at": _now(),
    }

    data = _load_json(comments_path(book), {})
    data.setdefault(chapter_id, []).append(comment)
    _save_json(comments_path(book), data)

    # create notifications for mentions
    for user in mentions:
        if user != author:  # don't notify self
            add_notification(
                book, user=user, type="mention",
                ref_chapter=chapter_id, ref_comment=comment["id"],
                message=f"{author} 在 ch {chapter_id.replace('ch_', '')} @了你: {text[:60]}",
            )

    return comment


def delete_comment(book: str, chapter_id: str, comment_id: str) -> bool:
    """Delete a comment. Returns True if found and deleted."""
    data = _load_json(comments_path(book), {})
    items = data.get(chapter_id, [])
    new_items = [it for it in items if it["id"] != comment_id]
    if len(new_items) == len(items):
        return False
    data[chapter_id] = new_items
    _save_json(comments_path(book), data)
    return True


# ── 通知 CRUD ────────────────────────────────────────────────────────

def list_notifications(book: str, user: str | None = None,
                       unread_only: bool = False) -> list[dict]:
    """List notifications. Filter by user / unread."""
    items = _load_json(notifications_path(book), [])
    if user is not None:
        items = [n for n in items if n.get("user") == user]
    if unread_only:
        items = [n for n in items if not n.get("read", False)]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def unread_count(book: str, user: str) -> int:
    return len(list_notifications(book, user=user, unread_only=True))


def add_notification(
    book: str,
    user: str,
    type: str,
    message: str,
    ref_chapter: str | None = None,
    ref_comment: str | None = None,
) -> dict:
    """Append a notification."""
    notif = {
        "id": f"n_{uuid.uuid4().hex[:8]}",
        "user": user,
        "type": type,  # 'mention' | 'flag' | 'approve' | 'reject' | 'comment'
        "ref_chapter": ref_chapter,
        "ref_comment": ref_comment,
        "message": message,
        "read": False,
        "created_at": _now(),
    }
    items = _load_json(notifications_path(book), [])
    items.append(notif)
    _save_json(notifications_path(book), items)
    return notif


def mark_notification_read(book: str, notif_id: str) -> bool:
    items = _load_json(notifications_path(book), [])
    found = False
    for n in items:
        if n["id"] == notif_id:
            n["read"] = True
            found = True
    if found:
        _save_json(notifications_path(book), items)
    return found


def mark_all_read(book: str, user: str) -> int:
    items = _load_json(notifications_path(book), [])
    n = 0
    for it in items:
        if it.get("user") == user and not it.get("read", False):
            it["read"] = True
            n += 1
    if n:
        _save_json(notifications_path(book), items)
    return n
