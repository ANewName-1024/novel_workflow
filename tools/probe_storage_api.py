"""tools/probe_storage_api.py — 查 storage 的真实函数签名, 修测试的调用姿势。"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

WANT = [
    "init_project", "write_json", "read_json", "project_root",
    "chapters_dir", "list_chapters", "project_exists",
]

src = (REPO / "lib" / "storage.py").read_text(encoding="utf-8")
tree = ast.parse(src)

print("=" * 72)
print("AST 签名(权威, 反映源码)")
print("=" * 72)
found = {}
for n in ast.walk(tree):
    if isinstance(n, ast.FunctionDef) and n.name in WANT:
        found[n.name] = n
        args = [a.arg for a in n.args.args]
        defaults = [ast.unparse(d) for d in n.args.defaults]
        sig = ", ".join(args)
        if defaults:
            sig += f"   defaults={defaults}"
        print(f"  L{n.lineno:<6} {n.name}({sig})")

print()
print("=" * 72)
print("运行时 inspect.signature(交叉验证)")
print("=" * 72)
from lib import storage  # noqa: E402

for name in WANT:
    fn = getattr(storage, name, None)
    if fn is None:
        print(f"  {name:<20} 不存在")
        continue
    try:
        print(f"  {name:<20} {inspect.signature(fn)}")
    except (TypeError, ValueError):
        print(f"  {name:<20} (无法 introspect)")

print()
print("=" * 72)
print("现有测试怎么建项目(照抄最稳)")
print("=" * 72)
for p in sorted((REPO / "tests").glob("*.py")):
    s = p.read_text(encoding="utf-8", errors="replace")
    if "init_project" in s or "project_root" in s:
        for i, line in enumerate(s.splitlines(), 1):
            if "init_project(" in line or ("conftest" in p.name and "projects_root" in line and "def " in line):
                print(f"  {p.name}:{i:<5} {line.strip()[:72]}")
        break

print()
print("=" * 72)
print("conftest 的 projects_root fixture 怎么建的目录")
print("=" * 72)
c = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
lines = c.splitlines()
for i, l in enumerate(lines, 1):
    if "def tmp_projects_root" in l or "def projects_root" in l:
        for j in range(i - 1, min(i + 22, len(lines))):
            print(f"  L{j + 1}: {lines[j]}")
        break
print("=" * 72)