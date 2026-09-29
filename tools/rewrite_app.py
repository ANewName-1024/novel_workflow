#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 收尾:app.py 瘦身(最小 diff 方案)
=========================================
原则:app.py 除"删掉已迁走的函数"+"插入蓝图注册"外,一个字符都不改。
      上一版按块重写会丢掉 _nav_context 依赖的模块级常量
      (_NAV_BOOK_ROUTES / _GLOBAL_ROUTES / ProxyFix 那段 if),
      风险太高。改成行级删除 + 定点插入,保留 100% 其余内容。
"""
import ast
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
UI = os.path.join(ROOT, "review_ui")
APP = os.path.join(UI, "app.py")
BAK = APP + ".bak_phase1"
BLUEPRINTS = ["chapter", "comments", "entities", "llm_config",
              "notifications", "outline", "pipeline", "projects", "review"]

man = json.load(open(os.path.join(ROOT, "tools", "_split_manifest.json"),
                     encoding="utf-8"))
moved = set(man["core"])
for v in man["routes"].values():
    moved |= set(v)
for v in man["helpers"].values():
    moved |= set(v)

src = open(BAK, encoding="utf-8").read()
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

# ── 1. 算要删的行区间(含装饰器) ──
drop = set()
moved_found = set()
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in moved:
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        for ln in range(start, node.end_lineno + 1):
            drop.add(ln)
        moved_found.add(node.name)

missing = moved - moved_found
print(f"待删除函数 {len(moved_found)} 个,行区间 {len(drop)} 行")
if missing:
    print(f"  ⚠ 未在 app.py 找到(可能已在 core.py): {sorted(missing)}")

# ── 2. 删完后清理连续空行(最多留 1 个) ──
out = [l for i, l in enumerate(lines, 1) if i not in drop]
text = "\n".join(x.rstrip("\n") for x in out) + "\n"
while "\n\n\n\n" in text:
    text = text.replace("\n\n\n\n", "\n\n\n")

# ── 3. 插入 9 个业务蓝图注册 ──
# 注意:app_log 的注册在 try/except 里,不能拿它当锚点,
#       否则新代码会落在 try 与 except 之间 -> SyntaxError。
#       改用其后唯一的顶层语句 app.secret_key 作为锚点。
anchor = "app.secret_key = os.environ.get("
ins = f"""

# ── Phase 1: 业务域蓝图(自 review_ui/app.py 拆出)────────────────────
# 用相对导入 (review_ui.bp) —— review_ui/ 与 review_ui/bp/ 都是真 package,
# pytest 的 importlib 加载与常规 import 都能解析。
# endpoint 会带上蓝图名前缀(如 outline.api_outline_get);
# 模板未直接 url_for 这些 endpoint(已核查,仅用 url_for('static')),
# 故重命名不影响模板渲染。
from .bp import (  # noqa: E402
    chapter_bp, comments_bp, entities_bp, llm_config_bp,
    notifications_bp, outline_bp, pipeline_bp, projects_bp, review_bp,
)
for _bp in (outline_bp, chapter_bp, entities_bp, projects_bp, review_bp,
            comments_bp, notifications_bp, llm_config_bp, pipeline_bp):
    app.register_blueprint(_bp)"""

if anchor in text:
    text = text.replace(anchor, ins.strip("\n") + "\n\n" + anchor, 1)
    print("已插入蓝图注册块(置于 app.secret_key 之前)")
else:
    print("!! 找不到锚点 app.register_blueprint(app_log_bp),未插入")
    sys.exit(1)

open(APP, "w", encoding="utf-8").write(text)
n_new = len(text.splitlines())
print(f"app.py: {len(lines)} 行 -> {n_new} 行")

# ── 4. pyflakes 静态查漏 ──
print()
print("=" * 70)
print("pyflakes 静态检查")
print("=" * 70)
targets = [APP, os.path.join(UI, "core.py")] + \
          [os.path.join(UI, "bp", f"{b}.py") for b in BLUEPRINTS]
r = subprocess.run([sys.executable, "-m", "pyflakes"] + targets,
                   capture_output=True, cwd=ROOT)
out_txt = r.stdout.decode("utf-8", "replace")
errs = [l for l in out_txt.splitlines() if "undefined name" in l]
warns = [l for l in out_txt.splitlines() if "undefined name" not in l]
if errs:
    print(f"  ✗ {len(errs)} 处未定义名:")
    for l in errs[:25]:
        print("    " + l.replace(str(ROOT) + "\\", ""))
else:
    print("  ✓ 无 undefined name")
if warns:
    print(f"  (其他提示 {len(warns)} 条,前 5 条)")
    for l in warns[:5]:
        print("    " + l.replace(str(ROOT) + "\\", ""))
print("=" * 70)
