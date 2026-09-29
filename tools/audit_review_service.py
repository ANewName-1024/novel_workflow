"""tools/audit_review_service.py — 查 review_service 的 8 处静默吞异常。

背景: 最开始的审计(tools/audit_excepts.py)就把 review_service.py 列为
except 密集模块(12 处, 其中 8 处 SWALLOW), 但后续几轮聚焦在 pipeline 与
chapter, 这 8 处一直没处理。

评审数据的写入失败如果被静默吞掉, 后果与 f34aa40 修的那 7 处同类:
UI 会展示过期的评审结果, 而用户以为那是最新的 —— 「失败伪装成成功」。

本脚本区分三类:
  A. 写操作吞掉异常  → 用户改了东西, 系统假装改成功了 —— 最危险
  B. 读操作吞掉异常  → 与 f34aa40 同类, 需记日志
  C. 刻意 best-effort → 按 L98 不动
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "lib" / "review_service.py"

# 写操作关键词 —— 出现在函数名里就归 A 类
WRITE_HINTS = ("save", "write", "update", "insert", "create", "delete",
               "approve", "reject", "edit", "flag", "mark", "apply")


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    funcs = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            funcs.append((n.lineno, n.end_lineno or 0, n.name))
    funcs.sort()

    rows = []
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler):
            continue
        has_log = any(
            isinstance(s, ast.Call) and getattr(s.func, "attr", "") in
            ("warning", "error", "info", "debug", "exception", "critical")
            for s in ast.walk(h))
        if has_log:
            continue
        owner = ("?", 0)
        for lo, hi, nm in funcs:
            if lo <= h.lineno <= hi and h.lineno >= owner[1]:
                owner = (nm, lo)
        is_write = any(k in owner[0].lower() for k in WRITE_HINTS)
        kinds = [type(s).__name__ for s in h.body]
        pure_swallow = kinds == ["Pass"] or (
            len(h.body) == 1 and isinstance(h.body[0], ast.Expr)
            and isinstance(h.body[0].value, ast.Constant))
        rows.append({
            "line": h.lineno, "func": owner[0], "write": is_write,
            "swallow": pure_swallow, "kinds": kinds,
        })

    a = [r for r in rows if r["write"] and r["swallow"]]
    b = [r for r in rows if not r["write"] and r["swallow"]]
    c = [r for r in rows if not r["swallow"]]

    print("=" * 74)
    print(f"review_service.py  静默 handler 共 {len(rows)} 处")
    print(f"  A 写操作静默 (最危险) : {len(a)}")
    print(f"  B 读操作静默          : {len(b)}")
    print(f"  C 有其他处理          : {len(c)}")
    print("=" * 74)

    print()
    print("### A. 写操作失败被吞 —— 用户以为改成功了")
    print("-" * 74)
    for r in sorted(a, key=lambda x: x["line"]):
        body = "\n".join(lines[r["line"]:r["line"] + 3])
        print(f"  L{r['line']:<5} {r['func']}()")
        for i, l in enumerate(body.splitlines()[:3]):
            print(f"        {l.strip()[:66]}")

    print()
    print("### B. 读操作静默")
    print("-" * 74)
    for r in sorted(b, key=lambda x: x["line"]):
        print(f"  L{r['line']:<5} {r['func']}()   体: {r['kinds']}")

    print()
    print("### C. 捕获了异常但有后续处理(非静默, 仅供对照)")
    print("-" * 74)
    for r in sorted(c, key=lambda x: x["line"]):
        print(f"  L{r['line']:<5} {r['func']}()   体: {r['kinds']}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())