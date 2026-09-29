"""tools/diff_batch_approve_reject.py — 看这两个批量接口到底差在哪。"""
from __future__ import annotations

import ast
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REL = "review_ui/bp/review.py"
A, B = "api_batch_approve", "api_batch_reject"


def get_fn(src: str, name: str):
    tree = ast.parse(src)
    lines = src.splitlines()
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return lines, n.lineno, n.end_lineno or n.lineno
    return lines, 0, 0


def main() -> int:
    src = (REPO / REL).read_text(encoding="utf-8")
    lines, la1, la2 = get_fn(src, A)
    _, lb1, lb2 = get_fn(src, B)
    print("=" * 74)
    print(f"{A}  L{la1}-L{la2}")
    print("=" * 74)
    for i in range(la1 - 1, la2):
        print(f"  {lines[i]}")
    print()
    print("=" * 74)
    print(f"{B}  L{lb1}-L{lb2}")
    print("=" * 74)
    for i in range(lb1 - 1, lb2):
        print(f"  {lines[i]}")
    print()
    print("=" * 74)
    print("diff")
    print("=" * 74)
    sa = "\n".join(lines[la1 - 1:la2]).splitlines()
    sb = "\n".join(lines[lb1 - 1:lb2]).splitlines()
    for line in difflib.unified_diff(sa, sb, fromfile=A, tofile=B,
                                     lineterm="", n=2):
        print(f"  {line}")

    print()
    print("=== 两者的路由装饰器 ===")
    for i in range(max(0, la1 - 6), la2):
        if "@bp.route" in lines[i] or ".route(" in lines[i]:
            print(f"  L{i + 1}: {lines[i].strip()}")
    for i in range(max(0, lb1 - 6), lb2):
        if "@bp.route" in lines[i] or ".route(" in lines[i]:
            print(f"  L{i + 1}: {lines[i].strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())