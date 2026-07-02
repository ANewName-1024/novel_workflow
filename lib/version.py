"""
lib/version.py — 章节版本控制 (v1.2 M3)

自研文件版本 (不用 git init),理由:
- 单文件/单人/单机能 cover 95% 场景
- 写盘 0.5ms, 不阻塞章节保存
- 元数据 (ts/触发原因/字数差) JSON 一目了然
- 出问题可手动回滚, 也好写迁移脚本

存储: projects/<book>/chapters/.versions/<chapter_id>/v001.json
  v001.json = {content, ts, char_count, trigger, prev_id, meta}
  manifest.json = [v001, v002, ...] 索引, 读快
"""
from __future__ import annotations
import json
import datetime
import hashlib
import difflib
from pathlib import Path
from typing import Any


# ── 路径 ────────────────────────────────────────────────────────────────

def _versions_dir(book: str) -> Path:
    from . import storage
    d = storage.project_root(book) / "chapters" / ".versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chapter_versions_dir(book: str, chapter_id: str) -> Path:
    d = _versions_dir(book) / chapter_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_path(book: str, chapter_id: str, version_id: str) -> Path:
    return _chapter_versions_dir(book, chapter_id) / f"{version_id}.json"


def _manifest_path(book: str, chapter_id: str) -> Path:
    return _chapter_versions_dir(book, chapter_id) / "manifest.json"


# ── 内部: manifest ─────────────────────────────────────────────────────

def _load_manifest(book: str, chapter_id: str) -> list[dict]:
    p = _manifest_path(book, chapter_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_manifest(book: str, chapter_id: str, items: list[dict]) -> None:
    _manifest_path(book, chapter_id).write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 核心 API ───────────────────────────────────────────────────────────

def create_version(
    book: str,
    chapter_id: str,
    content: str,
    trigger: str = "manual",
    meta: dict[str, Any] | None = None,
) -> dict:
    """创建一个版本. 返回 version dict (含 version_id).

    trigger: 'auto' | 'manual' | 'edit' | 'revert' | 'rollback'
    meta: 额外元数据 (e.g. {"author": "wei_chao", "words": 2500})

    如果新内容与上一版本相同, 跳过并返回 last record.
    """
    manifest = _load_manifest(book, chapter_id)
    prev_id = manifest[-1]["version_id"] if manifest else None
    prev_content = ""
    if prev_id:
        try:
            prev_content = json.loads(_version_path(book, chapter_id, prev_id).read_text(encoding="utf-8"))["content"]
        except (json.JSONDecodeError, OSError, KeyError):
            prev_content = ""

    # 重复内容跳过, 返回 last
    if prev_content == content and manifest:
        return json.loads(_version_path(book, chapter_id, prev_id).read_text(encoding="utf-8"))

    n = len(manifest) + 1
    version_id = f"v{n:03d}"

    char_count = len(content)
    char_diff = char_count - len(prev_content) if prev_content else char_count
    word_diff = _word_count_diff(prev_content, content)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]

    record = {
        "version_id": version_id,
        "chapter_id": chapter_id,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "trigger": trigger,
        "char_count": char_count,
        "char_diff": char_diff,
        "word_diff": word_diff,
        "content_hash": content_hash,
        "prev_id": prev_id,
        "meta": meta or {},
        "content": content,
    }

    _version_path(book, chapter_id, version_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest.append({k: v for k, v in record.items() if k != "content"})
    _save_manifest(book, chapter_id, manifest)
    return record


def list_versions(book: str, chapter_id: str) -> list[dict]:
    """列出所有版本 (不含 content)."""
    return _load_manifest(book, chapter_id)


def get_version(book: str, chapter_id: str, version_id: str) -> dict | None:
    p = _version_path(book, chapter_id, version_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def latest_version(book: str, chapter_id: str) -> dict | None:
    m = _load_manifest(book, chapter_id)
    return m[-1] if m else None


def revert_to(
    book: str,
    chapter_id: str,
    version_id: str,
    by: str = "wei_chao",
) -> dict:
    from . import storage

    target = get_version(book, chapter_id, version_id)
    if not target:
        raise ValueError(f"version {version_id} not found")

    current = storage.read_chapter(book, chapter_id) or ""
    if current == target["content"]:
        raise ValueError("target version is already the current content, no-op")

    create_version(
        book, chapter_id, current, trigger="pre_revert",
        meta={"reverting_to": version_id, "by": by},
    )
    # 直接写文件, 绕过 write_chapter 的 auto-snapshot
    chapter_path = storage.chapters_dir(book) / f"{chapter_id}.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(target["content"], encoding="utf-8")
    record = create_version(
        book, chapter_id, target["content"], trigger="revert",
        meta={"reverted_to": version_id, "from_version": target["version_id"], "by": by},
    )
    return record


def diff_versions(
    book: str, chapter_id: str, v1: str, v2: str,
) -> dict:
    r1 = get_version(book, chapter_id, v1)
    r2 = get_version(book, chapter_id, v2)
    if not r1 or not r2:
        raise ValueError(f"version not found: v1={v1} v2={v2}")
    text1, text2 = r1["content"], r2["content"]
    diff = list(difflib.unified_diff(
        text1.splitlines(), text2.splitlines(),
        fromfile=v1, tofile=v2, lineterm="", n=3,
    ))
    return {
        "v1": v1, "v2": v2,
        "has_diff": text1 != text2,
        "diff": diff,
        "char_diff": len(text2) - len(text1),
        "char_v1": len(text1), "char_v2": len(text2),
    }


def _word_count_diff(text1: str, text2: str) -> int:
    return len(text2) - len(text1)


def auto_snapshot_on_write(book: str, chapter_id: str, new_content: str) -> dict | None:
    from . import storage
    old = storage.read_chapter(book, chapter_id) or ""
    if old == new_content:
        return None
    return create_version(
        book, chapter_id, new_content, trigger="auto",
        meta={"prev_chars": len(old), "new_chars": len(new_content)},
    )
