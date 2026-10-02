"""
Chapter review service: state machine + audit trail for manual review.

v1.3 M6: dual-write to SQLite (lib.db). File kept for git diff + fallback.

Workflow:
  1. After each chapter write, an auto-review record is created based on
     self-check severity.
  2. Human (or scheduled job) reviews flagged chapters and marks them:
     approved / needs_rewrite / human_edited / false_positive.
  3. Audit log captures all state transitions chronologically.

Storage layout under projects/<book>/reviews/:
  ├── ch_001.review.json     # per-chapter review record + history
  ├── ch_002.review.json
  ├── ...
  ├── ch_001.v2.md           # optional human-edited version
  └── audit.log              # chronological log (text)

Status states:
  auto_passed       - auto self-check severity=none/minor (no review needed)
  pending_review    - auto-flagged (moderate/critical), awaiting human
  approved          - human approved as-is
  needs_rewrite     - human rejected, marked for re-write
  human_edited      - human provided edited text (v2.md present)
  false_positive    - human disagrees with auto-flag, archived
"""
from __future__ import annotations
import json, datetime, threading
from pathlib import Path
from typing import Optional
from . import storage, self_check as scmod
import logging

log = logging.getLogger(__name__)

REVIEW_STATUS = {
    "AUTO_PASSED":    "auto_passed",
    "PENDING_REVIEW": "pending_review",
    "APPROVED":       "approved",
    "NEEDS_REWRITE":  "needs_rewrite",
    "HUMAN_EDITED":   "human_edited",
    "FALSE_POSITIVE": "false_positive",
}

# Severity → initial status mapping
SEVERITY_TO_STATUS = {
    "none":     REVIEW_STATUS["AUTO_PASSED"],
    "minor":    REVIEW_STATUS["AUTO_PASSED"],
    "moderate": REVIEW_STATUS["PENDING_REVIEW"],
    "critical": REVIEW_STATUS["PENDING_REVIEW"],
    "unknown":  REVIEW_STATUS["PENDING_REVIEW"],  # conservative default
}

# ── paths ─────────────────────────────────────────────────────────────────

def review_dir(book: str) -> Path:
    d = storage.project_root(book) / "reviews"
    d.mkdir(exist_ok=True)
    return d

def review_path(book: str, chapter_id: str) -> Path:
    return review_dir(book) / f"{chapter_id}.review.json"

def edited_path(book: str, chapter_id: str) -> Path:
    """Where human-edited versions live (ch_001.v2.md, v3.md, ...)."""
    return review_dir(book) / f"{chapter_id}.v2.md"

def audit_log_path(book: str) -> Path:
    return review_dir(book) / "audit.log"

# ── CRUD on review records (SQLite + file dual-write) ─────────────────────

def _empty_record(chapter_id: str) -> dict:
    return {
        "chapter_id": chapter_id,
        "status": REVIEW_STATUS["AUTO_PASSED"],
        "auto_severity": None,
        "auto_issues_count": 0,
        "auto_result": None,
        "reviewer": None,
        "reviewer_notes": None,
        "reviewed_at": None,
        "history": [],
        "created_at": None,
        "updated_at": None,
    }

def get_review(book: str, chapter_id: str) -> dict | None:
    """Read review. SQLite first, .review.json fallback."""
    try:
        from . import db as _dbmod
        r = _dbmod.get_review(storage.ROOT, book, chapter_id)
        if r:
            return r
    except Exception:
        log.warning("SQLite 评审读取失败,回退文件 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass
    p = review_path(book, chapter_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # 评审文件损坏 != 「本章没有评审」。返回 None 一样会被 UI 读成
        # 「无评审」, 所以这里必须留痕。
        log.warning("评审记录损坏,按「无评审」处理: %s | %s: %s",
                    p, type(e).__name__, e)
        return None

def save_review(book: str, record: dict) -> None:
    """Save review. Dual-write: file (for git) + SQLite (for fast query)."""
    record["updated_at"] = datetime.datetime.now().isoformat()
    chapter_id = record.get("chapter_id", "")
    # File write
    review_path(book, chapter_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # SQLite write
    try:
        from . import db as _dbmod
        _dbmod.upsert_review(
            storage.ROOT, book, chapter_id,
            status=record.get("status", "pending_review"),
            auto_severity=record.get("auto_severity"),
            auto_issues_count=record.get("auto_issues_count", 0),
            auto_result=record.get("auto_result"),
            reviewer=record.get("reviewer"),
            reviewer_notes=record.get("reviewer_notes"),
            v2_chars=record.get("v2_chars", 0),
            # 不传 history 的话 DB 侧永远是 []。而 get_review 是 DB 优先,
            # 于是评审审计轨迹(谁在何时批准/拒绝/编辑了哪章)在 UI 上完全不可见 ——
            # 文件里有, 读回来没有, 且没有任何报错。
            history=record.get("history") or [],
        )
    except Exception:
        log.warning("SQLite 评审镜像写失败,仅文件已存 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass

def append_audit(book: str, chapter_id: str, action: str, by: str, notes: str = "") -> None:
    """Append a line to audit.log."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] ch={chapter_id} | action={action} | by={by}"
    if notes:
        notes_short = notes[:200].replace("\n", " ")
        line += f" | notes={notes_short}"
    with audit_log_path(book).open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def record_cli_review(book: str, chapter_id: str, review_text: str,
                      by: str = "CLI") -> dict:
    """把 lib/review.py 产出的自由文本审校结果落到评审记录里。

    为什么需要这个
    --------------
    仓库里有两套审校, 至今互不相通:
      lib/review.py    自由文本 Markdown -> reviews/<ch>.md  (CLI `review` 走这条)
      review_service   结构化 + 状态机 + 待审队列 + audit.log  (Web UI 队列走这条)

    后果是实跑验证过的: `novel.py review probe_run ch_001` 跑完, reviews/ch_001.md
    有 4502 字节正经内容, 但 `novel.py review-queue probe_run` 仍打印
    「✓ 评审队列为空」—— 人工在队列里根本看不到任何待处理的东西。
    审校做过了, 但结论没有进入任何决策流程。

    状态一律落 PENDING_REVIEW, **不落 AUTO_PASSED**
    --------------------------------------------
    自由文本审校没有 severity 字段, 无从判断"通过"与否。而
    「因为没检查过所以判定合格」正是 2026-10-02 被推翻的那种降级
    (见 backfill_missing_reviews 的说明)。既然查了, 就该让人看一眼。

    不覆盖人工结论
    --------------
    若该章已有人工决策(approved / human_edited / needs_rewrite / false_positive),
    只补审计与文本, **不动 status**。CLI 重复跑一次 review 不该把人工改过的
    章打回待审。
    """
    if not (review_text or "").strip():
        # 空审校文本不能记成"审过了"。今天实测踩过: 空串落盘成 0 字节的
        # reviews/<ch>.md, 而队列/状态毫无异常。
        raise ValueError(
            f"章节 {chapter_id} 的审校结果为空, 拒绝记录。"
            f"空审校记录会让队列里出现一个'已审过但没有内容'的条目。"
        )

    existing = get_review(book, chapter_id)
    rec = dict(existing) if existing else _empty_record(chapter_id)
    rec["chapter_id"] = chapter_id

    human_decided = rec.get("status") in {
        REVIEW_STATUS["APPROVED"], REVIEW_STATUS["HUMAN_EDITED"],
        REVIEW_STATUS["NEEDS_REWRITE"], REVIEW_STATUS["FALSE_POSITIVE"],
    }
    if not human_decided:
        rec["status"] = REVIEW_STATUS["PENDING_REVIEW"]
    # 结果正文挂在 auto_result 下, 与 self_check 的结果同字段但带 source 标记,
    # 这样 UI 读到的"AI 结论"不会把自由文本误当成结构化 severity。
    #
    # 合并规则: 同源(cli_review)才覆盖成最新正文; 异源(self_check 的结构化结果)
    # 另起一个键, 不能把自检结论冲掉。
    prev = rec.get("auto_result")
    if isinstance(prev, dict) and prev.get("source") == "cli_review":
        prev["text"] = review_text
    elif isinstance(prev, dict):
        prev["cli_review"] = review_text
    else:
        rec["auto_result"] = {"source": "cli_review", "text": review_text}
    if rec.get("created_at") is None:
        rec["created_at"] = datetime.datetime.now().isoformat()
    save_review(book, rec)
    append_audit(
        book, chapter_id,
        "cli_review_recorded" if not human_decided else "cli_review_attached",
        by,
        notes=("自由文本审校已进待审队列" if not human_decided
               else "已有人工结论, 仅补充审校正文, 未改状态"),
    )
    return rec


# ── transitions ────────────────────────────────────────────────────────────

def auto_flag(book: str, chapter_id: str, self_check_result: dict, by: str = "AI") -> dict:
    """Called by chapter.py post-write pipeline after self-check."""
    sev = self_check_result.get("severity", "unknown")
    issues_count = sum(
        len(self_check_result.get(k, []))
        for k in ("character_inconsistency", "timeline_conflict",
                  "location_or_item_conflict", "foreshadow_problem",
                  "personality_drift")
    )
    new_status = SEVERITY_TO_STATUS.get(sev, REVIEW_STATUS["PENDING_REVIEW"])

    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["chapter_id"]      = chapter_id
    record["auto_severity"]   = sev
    record["auto_issues_count"] = issues_count
    record["auto_result"]     = self_check_result
    record["status"]          = new_status
    record.setdefault("created_at", datetime.datetime.now().isoformat())

    record["history"].append({
        "at":    datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "auto_flagged",
        "by":    by,
        "severity": sev,
        "issues_count": issues_count,
        "new_status": new_status,
    })

    save_review(book, record)
    append_audit(book, chapter_id, "auto_flagged", by,
                 notes=f"severity={sev} issues={issues_count} → {new_status}")
    return record

def approve(book: str, chapter_id: str, reviewer: str, notes: str = "") -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["APPROVED"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "approved",
        "by": reviewer,
        "notes": notes,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "approved", reviewer, notes)
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        log.info("章节完成标记更新失败 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass
    return record

def reject(book: str, chapter_id: str, reviewer: str, reason: str) -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["NEEDS_REWRITE"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = reason
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "needs_rewrite",
        "by": reviewer,
        "reason": reason,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "needs_rewrite", reviewer, reason)
    return record

def edit(book: str, chapter_id: str, reviewer: str, new_text: str, notes: str = "") -> dict:
    """Save human-edited version to reviews/<chapter_id>.v2.md."""
    edited_path(book, chapter_id).write_text(new_text, encoding="utf-8")
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["HUMAN_EDITED"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "human_edited",
        "by": reviewer,
        "notes": notes,
        "edited_chars": len(new_text),
    })
    save_review(book, record)
    append_audit(book, chapter_id, "human_edited", reviewer,
                 notes=f"chars={len(new_text)} {notes}")
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        log.info("章节完成标记更新失败 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass
    return record

def mark_false_positive(book: str, chapter_id: str, reviewer: str, notes: str) -> dict:
    record = get_review(book, chapter_id) or _empty_record(chapter_id)
    record["status"]         = REVIEW_STATUS["FALSE_POSITIVE"]
    record["reviewer"]       = reviewer
    record["reviewer_notes"] = notes
    record["reviewed_at"]    = datetime.datetime.now().isoformat()
    record["history"].append({
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": "false_positive",
        "by": reviewer,
        "notes": notes,
    })
    save_review(book, record)
    append_audit(book, chapter_id, "false_positive", reviewer, notes)
    # L51 修复: false_positive 也算 review 过, 同步 mark chapter completed
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        log.info("章节完成标记更新失败 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass
    return record

def apply_edit_to_chapter(book: str, chapter_id: str) -> bool:
    """Replace chapters/<chapter_id>.md with reviews/<chapter_id>.v2.md (after approval)."""
    src = edited_path(book, chapter_id)
    if not src.exists():
        return False
    dst = storage.chapters_dir(book) / f"{chapter_id}.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        num = int(chapter_id.split("_")[-1]) if chapter_id.startswith("ch_") else None
        storage.mark_chapter_completed(book, chapter_id, num)
    except Exception:
        log.info("章节完成标记更新失败 (book=%s ch=%s)", book, chapter_id, exc_info=True)
        pass
    return True

# ── queries (SQLite first, file fallback) ──────────────────────────────────

def get_review_queue(book: str) -> list[dict]:
    """Return all chapters with status pending_review or needs_rewrite. SQLite first.
    每个 item 额外带 chapter_title (从 chapters 目录解析), 供 /book/<book> 待审表格显示.
    """
    out: list[dict] = []
    try:
        from . import db as _dbmod
        rows = _dbmod.list_reviews(storage.ROOT, book, status="pending_review")
        rows2 = _dbmod.list_reviews(storage.ROOT, book, status="needs_rewrite")
        out = rows + rows2
        if out:
            # 兼容 layer: DB schema 用 ch_id, 上层 API 用 chapter_id
            for r in out:
                if "chapter_id" not in r and "ch_id" in r:
                    r["chapter_id"] = r["ch_id"]
    except Exception:
        # 2026-10-01: 这里过去写的是 chapter_id —— 但本函数签名是 (book) only,
        # 作用域内根本没有这个名字。SQLite 一旦不可用(本该优雅回退到文件),
        # except 块自己先抛 NameError 冒到 Web 层, 待审队列页 500。
        # 与作者已在 db.py:361-372 修过的 `d = dict(r)` 同类复制粘贴残留。
        # 测试盲区: test_review_service.py / test_review_queue_enrich_titles.py
        # 只在 SQLite 正常时跑, 永远走不到这个 except。
        log.warning("SQLite 队列查询失败,回退文件 (book=%s)", book, exc_info=True)
        pass
    if not out:
        for p in sorted(review_dir(book).glob("ch_*.review.json")):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            s = r.get("status")
            if s in ("pending_review", "needs_rewrite"):
                out.append(r)
    # Enrich: cross-ref chapters dir to get titles (前 bug: 待审表格只显示 ch_001, 没标题)
    try:
        ch_map = {ch.get("id"): ch for ch in storage.list_chapters(book)}
        for r in out:
            cid = r.get("chapter_id") or r.get("ch_id", "")
            ch = ch_map.get(cid, {})
            r["chapter_title"] = ch.get("title", "")
            r["word_count"] = ch.get("word_count", 0)
            r["preview"] = ch.get("preview", "")
    except Exception:
        # chapter 目录可能还没创建, 静默 fallback
        for r in out:
            r.setdefault("chapter_title", "")
            r.setdefault("word_count", 0)
            r.setdefault("preview", "")
    return sorted(out, key=lambda r: r.get("chapter_id") or r.get("ch_id", ""))

def get_review_stats(book: str) -> dict:
    """Aggregate counts. SQLite first, file fallback."""
    try:
        from . import db as _dbmod
        return _dbmod.review_stats(storage.ROOT, book)
    except Exception:
        # 2026-10-01: 同 get_review_queue —— chapter_id 不在本函数作用域内。
        log.warning("SQLite 统计查询失败,回退文件 (book=%s)", book, exc_info=True)
        pass
    counts = {v: 0 for v in REVIEW_STATUS.values()}
    counts["total"] = 0
    for p in review_dir(book).glob("ch_*.review.json"):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        s = r.get("status", "unknown")
        if s in counts:
            counts[s] += 1
        counts["total"] += 1
    return counts


def render_queue(book: str, queue: list[dict]) -> str:
    """Pretty-print the review queue for CLI display."""
    if not queue:
        return "✓ 评审队列为空 (所有章节均已审核或自动通过)"
    lines = [f"\n{'═' * 60}", f"  评审队列 ({len(queue)} 章待审)", f"{'═' * 60}"]
    for r in queue:
        sev = r.get("auto_severity", "?")
        cnt = r.get("auto_issues_count", 0)
        st  = r.get("status", "?")
        rev = r.get("reviewer") or "-"
        lines.append(f"  {r['chapter_id']}  [{st}]  severity={sev}  issues={cnt}  reviewer={rev}")
    lines.append(f"\n查看详情: novel.py review-show <书名> <chapter_id>")
    return "\n".join(lines)

def format_review_record(book: str, record: dict, include_chapter: bool = False,
                          chapter_text_chars: int = 800) -> str:
    """Pretty-print a review record + (optionally) the chapter text."""
    lines = []
    lines.append(f"{'═' * 60}")
    lines.append(f"  {record['chapter_id']} - 评审记录")
    lines.append(f"{'═' * 60}")
    lines.append(f"  状态: {record['status']}")
    if record.get("auto_severity"):
        lines.append(f"  自检严重度: {record['auto_severity']}")
    if record.get("auto_issues_count"):
        lines.append(f"  发现问题: {record['auto_issues_count']} 项")
    if record.get("reviewer"):
        lines.append(f"  审核人: {record['reviewer']}")
    if record.get("reviewed_at"):
        lines.append(f"  审核时间: {record['reviewed_at']}")
    if record.get("reviewer_notes"):
        lines.append(f"  审核备注: {record['reviewer_notes']}")

    lines.append(f"\n  历史 ({len(record.get('history',[]))} 条):")
    for ev in record.get("history", []):
        lines.append(f"    [{ev.get('at','')}] {ev.get('action','?')} by {ev.get('by','?')}")

    ar = record.get("auto_result") or {}
    flagged = []
    for key, label in (
        ("character_inconsistency", "角色一致性"),
        ("timeline_conflict", "时间线"),
        ("location_or_item_conflict", "地点/物品"),
        ("foreshadow_problem", "伏笔"),
        ("personality_drift", "性格漂移"),
    ):
        items = ar.get(key, [])
        if items:
            flagged.append(f"  {label} ({len(items)} 项):")
            for it in items[:3]:
                q = it.get("quote", "")[:80]
                lines_nl = q.replace("\n", " ")
                flagged.append(f"    • {it.get('issue','')[:120]}")
                if lines_nl:
                    flagged.append(f"      「{lines_nl}」")
    if flagged:
        lines.append(f"\n  自检报告摘录:")
        lines.extend(flagged)

    if include_chapter:
        text = storage.read_chapter(book, record["chapter_id"])
        if text:
            lines.append(f"\n  章节正文 ({len(text)} 字):")
            lines.append(f"  {'─' * 50}")
            lines.append(text[:chapter_text_chars])
            if len(text) > chapter_text_chars:
                lines.append(f"  ... (后续省略 {len(text) - chapter_text_chars} 字)")
            lines.append(f"  {'─' * 50}")

    return "\n".join(lines)


_backfill_lock = threading.Lock()
# 记住每本书上次 backfill 时看到的章节指纹。backfill 本身幂等(get_review 命中即
# 跳过), 但每次都全量遍历 chapters 并对每章查一次评审记录 —— 而调用方是
# book_page / api_queue / api_queue_filtered 三条【GET】路由, 也就是每一次页面
# 渲染、每一次浏览器预取、每一个爬虫请求都会跑一遍。
# 2026-10-01: 加锁 + 指纹短路。指纹 = (章节数, chapters 目录 mtime)。目录 mtime
# 在新增/删除章节文件时变化, 足够覆盖「有新章节要补」; 正文改动不影响 mtime,
# 但正文改动不产生新章节, 不需要重跑 backfill。
_backfill_fingerprint: dict[str, tuple] = {}


def _book_chapter_fingerprint(book: str) -> tuple:
    try:
        d = storage.chapters_path(book)
        files = list(d.glob("*.md"))
        return (len(files), d.stat().st_mtime)
    except OSError:
        return (-1, 0.0)


def backfill_missing_reviews(book: str, force: bool = False) -> int:
    """为没有评审记录的章节补建记录, 返回补建的条数。

    force=True 时跳过指纹短路(CLI 显式修复数据时用)。

    这段逻辑此前有两份拷贝: novel.py 的 _ensure_review_for_existing() 与
    review_ui/bp/review.py 的 _ensure_review_backfill()（后者 docstring 写着
    "Same backfill as cmd_review_queue"）。两份都在 6dd453b 修的 bug 上一模一样,
    而我第一次只改了 CLI 那份 —— Web UI 入口继续把损坏的 self_check 当成
    「没有自检」→ 写成 AUTO_PASSED。

    逻辑重复会让「修一半」成为默认结果, 所以合并到这一处, 两个调用方都委派。

    关键语义（6dd453b 建立, 2026-10-02 修正）:
      自检文件不存在 → 转【人工待审】。2026-10-02 之前这里是"默认通过",
                       现在改了 —— 理由见下面「降级方向」一节。
      自检文件损坏   → 记 ERROR 并跳过这一章, 【不落任何评审结论】

    「缺失」与「损坏」必须区分: 损坏是错误, 应当留痕并跳过; 缺失只是没数据,
    同样不该被当成结论。

    降级方向（2026-10-02）
    ----------------------
    原来的语义是「自检文件不存在 → AUTO_PASSED」, 理由是"章节写于自检功能
    之前, 默认通过是既定行为"。实跑证明这个既定行为本身是错的:

        extract 抽不出东西(推理模型把 max_tokens 花在思维链上, 正文为空)
          -> characters/events/foreshadowing 三张表全空
        summary 落盘 0 字节
        自检数据不存在
          -> 写 AUTO_PASSED, 审计备注"章节无自检数据，默认通过"
          -> 队列里显示「已自动通过」, 而实际上没有任何东西检查过这一章

    「因为没检查过所以判定合格」是所有降级里最坏的一种: 缺失被当成了通过,
    且没有任何一处会提醒你缺了。所以改成 PENDING_REVIEW, 让人来决定。
    """
    fp = _book_chapter_fingerprint(book)
    if not force and _backfill_fingerprint.get(book) == fp:
        return 0          # 章节集合没变, 上次已补过

    # 并发 GET 会对同一本书同时进入这段读改写。加锁串行化 —— 没有锁时两个请求
    # 会同时判定「这一章还没有评审记录」, 然后各写一份, 审计轨迹出现重复条目。
    with _backfill_lock:
        if not force and _backfill_fingerprint.get(book) == fp:
            return 0      # 等锁期间别人已经补完了
        chapters = storage.list_chapters(book)
        created = 0
        for ch in chapters:
            if get_review(book, ch["id"]):
                continue
            sc_path = storage.project_root(book) / "self_checks" / f"{ch['id']}.json"
            sc_result = None
            if sc_path.exists():
                try:
                    sc_result = json.loads(sc_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    log.error("自检记录损坏, 本章不做 backfill (book=%s ch=%s): %s: %s",
                              book, ch["id"], type(e).__name__, e)
                    continue
            if sc_result:
                auto_flag(book, ch["id"], sc_result, by="AI-backfill")
            else:
                # 2026-10-02: 降级方向反转。
                #
                # 原来是 `AUTO_PASSED` + 备注"章节无自检数据，默认通过" ——
                # 也就是**因为没检查过所以判定合格**。2026-10-02 实跑撞上的
                # 完整后果: 自检数据缺失 → 自动通过 → 审校记录全 null →
                # 队列里显示"已自动通过" → 而实际上这一章的抽取和摘要都是
                # 空的, 没有任何东西检查过它。
                #
                # 缺失不等于合格。这里改成进人工队列, 让人来决定。
                empty = _empty_record(ch["id"])
                empty["status"] = REVIEW_STATUS["PENDING_REVIEW"]
                empty["auto_result"] = None
                save_review(book, empty)
                append_audit(book, ch["id"], "backfilled_no_selfcheck", "system",
                             notes="章节无自检数据，已转人工待审（不再默认通过）")
                log.info(
                    "章节无自检数据, 转人工待审 (book=%s ch=%s)。"
                    "如需自动通过请先跑 self_check, 或人工 approve。", book, ch["id"])
            created += 1
    _backfill_fingerprint[book] = _book_chapter_fingerprint(book)
    return created
