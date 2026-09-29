"""tools/diff_duplicate_pairs.py — 逐字 diff 重复函数对, 看清差异在哪。

find_duplicate_logic.py 告诉你「哪两个像」, 但合并前必须知道差在哪。
差异决定了能不能参数化: 只差一个目标文件名的, 抽公共函数;
连语义都不同的, 那是巧合不是重复, 别动。
"""
from __future__ import annotations

import ast
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PAIRS = [
    ("lib/memory.py", "get_events", "get_foreshadowing"),
    ("lib/memory.py", "update_events", "update_foreshadowing"),
    ("lib/memory.py", "get_event", "get_foreshadow"),
    ("lib/memory.py", "list_events", "list_foreshadows"),
    ("lib/memory.py", "delete_event", "delete_foreshadow"),
    ("lib/memory.py", "update_characters", "update_world"),
    ("review_ui/bp/review.py", "api_batch_approve", "api_batch_reject"),
    ("lib/session_log.py", "log_key_decision", "log_unfinished"),
    ("lib/session_log.py", "hook_pipeline_start", "hook_pipeline_done"),
]


def get_fn(rel: str, name: str) -> tuple[str, int, int]:
    src = (REPO / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            lines = src.splitlines()
            body = ast.get_source_segment(src, n) or ""
            return body, n.lineno, n.end_lineno or n.lineno
    return "", 0, 0


def main() -> int:
    for rel, a, b in PAIRS:
        sa, la, _ = get_fn(rel, a)
        sb, lb, _ = get_fn(rel, b)
        print("=" * 76)
        print(f"{rel}  {a}() L{la}   vs   {b}() L{lb}")
        print("=" * 76)
        if not sa or not sb:
            print("  (没找到)")
            continue
        dl = list(difflib.unified_diff(
            sa.splitlines(), sb.splitlines(),
            fromfile=a, tofile=b, lineterm="", n=1))
        if not dl:
            print("  完全相同")
        else:
            for line in dl[2:]:
                print(f"  {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())