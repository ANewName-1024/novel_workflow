"""tools/check_docs_commits.py — 校验两份文档里引用的 commit hash 是否真实存在。

check_doc_structure.py 只管 重构实施计划.md 的结构。本次 进展追踪.md 里
新增了 20+ 个 commit 引用, 需要同样校验。

写错 hash 比不写更危险: 下一个接手的人会照着一个不存在的 commit 去找改动,
找不到就以为文档在骗人, 进而不再信任整份文档。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = [
    REPO / "docs" / "重构实施计划.md",
    REPO / "docs" / "进展追踪.md",
]


def known_hashes() -> set[str]:
    out = subprocess.run(
        ["git", "log", "--format=%h", "-500"],
        cwd=REPO, capture_output=True, text=True, timeout=90).stdout
    return set(out.split())


def main() -> int:
    real = known_hashes()
    if not real:
        print("拿不到 git log, 无法校验")
        return 1
    print(f"仓库可见 commit: {len(real)} 个(近 500 条)")
    print()
    total_bad = 0
    for doc in DOCS:
        if not doc.exists():
            print(f"  跳过(不存在): {doc.name}")
            continue
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        hashes = sorted(set(re.findall(r"`\b([0-9a-f]{7})\b`", text)))
        bad = [h for h in hashes if h not in real]
        total_bad += len(bad)
        mark = "OK" if not bad else "有问题"
        print(f"  [{mark:<4}] {doc.name}: 引用 {len(hashes)} 个, 缺失 {len(bad)} 个")
        for h in bad:
            ctx = next((l.strip()[:70] for l in lines if h in l), "")
            print(f"         ❌ {h}  {ctx}")
    print()
    print("=" * 70)
    print("结论:", "全部可解析" if total_bad == 0 else f"{total_bad} 个 hash 不存在")
    print("=" * 70)
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())