#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1-A: 把 review_ui/app.py 拆成 Blueprint

做法(机械、逐字保留函数体,只改装饰器):
  1. AST 解析 app.py
  2. 按 URL 前缀把 @app.route 路由分域
  3. 辅助函数跟随调用者归位
  4. 共享逻辑(auth / nav / error)留在 app.py
  5. 生成 bp/<domain>.py,重写 app.py
  6. 用 pyflakes 静态查漏,pytest 验证
"""
import ast
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
APP = os.path.join(ROOT, "review_ui", "app.py")
BP_DIR = os.path.join(ROOT, "review_ui", "bp")

# ── 分域规则:URL 前缀 → 蓝图名 ──
DOMAINS = [
    ("outline",       lambda u: u.startswith("/api/outline")),
    ("chapter",       lambda u: u.startswith("/api/chapter")),
    ("entities",      lambda u: u.startswith("/api/entity")),
    ("comments",      lambda u: u.startswith("/api/comment")),
    ("notifications", lambda u: u.startswith("/api/notification")),
    ("projects",      lambda u: u.startswith("/api/project")),
    ("review",        lambda u: u.startswith(("/api/queue", "/api/review", "/api/diff",
                                                "/api/approve", "/api/reject", "/api/edit",
                                                "/api/false-positive", "/api/batch",
                                                "/api/history", "/api/stats"))),
    ("llm_config",    lambda u: u.startswith(("/api/llm", "/api/config"))),
    ("pipeline",      lambda u: u.startswith("/api/pipeline")),
]

# 留在 app.py 的函数(共享逻辑 / 顶层入口)
STAY = {
    "_get_auth", "_is_authed", "_check_basic_auth_header", "_auth_gate",
    "_nav_context", "err_404", "_ensure_review_backfill", "index",
    "login", "main", "_page_shell",
}

src = open(APP, encoding="utf-8").read()
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

# ── 收集顶层节点 ──
routes = {}      # name -> (url, domain, lineno, end_lineno)
helpers = {}     # name -> (lineno, end_lineno)
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        name = node.name
        url = None
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route":
                if d.args and isinstance(d.args[0], ast.Constant):
                    url = str(d.args[0].value)
        if url:
            routes[name] = (url, node.lineno, node.end_lineno)
        else:
            helpers[name] = (node.lineno, node.end_lineno)

def domain_of(url):
    for dname, test in DOMAINS:
        if test(url):
            return dname
    return None

assigned = defaultdict(list)   # domain -> [name]
for name, (url, _, _) in routes.items():
    d = domain_of(url)
    if d and name not in STAY:
        assigned[d].append(name)

print("=" * 74)
print("分域结果")
print("=" * 74)
for d, names in sorted(assigned.items(), key=lambda x: -len(x[1])):
    print(f"  {d:<16} {len(names):>2} 路由  {', '.join(sorted(names)[:4])}{'...' if len(names) > 4 else ''}")

unassigned = [n for n, (u, _, _) in routes.items()
              if not domain_of(u) or n in STAY]
print()
print(f"  留在 app.py: {len(routes) - sum(len(v) for v in assigned.values())} 路由")
if unassigned:
    for n in sorted(unassigned):
        print(f"      {n:<28} {routes[n][0]}")

# ── 辅助函数:看谁调用它 ──
print()
print("=" * 74)
print("辅助函数归属")
print("=" * 74)
helper_assign = {}
for hname in helpers:
    if hname in STAY:
        print(f"  [app.py]  {hname}")
        continue
    # 全文搜索调用点,看属于哪个域的函数
    callers = set()
    for rname in routes:
        rln, ren = routes[rname][1], routes[rname][2]
        body = "".join(lines[rln - 1:ren])
        if re.search(rf"\b{re.escape(hname)}\s*\(", body):
            callers.add(domain_of(routes[rname][0]))
    callers.discard(None)
    if len(callers) == 1:
        helper_assign[hname] = next(iter(callers))
        print(f"  [{next(iter(callers))}]  {hname}")
    elif len(callers) > 1:
        print(f"  [多域调用] {hname}  ← {sorted(callers)}")
        helper_assign[hname] = "app.py"   # 保守:留在 app.py
    else:
        print(f"  [无调用]  {hname}  ← 死代码?")

print()
print("=" * 74)
print(f"共需迁移 {sum(len(v) for v in assigned.values())} 路由 + {len(helper_assign)} 辅助函数")
print("=" * 74)
