"""
Storage layer: JSON + Markdown + SQLite, project-scoped.
v1.3 M6: 元数据可走 SQLite (lib.db), 章节内容仍在 .md 文件.

策略:
  - read_json / write_json 优先 SQLite, 文件后备 (dual-write)
  - list_projects / project_exists 走 SQLite
  - read_chapter / write_chapter / list_chapters 纯文件系统
"""
from __future__ import annotations

import json, logging, os, re, tempfile, uuid
from pathlib import Path
from typing import Any, Optional

PROJECTS_ROOT = Path(__file__).parent.parent / "projects"
ROOT = PROJECTS_ROOT  # alias; code uses ROOT throughout

# 本模块此前没有 logger, 导致「版本快照失败」完全静默 —— 而那个快照是
# 覆盖已有正文时【唯一的回退手段】。快照静默失败 = 旧内容被覆盖且无处可寻。
log = logging.getLogger("novel.lib.storage")

# ── path helpers ────────────────────────────────────────────────────────────
#
# 这里刻意分成两族:
#
#   *_path()  纯计算, 【绝不创建任何目录】。读路径与存在性检查只能用这一族。
#   *_dir()   顺带 mkdir。只给写路径用。
#
# 之前只有一个 project_root(), 无条件 mkdir —— 于是 project_exists()
# 「检查这本书存不存在」这个动作本身就会把目录建出来。生产上已经能看到后果:
# projects/ 下躺着一个字面量名为 `${BOOK}` 的空目录(某处环境变量没展开),
# 而 Web 层任何一条 404 路径(比如 /dashboard/<不存在的书>)都会再盖一个。
# 配合公网可达且未鉴权, 这等于一个可被外部无限造目录的接口。

def project_path(book: str) -> Path:
    """纯路径计算 —— 不创建任何东西。读路径一律用这个。"""
    return ROOT / book


def project_root(book: str) -> Path:
    """写路径: 返回项目根并确保存在。新建项目请走 init_project()。"""
    p = project_path(book)
    p.mkdir(parents=True, exist_ok=True)
    return p


def chapters_path(book: str) -> Path:
    return project_path(book) / "chapters"


def chapters_dir(book: str) -> Path:
    d = chapters_path(book)
    d.mkdir(parents=True, exist_ok=True)
    return d


def memory_path(book: str) -> Path:
    return project_path(book) / "memory"


def memory_dir(book: str) -> Path:
    d = memory_path(book)
    d.mkdir(parents=True, exist_ok=True)
    return d


def summaries_path(book: str) -> Path:
    """Per-chapter rolling narrative summaries (~200 chars each)."""
    return project_path(book) / "summaries"


def summaries_dir(book: str) -> Path:
    d = summaries_path(book)
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(book: str) -> Path:
    return project_path(book) / "state.json"


def style_path(book: str) -> Path:
    return project_path(book) / "style.json"


def selfcheck_file(book: str, chapter_id: str) -> Path:
    """纯路径 —— 读 self_check 结果用这个。"""
    return project_path(book) / "self_checks" / f"{chapter_id}.json"


def selfcheck_path(book: str, chapter_id: str) -> Path:
    """写路径: 顺带确保 self_checks/ 存在。"""
    d = project_path(book) / "self_checks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{chapter_id}.json"

# ── JSON helpers ────────────────────────────────────────────────────────────

def read_json(book: str, filename: str) -> dict[str, Any] | None:
    path = project_path(book) / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 静默返回 None 是个陷阱: 调用方分不清「文件不存在」和「文件损坏」,
        # 于是拿 DEFAULT_* 覆盖写回去, 把损坏固化成默认值。
        # 这里至少留一条痕迹, 让 mark_chapter_completed 之类的读改写能被追到。
        log.warning("读 JSON 失败, 按『不存在』处理 (book=%s file=%s) —— "
                    "若该文件本应存在, 写回时会用默认值覆盖", book, filename,
                    exc_info=True)
        return None


def write_json(book: str, filename: str, data: dict[str, Any], indent: int = 2) -> None:
    """原子写。

    之前是裸 path.write_text —— 进程在写一半时被杀, 留下半截 JSON。
    read_json 对半截 JSON 静默返回 None, mark_chapter_completed 随即用
    {**DEFAULT_PROGRESS} 覆盖, chapters_completed 整段清空且全程无报错。
    临时文件 + os.replace 保证读者要么看到旧的完整内容, 要么看到新的完整内容。
    """
    path = project_path(book) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── chapter I/O ─────────────────────────────────────────────────────────────

def read_chapter(book: str, chapter_id: str) -> Optional[str]:
    path = chapters_path(book) / f"{chapter_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None

def write_chapter(book: str, chapter_id: str, content: str,
                  allow_overwrite: bool = False) -> None:
    """写章节。

    allow_overwrite=False(默认)时, 若目标已存在且非空且内容不同, 则拒绝写入。
    产品问题: novel.py:312 用 progress.current_chapter 算 start, 断点可能落在
    一个【已写完但未记账】的章节上。实测 projects/测试书籍: 磁盘有 ch_008
    (2972 字), chapters_completed 里没有它, current_chapter=7 → continue 从
    ch_008 开始写, 会把已完成的正文整个重写掉; 而该书既无 versions 快照, 两个
    tar 备份也都不含 ch_008 —— 覆盖后不可恢复。

    合法覆盖的路径(self_check.rewrite_chapter 自检重写)必须显式传 True。
    """
    path = chapters_dir(book) / f"{chapter_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # v1.2 M3: 读老内容, 写完后再 snapshot
    old_content = read_chapter(book, chapter_id)
    if (not allow_overwrite and old_content and old_content.strip()
            and old_content != content):
        raise FileExistsError(
            f"章节 {chapter_id} 已存在且非空({len(old_content)} 字), 拒绝覆盖。\n"
            f"  确认要重写 → 调用方传 allow_overwrite=True\n"
            f"  断点错位   → 修 progress.json 的 current_chapter, "
            f"或把 {chapter_id} 加入 chapters_completed"
        )
    path.write_text(content, encoding="utf-8")
    if old_content != content:
        try:
            from . import version as _v
            _v.create_version(
                book, chapter_id, content, trigger="auto",
                meta={"prev_chars": len(old_content) if old_content else 0,
                      "new_chars": len(content)},
            )
        except Exception as e:
            # 这里原本是静默 pass。语义本身对(快照失败不该中断写作),
            # 但这份快照是覆盖前【唯一的回退手段】: 旧正文已经写掉了。
            # 静默失败意味着用户永远不知道自己的作品已无版本可回退。
            # 产品实测: 该书无 versions/ 目录, 快照从来就没成功建立过。
            log.error("版本快照失败, 旧正文已被覆盖且无版本可回退 "
                      "(book=%s ch=%s 旧 %d 字 → 新 %d 字): %s: %s",
                      book, chapter_id, len(old_content or ""), len(content),
                      type(e).__name__, e, exc_info=True)

def count_words(text: str) -> int:
    """全仓唯一的字数口径。

    之前这里写的是 len(re.findall(r"[\\u4e00-\\u9fff]+", text)): 那个 `+` 量词
    数的是【连续片段】而不是【字】。实测 projects/测试书籍/ch_001.md
    (真实汉字 2603) 旧口径给出 315, 偏低 8.3 倍, 而该值会落库并被
    novel.py 的全书总字数导出、review 列表、get_review_queue 一起消费。

    英文词另按 3 字母以上计一个。两段合起来是全仓口径, 别再各写一份正则 ——
    tools/migrate_to_sqlite.py 曾是第二份实现。
    """
    return (len(re.findall(r"[\u4e00-\u9fff]", text))
            + len(re.findall(r"[a-zA-Z]{3,}", text)))


def list_chapters(book: str) -> list[dict]:
    """Return sorted list of {id, title, word_count, preview} from chapters dir.
    Also syncs metadata to SQLite (if lib.db available)."""
    chapters = []
    for p in sorted(chapters_path(book).glob("*.md")):
        text = p.read_text(encoding="utf-8")
        # First H1 or H2 is the title
        m = re.search(r"^#+\s+(.+)$", text, re.MULTILINE)
        title = m.group(1).strip() if m else p.stem
        wc = count_words(text)
        # Preview: first non-empty paragraph after the title (max 50 chars)
        body = text[m.end():] if m else text
        body_lines = [l.strip() for l in body.splitlines() if l.strip()]
        preview = body_lines[0][:50] + ("…" if len(body_lines[0]) > 50 else "") if body_lines else ""
        chapters.append({"id": p.stem, "title": title, "word_count": wc, "preview": preview})
        # Sync chapter meta to SQLite (v1.3 M6)
        try:
            from . import db as _dbmod
            st = p.stat()
            _dbmod.upsert_chapter_meta(
                ROOT, book, p.stem, title, wc, preview,
                str(p.relative_to(ROOT)), st.st_mtime, st.st_size,
            )
        except Exception:
            pass
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
        if not (memory_path(book) / fname).exists():
            write_json(book, f"memory/{fname}", {} if "json" in fname else [], indent=1)
    # Outline placeholders
    write_json(book, "outline.json", {"meta": {}, "volumes": [], "chapters": []})

def project_exists(book: str) -> bool:
    # 必须走 project_path(纯计算)。之前这里调 project_root(), 而后者会 mkdir ——
    # 于是「书不存在吗」这个问题本身就把书目录建出来了, 404 请求每打一次多一个空目录。
    return (project_path(book) / "config.json").exists()

def list_projects() -> list[str]:
    """Return all project names that have a config.json (sorted)."""
    if not PROJECTS_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROJECTS_ROOT.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    ])
