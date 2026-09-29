"""tools/find_duplicate_logic.py — 找重复的函数实现。

动机: 2ef28ac 的教训。novel.py::_ensure_review_for_existing 与
review_ui/bp/review.py::_ensure_review_backfill 是同一段逻辑的两份拷贝,
都带着 6dd453b 修的同一个 bug, 而我第一次只改了其中一份。重复实现让
「只修一半」成为默认结果。

本脚本两级检测:
  A. 精确重复 —— 规范化后的函数体完全一致
     规范化: 去 docstring/注释, 把标识符换成序号, 字符串/数字换成占位符。
     这样 `def f(book, ch): save(book, ch)` 与 `def g(x, y): save(x, y)`
     会被判为重复。
  B. 近似重复 —— AST 节点类型序列的相似度 ≥ 阈值
     这才是重点: 两份拷贝总会漂移(少了某个调用、改了措辞), 精确匹配抓不到。

只报, 不动。判定「是否该合并」要人读上下文 —— 有些重复是刻意的
(性能敏感的热点, 或语义确实不同)。
"""
from __future__ import annotations

import ast
import copy
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "lib", REPO / "review_ui"]
TOPLEVEL = REPO / "novel.py"
SKIP_DIRS = {"__pycache__", "node_modules", "mobile", "venv", ".git",
             "build", "dist", "tools", "scripts"}

# 名字像这些的函数【理应】重复, 重复不是问题
BENIGN_PREFIXES = ("test_", "_test", "mock_", "fake_", "tmp_")
BENIGN_NAMES = {
    "_now", "_now_iso", "repl",
    # 路径拼装helper —— 一个几行的样板, 合并反而增加间接层
    "chapters_dir", "memory_dir", "summaries_dir", "reviews_dir",
    "comments_path", "notifications_path", "audit_log_path",
    "review_path", "edited_path", "state_path", "style_path",
    "pipeline_state_path", "metrics_path", "checkpoint_path",
}

NEAR_THRESHOLD = 0.90
MIN_STMT_NODES = 25          # 样板函数太小, 相似度是噪声
MAX_REPORT = 40              # 硬上限, 防止又刷出几万字符


def is_benign(fn: ast.FunctionDef) -> bool:
    name = fn.name
    if name in BENIGN_NAMES:
        return True
    if any(name.startswith(p) for p in BENIGN_PREFIXES):
        return True
    if fn.decorator_list:      # pytest fixture / property
        for d in fn.decorator_list:
            f = getattr(d, "attr", None) or getattr(d, "id", None)
            if f in ("fixture", "property", "staticmethod", "classmethod"):
                return True
    return False



def iter_files():
    for root in ROOTS:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            if any(s in p.parts for s in SKIP_DIRS):
                continue
            yield p
    if TOPLEVEL.exists():
        yield TOPLEVEL


def _clean_copy(fn: ast.FunctionDef) -> ast.FunctionDef:
    """深拷贝并去掉 docstring, 不改原树。"""
    node = copy.deepcopy(fn)
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:] or [ast.Pass()]
    return node


def body_signature(fn: ast.FunctionDef) -> str:
    """规范化后的函数体文本 —— 命名与字面量全部抹平。"""
    try:
        return ast.unparse(Normalize().visit(_clean_copy(fn)))
    except Exception:
        return ""


def struct_signature(fn: ast.FunctionDef) -> list[str]:
    """AST 节点类型序列, 忽略命名与字面量。"""
    try:
        node = _clean_copy(fn)
    except Exception:
        return []
    return [type(n).__name__ for n in ast.walk(node)]


class Normalize(ast.NodeTransformer):
    """把标识符/字面量抹平, 让结构相同但命名不同的函数可比。"""

    def __init__(self):
        self.names: dict[str, str] = {}

    def _n(self, name: str) -> str:
        if name not in self.names:
            self.names[name] = f"v{len(self.names)}"
        return self.names[name]

    def visit_Name(self, node):
        node.id = self._n(node.id)
        return node

    def visit_arg(self, node):
        node.arg = self._n(node.arg)
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        node.attr = "_A"
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            node.value = "_S"
        elif isinstance(node.value, (int, float)):
            node.value = 0
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        node.name = "_F"
        return node

    def visit_Import(self, node):
        return ast.Pass()

    def visit_ImportFrom(self, node):
        return ast.Pass()


def body_signature_unused_removed() -> None:
    """占位: 旧实现已移除, 保留以免历史引用误导。"""


def main() -> int:
    exact: dict[str, list[str]] = defaultdict(list)
    sigs = []

    for p in iter_files():
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name.startswith("__") and fn.name.endswith("__"):
                continue
            if any(d in fn.name for d in ("fixture", "tmp_", "mock", "fake")):
                continue
            if is_benign(fn):
                continue
            loc = f"{rel}::{fn.name}()  L{fn.lineno}"
            sig = body_signature(fn)
            if len(sig) > 40:
                exact[sig].append(loc)
            ss = struct_signature(fn)
            if len(ss) >= MIN_STMT_NODES:
                sigs.append((loc, ss))

    print("=" * 78)
    print("A. 精确重复(规范化后函数体一致)")
    print("=" * 78)
    dups = {k: v for k, v in exact.items() if len(v) > 1}
    if not dups:
        print("  无")
    for k, locs in sorted(dups.items(), key=lambda x: -len(x[1])):
        print(f"  {len(locs)} 份:")
        for l in locs:
            print(f"      {l}")

    print()
    print("=" * 78)
    print(f"B. 近似重复(结构相似度 ≥ {NEAR_THRESHOLD}, 至少 {MIN_STMT_NODES} 个节点)")
    print("=" * 78)
    pairs = []
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            a, b = sigs[i], sigs[j]
            # 粗筛: 长度差太多不可能像
            la, lb = len(a[1]), len(b[1])
            if abs(la - lb) > max(la, lb) * 0.4:
                continue
            r = SequenceMatcher(None, a[1], b[1]).ratio()
            if r >= NEAR_THRESHOLD:
                pairs.append((r, a[0], b[0]))

    pairs.sort(reverse=True)
    shown = 0
    for r, a, b in pairs:
        if shown >= MAX_REPORT:
            print(f"  ... 另有 {len(pairs) - shown} 对未显示 (--max 提高上限)")
            break
        # 同一文件内的对也要报(同一文件两份拷贝同样危险), 但去掉自比
        if a.split("::")[0] == b.split("::")[0] and a.split("::")[1] == b.split("::")[1]:
            continue
        print(f"  {r:.3f}")
        print(f"      {a}")
        print(f"      {b}")
        shown += 1

    print()
    print("=" * 78)
    print(f"合计: 精确 {len(dups)} 组, 近似 {len(pairs)} 对")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())