"""tools/verify_db_read_path_live.py — 证明 DB 读路径真的通了, 不是在静默回退。

为什么要专门验证: 743 个测试全绿, 但如果 review_service.get_review() 里
db.get_review() 仍在抛异常、被 except 吞掉后回退读文件, 测试同样会绿 ——
因为文件侧数据是全的。所以「测试通过」不等于「DB 路径被使用」。

三个独立证据:
  1. db.get_review() 直接调用, 不抛异常
  2. review_service.get_review() 读回时【不产生】我加的那条 WARNING 日志
     (5ac8df0 加的: "SQLite 评审读取失败,回退文件")
  3. DB 行不存在 与 DB 读失败 是两种情况, 处理必须不同:
       - 行不存在是正常状态(首次保存前/手工清理后), 静默回退, 不报警
       - 读失败意味着镜像可能已失效, 必须报警(5ac8df0 的初衷)
     我第一版的检查写成了「无行时应有回退日志」, 那是错的 ——
     正常状态报 warning 只会把真故障埋掉。
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib import review_service as revserv  # noqa: E402
from lib import storage, db as _db  # noqa: E402

FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAIL
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAIL += 1


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dbverify_"))
    storage.PROJECTS_ROOT = tmp / "projects"
    storage.PROJECTS_ROOT.mkdir(parents=True)
    storage.ROOT = storage.PROJECTS_ROOT

    book = "verify_book"
    storage.init_project(book, {"book_name": book, "genre": "测试"})
    ch = "ch_001"
    (storage.chapters_dir(book) / f"{ch}.md").write_text("正文\n", encoding="utf-8")

    rec = revserv._empty_record(ch)
    rec["status"] = "pending_review"
    rec["auto_severity"] = "high"
    rec["history"] = [{"at": "t1", "action": "auto_flagged", "by": "AI"}]
    revserv.save_review(book, rec)

    print()
    print("=" * 72)
    print("1. db.get_review() 直接调用不抛异常")
    print("=" * 72)
    try:
        row = _db.get_review(storage.ROOT, book, ch)
        check("返回行", row is not None)
        check("带 chapter_id 兼容字段", bool(row and row.get("chapter_id") == ch))
        check("history 已从 history_json 解出",
              bool(row and row.get("history")))
        check("history 长度正确", bool(row and len(row["history"]) == 1),
              f"实得 {row.get('history') if row else None}")
    except Exception as e:
        check("未抛异常", False, f"{type(e).__name__}: {e}")
        row = None

    print()
    print("=" * 72)
    print("2. review_service.get_review() 不产生回退日志")
    print("=" * 72)
    logger = logging.getLogger("novel.lib.review_service")
    if not logger.name.endswith("review_service"):
        logger = logging.getLogger("lib.review_service")
    captured = []

    class Cap(logging.Handler):
        def emit(self, record):
            captured.append(record)

    h = Cap()
    for lg in (logging.getLogger("lib.review_service"),
               logging.getLogger("review_service")):
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)

    got = revserv.get_review(book, ch)
    warns = [r for r in captured if r.levelno >= logging.WARNING]
    check("读回成功", got is not None)
    check("无回退警告", not warns,
          f"实得 {[r.getMessage() for r in warns][:2]}")
    check("读回的 history 非空", bool(got and got.get("history")),
          f"实得 {got.get('history') if got else None}")

    print()
    print("=" * 72)
    print("3. DB 无行 vs DB 读失败 —— 两者必须能区分")
    print("=" * 72)
    # 3a. DB 无行: 正常状态, 静默回退是对的(记 warning 反而是噪音)
    conn = _db.get_conn(_db.default_db_path(storage.ROOT))
    conn.execute("DELETE FROM reviews WHERE book=? AND ch_id=?", (book, ch))
    conn.commit()

    captured.clear()
    got2 = revserv.get_review(book, ch)
    warns2 = [r for r in captured if r.levelno >= logging.WARNING]
    check("DB 无行时仍能读到(来自文件)", got2 is not None)
    check("DB 无行时保持静默(正常状态, 不该报警)", not warns2,
          f"实得 {[r.getMessage() for r in warns2][:2]}")
    check("兜底读到的 history 仍完整", bool(got2 and got2.get("history")),
          f"实得 {got2.get('history') if got2 else None}")

    # 3b. DB 读【失败】: 必须报警, 否则镜像失效完全不可见
    #     这正是 5ac8df0 加日志的初衷。
    real_get = _db.get_review

    def boom(*a, **k):
        raise RuntimeError("模拟 SQLite 读取失败")

    _db.get_review = boom
    try:
        captured.clear()
        got3 = revserv.get_review(book, ch)
        warns3 = [r for r in captured if r.levelno >= logging.WARNING]
        check("DB 读失败时仍能回退(不中断业务流程)", got3 is not None)
        check("DB 读失败时必须报警", bool(warns3),
              "5ac8df0 加的警告没有触发 —— 镜像失效会完全静默")
    finally:
        _db.get_review = real_get

    print()
    print("=" * 72)
    print(f"结论: {'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())