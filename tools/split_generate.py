#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 生成器:app.py -> bp/*.py + core.py + 瘦身 app.py
=============================================================
安全措施:
  1. 备份 app.py -> app.py.bak_phase1
  2. 逐个生成,每个文件都带完整 import 块
  3. Blueprint 显式指定 template_folder(bp/ 的 root_path 与 review_ui/ 不同,
     不指定会找不到模板 —— 这是 Blueprint 拆分的经典坑)
  4. 跑 pyflakes 查未定义名
  5. 跑 pytest 验证
失败可回滚:  git checkout review_ui/app.py && rm -rf review_ui/bp
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
CORE = os.path.join(UI, "core.py")

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
STAY = {
    "_get_auth", "_is_authed", "_check_basic_auth_header", "_auth_gate",
    "_nav_context", "err_404", "err_400", "err_500", "_ensure_review_backfill",
    "index", "login", "logout", "main",
}
DOMAIN_DESC = {
    "outline": "大纲编辑 / 大纲 AI / 大纲 diff",
    "chapter": "章节正文 / 章节上下文 / 章节 diff / 反馈应用",
    "entities": "实体 CRUD / 一致性检查(角色/事件/伏笔/世界规则)",
    "projects": "项目(书籍)CRUD / 章节列表",
    "review": "评审队列 / 通过 / 驳回 / 编辑 / 批量 / 统计 / 历史",
    "comments": "批注系统",
    "notifications": "通知中心",
    "llm_config": "LLM provider 列表 / 健康检查 / 书籍级配置读写",
    "pipeline": "流水线中断记录 / 恢复",
}

# ── 读源码并解析 ──
src = open(APP, encoding="utf-8").read()
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

IMPORTS = {}
for node in tree.body:
    if isinstance(node, ast.Import):
        for a in node.names:
            b = a.asname or a.name.split(".")[0]
            IMPORTS[b] = f"import {a.name}" + (f" as {a.asname}" if a.asname else "")
    elif isinstance(node, ast.ImportFrom):
        mod = ("." * node.level) + (node.module or "")
        for a in node.names:
            b = a.asname or a.name
            s = f"from {mod} import {a.name}" + (f" as {a.asname}" if a.asname else "")
            IMPORTS[b] = s


def domain_of(url):
    segs = [s for s in url.split("?")[0].split("/") if s]
    if len(segs) >= 2 and segs[0] == "api":
        return SEG2DOMAIN.get(segs[1])
    return None


funcs, keeps = {}, []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        keeps.append(node)
        continue
    if isinstance(node, ast.FunctionDef):
        url = None
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route":
                if d.args and isinstance(d.args[0], ast.Constant):
                    url = str(d.args[0].value)
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        funcs[node.name] = {"node": node, "url": url,
                            "dom": domain_of(url) if url else None,
                            "src": "".join(lines[start - 1:node.end_lineno]),
                            "start": start, "end": node.end_lineno}
    else:
        keeps.append(node)


def free_names(node):
    b = set()
    a = node.args
    for x in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs):
        b.add(x.arg)
    if a.vararg:
        b.add(a.vararg.arg)
    if a.kwarg:
        b.add(a.kwarg.arg)
    used = set()
    for nd in ast.walk(node):
        if isinstance(nd, ast.Name):
            (b.add(nd.id) if isinstance(nd.ctx, ast.Store) else used.add(nd.id))
        elif isinstance(nd, ast.FunctionDef) and nd is not node:
            b.add(nd.name)
        elif isinstance(nd, ast.ClassDef):
            b.add(nd.name)
        elif isinstance(nd, ast.ExceptHandler) and nd.name:
            b.add(nd.name)
        elif isinstance(nd, (ast.Global, ast.Nonlocal)):
            b.update(nd.names)
    return used - b


# ── 归组 ──
routes = defaultdict(list)   # dom -> [name]
for n, f in funcs.items():
    if f["url"] and f["dom"] and n not in STAY:
        routes[f["dom"]].append(n)

callers = defaultdict(set)
for rn, rf in funcs.items():
    if not rf["dom"]:
        continue
    for h in funcs:
        if h == rn or funcs[h]["url"]:
            continue
        if re.search(rf"\b{re.escape(h)}\s*\(", rf["src"]):
            callers[h].add(rf["dom"])
helper_dom = {h: (next(iter(d)) if len(d) == 1 else "__core__")
              for h, d in callers.items() if d}
helpers_by_dom = defaultdict(list)
core_helpers = []
for h, d in helper_dom.items():
    (core_helpers if d == "__core__" else helpers_by_dom[d]).append(h)

# 传递闭包:域内路由还可能调用别的辅助,辅助又调辅助
for _ in range(3):
    for d in list(helpers_by_dom) + ["__core__"]:
        target = core_helpers if d == "__core__" else helpers_by_dom[d]
        for h in funcs:
            if h in helper_dom or funcs[h]["url"]:
                continue
            srcs = [f["src"] for rn, f in funcs.items()
                    if f["dom"] == d and rn not in STAY]
            srcs += [funcs[x]["src"] for x in target if x in funcs]
            if any(re.search(rf"\b{re.escape(h)}\s*\(", s) for s in srcs):
                if h not in target and h not in core_helpers:
                    target.append(h)

print("=" * 74)
print("生成计划")
print("=" * 74)
for d in sorted(routes):
    print(f"  bp/{d}.py     {len(routes[d])} 路由 + {len(helpers_by_dom.get(d, []))} 辅助"
          f"   # {DOMAIN_DESC.get(d, '')}")
print(f"  core.py        {len(core_helpers)} 个跨域共享辅助: {', '.join(core_helpers)}")
print(f"  app.py 保留    {sum(1 for f in funcs.values() if f['url'] and not f['dom'])} 页面路由"
      f" + {len([h for h in STAY if h in funcs])} 共享/入口函数")
print("=" * 74)

# ── 备份 ──
if not os.path.exists(BAK):
    shutil.copy2(APP, BAK)
    print(f"已备份: {os.path.basename(BAK)}")

os.makedirs(BP, exist_ok=True)

HEADER = '''"""{doc}

从 review_ui/app.py 拆出(Phase 1)。
路由: {routes} 条   辅助函数: {helpers} 个
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request, render_template, abort, Response, \\
    session, redirect, url_for

_HERE = Path(__file__).resolve().parent
_UI = _HERE.parent
if str(_UI.parent) not in sys.path:
    sys.path.insert(0, str(_UI.parent))
if str(_UI.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_UI.parent / "lib"))

{extra_imports}
bp = Blueprint("{dom}", __name__,
               template_folder=str(_UI / "templates"),
               static_folder=str(_UI / "static"),
               static_url_path="/static")
'''


def build_imports(names, dom=None, need_core=()):
    """根据函数体实际用到的名字生成 import 块。"""
    used = set()
    for n in names:
        if n in funcs:
            used |= free_names(funcs[n]["node"])
    stmts, seen = [], set()
    for nm in sorted(used):
        if nm in IMPORTS and IMPORTS[nm] not in seen:
            seen.add(IMPORTS[nm])
            stmts.append(IMPORTS[nm])
    extra = []
    if need_core:
        extra.append("from review_ui.core import " + ", ".join(sorted(need_core)))
    body = "\n".join(stmts)
    return (body + ("\n" if body else "") + "\n" + "\n".join(extra)
            if extra else body)


def emit(path, doc, dom, names, helpers, need_core=()):
    allnames = names + helpers
    extra = build_imports(allnames, dom, need_core)
    txt = HEADER.format(doc=doc, routes=len(names), helpers=len(helpers),
                        extra_imports=extra, dom=dom)
    parts = [txt]
    for n in names + helpers:
        s = funcs[n]["src"]
        if dom and "@app." in s:
            s = s.replace("@app.", "@bp.")
        parts.append("\n\n" + s.rstrip() + "\n")
    open(path, "w", encoding="utf-8").write("".join(parts))


# core.py —— 跨域共享辅助
core_src = '''"""review_ui/core.py — 跨域共享的内部辅助(Phase 1)

由 _ensure_book 跨 6 个域调用,故置于 core 而非某个 blueprint 内。
注意:本模块不做 auth 判定 —— @app.before_request 的 _auth_gate 仍在 app.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

_UI = Path(__file__).resolve().parent
if str(_UI.parent) not in sys.path:
    sys.path.insert(0, str(_UI.parent))
if str(_UI.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_UI.parent / "lib"))

from flask import jsonify, request

from lib import storage

'''
if core_helpers:
    imp = build_imports(core_helpers)
    core_src += imp + "\n\n"
    for h in core_helpers:
        core_src += funcs[h]["src"].rstrip() + "\n\n\n"
open(CORE, "w", encoding="utf-8").write(core_src)
print(f"\n写入 core.py  ({len(core_helpers)} 辅助)")

# bp/__init__.py
open(os.path.join(BP, "__init__.py"), "w", encoding="utf-8").write(
    '"""review_ui.bp — 按业务域拆分的蓝图(Phase 1)\n\n'
    "app.py 注册全部蓝图,模板目录显式指向 review_ui/templates。\n"
    '"""\n')

written = []
for d in sorted(routes):
    p = os.path.join(BP, f"{d}.py")
    emit(p, f"review_ui/bp/{d}.py — {DOMAIN_DESC.get(d, d)}", d,
         sorted(routes[d], key=lambda n: funcs[n]["start"]),
         sorted(helpers_by_dom.get(d, []), key=lambda n: funcs[n]["start"]),
         need_core=core_helpers)
    written.append(p)
    print(f"写入 bp/{d}.py  {len(routes[d])} 路由 + {len(helpers_by_dom.get(d, []))} 辅助")

json.dump({"routes": {k: v for k, v in routes.items()},
           "helpers": {k: v for k, v in helpers_by_dom.items()},
           "core": core_helpers},
          open(os.path.join(ROOT, "tools", "_split_manifest.json"), "w",
               encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n完成。下一步:重写 app.py 瘦身版,然后 pyflakes + pytest")
print("=" * 74)
