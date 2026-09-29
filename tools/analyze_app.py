#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 review_ui/app.py 的构成,给出可执行的拆分边界。"""
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
P = os.path.join(ROOT, "review_ui", "app.py")
src = open(P, encoding="utf-8").read()
tree = ast.parse(src)

print("=" * 76)
print(f"review_ui/app.py  {len(src.splitlines())} 行")
print("=" * 76)

# 1) 路由清单 —— 按 URL 前缀归组
print()
print("1) 路由按域归组")
print("=" * 76)
routes = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for d in node.decorator_list:
            # @app.route("/xxx", methods=[...])
            if isinstance(d, ast.Call):
                fn = d.func
                nm = getattr(fn, "attr", getattr(fn, "id", ""))
                if nm in ("route", "get", "post", "put", "delete", "patch"):
                    if d.args and isinstance(d.args[0], ast.Constant):
                        routes.append((str(d.args[0].value), nm.upper(), node.name, node.lineno))

def domain(u):
    u2 = u.lstrip("/")
    if not u2:
        return "(root)"
    if u2.startswith("api/"):
        rest = u2[4:]
        if "/" in rest:
            head = rest.split("/")[0]
            if head in ("book", "books"):
                return "api/book/..."
            return f"api/{head}"
        return "api/..."
    if u2.startswith("static") or u2.endswith((".css", ".js", ".png", ".ico")):
        return "(static)"
    return "(page)"

g = defaultdict(list)
for u, m, fn, ln in routes:
    g[domain(u)].append((u, m, fn, ln))

for d, items in sorted(g.items(), key=lambda x: -len(x[1])):
    meths = Counter(m for _, m, _, _ in items)
    ms = ",".join(f"{k}×{v}" for k, v in meths.most_common())
    lines = [i[3] for i in items]
    print(f"  {d:<18} {len(items):>3} 路由  [{ms}]  行 {min(lines)}-{max(lines)}")

print()
print(f"  路由总数: {len(routes)}")

# 2) 顶层函数与类
print()
print("2) 顶层结构")
print("=" * 76)
top_funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
top_classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
print(f"  顶层函数 {len(top_funcs)}   顶层类 {len(top_classes)}")

# 非路由的顶层函数(辅助/服务逻辑)—— 这些是"混在路由文件里的业务逻辑"
helpers = [(n.name, n.lineno, len(ast.get_source_segment(src, n).splitlines()))
           for n in top_funcs
           if not any(n is m for _, _, nm, _ in routes for m in
                      [x for x in ast.walk(tree)
                       if isinstance(x, ast.FunctionDef) and x.name == nm and x is n])]
print()
print("  非路由的顶层函数(业务逻辑混在路由文件里):")
for n, ln, sz in sorted(helpers, key=lambda x: -x[2])[:16]:
    print(f"    L{ln:<6} {n[:44]:<46} {sz:>4} 行")

# 3) 与其他模块的耦合
print()
print("3) app.py 依赖的 lib 模块")
print("=" * 76)
imps = defaultdict(list)
for n in ast.walk(tree):
    if isinstance(n, ast.ImportFrom) and n.module:
        imps[n.module].extend(a.name for a in n.names)
    elif isinstance(n, ast.Import):
        for a in n.names:
            imps[a.name].append("*")
for m, names in sorted(imps.items(), key=lambda x: -len(x[1])):
    if m in ("flask", "os", "sys", "json", "re", "time", "subprocess", "hashlib",
             "sqlite3", "logging", "datetime", "pathlib", "typing", "functools",
             "shutil", "threading", "traceback", "argparse", "io", "math", "html",
             "urllib", "tempfile", "uuid", "collections", "itertools", "copy"):
        continue
    uniq = sorted(set(names))
    print(f"  {m:<28} {len(uniq):>2} 个  {', '.join(uniq[:8])}")

# 4) review_ui 目录现状
print()
print("=" * 76)
print("4) review_ui/ 目录现状")
print("=" * 76)
d = os.path.join(ROOT, "review_ui")
for f in sorted(os.listdir(d)):
    fp = os.path.join(d, f)
    if os.path.isfile(fp):
        n = len(open(fp, encoding="utf-8", errors="replace").read().splitlines())
        print(f"  {f:<28} {n:>6} 行  {os.path.getsize(fp)/1024:>7.0f} KB")
    else:
        cnt = len(os.listdir(fp))
        print(f"  {f+'/':<28} {cnt:>6} 项")
print("=" * 76)
