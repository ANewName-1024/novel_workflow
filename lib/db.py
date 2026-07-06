"""
SQLite-backed metadata store for novel_workflow (v1.3 M6 - 方案 B).

策略: 元数据入数据库, 章节内容(.md)继续用文件系统.
这样既保留 git diff/备份/AI 直读能力, 又获得 SQL 查询/事务/统计的便利.

表设计:
  projects(id PK, name, config_json, created_at, updated_at)
  chapters_meta(book, ch_id, title, word_count, preview, file_path, file_mtime, file_size)
  reviews(id PK, book, ch_id, status, auto_severity, auto_issues_count,
          auto_result_json, reviewer, reviewer_notes, reviewed_at,
          created_at, updated_at, history_json, v2_chars)
  entities(id PK, book, type, name, category, status, content_json, updated_at)
  outlines(book PK, outline_json, updated_at, version)
  state(book PK, state_json, progress_json, updated_at)

路径: <root>/.meta/db.sqlite3 (gitignored, 自动创建)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import datetime
from pathlib import Path
from typing import Any, Optional


# ── defaults ────────────────────────────────────────────────────────────────

def default_db_path(root: Path) -> Path:
    """数据库路径: <root>/.meta/db.sqlite3 (跟 chapters/ 同级, 但 .meta 隐藏)"""
    p = root / ".meta"
    p.mkdir(parents=True, exist_ok=True)
    return p / "db.sqlite3"


# ── schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters_meta (
    book       TEXT NOT NULL,
    ch_id      TEXT NOT NULL,
    title      TEXT,
    word_count INTEGER DEFAULT 0,
    preview    TEXT,
    file_path  TEXT,
    file_mtime REAL,
    file_size  INTEGER,
    updated_at TEXT,
    PRIMARY KEY (book, ch_id)
);

CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters_meta(book);

CREATE TABLE IF NOT EXISTS reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    book              TEXT NOT NULL,
    ch_id             TEXT NOT NULL,
    status            TEXT NOT NULL,
    auto_severity     TEXT,
    auto_issues_count INTEGER DEFAULT 0,
    auto_result_json  TEXT,
    reviewer          TEXT,
    reviewer_notes    TEXT,
    reviewed_at       TEXT,
    v2_chars          INTEGER DEFAULT 0,
    history_json      TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (book, ch_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_book ON reviews(book);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book        TEXT NOT NULL,
    type        TEXT NOT NULL,        -- character/event/foreshadow/world_rule
    name        TEXT NOT NULL,
    category    TEXT,
    status      TEXT,
    content_json TEXT NOT NULL,        -- full entity data
    updated_at  TEXT NOT NULL,
    UNIQUE (book, type, name)
);

CREATE INDEX IF NOT EXISTS idx_entities_book ON entities(book);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

CREATE TABLE IF NOT EXISTS outlines (
    book        TEXT PRIMARY KEY,
    outline_json TEXT NOT NULL,
    version     INTEGER DEFAULT 1,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    book          TEXT PRIMARY KEY,
    state_json    TEXT,
    progress_json TEXT,
    updated_at    TEXT NOT NULL
);
"""


# ── connection helper ────────────────────────────────────────────────────────

_local = threading.local()


def get_conn(db_path: Path) -> sqlite3.Connection:
    """Per-thread connection. Same-thread reuse = OK (sqlite is thread-safe within thread)."""
    conn = getattr(_local, "conn_dict", None)
    if conn is None:
        conn = {}
        _local.conn_dict = conn
    key = str(db_path)
    if key not in conn:
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        c.execute("PRAGMA journal_mode = WAL;")  # better concurrency
        conn[key] = c
        # ensure schema
        c.executescript(SCHEMA)
        c.commit()
    return conn[key]


def init_db(root: Path) -> Path:
    """Initialize DB at <root>/.meta/db.sqlite3, return path."""
    p = default_db_path(root)
    conn = get_conn(p)
    conn.executescript(SCHEMA)
    conn.commit()
    return p


# ── projects ─────────────────────────────────────────────────────────────────

def upsert_project(root: Path, project_id: str, name: str, config: dict) -> None:
    """Insert or update project metadata."""
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))
    conn.execute(
        """INSERT INTO projects (id, name, config_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name,
             config_json=excluded.config_json,
             updated_at=excluded.updated_at""",
        (project_id, name, json.dumps(config, ensure_ascii=False),
         config.get("created_at") or now, now),
    )
    conn.commit()


def get_project_config(root: Path, project_id: str) -> Optional[dict]:
    conn = get_conn(default_db_path(root))
    row = conn.execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    return json.loads(row["config_json"])


def set_project_config(root: Path, project_id: str, patch: dict) -> dict:
    """Merge patch into project config, return updated config."""
    conn = get_conn(default_db_path(root))
    row = conn.execute("SELECT config_json, name FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"Project not found: {project_id}")
    cfg = json.loads(row["config_json"])
    cfg.update(patch)
    now = datetime.datetime.now().isoformat()
    conn.execute(
        "UPDATE projects SET config_json=?, updated_at=? WHERE id=?",
        (json.dumps(cfg, ensure_ascii=False), now, project_id),
    )
    conn.commit()
    return cfg


def list_projects(root: Path) -> list[dict]:
    """List all projects with display_name and stats."""
    conn = get_conn(default_db_path(root))
    rows = conn.execute("SELECT id, name, config_json, created_at FROM projects ORDER BY id").fetchall()
    out = []
    for r in rows:
        cfg = json.loads(r["config_json"])
        out.append({
            "id": r["id"],
            "name": r["name"],
            "display_name": cfg.get("book_name", r["name"]),
            "created_at": r["created_at"],
            "config": cfg,
        })
    return out


def list_projects_with_stats(root: Path) -> list[dict]:
    """List projects with chapter count + review stats."""
    conn = get_conn(default_db_path(root))
    rows = conn.execute(
        """SELECT p.id, p.name, p.config_json, p.created_at,
                  (SELECT COUNT(*) FROM chapters_meta WHERE book=p.id) AS chapter_count,
                  (SELECT COUNT(*) FROM reviews WHERE book=p.id AND status='pending_review') AS pending_reviews,
                  (SELECT COUNT(*) FROM reviews WHERE book=p.id AND status='approved') AS approved,
                  (SELECT COUNT(*) FROM reviews WHERE book=p.id AND status='needs_rewrite') AS rejected
           FROM projects p ORDER BY p.id"""
    ).fetchall()
    out = []
    for r in rows:
        cfg = json.loads(r["config_json"])
        out.append({
            "id": r["id"],
            "name": r["name"],
            "display_name": cfg.get("book_name", r["name"]),
            "created_at": r["created_at"],
            "config": cfg,
            "total_chapters": r["chapter_count"] or 0,
            "pending_reviews": r["pending_reviews"] or 0,
            "approved": r["approved"] or 0,
            "rejected": r["rejected"] or 0,
        })
    return out


def delete_project(root: Path, project_id: str) -> None:
    """Hard delete project + cascade metadata."""
    conn = get_conn(default_db_path(root))
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.execute("DELETE FROM chapters_meta WHERE book=?", (project_id,))
    conn.execute("DELETE FROM reviews WHERE book=?", (project_id,))
    conn.execute("DELETE FROM entities WHERE book=?", (project_id,))
    conn.execute("DELETE FROM outlines WHERE book=?", (project_id,))
    conn.execute("DELETE FROM state WHERE book=?", (project_id,))
    conn.commit()


# ── chapters meta ───────────────────────────────────────────────────────────

def upsert_chapter_meta(root: Path, book: str, ch_id: str,
                         title: str, word_count: int, preview: str,
                         file_path: str, file_mtime: float, file_size: int) -> None:
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))
    conn.execute(
        """INSERT INTO chapters_meta (book, ch_id, title, word_count, preview,
                                     file_path, file_mtime, file_size, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(book, ch_id) DO UPDATE SET
             title=excluded.title,
             word_count=excluded.word_count,
             preview=excluded.preview,
             file_path=excluded.file_path,
             file_mtime=excluded.file_mtime,
             file_size=excluded.file_size,
             updated_at=excluded.updated_at""",
        (book, ch_id, title, word_count, preview, file_path, file_mtime, file_size, now),
    )
    conn.commit()


def delete_chapter_meta(root: Path, book: str, ch_id: str) -> None:
    conn = get_conn(default_db_path(root))
    conn.execute("DELETE FROM chapters_meta WHERE book=? AND ch_id=?", (book, ch_id))
    conn.commit()


def list_chapters_meta(root: Path, book: str) -> list[dict]:
    conn = get_conn(default_db_path(root))
    rows = conn.execute(
        "SELECT ch_id, title, word_count, preview, file_mtime, file_size, updated_at "
        "FROM chapters_meta WHERE book=? ORDER BY ch_id",
        (book,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── reviews ─────────────────────────────────────────────────────────────────

def upsert_review(root: Path, book: str, ch_id: str,
                  status: str, auto_severity: Optional[str] = None,
                  auto_issues_count: int = 0, auto_result: Optional[dict] = None,
                  reviewer: Optional[str] = None, reviewer_notes: Optional[str] = None,
                  v2_chars: int = 0, append_history: Optional[dict] = None) -> int:
    """Insert or update review; returns review id."""
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))

    # Load existing for history merge
    row = conn.execute(
        "SELECT id, history_json, created_at FROM reviews WHERE book=? AND ch_id=?",
        (book, ch_id),
    ).fetchone()
    if row:
        history = json.loads(row["history_json"])
        if append_history:
            history.append(append_history)
        rid = row["id"]
        created_at = row["created_at"]
        reviewed_at = now if reviewer else None
        conn.execute(
            """UPDATE reviews SET status=?, auto_severity=?, auto_issues_count=?,
                  auto_result_json=?, reviewer=?, reviewer_notes=?, reviewed_at=?,
                  v2_chars=?, history_json=?, updated_at=?
               WHERE id=?""",
            (status, auto_severity, auto_issues_count,
             json.dumps(auto_result, ensure_ascii=False) if auto_result else None,
             reviewer, reviewer_notes, reviewed_at,
             v2_chars, json.dumps(history, ensure_ascii=False), now, rid),
        )
    else:
        history = [append_history] if append_history else []
        reviewed_at = now if reviewer else None
        cur = conn.execute(
            """INSERT INTO reviews (book, ch_id, status, auto_severity, auto_issues_count,
                  auto_result_json, reviewer, reviewer_notes, reviewed_at,
                  v2_chars, history_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (book, ch_id, status, auto_severity, auto_issues_count,
             json.dumps(auto_result, ensure_ascii=False) if auto_result else None,
             reviewer, reviewer_notes, reviewed_at,
             v2_chars, json.dumps(history, ensure_ascii=False), now, now),
        )
        rid = cur.lastrowid
    conn.commit()
    return rid or 0


def get_review(root: Path, book: str, ch_id: str) -> Optional[dict]:
    conn = get_conn(default_db_path(root))
    row = conn.execute(
        "SELECT * FROM reviews WHERE book=? AND ch_id=?", (book, ch_id)
    ).fetchone()
    if not row:
        return None
    d = dict(r)
    if d.get("auto_result_json"):
        d["auto_result"] = json.loads(d["auto_result_json"])
    if d.get("history_json"):
        d["history"] = json.loads(d["history_json"])
    return d


def list_reviews(root: Path, book: str, status: Optional[str] = None) -> list[dict]:
    conn = get_conn(default_db_path(root))
    if status:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE book=? AND status=? ORDER BY updated_at DESC",
            (book, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE book=? ORDER BY updated_at DESC", (book,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("auto_result_json"):
            d["auto_result"] = json.loads(d["auto_result_json"])
        if d.get("history_json"):
            d["history"] = json.loads(d["history_json"])
        out.append(d)
    return out


def review_stats(root: Path, book: str) -> dict:
    conn = get_conn(default_db_path(root))
    row = conn.execute(
        """SELECT
              SUM(CASE WHEN status='auto_passed' THEN 1 ELSE 0 END) AS auto_passed,
              SUM(CASE WHEN status='pending_review' THEN 1 ELSE 0 END) AS pending_review,
              SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
              SUM(CASE WHEN status='needs_rewrite' THEN 1 ELSE 0 END) AS needs_rewrite,
              SUM(CASE WHEN status='human_edited' THEN 1 ELSE 0 END) AS human_edited,
              SUM(CASE WHEN status='false_positive' THEN 1 ELSE 0 END) AS false_positive
           FROM reviews WHERE book=?""",
        (book,),
    ).fetchone()
    return {
        "auto_passed": row["auto_passed"] or 0,
        "pending_review": row["pending_review"] or 0,
        "approved": row["approved"] or 0,
        "needs_rewrite": row["needs_rewrite"] or 0,
        "human_edited": row["human_edited"] or 0,
        "false_positive": row["false_positive"] or 0,
    }


# ── entities ────────────────────────────────────────────────────────────────

def upsert_entity(root: Path, book: str, etype: str, name: str,
                  category: Optional[str] = None, status: Optional[str] = None,
                  content: dict = None) -> int:
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))
    cur = conn.execute(
        """INSERT INTO entities (book, type, name, category, status, content_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(book, type, name) DO UPDATE SET
             category=excluded.category,
             status=excluded.status,
             content_json=excluded.content_json,
             updated_at=excluded.updated_at""",
        (book, etype, name, category, status,
         json.dumps(content or {}, ensure_ascii=False), now),
    )
    conn.commit()
    return cur.lastrowid or 0


def list_entities(root: Path, book: str, etype: Optional[str] = None) -> list[dict]:
    conn = get_conn(default_db_path(root))
    if etype:
        rows = conn.execute(
            "SELECT * FROM entities WHERE book=? AND type=? ORDER BY name",
            (book, etype),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities WHERE book=? ORDER BY type, name", (book,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("content_json"):
            d["content"] = json.loads(d["content_json"])
        out.append(d)
    return out


def get_entity(root: Path, book: str, etype: str, name: str) -> Optional[dict]:
    conn = get_conn(default_db_path(root))
    row = conn.execute(
        "SELECT * FROM entities WHERE book=? AND type=? AND name=?",
        (book, etype, name),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("content_json"):
        d["content"] = json.loads(d["content_json"])
    return d


def delete_entity(root: Path, book: str, etype: str, name: str) -> None:
    conn = get_conn(default_db_path(root))
    conn.execute(
        "DELETE FROM entities WHERE book=? AND type=? AND name=?",
        (book, etype, name),
    )
    conn.commit()


def entity_counts(root: Path, book: str) -> dict:
    conn = get_conn(default_db_path(root))
    rows = conn.execute(
        "SELECT type, COUNT(*) AS cnt FROM entities WHERE book=? GROUP BY type",
        (book,),
    ).fetchall()
    return {r["type"]: r["cnt"] for r in rows}


# ── outline ─────────────────────────────────────────────────────────────────

def get_outline(root: Path, book: str) -> Optional[dict]:
    conn = get_conn(default_db_path(root))
    row = conn.execute("SELECT outline_json, version FROM outlines WHERE book=?",
                       (book,)).fetchone()
    if not row:
        return None
    return {"version": row["version"], **json.loads(row["outline_json"])}


def save_outline(root: Path, book: str, outline: dict) -> int:
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))
    cur = conn.execute(
        """INSERT INTO outlines (book, outline_json, version, updated_at)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(book) DO UPDATE SET
             outline_json=excluded.outline_json,
             version=version+1,
             updated_at=excluded.updated_at""",
        (book, json.dumps(outline, ensure_ascii=False), now),
    )
    conn.commit()
    return cur.lastrowid or 0


# ── state / progress ────────────────────────────────────────────────────────

def get_state(root: Path, book: str) -> dict:
    conn = get_conn(default_db_path(root))
    row = conn.execute(
        "SELECT state_json, progress_json FROM state WHERE book=?", (book,)
    ).fetchone()
    if not row:
        return {"state": {}, "progress": {}}
    return {
        "state": json.loads(row["state_json"]) if row["state_json"] else {},
        "progress": json.loads(row["progress_json"]) if row["progress_json"] else {},
    }


def save_state(root: Path, book: str, state: dict, progress: dict) -> None:
    now = datetime.datetime.now().isoformat()
    conn = get_conn(default_db_path(root))
    conn.execute(
        """INSERT INTO state (book, state_json, progress_json, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(book) DO UPDATE SET
             state_json=excluded.state_json,
             progress_json=excluded.progress_json,
             updated_at=excluded.updated_at""",
        (book, json.dumps(state, ensure_ascii=False),
         json.dumps(progress, ensure_ascii=False), now),
    )
    conn.commit()


# ── utility ─────────────────────────────────────────────────────────────────

def close_all() -> None:
    """Close all thread-local connections. Call on app shutdown."""
    conn_dict = getattr(_local, "conn_dict", None)
    if conn_dict:
        for c in conn_dict.values():
            try:
                c.close()
            except Exception:
                pass
        _local.conn_dict = {}


def stats(root: Path) -> dict:
    """DB-wide stats (for /api/admin/db-stats)."""
    conn = get_conn(default_db_path(root))
    tables = ["projects", "chapters_meta", "reviews", "entities", "outlines", "state"]
    counts = {}
    for t in tables:
        counts[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
    return counts