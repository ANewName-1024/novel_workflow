#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 审计:逐条分类 except
=============================
计划里明确写了「逐条判定,不做机械替换」—— 每处 except 都要回答
「这里失败应该怎么办」。所以先把它列全,再人工归类。

分类:
  KEEP      捕获了具体异常类型,处理得当 —— 不动
  SWALLOW   裸 except 或 except Exception 后 pass —— 必须消除
  SILENT    捕获过宽但不抛不记 —— 降级返回,需加日志
  WRAP      应转为领域异常(PipelineError 及其子类)向上抛
"""
import ast
import pathlib
import re
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(".")
SKIP = {".git", "__pycache__", "venv", "node_modules", "build", "mobile",
        ".dart_tool", "projects", "dist", ".pytest_cache"}

# Phase 3 目标范围:异常密度最高的模块
TARGETS = [
    "lib/pipeline/process.py",
    "lib/pipeline/state.py",
    "lib/chapter.py",
    "lib/review_service.py",
    "lib/entity_diff.py",
    "lib/review_actions.py",
    "novel.py",
]


def classify(node, src_lines):
    """判定一个 ExceptHandler 的处理方式。"""
    t = node.type
    body = node.body
    # 只剩 pass / continue / ... -> 完全吞掉
    kinds = []
    for st in body:
        if isinstance(st, ast.Pass):
            kinds.append("pass")
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            kinds.append("expr:" + repr(st.value.value)[:20])
        elif isinstance(st, ast.Raise):
            kinds.append("raise")
        else:
            kinds.append("stmt")

    has_log = any(
        isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
        and getattr(getattr(st.value, "func", None), "attr", "") in
        ("warning", "error", "info", "debug", "exception")
        for st in body
    )
    # 异常类型文本
    try:
        tname = ast.unparse(t) if t is not None else "BARE"
    except Exception:
        tname = "?"

    if kinds == ["pass"]:
        cls = "SWALLOW"
    elif set(kinds) <= {"pass", "expr:Ellipsis"}:
        cls = "SWALLOW"
    elif "raise" in kinds and kinds.count("stmt") <= 1:
        cls = "WRAP"
    elif has_log:
        cls = "KEEP"
    else:
        cls = "SILENT"
    return cls, tname, kinds


total = Counter()
rows = []
for f in TARGETS:
    p = ROOT / f
    if not p.exists():
        continue
    src = p.read_text(encoding="utf-8")
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"[跳过] {f}: 语法错误 {e}")
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        cls, tname, kinds = classify(node, lines)
        total[cls] += 1
        # 往上找最近的函数定义
        fn = "?"
        best = -1
        for fnode in ast.walk(tree):
            if isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if fnode.lineno <= node.lineno <= (fnode.end_lineno or 0):
                    if fnode.lineno > best:
                        best = fnode.lineno
                        fn = fnode.name
        # handler 体首行,便于判断
        body_head = ""
        if node.body:
            try:
                body_head = ast.unparse(node.body[0])[:56]
            except Exception:
                body_head = "?"
        rows.append((f, node.lineno, cls, tname, fn, body_head))

print("=" * 96)
print("except 分类汇总")
print("=" * 96)
for k, v in total.most_common():
    flag = {"SWALLOW": "  ← 必须消除", "SILENT": "  ← 需加日志",
            "WRAP": "  ← 可选收紧", "KEEP": "  ← 不动"}.get(k, "")
    print(f"  {k:<10} {v:>3} 处{flag}")
print(f"  {'合计':<10} {sum(total.values()):>3} 处")

print()
print("=" * 96)
print("必须消除的 SWALLOW(裸吞异常)")
print("=" * 96)
for f, ln, cls, t, fn, head in rows:
    if cls == "SWALLOW":
        print(f"  {f}:{ln}")
        print(f"      函数 {fn}()   捕获 {t}")
        print(f"      体: {head}")

print()
print("=" * 96)
print("SILENT(捕获过宽且未记日志)—— 按文件汇总")
print("=" * 96)
byfile = defaultdict_list = {}
for f, ln, cls, t, fn, head in rows:
    if cls == "SILENT":
        byfile_list = byfile.setdefault(f, [])
        byfile_list.append((ln, t, fn, head))
for f, items in sorted(byfile.items(), key=lambda x: -len(x[1])):
    print(f"  {f}  ({len(items)} 处)")
    for ln, t, fn, head in items[:4]:
        print(f"      L{ln:<5} 捕获 {t:<28} in {fn}()")
        print(f"             体: {head}")
    if len(items) > 4:
        print(f"      ... 另有 {len(items)-4} 处")

print()
print("=" * 96)
print("WRAP(已向上抛,只看捕获类型是否过宽)")
print("=" * 96)
for f, ln, cls, t, fn, head in rows:
    if cls == "WRAP":
        print(f"  {f}:{ln:<5} 捕获 {t:<30} in {fn}()")
print("=" * 96)
