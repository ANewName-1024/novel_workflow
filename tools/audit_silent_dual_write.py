"""tools/audit_silent_dual_write.py — 查漏网的「双写失败静默」。

背景: 5ac8df0 给 lib/review_service.py 的 8 处静默补了日志(其中
_save/upsert 那一处正是双写)。但那一轮只扫了 lib/review_service.py,
没扫 review_ui/。今天在 review_ui/bp/projects.py::_update_project()
又看到同一形态:

    storage.write_json(book, "config.json", cfg)      # 主写
    try:
        _dbmod.upsert_project(...)                     # 镜像写
    except Exception:
        pass                                            # 静默

主写在 try 外会正常抛, 被吞的是镜像写 —— 与 review_service 那 8 处定性
完全一致: 语义正确, 只是不可观测。镜像一旦停写, 读回走 DB 优先会拿到
过期数据, 而没有任何信号。

本脚本全仓找这种形态: 同一函数内既有文件写又有 DB 写, 且 DB 写被
except 吞掉且无日志。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "lib", REPO / "review_ui"]
SKIP = {"__pycache__", "node_modules", "mobile", "venv", ".git"}
EXTRA = [REPO / "novel.py"]

FILE_WRITE = ("write_text", "write_json", "write_bytes", "write_chapter",
              "save_json", "atomic_write")
DB_WRITE_HINT = ("upsert", "insert_", "update_", "set_", "commit", "_dbmod",
                 "dbmod", "db.")
LOGGERS = ("warning", "error", "info", "debug", "exception", "critical")


def is_db_write(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        n = node.func.id
    elif isinstance(node.func, ast.Attribute):
        n = node.func.attr
    else:
        return ""
    if n.startswith(("upsert", "insert_", "set_")) or n in ("commit", "update"):
        return n
    if any(h in n for h in ("_dbmod.", "dbmod.")):
        return n
    if n in ("upsert_review", "upsert_project", "upsert_state", "upsert_entity",
             "init_db"):
        return n
    return ""


def main() -> int:
    rows = []
    files = [p for r in ROOTS if r.exists() for p in sorted(r.rglob("*.py"))]
    files += [p for p in EXTRA if p.exists()]

    for p in files:
        if any(s in p.parts for s in SKIP):
            continue
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            fw = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                  and getattr(c.func, "attr", getattr(c.func, "id", "")) in FILE_WRITE]
            if not fw:
                continue
            for h in ast.walk(fn):
                if not isinstance(h, ast.ExceptHandler):
                    continue
                has_log = any(
                    isinstance(s, ast.Call) and getattr(s.func, "attr", "") in LOGGERS
                    for s in ast.walk(h))
                if has_log:
                    continue
                swallow = (len(h.body) == 1 and isinstance(h.body[0], ast.Pass))
                if not swallow:
                    continue
                # DB 写不在 handler 体内, 而在它上面的 try 体里。
                # 第一版把范围限定成 h.lineno..h.end_lineno(只盖住 handler 自己),
                # 于是永远看不到 try 体, 真实存在的位点被报成 0。
                # 改为在【整个函数】里找 DB 写 —— 条件是: 该函数既有主写,
                # 又有被吞的 except, 两件事在同一步发生才有意义。
                dbs = []
                for c in ast.walk(fn):
                    if not isinstance(c, ast.Call):
                        continue
                    d = is_db_write(c)
                    if d:
                        dbs.append((d, c.lineno))
                if dbs:
                    dbs.sort(key=lambda x: x[1])
                    rows.append((rel, fn.name, h.lineno, [d for d, _ in dbs],
                                 dbs[0][1]))

    print("=" * 76)
    print(f"「主写 + 静默镜像写」位点: {len(rows)} 处")
    print("=" * 76)
    if not rows:
        print("  无")
    for rel, fn, ln, dbs, db_line in rows:
        print(f"  {rel}::{fn}()  except@L{ln}")
        print(f"      静默的 DB 写 (L{db_line}): {dbs}")
    print()
    if rows:
        print("  这类位点的语义是对的 —— 主写在 try 外正常抛, 镜像写失败不该中断")
        print("  业务。缺的只是可观测性: 镜像停写时读回走 DB 优先会拿到过期数据,")
        print("  而没有任何信号。处理方式与 5ac8df0 相同: 补一条日志, 不改语义。")
    print("=" * 76)
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())