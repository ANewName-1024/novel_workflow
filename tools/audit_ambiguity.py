"""tools/audit_ambiguity.py — 找出「失败伪装成成功」的位点。

L98 的教训: 48 处 SILENT 里多数无害, 一刀切改成 raise 会制造回归。
真正该改的排序是:
    返回值有歧义  >  无日志  >  捕获过宽

「歧义」的判定: 函数在 except 里返回 None / [] / {} / False,
而这个返回值与「合法的空」无法区分 —— 调用方分不清「确实没有」
和「读取失败」。这是会误导用户的一类 bug, 比缺日志严重。

只读审计, 不改代码。输出按危害排序, 供人工逐条判定。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = [
    "lib/pipeline/process.py",
    "lib/pipeline/state.py",
    "lib/chapter.py",
    "lib/review_service.py",
    "lib/entity_diff.py",
    "lib/review_actions.py",
    "lib/memory.py",
    "lib/db.py",
    "lib/outline_editor.py",
    "lib/self_check.py",
    "novel.py",
]

# 「空」返回值 —— 与「合法的空」不可区分
EMPTY = {"None", "[]", "{}", "False", "0", "''", '""'}


def handler_returns(node: ast.ExceptHandler) -> tuple[bool, str]:
    """except 体是否 return 了一个「空」值。返回 (是否, 该值文本)。"""
    for st in ast.walk(node):
        if isinstance(st, ast.Return) and st.value is not None:
            try:
                txt = ast.unparse(st.value)
            except Exception:
                return False, ""
            if txt.strip() in EMPTY:
                return True, txt.strip()
            return False, txt
    return False, ""


def handler_is_silent(node: ast.ExceptHandler) -> bool:
    """except 体里既没 raise 也没 log。"""
    has_log = any(
        isinstance(st, ast.Call)
        and getattr(st.func, "attr", "") in
        ("warning", "error", "info", "debug", "exception", "critical")
        for st in ast.walk(node)
    )
    has_raise = any(isinstance(st, ast.Raise) for st in ast.walk(node))
    return not (has_log or has_raise)


def catches_broad(node: ast.ExceptHandler) -> str:
    t = node.type
    if t is None:
        return "BARE"
    try:
        return ast.unparse(t)
    except Exception:
        return "?"


def main() -> int:
    rows: list[dict] = []

    for rel in TARGETS:
        p = REPO / rel
        if not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        # 函数名 -> 行号区间
        funcs = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((n.name, n.lineno, n.end_lineno or 0, n.returns))
        funcs.sort(key=lambda x: x[1])

        def owner_of(line: int):
            best = None
            for name, lo, hi, ret in funcs:
                if lo <= line <= hi:
                    if best is None or lo > best[1]:
                        best = (name, lo, ret)
            return best

        for h in ast.walk(tree):
            if not isinstance(h, ast.ExceptHandler):
                continue
            returns_empty, val = handler_returns(h)
            silent = handler_is_silent(h)
            broad = catches_broad(h)
            owner = owner_of(h.lineno)
            if owner is None:
                continue
            fname, flo, ret_ann = owner
            rows.append({
                "file": rel, "line": h.lineno, "func": fname,
                "broad": broad, "returns_empty": returns_empty,
                "value": val, "silent": silent,
                "ann": ast.unparse(ret_ann) if ret_ann else None,
            })

    ambiguous = [r for r in rows if r["returns_empty"] and r["silent"]]
    rest = [r for r in rows if r["silent"] and not r["returns_empty"]]
    broad_and_silent = [r for r in rows if r["silent"] and r["broad"] in ("BARE", "Exception")]

    print("=" * 78)
    print(f"总 except: {len(rows)}   "
          f"歧义(返回空+静默): {len(ambiguous)}   "
          f"仅静默: {len(rest)}   静默且捕获过宽: {len(broad_and_silent)}")
    print("=" * 78)

    print()
    print("### A. 歧义 —— 返回值与「合法的空」不可区分 (危害最高)")
    print("-" * 78)
    if not ambiguous:
        print("  (无)")
    for r in sorted(ambiguous, key=lambda x: (x["file"], x["line"])):
        ann = f"  -> {r['ann']}" if r["ann"] else ""
        print(f"  {r['file']}:{r['line']}  {r['func']}()")
        print(f"      except {r['broad']}  return {r['value']}{ann}")

    print()
    print("### B. 静默但不返回空 (只缺日志)")
    print("-" * 78)
    if not rest:
        print("  (无)")
    for r in sorted(rest, key=lambda x: (x["file"], x["line"]))[:24]:
        ann = f"  -> {r['ann']}" if r["ann"] else ""
        print(f"  {r['file']}:{r['line']}  {r['func']}()   "
              f"except {r['broad']}{ann}")

    print()
    print("### C. 静默 + 捕获过宽 (BARE/Exception)")
    print("-" * 78)
    if not broad_and_silent:
        print("  (无)")
    for r in sorted(broad_and_silent, key=lambda x: (x["file"], x["line"])):
        print(f"  {r['file']}:{r['line']}  {r['func']}()   except {r['broad']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())