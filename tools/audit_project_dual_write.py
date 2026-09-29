"""tools/audit_project_dual_write.py — projects 的双写是否也在丢字段。

来源: tools/audit_dual_write.py 报了第二处双写位点
  review_ui/bp/projects.py::_update_project()  L171
    文件写: write_json(完整项目记录)
    DB 写  : upsert_project(root, project_id, name, config)   ← 只有 4 个

这与 review 那处是同一个形状。若项目记录有 upsert_project 没写的字段,
而读路径 DB 优先, 那些字段就静默消失。

本脚本:
  1. 建项目时实际落盘的键(init_project / update_project 写什么)
  2. projects 表的列
  3. 差集 —— 非空即第二个丢字段的点
  4. 读回路径
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def create_tables() -> dict[str, list[str]]:
    src = (REPO / "lib" / "db.py").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(
            r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)\s*\((.*?)\n\s*\)", src, re.S):
        cols, depth, cur = [], 0, ""
        for ch in m.group(2):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                cols.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            cols.append(cur.strip())
        names = [c.split()[0] for c in cols
                 if c and not c.upper().startswith(
                     ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK"))]
        out[m.group(1)] = names
    return out


def fn_src(rel: str, name: str) -> str:
    src = (REPO / rel).read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


def main() -> int:
    print("=" * 76)
    print("1. projects 表列")
    print("=" * 76)
    cols = create_tables()
    for t, c in sorted(cols.items()):
        print(f"  {t:<16} ({len(c)}) {c}")
    print()

    print("=" * 76)
    print("2. upsert_project / get_project / list_projects 签名")
    print("=" * 76)
    db_src = (REPO / "lib" / "db.py").read_text(encoding="utf-8")
    tree = ast.parse(db_src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and "project" in n.name:
            args = [a.arg for a in n.args.args]
            print(f"  L{n.lineno:<5} {n.name}({', '.join(args)})")
    print()

    print("=" * 76)
    print("3. _update_project 的双写现场")
    print("=" * 76)
    src = fn_src("review_ui/bp/projects.py", "_update_project")
    print("\n".join("  " + l for l in src.splitlines()))
    print()

    print("=" * 76)
    print("4. 项目记录实际有哪些键")
    print("=" * 76)
    ip = fn_src("lib/storage.py", "init_project")
    print("  init_project 写到 config.json 的键:")
    for l in ip.splitlines():
        if '"' in l and ":" in l and "=" in l and "cfg" not in l.split(":")[0]:
            print(f"    {l.strip()[:66]}")
    print()

    print("=" * 76)
    print("5. 读回路径: projects 列表/详情从哪读")
    print("=" * 76)
    for rel in ("review_ui/bp/projects.py", "review_ui/app.py",
                "lib/storage.py", "lib/db.py"):
        p = REPO / rel
        if not p.exists():
            continue
        for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"list_projects|get_project\b", l):
                print(f"  {rel}:{i:<5} {l.strip()[:66]}")
    print()
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())