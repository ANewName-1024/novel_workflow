"""tools/fix_blueprint_static.py — 移除蓝图上重复注册的 static 路由。

Bug 溯源(Phase 1, cda4324): 我给 review_ui/bp/ 下每个 Blueprint 都写了
    static_folder=str(_UI / "static"),
    static_url_path="/static"
于是同一个 /static/<path:filename> GET 被注册 10 次(app 1 次 + 9 个蓝图各 1 次)。

现在没出事只因为 9 个蓝图指向同一目录。若将来任一蓝图改了自己的 static
目录, Flask 按注册顺序先匹配命中, 会静默劫持全部静态资源。

正确做法: 静态资源属于应用级, 由 app.py 的 static_folder 统一提供, 蓝图不重复声明。
template_folder 保留 —— bp/ 的 root_path 与 review_ui/ 不同, 显式指定更稳,
且不产生重复规则(app 与 bp 的模板查找是 dispatcher 合并, 不是重复注册)。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BP_DIR = REPO / "review_ui" / "bp"

PATTERN = re.compile(
    r",\s*\n\s*static_folder\s*=\s*str\(_UI\s*/\s*[\"']static[\"']\)\s*,"
    r"\s*\n\s*static_url_path\s*=\s*[\"']/static[\"']\s*\)"
)


def main() -> int:
    changed = []
    for p in sorted(BP_DIR.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if "static_folder" not in text:
            continue
        new = PATTERN.sub(")", text)
        if new == text:
            # 退化路径: 报告原文, 便于人工确认格式
            print(f"  [未匹配] {p.name}")
            continue
        try:
            ast.parse(new)
        except SyntaxError as e:
            print(f"  [语法错误] {p.name}: {e}")
            return 1
        p.write_text(new, encoding="utf-8", newline="")
        changed.append(p.name)

    print(f"已修 {len(changed)} 个蓝图:")
    for c in changed:
        print(f"  {c}")

    # 校验
    sys.path.insert(0, str(REPO))
    from collections import Counter

    from review_ui import app as ra

    c: Counter = Counter()
    for r in ra.app.url_map.iter_rules():
        for m in r.methods - {"HEAD", "OPTIONS"}:
            c[(str(r.rule), m)] += 1
    dupes = {k: v for k, v in c.items() if v > 1}
    print()
    print(f"路由总数: {sum(c.values())}")
    print(f"重复规则: {len(dupes)}")
    for k, v in list(dupes.items())[:5]:
        print(f"  {k} x{v}")
    return 0 if not dupes else 1


if __name__ == "__main__":
    raise SystemExit(main())