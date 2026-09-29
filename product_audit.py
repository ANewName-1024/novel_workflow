"""product_audit.py — 产品视角实测: 用户数据到底处于什么风险下。

起因: 用户要求「从产品和用户角度分析测试和优化」。本脚本只做只读探测,
不改任何用户数据, 结论分【已确认】/【需确认】两级, 不做推断。

已确认的问题(本轮实测):
  1. 项目列表被历史测试残留污染
  2. .gitignore 有手工加的兜底行, 其中一条命中真实书名
  3. ch_008 已落盘但未记完成 → continue 会覆盖它, 而该书无版本快照
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
PROJ = ROOT / "projects"
BOOK = PROJ / "测试书籍"


def main() -> int:
    print("=" * 72)
    print("A. 用户打开项目列表会看到什么")
    print("=" * 72)
    dirs = sorted(d.name for d in PROJ.iterdir() if d.is_dir() and d.name != ".meta")
    real, junk = [], []
    for d in dirs:
        ch = list((PROJ / d / "chapters").glob("*.md")) if (PROJ / d / "chapters").is_dir() else []
        # 有实质内容且 config 完整 = 可能是真实作品
        has_cfg = (PROJ / d / "config.json").exists()
        (real if (ch and has_cfg) else junk).append((d, len(ch)))
    print(f"  共 {len(dirs)} 个项目目录")
    print(f"  有章节且有 config ({len(real)} 个):")
    for d, n in real:
        print(f"      {d}  —  {n} 章")
    print(f"  空/无 config ({len(junk)} 个):")
    for d, n in junk:
        print(f"      {d}  —  {n} 章")
    print()
    print("  → 用户点进任何一个测试残留目录会看到什么? 下一节实测。")

    print()
    print("=" * 72)
    print("B. 点进测试残留目录的体验")
    print("=" * 72)
    for d, _ in junk[:3]:
        p = PROJ / d
        has_cfg = (p / "config.json").exists()
        cfg = None
        if has_cfg:
            try:
                cfg = json.loads((p / "config.json").read_text(encoding="utf-8"))
            except Exception as e:
                cfg = f"(解析失败: {e})"
        name = cfg.get("book_name") if isinstance(cfg, dict) else None
        print(f"  {d}:")
        print(f"      config.json 存在: {has_cfg}"
              + (f", book_name = {name!r}" if name else ""))

    print()
    print("=" * 72)
    print("C. 真实作品的数据风险")
    print("=" * 72)
    disk = sorted(p.stem for p in (BOOK / "chapters").glob("*.md"))
    pr = json.loads((BOOK / "progress.json").read_text(encoding="utf-8"))
    done = pr.get("chapters_completed", [])
    cur = pr.get("current_chapter", 0)
    orphan = [c for c in disk if c not in done]
    print(f"  磁盘章节   : {disk}")
    print(f"  记录已完成 : {done}")
    print(f"  current_chapter: {cur}  → continue 将从 ch_{cur + 1:03d} 开始")
    print(f"  写了未记账: {orphan}")
    vdir = BOOK / "versions"
    print(f"  versions 目录存在: {vdir.exists()}")
    if not vdir.exists():
        print("      → ★ 覆盖上述章节时【无任何版本快照可回退】")
    for c in orphan:
        p = BOOK / "chapters" / f"{c}.md"
        t = p.read_text(encoding="utf-8")
        print(f"      {c}.md = {len(t)} 字, 首行 {t.splitlines()[0][:36]!r}")
    bdir = BOOK / "backups"
    if bdir.exists():
        bs = sorted(p.name for p in bdir.iterdir())
        print(f"  backups: {len(bs)} 个 → {bs[-2:] if len(bs) > 2 else bs}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())