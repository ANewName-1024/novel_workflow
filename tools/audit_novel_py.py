"""tools/audit_novel_py.py — 审 lib 之外的最后一类: novel.py(CLI 入口, 884 行)。

此前几轮都在 lib/ 与 review_ui/, novel.py 是今天唯一没系统审过的大文件。
CLI 的异常处理有个特殊风险: 顶层吞异常会让用户以为命令成功了。

按危害排序(A 歧义 > B 静默 > C 过宽), 与 audit_ambiguity.py 同一套标准。
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "novel.py"

WRITE_HINTS = ("save", "write", "create", "update", "delete", "init",
               "migrate", "backup", "import", "export", "generate")


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    funcs = sorted(
        [(n.lineno, n.end_lineno or 0, n.name) for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef)]
    )

    buckets = defaultdict(list)
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler):
            continue
        try:
            t = ast.unparse(h.type) if h.type else "BARE"
        except Exception:
            t = "?"

        has_log = any(
            isinstance(s, ast.Call) and getattr(s.func, "attr", "") in
            ("warning", "error", "info", "debug", "exception", "critical")
            for s in ast.walk(h)
        )
        has_raise = any(isinstance(s, ast.Raise) for s in ast.walk(h))
        has_return = any(isinstance(s, ast.Return) for s in ast.walk(h))
        sys_exit = any(
            isinstance(s, ast.Call) and getattr(s.func, "id", "") == "exit"
            for s in ast.walk(h)
        )

        owner = ("?", 0)
        for lo, hi, nm in funcs:
            if lo <= h.lineno <= hi and h.lineno >= owner[1]:
                owner = (nm, lo)

        ret_txt = ""
        for s in ast.walk(h):
            if isinstance(s, ast.Return) and s.value is not None:
                try:
                    ret_txt = ast.unparse(s.value)[:40]
                except Exception:
                    ret_txt = "?"
                break

        entry = (h.lineno, owner[0], t, ret_txt, lines[h.lineno - 1].strip()[:60])

        if has_log:
            buckets["C-已有日志"].append(entry)
        elif has_raise or sys_exit:
            buckets["C-已处理(raise/exit)"].append(entry)
        elif has_return:
            if ret_txt.strip() in ("None", "False", "[]", "{}", "0", "''", '""'):
                buckets["A-歧义: 返回空"].append(entry)
            else:
                buckets["B-返回具体值"].append(entry)
        else:
            buckets["B-完全静默"].append(entry)

    total = sum(len(v) for v in buckets.values())
    print("=" * 76)
    print(f"novel.py  except 共 {total} 处")
    for k in sorted(buckets):
        print(f"  {k:<22}{len(buckets[k]):>3} 处")
    print("=" * 76)

    for key in ("A-歧义: 返回空", "B-完全静默", "B-返回具体值"):
        items = buckets.get(key, [])
        if not items:
            continue
        print()
        print(f"### {key}  ({len(items)} 处)")
        print("-" * 76)
        for ln, fn, t, ret, src_line in sorted(items):
            w = " [写操作]" if any(k in fn.lower() for k in WRITE_HINTS) else ""
            print(f"  L{ln:<5} {fn}()   except {t}{w}")
            if ret:
                print(f"        return {ret}")
            print(f"        {src_line}")

    print()
    print("### 顶层 main() 是否有兜底")
    print("-" * 76)
    for lo, hi, nm in funcs:
        if nm == "main":
            body = "\n".join(lines[lo - 1:hi])
            has_exit = "sys.exit" in body
            print(f"  main()  L{lo}-L{hi}")
            print(f"    含 sys.exit : {has_exit}")
            print(f"    含裸 except : {'except:' in body or 'except Exception:\\n        pass' in body}")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())