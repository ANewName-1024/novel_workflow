#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 2 调查: pipeline.py (v1) vs pipeline_v2.py (v2)
====================================================
要回答的问题:
  1. 两者是「复制」还是「两代实现」?  若有业务能力只在一边,不能直接删
  2. 公开符号清单,谁被外部引用
  3. v2 是否已覆盖 v1 的能力(否则合并会丢功能)
"""
import ast
import os
import re
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
P1 = os.path.join(ROOT, "lib", "pipeline.py")
P2 = os.path.join(ROOT, "lib", "lib.pipeline.state.py")
SKIP = ("__pycache__", ".git", "venv", "node_modules", "build", "mobile", ".dart_tool")


def api_of(path):
    src = open(path, encoding="utf-8").read()
    t = ast.parse(src)
    pub, priv = {}, {}
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            (pub if not n.name.startswith("_") else priv)[n.name] = n.lineno
        elif isinstance(n, ast.ClassDef):
            methods = [m.name for m in n.body if isinstance(m, ast.FunctionDef)]
            (pub if not n.name.startswith("_") else priv)[n.name] = {
                "line": n.lineno, "methods": methods}
    return src, t, pub, priv


s1, t1, pub1, priv1 = api_of(P1)
s2, t2, pub2, priv2 = api_of(P2)

print("=" * 76)
print("1. 两套实现的规模与结构")
print("=" * 76)
for label, path, src, t, pub, priv in (("v1 pipeline.py", P1, s1, t1, pub1, priv1),
                                       ("v2 pipeline_v2.py", P2, s2, t2, pub2, priv2)):
    lines = len(src.splitlines())
    funcs = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    exc = len(re.findall(r"\bexcept\b", src))
    print(f"  {label:<20} {lines:>4} 行   函数 {len(funcs):>3}   except {exc:>3}   "
          f"类 {sum(1 for n in t.body if isinstance(n, ast.ClassDef))}")

print()
print("=" * 76)
print("2. 公开符号对比")
print("=" * 76)
k1, k2 = set(pub1), set(pub2)
print(f"  仅 v1 有 ({len(k1 - k2)}): {sorted(k1 - k2)}")
print(f"  仅 v2 有 ({len(k2 - k1)}): {sorted(k2 - k1)}")
print(f"  两边都有 ({len(k1 & k2)}): {sorted(k1 & k2)}")

print()
print("  v1 私有辅助:")
for n in sorted(priv1):
    print(f"    {n}")
print("  v2 私有辅助:")
for n in sorted(priv2):
    print(f"    {n}")

print()
print("=" * 76)
print("3. 类的方法对比(PipelineRunner vs PipelineV2)")
print("=" * 76)


def methods_of(src, cls):
    t = ast.parse(src)
    for n in t.body:
        if isinstance(n, ast.ClassDef) and n.name == cls:
            return {m.name: m.lineno for m in n.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {}


m1 = methods_of(s1, "PipelineRunner")
m2 = methods_of(s2, "PipelineV2")
print(f"  PipelineRunner  方法 {len(m1)}: {sorted(m1)}")
print()
print(f"  PipelineV2      方法 {len(m2)}: {sorted(m2)}")
print()
only1 = set(m1) - set(m2)
only2 = set(m2) - set(m1)
print(f"  ⚠ 仅 v1 PipelineRunner 有 ({len(only1)}): {sorted(only1)}")
if only1:
    print("     -> 这些能力 v2 没有,直接删会丢功能")
print(f"  仅 v2 PipelineV2 有 ({len(only2)}: {sorted(only2)})")

# ── 外部引用面 ──
print()
print("=" * 76)
print("4. 外部引用面(谁 import 了什么)")
print("=" * 76)
files = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if not any(s in d for s in SKIP)]
    for f in fn:
        if f.endswith(".py"):
            files.append(os.path.join(dp, f))

use1, use2 = defaultdict(list), defaultdict(list)
for p in files:
    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    if rel in ("lib/pipeline.py", "lib/pipeline_v2.py"):
        continue
    try:
        src = open(p, encoding="utf-8").read()
    except Exception:
        continue
    for m in re.finditer(r"from\s+lib\s+import\s+([^\n#]+)", src):
        for nm in m.group(1).split(","):
            nm = nm.strip()
            if nm == "pipeline_v2":
                use2[rel].append("from lib import pipeline_v2")
            elif nm == "pipeline":
                use1[rel].append("from lib import pipeline")
    for m in re.finditer(r"from\s+lib\.pipeline\s+import\s+([^\n#]+)", src):
        for nm in m.group(1).split(","):
            use1[rel].append(f"pipeline.{nm.strip()}")
    for m in re.finditer(r"from\s+lib\.pipeline_v2\s+import\s+([^\n#]+)", src):
        for nm in m.group(1).split(","):
            use2[rel].append(f"lib.pipeline.state.{nm.strip()}")
    # 实际成员访问
    for m in re.finditer(r"\bpipeline_v2\.(\w+)", src):
        use2[rel].append(f".{m.group(1)}")
    for m in re.finditer(r"\bpipeline\.(\w+)", src):
        use1[rel].append(f".{m.group(1)}")

print(f"  引用 v1 的文件: {len(use1)}")
for f in sorted(use1):
    syms = sorted({s for s in use1[f]})
    print(f"      {f:<42} {syms}")
print()
print(f"  引用 v2 的文件: {len(use2)}")
for f in sorted(use2):
    syms = sorted({s for s in use2[f]})
    print(f"      {f:<42} {syms}")

print()
print("=" * 76)
print("5. v1 是否真被用?(若没人用,v1 可直接退役而非合并)")
print("=" * 76)
prod_users = [f for f in use1 if not f.startswith("tests/")]
if not prod_users:
    print("  ★ v1 在生产代码中零引用 —— 无需合并,可直接退役")
else:
    print(f"  v1 生产引用: {prod_users}")
    print("     -> 需逐个迁移到 v2,或提供兼容转发")
print("=" * 76)
