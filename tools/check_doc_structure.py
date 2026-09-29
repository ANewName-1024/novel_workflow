"""tools/check_doc_structure.py — 校验重构实施计划.md 的结构完整性。

文档改了这么多,必须确认:
  1. 标题层级不断裂
  2. 表格每行闭合(管道结尾)
  3. 代码栅栏成对
  4. 里程碑表里提到的 commit 都真实存在

第 4 条最有用 —— 文档里写错 commit 号比不写更危险, 下次接手的人会照着
一个不存在的 hash 去找。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "重构实施计划.md"


def main() -> int:
    if not DOC.exists():
        print(f"找不到 {DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    print("=" * 72)
    print(f"{DOC.name}  {len(lines)} 行")
    print("=" * 72)

    print("\n标题层级:")
    infence = False
    for i, l in enumerate(lines, 1):
        if l.lstrip().startswith("```"):
            infence = not infence
            continue
        if l.startswith("#") and not infence:
            # CommonMark 要求 # 后有空格才是标题。用同一条规则判断, 否则
            # 「#3 和 #4 …」这种正文会被误报成标题 —— 我自己的检查器
            # 今天已经因为「不验证工具」吃过四次亏, 这里不再犯。
            body = l.lstrip("#")
            if body.startswith((" ", "\t")) or l.rstrip() == "#":
                print(f"  L{i:<5} {l[:66]}")

    print("\n代码栅栏:")
    fences = [i for i, l in enumerate(lines, 1) if l.lstrip().startswith("```")]
    print(f"  {len(fences)} 个 {'成对' if len(fences) % 2 == 0 else '不成对!'}")

    print("\n表格行闭合:")
    infence = False
    bad = []
    for i, l in enumerate(lines, 1):
        if l.lstrip().startswith("```"):
            infence = not infence
            continue
        if infence:
            continue
        if l.startswith("|") and not l.rstrip().endswith("|"):
            bad.append((i, l[:56]))
    print(f"  未闭合 {len(bad)} 行")
    for i, l in bad[:6]:
        print(f"    L{i}: {l}")

    print("\n文档引用的 commit 是否真实存在:")
    hashes = sorted(set(re.findall(r"`\b([0-9a-f]{7})\b`", text)))
    real = set()
    try:
        out = subprocess.run(["git", "log", "--format=%h", "-300"],
                             cwd=REPO, capture_output=True, text=True,
                             timeout=60).stdout
        real = set(out.split())
    except Exception as e:
        print(f"  git 调用失败: {e}")
    missing = [h for h in hashes if h not in real]
    print(f"  引用 {len(hashes)} 个, 存在 {len(hashes) - len(missing)} 个, "
          f"缺失 {len(missing)} 个")
    for h in missing:
        ctx = next((l.strip()[:60] for l in lines if h in l), "")
        print(f"    ❌ {h}  {ctx}")

    print("\n" + "=" * 72)
    ok = not bad and len(fences) % 2 == 0 and not missing
    print("结论:", "结构完好" if ok else "有问题")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())