#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config_loader 调用面调查 —— 改契约前必须知道谁在依赖现有行为。"""
import ast
import os
import re
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
SKIP = ("__pycache__", ".git", "venv", "node_modules", "build", "mobile", ".dart_tool")

files = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if not any(s in d for s in SKIP)]
    for f in fn:
        if f.endswith(".py"):
            files.append(os.path.join(dp, f))

print("=" * 76)
print("1. get_config / reset_cache 调用面")
print("=" * 76)

callers = []           # (rel, lineno, arg?)
mutators = []          # 疑似「拿到 dict 后就地改」的地方
modifiers = []
importers = set()

PAT = re.compile(r"\bget_config\s*\(([^)]*)\)")
RESET = re.compile(r"\breset_cache\s*\(")

for p in files:
    try:
        src = open(p, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        continue
    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    if "config_loader" in src and rel != "lib/config_loader.py":
        for m in re.finditer(r"from\s+lib\.config_loader\s+import\s+([^\n#]+)|"
                             r"from\s+\.config_loader\s+import\s+([^\n#]+)|"
                             r"from\s+lib\s+import\s+config_loader", src):
            g = m.group(1) or m.group(2) or "config_loader"
            for nm in g.split(","):
                nm = nm.strip().split(" as ")[0].strip()
                if nm:
                    importers.add(nm)
    for i, line in enumerate(src.splitlines(), 1):
        for m in PAT.finditer(line):
            callers.append((rel, i, m.group(1).strip()))
        if RESET.search(line):
            callers.append((rel, i, "<reset_cache>"))

# 把 get_config() 的结果赋给变量、再对变量做 mutate 的模式
ASSIGN = re.compile(r"(\w+)\s*=\s*get_config\s*\(\s*\)")
for p in files:
    try:
        src = open(p, encoding="utf-8").read()
    except Exception:
        continue
    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        m = ASSIGN.search(line)
        if not m:
            continue
        var = m.group(1)
        for j in range(i, min(i + 40, len(lines))):
            s = lines[j]
            if re.search(rf"\b{re.escape(var)}\s*\[[^\]]+\]\s*=", s):
                mutators.append((rel, j + 1, s.strip()[:74]))
            if re.search(rf"\b{re.escape(var)}\.(update|setdefault|pop|clear|popitem)\s*\(", s):
                modifiers.append((rel, j + 1, s.strip()[:74]))

print(f"  总调用点: {len(callers)} 处,分布在 {len({c[0] for c in callers})} 个文件")
print(f"  import 的名字: {sorted(importers)}")
print()
args = Counter(c[2] for c in callers)
print("  按实参分类:")
for a, n in args.most_common():
    print(f"      {a!r:<16} {n:>3} 次")

print()
print("=" * 76)
print("2. 是否有人就地把 get_config() 的结果改掉(决定能否返回不可变快照)")
print("=" * 76)
if not mutators and not modifiers:
    print("  ✅ 无人就地修改 —— 可以安全改为「每次返回深拷贝」或不可变视图")
else:
    print(f"  ⚠ {len(mutators) + len(modifiers)} 处就地修改:")
    for r, ln, s in (mutators + modifiers)[:12]:
        print(f"      {r}:{ln}  {s}")

print()
print("=" * 76)
print("3. 测试对 config_loader 的契约")
print("=" * 76)
tp = os.path.join(ROOT, "tests", "test_config_loader.py")
if os.path.exists(tp):
    tsrc = open(tp, encoding="utf-8").read()
    for m in re.finditer(r"^\s*def (test_\w+)", tsrc, re.M):
        print(f"    {m.group(1)}")
    for kw in ("reset_cache", "reload", "_cached", "_load_dotenv", "_DOTENV_LOADED",
               "monkeypatch", "is", "monkeypatch.setattr"):
        n = tsrc.count(kw)
        if n:
            print(f"    用到 {kw}: {n} 次")

print()
print("=" * 76)
print("4. 谁 import 了 _load_dotenv / _cached 这类私有符号")
print("=" * 76)
priv = defaultdict(list)
for p in files:
    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    if rel == "lib/config_loader.py":
        continue
    try:
        src = open(p, encoding="utf-8").read()
    except Exception:
        continue
    for m in re.finditer(r"from\s+lib\.config_loader\s+import\s+([^\n#]+)", src):
        for nm in m.group(1).split(","):
            nm = nm.strip()
            if nm.startswith("_"):
                priv[nm].append(rel)
for k, v in priv.items():
    print(f"  {k:<20} <- {sorted(set(v))}")
if not priv:
    print("  ✅ 外部无人 import 私有符号 —— 内部重构是安全的")
print("=" * 76)
