"""tools/find_best_effort_sites.py — 找出真正该用 best_effort() 的位点。

背景: lib/pipeline/errors.py 的 best_effort() 自 1d0e70c 起就定义好并有 9 处测试,
但生产代码零使用 —— 是架台。它真正的用处是替代「静默吞异常」:

    except Exception:
        pass

这类位点的特征(与 L98 区分):
  - 不返回任何值(不是「有歧义的返回值」, 那是 f34aa40 处理的)
  - 失败后流程继续(刻意的 best-effort 语义)
  - 但**不留任何记录** —— 这才是要修的

按 L98: 刻意 best-effort 是对的, 静默才是错的。所以本脚本只找「静默且
无返回值」的位点, 不碰有歧义返回值的(那些已处理), 也不碰 bool 返回的。
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TARGETS = [
    "lib/chapter.py", "lib/memory.py", "lib/db.py", "lib/outline_editor.py",
    "lib/pipeline/process.py", "lib/pipeline/state.py", "lib/review_actions.py",
    "lib/entity_diff.py", "lib/summary.py", "lib/extract.py",
]


def has_log(node: ast.ExceptHandler) -> bool:
    for s in ast.walk(node):
        if isinstance(s, ast.Call) and getattr(s.func, "attr", "") in (
                "warning", "error", "info", "debug", "exception", "critical"):
            return True
    return False


def has_raise(node: ast.ExceptHandler) -> bool:
    return any(isinstance(s, ast.Raise) for s in ast.walk(node))


def has_return(node: ast.ExceptHandler) -> bool:
    return any(isinstance(s, ast.Return) for s in ast.walk(node))


def main() -> int:
    rows = []
    for rel in TARGETS:
        p = REPO / rel
        if not p.exists():
            continue
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()

        funcs = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((n.lineno, n.end_lineno or 0, n.name))
        funcs.sort()

        for h in ast.walk(tree):
            if not isinstance(h, ast.ExceptHandler):
                continue
            if has_log(h) or has_raise(h):
                continue
            # 有返回值 -> 歧义类, f34aa40 已单独处理, 不在此列
            if has_return(h):
                continue
            # 主体只有 pass/continue -> 纯静默
            kinds = []
            for st in h.body:
                if isinstance(st, ast.Pass):
                    kinds.append("pass")
                elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
                    kinds.append("ellipsis")
                else:
                    kinds.append("stmt")
            if kinds != ["pass"]:
                continue
            owner = "?"
            for lo, hi, nm in funcs:
                if lo <= h.lineno <= hi:
                    owner = nm
            try:
                t = ast.unparse(h.type) if h.type else "BARE"
            except Exception:
                t = "?"
            rows.append((rel, h.lineno, owner, t, lines[h.lineno - 1].strip()))

    print("=" * 76)
    print(f"纯静默 best-effort 位点: {len(rows)} 处")
    print("=" * 76)
    by = defaultdict(list)
    for rel, ln, fn, t, txt in rows:
        by[rel].append((ln, fn, t))

    for rel, items in sorted(by.items(), key=lambda x: -len(x[1])):
        print(f"\n  {rel}  ({len(items)} 处)")
        for ln, fn, t in items:
            bare = "  [裸 except]" if t == "BARE" else ""
            broad = "  [捕获过宽]" if t == "Exception" else ""
            print(f"      L{ln:<5} {fn}()   except {t}{bare}{broad}")
    print()
    print(f"合计 {len(rows)} 处 —— 这些是 best_effort() 的合法落点")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())