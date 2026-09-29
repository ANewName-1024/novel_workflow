"""tools/audit_split_regressions.py — 系统排查 Phase 1 蓝图拆分引入的静默回归。

背景: 蓝图拆分(commit cda4324)把 66 条路由从 app.py 搬进 9 个 Blueprint,
单测只覆盖了一部分。已确认抓到 2 个静默回归:
  1. 每个蓝图都声明 static_folder -> /static 被注册 10 次
  2. _nav_context 精确匹配 endpoint -> dashboard/overview 导航高亮消失

两者都不抛异常、不影响功能, 纯 UI/行为层损坏。本脚本把同类风险点系统扫一遍。

检查项:
  A. 模板/代码里的 url_for 是否指向被搬走的 endpoint(会 BuildError 或 404)
  B. request.endpoint 的其他使用点是否假设了无前缀
  C. before_request / after_request / errorhandler 是否还挂在正确的 app 上
  D. Blueprint 是否重复注册了应用级资源(static / 模板根)
  E. 模板能否被解析(渲染时会不会 TemplateNotFound)
  F. 蓝图内是否有跨蓝图直接 import(应走包入口)
"""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

UI = REPO / "review_ui"
SKIP = {"__pycache__", "node_modules"}

issues: list[tuple[str, str]] = []   # (等级, 说明)


def add(level: str, msg: str) -> None:
    issues.append((level, msg))


# ── A. url_for 指向检查 ────────────────────────────────────────────────────
URL_FOR = re.compile(r"""url_for\(\s*['"]([a-zA-Z_][a-zA-Z0-9_.]*)['"]""")
from review_ui import app as ra  # noqa: E402

endpoints = {r.endpoint for r in ra.app.url_map.iter_rules()}
bare = {e for e in endpoints if "." not in e}
prefixed = {e for e in endpoints if "." in e}

print("=" * 76)
print("A. url_for 引用是否可解析")
print("=" * 76)
urlfor_hits = defaultdict(list)
for p in list(UI.rglob("*.html")) + list(UI.rglob("*.py")):
    if any(s in p.parts for s in SKIP):
        continue
    try:
        src = p.read_text(encoding="utf-8")
    except OSError:
        continue
    for i, line in enumerate(src.splitlines(), 1):
        for m in URL_FOR.finditer(line):
            ep = m.group(1)
            urlfor_hits[ep].append(f"{p.relative_to(REPO)}:{i}")

for ep, locs in sorted(urlfor_hits.items()):
    root = ep.split(".")[0]
    if ep in endpoints or ep in bare:
        status = "OK"
    elif root in {"static"}:
        status = "OK(应用级)"
    else:
        status = "✗ 无法解析"
        add("HIGH", f"url_for('{ep}') 无法解析 —— {locs[0]}")
    print(f"  {ep:<28}{len(locs):>3} 次   {status}")

# ── B. request.endpoint 的假设 ─────────────────────────────────────────────
print()
print("=" * 76)
print("B. request.endpoint 使用点(是否假设无蓝图前缀)")
print("=" * 76)
for p in sorted(UI.rglob("*.py")):
    if any(s in p.parts for s in SKIP):
        continue
    try:
        src = p.read_text(encoding="utf-8")
    except OSError:
        continue
    for i, line in enumerate(src.splitlines(), 1):
        if "request.endpoint" not in line:
            continue
        rel = p.relative_to(REPO)
        has_fix = "rsplit" in line or "split('.')" in line or 'split(".")' in line
        status = "已归一化" if has_fix else "裸取值"
        if not has_fix and "=" in line:
            add("MED", f"{rel}:{i} 裸取 request.endpoint, 未剥蓝图前缀")
        print(f"  {rel}:{i:<4} {status:<12} {line.strip()[:52]}")

# ── C. 全局钩子仍挂在 app 上 ───────────────────────────────────────────────
print()
print("=" * 76)
print("C. 全局钩子 / 错误处理器")
print("=" * 76)
app_src = (UI / "app.py").read_text(encoding="utf-8")
for pat, label in [
    (r"@app\.before_request", "before_request"),
    (r"@app\.after_request", "after_request"),
    (r"@app\.teardown_request", "teardown_request"),
    (r"@app\.context_processor", "context_processor"),
    (r"@app\.errorhandler", "errorhandler"),
    (r"@app\.template_filter", "template_filter"),
    (r"@app\.context_processor", "context_processor"),
]:
    n = len(re.findall(pat, app_src))
    print(f"  {label:<22}{n:>3} 处   {'OK' if n else '(无)'}")
    if label in {"before_request", "errorhandler"} and n == 0:
        add("HIGH", f"app.py 上没有 {label} —— 鉴权/错误处理可能失效")

auth_gate = len(re.findall(r"def _auth_gate", app_src))
print(f"  auth 钩子 _auth_gate     {auth_gate:>3} 处   {'OK' if auth_gate else '缺失!'}")
if not auth_gate:
    add("HIGH", "找不到 _auth_gate")

# ── D. 蓝图是否重复声明应用级资源 ─────────────────────────────────────────
print()
print("=" * 76)
print("D. 蓝图是否重复声明 static / 模板根")
print("=" * 76)
for p in sorted((UI / "bp").glob("*.py")):
    src = p.read_text(encoding="utf-8")
    has_static = "static_folder" in src
    has_tmpl = "template_folder" in src
    if has_static:
        add("HIGH", f"bp/{p.name} 声明了 static_folder —— 会重复注册 /static")
    print(f"  {p.name:<20} static={has_static!s:<6} template={has_tmpl}")

# ── E. 模板可解析性 ────────────────────────────────────────────────────────
print()
print("=" * 76)
print("E. 模板能否被 Flask 定位(渲染期才发现的错)")
print("=" * 76)
jinja = ra.app.jinja_loader
bad = []
for tpl in sorted((UI / "templates").glob("*.html")):
    try:
        jinja.get_source(ra.app, tpl.name)[0]
    except Exception as e:
        bad.append(f"{tpl.name}: {type(e).__name__}")
        add("HIGH", f"模板无法定位: {tpl.name} ({type(e).__name__})")
print(f"  模板 {len(list((UI / 'templates').glob('*.html')))} 个, 无法定位 {len(bad)} 个")
for b in bad:
    print(f"    {b}")

# ── F. 蓝图是否直接 import 其他模块(应走包入口) ───────────────────────────
print()
print("=" * 76)
print("F. 蓝图内的 import 方式")
print("=" * 76)
for p in sorted((UI / "bp").glob("*.py")):
    src = p.read_text(encoding="utf-8")
    direct = re.findall(r"^from\s+review_ui\s+import\s+(\w+)", src, re.M)
    rel = re.findall(r"^from\s+\.\.(\w+)\s+import", src, re.M)
    print(f"  {p.name:<20} 包内相对: {rel or '无'}")
    for d in direct:
        add("LOW", f"bp/{p.name} 用了 'from review_ui import {d}' 而非相对导入")

# ── 汇总 ──────────────────────────────────────────────────────────────────
print()
print("=" * 76)
print("汇总")
print("=" * 76)
if not issues:
    print("  ✓ 未发现同类问题")
else:
    by = defaultdict(list)
    for lvl, msg in issues:
        by[lvl].append(msg)
    for lvl in ("HIGH", "MED", "LOW"):
        if lvl in by:
            print(f"  [{lvl}] {len(by[lvl])} 处")
            for m in by[lvl]:
                print(f"      {m}")
n_high = sum(1 for lvl, _ in issues if lvl == "HIGH")
print()
print(f"HIGH {n_high} 处, 合计 {len(issues)} 处")
print("=" * 76)
raise SystemExit(1 if n_high else 0)
