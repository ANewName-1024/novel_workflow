"""tools/gen_route_baseline.py — 从拆分前的源码生成路由基线常量。

用法:
    python tools/gen_route_baseline.py           # 打印
    python tools/gen_route_baseline.py --emit    # 打印可直接粘贴的 set 字面量

基线来源是 git show cda4324^:review_ui/app.py, 即 Phase 1 拆分蓝图之前的
那个文件。手写基线会失真(我第一版就漏了 9 条), 所以必须机器生成。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = "cda4324^"
SRC = "review_ui/app.py"


def extract(src: str) -> list[str]:
    tree = ast.parse(src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route":
                if d.args and isinstance(d.args[0], ast.Constant):
                    out.add(str(d.args[0].value))
    return sorted(out)


def main() -> int:
    r = subprocess.run(
        ["git", "show", f"{BASELINE}:{SRC}"],
        cwd=REPO, capture_output=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if r.returncode != 0:
        print(f"git show 失败: {r.stderr[:200]}", file=sys.stderr)
        return 1

    routes = extract(r.stdout)
    print(f"# 基线: {BASELINE}:{SRC}  共 {len(routes)} 条", file=sys.stderr)
    if "--emit" in sys.argv:
        print(f"BASELINE_ROUTES = {{")
        for x in routes:
            print(f"    {x!r},")
        print("}")
    else:
        for x in routes:
            print(f"  {x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())