"""tools/inspect_save_outline.py — 查 save_outline 的静默失败是否等于数据丢失。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {"__pycache__", "node_modules", "mobile", "venv", ".git"}


def main() -> int:
    p = REPO / "lib" / "outline_editor.py"
    src = p.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "save_outline":
            print("=" * 72)
            print(f"save_outline   L{n.lineno}-L{n.end_lineno}")
            print("=" * 72)
            for i in range(n.lineno - 1, n.end_lineno):
                print(f"  L{i + 1}: {lines[i]}")
            break

    print()
    print("=" * 72)
    print("调用点(save_outline)")
    print("=" * 72)
    for f in sorted(REPO.rglob("*.py")):
        if any(s in f.parts for s in SKIP):
            continue
        try:
            s = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "def save_outline" in s:
            continue
        for i, line in enumerate(s.splitlines(), 1):
            if "save_outline" in line:
                rel = str(f.relative_to(REPO)).replace("\\", "/")
                print(f"  {rel}:{i:<5} {line.strip()[:70]}")

    print()
    print("=" * 72)
    print("HTTP 层怎么处理它的失败(outline 蓝图)")
    print("=" * 72)
    bp = REPO / "review_ui" / "bp" / "outline.py"
    if bp.exists():
        s = bp.read_text(encoding="utf-8")
        lines = s.splitlines()
        tree = ast.parse(s)
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and "outline" in n.name.lower():
                body = "\n".join(lines[n.lineno - 1:n.end_lineno])
                if "save_outline" in body or "save_node" in body:
                    print(f"  --- {n.name}  L{n.lineno} ---")
                    for i in range(n.lineno - 1, n.end_lineno):
                        print(f"    L{i + 1}: {lines[i]}")
                    print()
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())