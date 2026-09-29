"""tools/find_undefined_row_vars.py — 查 db.py 里这类「变量名写错」的读取崩溃。

来源: db.py:351  `d = dict(r)` —— 上面是 `row = conn.execute(...).fetchone()`,
r 从未定义。db.get_review() 每次查到行都抛 NameError, 被 review_service 的
try/except 吞掉后回退读文件, 于是 SQLite 读路径完全失效却看不出任何异常。

工具做法: 对每个函数做作用域分析
  1. 收集函数内所有被「绑定」的名字(赋值 / for / with / except as / 参数 / import)
  2. 收集所有被「读取」的名字
  3. 读而不绑 = 可能未定义

只看形如 dict(x) / x[...] / x.attr / 传参 的实际使用, 排除局部函数与推导式。
这是粗筛, 结果要人读上下文确认 —— 但它能抓住「上面叫 row 下面用 r」这种
最容易在 review 中滑过去的错。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = [REPO / "lib" / "db.py"]
BUILTINS = set(dir(builtins)) | {
    "self", "cls", "__name__", "__file__", "__doc__", "__package__",
}


class Scope(ast.NodeVisitor):
    def __init__(self):
        self.bound: set[str] = set()
        self.used: dict[str, int] = {}     # name -> lineno

    # ── 绑定 ──
    def visit_Assign(self, node):
        for t in node.targets:
            self._bind(t)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._bind(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._bind(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        self._bind(node.target)
        self.generic_visit(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars is not None:
                self._bind(item.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        a = node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
            self.bound.add(arg.arg)
        if a.vararg:
            self.bound.add(a.vararg.arg)
        if a.kwarg:
            self.bound.add(a.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node):
        for al in node.names:
            self.bound.add((al.asname or al.name).split(".")[0])

    def visit_ImportFrom(self, node):
        for al in node.names:
            self.bound.add(al.asname or al.name)

    def visit_Global(self, node):
        self.bound.update(node.names)
    visit_Nonlocal = visit_Global

    def _bind(self, t):
        if isinstance(t, ast.Name):
            self.bound.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._bind(e)
        elif isinstance(t, ast.Starred):
            self._bind(t.value)

    # ── 读取 ──
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.used.setdefault(node.id, node.lineno)
        self.generic_visit(node)


def main() -> int:
    print("=" * 76)
    print("db.py 中「只读不绑」的名字(可能是拼错)")
    print("=" * 76)
    found = 0
    for p in TARGETS:
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = ast.parse(src)

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            sc = Scope()
            sc.bound = set()
            # 关键: 先绑定【本函数自己的】参数。之前只 visit(fn.body) 从不
            # visit(fn) 本身, 于是 root/book/ch_id 全被误报为未定义, 158 处噪声。
            a = fn.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
                sc.bound.add(arg.arg)
            if a.vararg:
                sc.bound.add(a.vararg.arg)
            if a.kwarg:
                sc.bound.add(a.kwarg.arg)
            sc.bound.add(fn.name)
            for stmt in fn.body:
                sc.visit(stmt)

            undefined = {
                n: l for n, l in sc.used.items()
                if n not in sc.bound and n not in BUILTINS and not n.startswith("_")
            }
            # 只报【短名字】: 上面叫 row 下面用 r 这类错, 特征就是短。
            # 长名字未定义多半是真的(比如漏导入), 另开工具查, 不混在一起。
            undefined = {n: l for n, l in undefined.items() if len(n) <= 2}
            if not undefined:
                continue
            found += len(undefined)
            print(f"  L{fn.lineno} {fn.name}()")
            for n, l in sorted(undefined.items(), key=lambda x: x[1]):
                ctx = lines[l - 1].strip()[:64] if l <= len(lines) else ""
                print(f"      L{l:<5} {n:<20} {ctx}")

    print()
    print("=" * 76)
    print(f"合计 {found} 处")
    if found:
        print("每一处都要读上下文确认 —— 可能是真崩溃, 也可能是模块级名字。")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())