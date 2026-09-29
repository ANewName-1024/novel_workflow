#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小说工作流系统诊断 —— 找出真实问题,不看表面。"""
import ast
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
SKIP = ("venv", ".git", "__pycache__", "node_modules", "ephemeral",
        "build", ".dart_tool")

print("=" * 78)
print("1. 大文件 / 复杂度")
print("=" * 78)

pyfiles = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if not any(s in d for s in SKIP)]
    for f in fn:
        if f.endswith(".py"):
            pyfiles.append(os.path.join(dp, f))

rows = []
for p in pyfiles:
    try:
        src = open(p, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        continue
    lines = len(src.splitlines())
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    routes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and any(getattr(d, "id", "") in ("route", "get", "post", "put", "delete", "patch")
                      for d in n.decorator_list)]
    longest = max((len(ast.get_source_segment(src, f) or "") for f in funcs), default=0)
    # 圈复杂度近似:分支数
    branches = sum(1 for n in ast.walk(tree)
                   if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                     ast.BoolOp, ast.IfExp, ast.comprehension)))
    rows.append({
        "rel": os.path.relpath(p, ROOT).replace("\\", "/"),
        "lines": lines, "funcs": len(funcs), "classes": len(classes),
        "routes": len(routes), "longest_fn": longest, "branches": branches,
        "cc": branches / max(1, len(funcs)),
    })

rows.sort(key=lambda x: -x["lines"])
print(f"  {'文件':<34}{'行':>6}{'函数':>6}{'类':>4}{'路由':>5}{'最长函数':>8}{'分支/函数':>9}")
for r in rows[:14]:
    print(f"  {r['rel'][:33]:<34}{r['lines']:>6}{r['funcs']:>6}{r['classes']:>4}"
          f"{r['routes']:>5}{r['longest_fn']:>8}{r['cc']:>9.1f}")

print()
print("  ⚠ 告警:")
for r in rows:
    if r["lines"] > 600:
        print(f"    [{r['rel'][:40]}] {r['lines']} 行 —— 建议拆分")
    if r["longest_fn"] > 80:
        print(f"    [{r['rel'][:40]}] 单函数 {r['longest_fn']} 行 —— 建议拆解")
    if r["routes"] > 25:
        print(f"    [{r['rel'][:40]}] {r['routes']} 个路由 —— 建议按域分组")

# ---- 2. pipeline 重复 ----
print()
print("=" * 78)
print("2. pipeline.py vs pipeline_v2.py 重复度")
print("=" * 78)
p1 = os.path.join(ROOT, "lib", "pipeline.py")
p2 = os.path.join(ROOT, "lib", "pipeline_v2.py")
if os.path.exists(p1) and os.path.exists(p2):
    s1 = open(p1, encoding="utf-8").read()
    s2 = open(p2, encoding="utf-8").read()

    def funcs_of(src):
        t = ast.parse(src)
        return {n.name: n.lineno for n in t.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}

    f1, f2 = funcs_of(s1), funcs_of(s2)
    dup = set(f1) & set(f2)
    print(f"  pipeline.py   定义 {len(f1)}: {', '.join(sorted(f1)[:6])}")
    print(f"  pipeline_v2.py 定义 {len(f2)}: {', '.join(sorted(f2)[:6])}")
    print(f"  重名(疑似重复): {len(dup)} 个 -> {', '.join(sorted(dup)[:8])}")
    l1 = set(x.strip() for x in s1.splitlines() if len(x.strip()) > 30)
    l2 = set(x.strip() for x in s2.splitlines() if len(x.strip()) > 30)
    common = l1 & l2
    print(f"  实文本重复行: {len(common)} 行 (共 {len(l1)}+{len(l2)})")
    print()
    # 引用方
    for name in ("pipeline_v2", "pipeline"):
        refs = []
        for p in pyfiles:
            if "pipeline" in p:
                continue
            try:
                src = open(p, encoding="utf-8").read()
            except Exception:
                continue
            if re.search(rf"\b(import|from)\s+\S*{name}\b", src):
                refs.append(os.path.relpath(p, ROOT).replace("\\", "/"))
        print(f"  引用 {name:<12}: {len(refs)} 个文件")
        for r in refs[:6]:
            print(f"      {r}")

# ---- 3. 全局状态 / 循环导入 ----
print()
print("=" * 78)
print("3. 技术债信号")
print("=" * 78)
issues = defaultdict(list)
for p in pyfiles:
    try:
        src = open(p, encoding="utf-8").read()
    except Exception:
        continue
    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    n = len(re.findall(r"^\s*global\s+\w+", src, re.M))
    if n >= 3:
        issues["global 可变状态"].append(f"{rel} ({n} 处)")
    if re.search(r"except\s*:\s*\n\s*pass", src):
        issues["裸 except 吞异常"].append(rel)
    if len(re.findall(r"\bexcept\b", src)) >= 8:
        issues["except 密集(可能过度捕获)"].append(f"{rel} ({len(re.findall(r'\bexcept\b', src))} 处)")
    if re.search(r"#\s*TODO|#\s*FIXME|#\s*HACK|#\s*XXX", src):
        c = len(re.findall(r"#\s*(TODO|FIXME|HACK|XXX)", src))
        issues["TODO/FIXME"].append(f"{rel} ({c})")
    if re.search(r"input\s*\(", src) and "novel.py" in rel:
        issues["CLI 直接 input()"].append(rel)
    if len(re.findall(r"requests\.(get|post)", src)) >= 3:
        issues["裸 requests 无重试"].append(rel)
for k, v in issues.items():
    print(f"  [{k}] {len(v)} 处")
    for x in v[:5]:
        print(f"      {x}")

# ---- 4. 测试 ----
print()
print("=" * 78)
print("4. 测试现状")
print("=" * 78)
t = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                   cwd=ROOT, capture_output=True, timeout=180)
out = (t.stdout + t.stderr).decode("utf-8", "replace")
m = re.search(r"(\d+)\s+tests? collected", out)
if m:
    print(f"  收集到测试: {m.group(1)} 个")
else:
    lines = [l for l in out.splitlines() if "test" in l.lower()][-3:]
    print("  收集失败或无输出:")
    for l in lines:
        print("   ", l[:100])
print()
print("=" * 78)
