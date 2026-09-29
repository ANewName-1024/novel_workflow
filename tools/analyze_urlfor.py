#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查清 url_for 引用 —— Blueprint 会改 endpoint 名,必须先知道谁依赖谁。"""
import os
import re
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
SKIP = ("venv", ".git", "__pycache__", "node_modules", "ephemeral", "build", ".dart_tool")

# url_for('name')  或  url_for("name")
PAT = re.compile(r"""url_for\(\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['"]""")

print("=" * 76)
print("url_for 引用统计(拆 Blueprint 前的必查项)")
print("=" * 76)

found = defaultdict(list)   # endpoint -> [(relfile, lineno)]

for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if not any(s in d for s in SKIP)]
    for f in fn:
        if not f.endswith((".py", ".html", ".js")):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            for m in PAT.finditer(line):
                found[m.group(1)].append((rel, i))

if not found:
    print("  没有发现任何 url_for('endpoint') 字面量引用")
else:
    for name, refs in sorted(found.items(), key=lambda x: -len(x[1])):
        files = sorted(set(r[0] for r in refs))
        print(f"  {name:<26} {len(refs):>3} 次  {len(files)} 个文件")
        for fl in files[:4]:
            print(f"        {fl}")

# endpoint 定义在哪
print()
print("=" * 76)
print("endpoint 定义来源")
print("=" * 76)
app_py = os.path.join(ROOT, "review_ui", "app.py")
src = open(app_py, encoding="utf-8").read()

defined = set()
for m in re.finditer(r"@app\.route\(\s*['\"]([^'\"]+)['\"]", src):
    defined.add(("app.py", m.group(1)))

# 函数名 -> route
cur = None
for i, line in enumerate(src.splitlines(), 1):
    m = re.match(r"def ([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line.strip())
    if m:
        cur = m.group(1)

for bp in ("dashboard.py", "app_log.py"):
    p = os.path.join(ROOT, "review_ui", bp)
    if os.path.exists(p):
        s = open(p, encoding="utf-8").read()
        for m in re.finditer(rf"@{bp[:-3]}_bp\.route\(\s*['\"]([^'\"]+)['\"]", s):
            print(f"  {bp:<18} {m.group(1)}")
            defined.add((bp, m.group(1)))

print()
print(f"  app.py 直接定义路由: {sum(1 for f, _ in defined if f == 'app.py')} 条")

# 关键判断:模板里有没有指向 app.py 路由的 url_for
tpl_refs = {n for n, refs in found.items()
            if any(r[0].startswith("review_ui/templates") for r in refs)}
app_eps = set()
cur = None
for i, line in enumerate(src.splitlines(), 1):
    if re.match(r"def ([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line.strip()):
        cur = re.match(r"def ([a-zA-Z_][a-zA-Z0-9_]*)", line.strip()).group(1)
    m = re.search(r"@app\.route\(\s*['\"][^'\"]+['\"]", line)
    if m and cur:
        app_eps.add(cur)

print()
print("=" * 76)
print("拆分风险判定")
print("=" * 76)
hit = tpl_refs & app_eps
if hit:
    print(f"  ⚠ 模板直接 url_for 了 app.py 的 endpoint:")
    for h in sorted(hit):
        print(f"      {h}")
    print("      → 拆 Blueprint 时必须用 @bp.route 保持同名 endpoint,")
    print("        或改成 app.add_url_rule(endpoint=原名)")
else:
    print("  ✅ 模板里没有直接 url_for app.py 的 endpoint")
    print("     (只引用了 'static')→ 拆 Blueprint 的 endpoint 改名风险低")

# redirect() 引用
red = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if not any(s in d for s in SKIP)]
    for f in fn:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for m in re.finditer(r"""redirect\(\s*url_for\(\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['"]""", s):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            red.append((m.group(1), rel))
if red:
    print()
    print("  redirect(url_for(...)) 用法:")
    for n, f in red[:10]:
        print(f"      {n:<24} {f}")
print("=" * 76)
