"""tools/log_review_service.py — 给 review_service 的次要写入/读取补日志。

审计定性(tools/audit_review_service.py + 逐行核对), 8 处静默全部是
**正确的 best-effort 设计**, 不该改语义, 只该留下记录:

  save_review        L131  SQLite 镜像写失败   -> 文件已写(try 外)
  get_review         L96   SQLite 读失败       -> 回退读文件(git 真相源)
  get_review_queue   L294  SQLite 列表失败     -> 回退遍历文件
  get_review_stats   L327  SQLite 统计失败     -> 回退遍历文件
  approve            L196  mark_chapter_completed 失败 -> 评审记录已存
  edit               L237  同上
  mark_false_positive L259 同上
  apply_edit_to_chapter L273 同上

注意: 关键写入都在 try 之外, 失败照常抛出。这里吞的只有 SQLite 镜像
与进度标记 —— 不改返回值, 不改控制流, 只记一笔。

用 AST 按 (函数名, 序号) 定位, 不用行号 —— 上一次用行号的脚本
15 处全未命中, 因为行号指向 except 行而非 pass 行。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "lib" / "review_service.py"

# 函数名 -> {静默位点序号: (级别, 说明)}
SITES = {
    "get_review": {0: ("warning", "SQLite 评审读取失败,回退文件")},
    "save_review": {0: ("warning", "SQLite 评审镜像写失败,仅文件已存")},
    "approve": {0: ("info", "章节完成标记更新失败")},
    "edit": {0: ("info", "章节完成标记更新失败")},
    "mark_false_positive": {0: ("info", "章节完成标记更新失败")},
    "apply_edit_to_chapter": {0: ("info", "章节完成标记更新失败")},
    "get_review_queue": {0: ("warning", "SQLite 队列查询失败,回退文件")},
    "get_review_stats": {0: ("warning", "SQLite 统计查询失败,回退文件")},
}


def silent_handlers(fn: ast.FunctionDef) -> list[ast.ExceptHandler]:
    out = []
    for h in ast.walk(fn):
        if not isinstance(h, ast.ExceptHandler):
            continue
        if any(isinstance(s, (ast.Raise, ast.Return)) for s in h.body):
            continue
        if any(
            isinstance(s, ast.Call)
            and getattr(s.func, "attr", "") in
            ("warning", "error", "info", "debug", "exception", "critical")
            for s in ast.walk(h)
        ):
            continue
        if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
            out.append(h)
    out.sort(key=lambda h: h.lineno)
    return out


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)

    inserts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in SITES:
            continue
        hs = silent_handlers(node)
        want = SITES[node.name]
        if len(hs) < max(want) + 1:
            print(f"  [跳过] {node.name}: 只找到 {len(hs)} 个静默位点")
            continue
        for idx, (lvl, msg) in want.items():
            h = hs[idx]
            passline = h.body[0].lineno
            indent = " " * (h.col_offset + 4)
            # 不引用异常变量: 原写法是 'except Exception:' 没有 as _e,
            # 改它会动到 except 子句。exc_info=True 自带完整堆栈, 够排查。
            ins = f'{indent}log.{lvl}("{msg} (book=%s ch=%s)", book, chapter_id, exc_info=True)\n'
            inserts.append((passline, ins, f"{node.name}[{idx}] L{passline}"))

    # 不再需要给 except 加 as _e
    for ln, ins, desc in sorted(inserts, key=lambda x: -x[0]):
        lines.insert(ln - 1, ins)
        print(f"  [log] {desc}")

    new = "".join(lines)

    try:
        ast.parse(new)
    except SyntaxError as e:
        print(f"  [失败] 语法错误: {e}")
        return 1

    TARGET.write_text(new, encoding="utf-8", newline="")
    print(f"\n已补 {len(inserts)} 处日志")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())