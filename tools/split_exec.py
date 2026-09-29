#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 执行器:把 review_ui/app.py 拆成 Blueprint
====================================================
机械迁移,函数体逐字保留,只改:
  - @app.route(...)  ->  @bp.route(...)
  - 归入 bp/<domain>.py
  - import 按各文件实际用到的名字精确生成

安全措施:
  1. 先备份 app.py -> app.py.bak_phase1
  2. 迁移后跑 pyflakes 查未定义名
  3. 跑 pytest 验证
  4. 任一步失败 -> 还原备份
"""
import ast
import json
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
UI = os.path.join(ROOT, "review_ui")
APP = os.path.join(UI, "app.py")
BP = os.path.join(UI, "bp")
BAK = APP + ".bak_phase1"

# ── 分域:按 /api/<段> 的段名归类 ──
SEG2DOMAIN = {
    "outline": "outline", "chapter": "chapter", "entities": "entities",
    "comments": "comments", "notifications": "notifications",
    "project": "projects", "projects": "projects",
    "queue": "review", "review": "review", "diff": "review",
    "approve": "review", "reject": "review", "edit": "review",
    "false-positive": "review", "batch-approve": "review",
    "batch-reject": "review", "history": "review", "stats": "review",
    "llm": "llm_config", "config": "llm_config", "book-config": "llm_config",
    "pipeline": "pipeline",
}

# 留在 app.py:共享逻辑 / 页面渲染 / 入口
STAY = {
    "_get_auth", "_is_authed", "_check_basic_auth_header", "_auth_gate",
    "_nav_context", "err_404", "err_400", "err_500", "_ensure_review_backfill",
    "index", "login", "logout", "main",
    "_NAV_BOOK_ROUTES", "_GLOBAL_ROUTES",
}


def domain_of(url):
    """返回 (domain, segment) —— 按 /api/<seg>/... 取段名。"""
    p = url.split("?")[0]
    segs = [s for s in p.split("/") if s]
    if len(segs) >= 2 and segs[0] == "api":
        return SEG2DOMAIN.get(segs[1]), segs[1]
    return None, segs[0] if segs else ""


# ══════════════ 解析 ══════════════
src = open(APP, encoding="utf-8").read()
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

# 模块级 import → 符号到 import 语句的映射
IMPORTS = {}   # bound_name -> import stmt
for node in tree.body:
    if isinstance(node, ast.Import):
        for a in node.names:
            bound = a.asname or a.name.split(".")[0]
            IMPORTS[bound] = f"import {a.name}" + (f" as {a.asname}" if a.asname else "")
    elif isinstance(node, ast.ImportFrom):
        mod = ("." * node.level) + (node.module or "")
        for a in node.names:
            bound = a.asname or a.name
            stmt = f"from {mod} import {a.name}"
            if a.asname:
                stmt += f" as {a.asname}"
            IMPORTS[bound] = stmt

# 收集顶层函数 + 其源码(含装饰器)
funcs = {}   # name -> dict(node, url, dom, seg, src_text)
module_names = set()   # 模块级定义的名字(函数/类/常量)

for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        module_names.add(node.name)
    elif isinstance(node, ast.ClassDef):
        module_names.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                module_names.add(t.id)

for node in tree.body:
    if not isinstance(node, ast.FunctionDef):
        continue
    url = None
    for d in node.decorator_list:
        if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route":
            if d.args and isinstance(d.args[0], ast.Constant):
                url = str(d.args[0].value)
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    body = "".join(lines[start - 1:node.end_lineno])
    dom, seg = domain_of(url) if url else (None, "")
    funcs[node.name] = {"node": node, "url": url, "dom": dom, "seg": seg,
                        "src": body, "start": start, "end": node.end_lineno,
                        "decorators": [ast.unparse(d) for d in node.decorator_list]}

print("=" * 74)
print("分域验证")
print("=" * 74)
assign = defaultdict(list)
unassigned = []
for name, f in funcs.items():
    if not f["url"] or name in STAY:
        continue
    if f["dom"]:
        assign[f["dom"]].append(name)
    else:
        unassigned.append((name, f["url"], f["seg"]))

for d, names in sorted(assign.items(), key=lambda x: -len(x[1])):
    print(f"  {d:<16} {len(names):>2} 路由")
print(f"\n  未归类(需人工判断): {len(unassigned)}")
for n, u, s in unassigned:
    print(f"      {n:<30} {u}   seg={s!r}")

# ══════════════ 自由变量分析 ══════════════
def free_names(node):
    bound = set()
    a = node.args
    for x in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs):
        bound.add(x.arg)
    if a.vararg:
        bound.add(a.vararg.arg)
    if a.kwarg:
        bound.add(a.kwarg.arg)
    used = set()
    for nd in ast.walk(node):
        if isinstance(nd, ast.Name):
            (bound.add(nd.id) if isinstance(nd.ctx, ast.Store) else used.add(nd.id))
        elif isinstance(nd, ast.FunctionDef):
            if nd is not node:
                bound.add(nd.name)
        elif isinstance(nd, ast.ClassDef):
            bound.add(nd.name)
        elif isinstance(nd, ast.ExceptHandler) and nd.name:
            bound.add(nd.name)
        elif isinstance(nd, (ast.Global, ast.Nonlocal)):
            bound.update(nd.names)
    return used - bound

# 辅助函数归属:直接调用者 + 传递依赖
callers = defaultdict(set)
for rname, rf in funcs.items():
    if not rf["dom"]:
        continue
    body = rf["src"]
    for h in funcs:
        if h == rname or funcs[h]["url"] is not None:
            continue
        if re.search(rf"\b{re.escape(h)}\s*\(", body):
            callers[h].add(rf["dom"])

helper_dom = {}
for h, doms in callers.items():
    if len(doms) == 1:
        helper_dom[h] = next(iter(doms))
    elif len(doms) > 1:
        helper_dom[h] = "__core__"

print()
print("=" * 74)
print("辅助函数归属")
print("=" * 74)
for h, d in sorted(helper_dom.items(), key=lambda x: x[1]):
    print(f"  {d:<14} {h}")
orphan = [h for h in funcs if not funcs[h]["url"] and h not in helper_dom and h not in STAY]
if orphan:
    print(f"\n  未被任何路由调用: {orphan}")

json.dump({"assign": {k: v for k, v in assign.items()},
           "helper_dom": helper_dom,
           "orphan": orphan},
          open(os.path.join(ROOT, "tools", "_split_plan.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print()
print("  计划已存 tools/_split_plan.json")
print("=" * 74)
