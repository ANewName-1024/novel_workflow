#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 2 收尾:把 lib.pipeline_v2 的引用全部改到 lib.pipeline.state
==============================================================
lib/pipeline_v2.py -> lib/pipeline/state.py
lib/pipeline.py   -> lib/pipeline/process.py
调用方保留原有别名名(pv2 / pv / _pv2),故函数体一行都不用动。
"""
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SKIP = {".git", "__pycache__", "venv", "node_modules", "build", "mobile",
        ".dart_tool", "projects", "dist", ".pytest_cache"}

# 顺序敏感:先改 import 语句,再改字符串字面量
RULES = [
    (re.compile(r"from\s+lib\s+import\s+pipeline_v2\s+as\s+(\w+)"),
     r"from lib.pipeline import state as \1"),
    (re.compile(r"from\s+\.\s+import\s+pipeline_v2\s+as\s+(\w+)"),
     r"from .pipeline import state as \1"),
    (re.compile(r"from\s+lib\.pipeline_v2\s+import\s+([\w, ]+)"),
     r"from lib.pipeline.state import \1"),
    (re.compile(r"from\s+\.pipeline_v2\s+import\s+([\w, ]+)"),
     r"from .pipeline.state import \1"),
    # patch("lib.pipeline.state.X") / patch("....pipeline_v2.X")
    (re.compile(r"([\"'])((?:lib\.)?pipeline_v2)\."), r"\1lib.pipeline.state."),
    (re.compile(r"([\"'])lib\.pipeline_v2\b"), r"\1lib.pipeline.state"),
]

changed = []
for p in pathlib.Path(".").rglob("*.py"):
    if any(part in SKIP for part in p.parts):
        continue
    try:
        t = p.read_text(encoding="utf-8")
    except Exception:
        continue
    o = t
    for pat, rep in RULES:
        t = pat.sub(rep, t)
    if t != o:
        p.write_text(t, encoding="utf-8", newline="\n")
        changed.append(str(p).replace("\\", "/"))

print("改动文件:")
for c in sorted(changed):
    print("  ", c)

print()
print("=" * 72)
print("残留的 pipeline_v2 代码引用(排除注释/docstring)")
print("=" * 72)
n = 0
for p in pathlib.Path(".").rglob("*.py"):
    if any(part in SKIP for part in p.parts):
        continue
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        continue
    for i, l in enumerate(lines, 1):
        if "pipeline_v2" not in l:
            continue
        s = l.strip()
        if s.startswith("#") or s.startswith('"""') or '"""' in s or s.startswith("'"):
            continue
        if "audit_pipeline" in str(dp) or "diagnose" in str(dp):
            continue
        print(f"  {str(dp).replace(chr(92), '/')}:{i}  {s[:70]}")
        n += 1
print(f"  合计 {n} 处")
print("=" * 72)
