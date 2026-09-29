"""tools/audit_corrupt_default_write.py — 扫「读取失败 → 默认值 → 写入」。

模式来源: 6dd453b 修的 novel.py bug
    except Exception: sc_result = None      # 损坏
    if sc_result: ...                        # 判假
    else: save_review(status=AUTO_PASSED)    # 依据「缺失」写了一个结论

关键在最后一步 —— 用「读取失败」推出的默认值去提交状态。
只读的地方(f34aa40 修的 7 处)最多显示过期数据;
写的地方会把错误结论固化, 且往往不再重算。

判定逻辑抽在 scan_source(), 供 CLI 与 tests/test_corrupt_default_detector.py
共用 —— 之前测试里重写了一份判定, 测的是副本, 通过了也说明不了脚本本身。
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
UI = REPO / "review_ui"
SKIP = {"__pycache__", "node_modules", "mobile", "venv", ".git"}

# 写入类调用: 精确名单
WRITE_EXACT = {
    "save_review", "save", "write_text", "write_json", "write_bytes",
    "upsert_review", "upsert", "update", "insert", "commit", "delete",
    "mark_chapter_completed", "mark_", "auto_flag", "append_audit",
    "create_version", "transition", "save_outline", "save_chapter",
    "set_stage_context", "put",
}

# 写入类调用: 前缀
WRITE_PREFIXES = ("save", "write", "upsert", "update", "insert",
                  "create", "delete", "mark_", "commit", "set_", "put_",
                  "apply_", "patch")

# 读取类调用: 优先级高于前缀规则(list_reports 之类不算写)
READ_PREFIXES = ("get", "list_", "read", "load_", "fetch", "find_", "count_")

FAALSY_DEFAULT = {"None", "False", "0", "{}", "[]", "''", '""',
                  "set()", "dict()", "list()", "()", "0.0"}


def is_write_call(node: ast.Call) -> tuple[bool, str]:
    """判断调用是否提交状态。返回 (是否, 名称)。

    读取类优先: list_reviews / get_review 之类不能因为名字像写就误判。
    """
    name = None
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    if not name:
        return False, ""
    if name in READ_PREFIXES or name.startswith(READ_PREFIXES):
        return False, name
    if name in WRITE_EXACT:
        return True, name
    if name.startswith(WRITE_PREFIXES) and len(name) > 3:
        return True, name
    return False, name


def scan_source(src: str, filename: str = "<memory>") -> list[dict]:
    """扫一段源码, 返回候选列表。每项描述一次「污染值流入写入调用」。

    判定链:
      1) except 块里把某个变量赋成 falsy 默认值      -> 该变量被「污染」
      2) 同函数内、handler 之后, 有写入类调用的实参用了这个变量 -> 报出

    刻意不做的事: 不排除「在 handler 内部被赋值」的变量。那正是本模式最常见
    的形态(在 except 里降级成默认值), 排除它等于把真 bug 全滤掉。
    """
    tree = ast.parse(src)
    out: list[dict] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue

        # 1) 被污染的变量
        poisoned: dict[str, int] = {}
        for h in ast.walk(fn):
            if not isinstance(h, ast.ExceptHandler):
                continue
            for st in h.body:
                if not isinstance(st, ast.Assign) or len(st.targets) != 1:
                    continue
                t = st.targets[0]
                if not isinstance(t, ast.Name):
                    continue
                try:
                    v = ast.unparse(st.value).strip()
                except Exception:
                    continue
                if v in FAALSY_DEFAULT:
                    poisoned[t.id] = st.lineno

        if not poisoned:
            continue

        # 2) 污染值流入写入调用
        handler_lines = [h.lineno for h in ast.walk(fn)
                         if isinstance(h, ast.ExceptHandler)]
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            ok, name = is_write_call(node)
            if not ok:
                continue
            for a in node.args:
                if isinstance(a, ast.Name) and a.id in poisoned:
                    out.append({
                        "file": filename,
                        "func": fn.name,
                        "line": node.lineno,
                        "call": name,
                        "var": a.id,
                        "assigned_in_handler": any(
                            node.lineno > hl for hl in handler_lines),
                    })
                # dict 下标形式: empty["x"] = f(x) 之类
                elif isinstance(a, ast.Subscript):
                    try:
                        txt = ast.unparse(a)
                    except Exception:
                        continue
                    for var in poisoned:
                        if var in txt:
                            out.append({
                                "file": filename, "func": fn.name,
                                "line": node.lineno, "call": name,
                                "var": f"{var} (下标形式)",
                                "assigned_in_handler": any(
                                    node.lineno > hl for hl in handler_lines),
                            })
    return out


def main() -> int:
    findings: list[dict] = []
    for base in (LIB, UI):
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(s in p.parts for s in SKIP):
                continue
            try:
                src = p.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = str(p.relative_to(REPO)).replace("\\", "/")
            findings.extend(scan_source(src, rel))

    print("=" * 78)
    print(f"「读取失败 → 默认值 → 写入」候选: {len(findings)} 处")
    print("=" * 78)

    by_file = defaultdict(list)
    for f in findings:
        by_file[f["file"]].append(f)

    for rel, items in sorted(by_file.items(), key=lambda x: -len(x[1])):
        print(f"\n  {rel}  ({len(items)} 处)")
        for f in sorted(items, key=lambda x: x["line"]):
            print(f"      L{f['line']:<5} {f['func']}()  →  "
                  f"{f['call']}(... {f['var']} ...)")

    print()
    print("=" * 78)
    print("逐条判读: 该默认值代表「读取失败」还是「确实没有」?")
    print("若代表失败而流入了写入, 就可能固化错误结论 —— 读上下文再定。")
    print("=" * 78)
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())