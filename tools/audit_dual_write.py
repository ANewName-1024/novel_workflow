"""tools/audit_dual_write.py — 查 file / SQLite 双写的数据一致性风险。

背景: MEMORY: novel-workflow 记的是「SQLite 优先, 文件后备」。
双写有两类风险, 严重性差一个量级:

  A. 一边失败   —— 5ac8df0 已经给这些位点补了日志, 读回时回退到文件。
  B. 两边都「成功」但内容不同 —— 今天没查过, 且不会报错。

B 类才是真问题: 没有异常、没有日志, 读回却拿到过期或残缺的数据。
典型成因是 SQLite 存的是字段投影(files 存全量), 投影漏了字段,
而读路径优先读 SQLite —— 那字段就凭空消失了。

本脚本找:
  1. 所有双写位点(file 写 + sqlite 写 配对)
  2. 每处 SQLite 实际写了哪些字段
  3. 每处的读回优先级
  4. 两边字段集合的差集 —— 差集非空即 B 类风险

只报事实, 不判定修法。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "lib", REPO / "review_ui"]
SKIP = {"__pycache__", "node_modules", "mobile", "venv", ".git"}


def kwarg_names(call: ast.Call) -> list[str]:
    out = []
    for kw in call.keywords:
        if kw.arg:
            out.append(kw.arg)
    return out


def pos_arg_fields(call: ast.Call, func_def: ast.FunctionDef | None) -> list[str]:
    """位置参数按被调函数的形参名映射。"""
    if func_def is None:
        return []
    names = [a.arg for a in func_def.args.args]
    return [names[i] for i in range(len(call.args)) if i < len(names)]


def find_db_funcs() -> dict[str, ast.FunctionDef]:
    p = REPO / "lib" / "db.py"
    if not p.exists():
        return {}
    tree = ast.parse(p.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def main() -> int:
    db_funcs = find_db_funcs()
    print("=" * 78)
    print("A. 双写位点(file 写 + SQLite 写 出现在同一函数)")
    print("=" * 78)
    sites = []
    for root in ROOTS:
        for p in sorted(root.rglob("*.py")):
            if any(s in p.parts for s in SKIP):
                continue
            rel = str(p.relative_to(REPO)).replace("\\", "/")
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
                file_writes, db_writes = [], []
                for c in calls:
                    name = None
                    if isinstance(c.func, ast.Name):
                        name = c.func.id
                    elif isinstance(c.func, ast.Attribute):
                        name = c.func.attr
                    if not name:
                        continue
                    if name in ("write_text", "write_json", "write_bytes",
                                "write_chapter", "save_json"):
                        file_writes.append(c)
                    elif name.startswith("upsert") or name in (
                            "insert_review", "update_review", "upsert_state",
                            "set_state", "upsert_entity"):
                        db_writes.append(c)
                if file_writes and db_writes:
                    sites.append((rel, fn.name, fn.lineno, file_writes, db_writes))

    if not sites:
        print("  无")
    for rel, fn, ln, fw, dw in sites:
        print(f"  {rel}::{fn}()  L{ln}")
        print(f"      文件写: {[getattr(c.func, 'id', getattr(c.func, 'attr', '?')) for c in fw]}")
        for c in dw:
            name = getattr(c.func, "attr", getattr(c.func, "id", "?"))
            kw = kwarg_names(c)
            fields = pos_arg_fields(c, db_funcs.get(name))
            print(f"      DB 写  : {name}()  投影字段 = {fields + kw}")

    print()
    print("=" * 78)
    print("B. 读回优先级(决定残缺数据会不会被看见)")
    print("=" * 78)
    reads = []
    for root in ROOTS:
        for p in sorted(root.rglob("*.py")):
            if any(s in p.parts for s in SKIP):
                continue
            rel = str(p.relative_to(REPO)).replace("\\", "/")
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                names = []
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call):
                        nm = (c.func.id if isinstance(c.func, ast.Name)
                              else getattr(c.func, "attr", None))
                        if nm and (nm in ("get_review", "list_reviews",
                                          "review_stats", "get_state",
                                          "load_state")
                                   or nm.startswith("get_") and "review" in nm):
                            names.append(nm)
                if names:
                    src = ast.unparse(fn)
                    # DB 在前还是文件在前
                    db_first = False
                    try:
                        if names:
                            idx = src.find(names[0])
                            # 找 fallback 信号
                            fb = min([i for i in (src.find("read_json"),
                                                 src.find(".review.json"),
                                                 src.find("glob("))
                                      if i >= 0] or [10**6])
                            db_first = idx < fb
                    except Exception:
                        pass
                    reads.append((rel, fn.name, fn.lineno, names, db_first))
    for rel, fn, ln, names, db_first in reads:
        order = "DB 优先, 文件兜底" if db_first else "文件优先, DB 兜底?"
        print(f"  {rel}::{fn}()  L{ln}  {sorted(set(names))}  → {order}")

    print()
    print("=" * 78)
    print("C. 结论要点")
    print("=" * 78)
    print("  B 类风险成立的条件: 某处双写时 SQLite 侧是【字段投影】而非全量。")
    print("  读回若 DB 优先, 投影里没有的字段就只在文件里 —— 走文件兜底才看得到,")
    print("  而 DB 优先意味着正常情况下根本不会走兜底 → 字段静默丢失。")
    print("  上表「投影字段」若少于文件内容, 即为该风险点。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())