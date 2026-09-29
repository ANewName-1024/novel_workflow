#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描 lib/ review_ui/ tests/ novel.py 的真实三方依赖,
对照 requirements.txt 找出缺失项。

背景:requirements.txt 注释乱码,把 ruff/mypy 等行吞进了注释里,
      实际安装集合与真实 import 需求可能已经脱节。
"""
import ast
import os
import re
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = r"D:\.openclaw\workspace\novel_workflow"
SCAN = ["lib", "review_ui", "tests", "tools", "scripts"]
SINGLE = ["novel.py"]

# 标准库(不需装)
STDLIB = set(sys.stdlib_module_names) | {
    "__future__", "typing_extensions", "win32com", "pythoncom",
}

# import 名 -> PyPI 包名
ALIAS = {
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "jwt": "PyJWT",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "serial": "pyserial",
    "Crypto": "pycryptodome",
    "google": "protobuf",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
    "openai": "openai",
    "anthropic": "anthropic",
    "requests": "requests",
    "flask": "flask",
    "psutil": "psutil",
    "tiktoken": "tiktoken",
    "responses": "responses",
    "pytest": "pytest",
    "numpy": "numpy",
    "pandas": "pandas",
}

found = defaultdict(set)
files = 0
for d in SCAN:
    p0 = os.path.join(ROOT, d)
    if not os.path.isdir(p0):
        continue
    for dp, dn, fn in os.walk(p0):
        dn[:] = [x for x in dn if x not in ("__pycache__", ".git", "venv",
                                            "node_modules", "build", "mobile")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except Exception:
                continue
            files += 1
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        found[a.name.split(".")[0]].add(
                            os.path.relpath(p, ROOT).replace("\\", "/"))
                elif isinstance(n, ast.ImportFrom):
                    if n.level:      # 相对 import
                        continue
                    if n.module:
                        found[n.module.split(".")[0]].add(
                            os.path.relpath(p, ROOT).replace("\\", "/"))
for s in SINGLE:
    p = os.path.join(ROOT, s)
    if os.path.exists(p):
        try:
            for n in ast.walk(ast.parse(open(p, encoding="utf-8").read())):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        found[a.name.split(".")[0]].add(s)
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    found[n.module.split(".")[0]].add(s)
        except Exception:
            pass

print("=" * 74)
print(f"扫描 {files}+ 文件的三方依赖")
print("=" * 74)

local_pkgs = {"lib", "review_ui", "tools", "scripts", "tests", "novel"}
third = {m: v for m, v in found.items()
         if m not in STDLIB and m not in local_pkgs
         and not m.startswith("_")}
for m in sorted(third, key=lambda x: -len(third[x])):
    pkg = ALIAS.get(m, m)
    locs = sorted(third[m])
    print(f"  {m:<18} -> {pkg:<20} {len(locs):>2} 文件")
    for l in locs[:3]:
        print(f"        {l}")

# ── 对照 requirements.txt ──
print()
print("=" * 74)
print("对照 requirements.txt(注意:注释乱码可能吞掉依赖行)")
print("=" * 74)
req = os.path.join(ROOT, "requirements.txt")
declared = set()
for i, line in enumerate(open(req, encoding="utf-8", errors="replace"), 1):
    s = line.split("#")[0].strip()
    if not s:
        continue
    m = re.match(r"([A-Za-z0-9_.\-]+)", s)
    if m:
        declared.add(m.group(1).lower().replace("_", "-"))
print(f"  实际生效的依赖行: {sorted(declared)}")

print()
print("  ⚠ 以下包被 import 但未在 requirements 中:")
missing = []
for m in sorted(third):
    pkg = ALIAS.get(m, m)
    key = pkg.lower().replace("_", "-")
    if key not in declared:
        missing.append((m, pkg, len(third[m])))
if missing:
    for m, pkg, n in missing:
        print(f"      {m:<16} ({pkg})  {n} 个文件用到")
else:
    print("      无")

# 检查乱码行
print()
print("  ⚠ requirements.txt 中的疑似乱码行:")
bad = 0
for i, line in enumerate(open(req, encoding="utf-8", errors="replace"), 1):
    if re.search(r"[\ufffd\u9500-\u9fff]{2,}", line) and "#" in line:
        # 中文注释里出现无法解码的片段
        if re.search(r"#\s*[^#\n]*[\ufffd]", line) or "?" in line.split("#")[-1]:
            print(f"      L{i}: {line.rstrip()[:88]}")
            bad += 1
if not bad:
    print("      (未检出,但文件头注释肉眼可见乱码)")
print("=" * 74)
