"""tools/diff_entity_touch.py — entity.py 里 touch() 出现三次, 看是否同一逻辑。"""
from __future__ import annotations

import ast
import difflib
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REL = "lib/entity.py"


def main() -> int:
    src = (REPO / REL).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "touch":
            hits.append(n)
    hits.sort(key=lambda f: f.lineno)

    print("=" * 74)
    print(f"{REL}: touch() 出现 {len(hits)} 次")
    print("=" * 74)

    # 所属类
    owners = {}
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for m in cls.body:
            if isinstance(m, ast.FunctionDef):
                owners[m.lineno] = cls.name

    bodies = []
    for f in hits:
        body = ast.get_source_segment(src, f) or ""
        bodies.append(body)
        print(f"  L{f.lineno:<5} class {owners.get(f.lineno, '?')}")
        for i, l in enumerate(body.splitlines()):
            print(f"        {l}")
        print()

    print("=" * 74)
    print("两两 diff")
    print("=" * 74)
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            d = list(difflib.unified_diff(
                bodies[i].splitlines(), bodies[j].splitlines(),
                fromfile=f"#{i+1} L{hits[i].lineno}",
                tofile=f"#{j+1} L{hits[j].lineno}", lineterm="", n=1))
            print(f"  #{i+1} vs #{j+1}: {'完全相同' if not d else str(len(d)) + ' 行差异'}")
            for line in d[2:]:
                print(f"      {line}")
            print()

    print("=" * 74)
    print("所有类与方法(看是否有多个类共用同名 mixin 方法)")
    print("=" * 74)
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        methods = [m.name for m in cls.body if isinstance(m, ast.FunctionDef)]
        dup = [k for k, v in Counter(methods).items() if v > 1]
        print(f"  class {cls.name:<16} {len(methods)} 方法  {('重复: ' + str(dup)) if dup else ''}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())