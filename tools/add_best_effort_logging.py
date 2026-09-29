"""tools/add_best_effort_logging.py — 给纯静默 best-effort 位点补日志(v2, AST 定位)。

v1 用硬编码行号, 15 处全部未命中 —— 行号指向 except 行而非 pass 行。
盲插会插到错误位置, 安全检查拦下了, 但已经给 4 个文件加了 logger 成了半成品。
v2 改用 AST 精确定位每个「body 恰好是 [Pass]」的 ExceptHandler, 不依赖行号。

分类(依据 MEMORY: novel-workflow L98, 不一刀切):
  跳过 _kill_pid_tree 的 3 处 —— 捕获 NoSuchProcess 表示进程已没了,
    这就是「杀成功」, 记 warning 是噪音。

级别:
  debug  —— 附属产物(版本快照、session log)
  info   —— 阶段性失败, 流程继续
  warning—— 状态/日志读取失败, 排查时需要

不用 best_effort 装饰器: 那是装饰器, 这些是内联 try 块,
为套装饰器重构函数结构是本末倒置。直接加一行 log 最直白。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (文件, 函数名, 该函数内第几个静默位点) —— 用函数+序号定位, 避免行号漂移
SITES = {
    "lib/chapter.py": {
        "_v2_mark": {0: "debug", 1: "debug"},          # session log, 快照
        "run_post_write_pipeline": {0: "info", 1: "info", 2: "info"},
    },
    "lib/memory.py": {
        "get_world": {0: "info"},
        "merge_extraction": {0: "info", 1: "info", 2: "info"},
    },
    "lib/db.py": {"close_all": {0: "info"}},
    "lib/outline_editor.py": {"save_outline": {0: "debug"}},
    "lib/pipeline/state.py": {"_atomic_write_json": {0: "warning"}},
    "lib/review_actions.py": {
        "apply_feedback_to_chapter": {0: "info"}},
    "lib/pipeline/process.py": {
        "status": {0: "warning"},
        "stream_log": {0: "warning"},
    },
}

SKIP_FUNCS = {"_kill_pid_tree", "_reap_zombie", "_is_pid_alive"}
LABEL = {
    "lib/chapter.py": "流水线",
    "lib/memory.py": "记忆合并",
    "lib/db.py": "数据库",
    "lib/outline_editor.py": "大纲",
    "lib/pipeline/state.py": "检查点",
    "lib/review_actions.py": "评审动作",
    "lib/pipeline/process.py": "进程编排",
}


def silent_handlers(fn: ast.FunctionDef) -> list[ast.ExceptHandler]:
    """函数内 body 恰好是 [Pass] 的 handler, 按出现顺序。"""
    out = []
    for h in ast.walk(fn):
        if not isinstance(h, ast.ExceptHandler):
            continue
        if any(isinstance(s, (ast.Raise, ast.Return)) for s in h.body):
            continue
        has_log = any(
            isinstance(s, ast.Call)
            and getattr(s.func, "attr", "") in
            ("warning", "error", "info", "debug", "exception", "critical")
            for s in ast.walk(h)
        )
        if has_log:
            continue
        if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
            out.append(h)
    out.sort(key=lambda h: h.lineno)
    return out


def ensure_logger(text: str) -> str:
    if "log = logging.getLogger" in text:
        return text
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = max(last, node.end_lineno or 0)
    if last == 0:
        raise RuntimeError("找不到顶层 import")
    if not any(l.startswith("import logging") for l in lines[:last + 1]):
        lines[last:last] = ["import logging\n"]
        last += 1
    lines[last:last] = ["\n", 'log = logging.getLogger(__name__)\n']
    return "".join(lines)


def main() -> int:
    total, problems = 0, []

    for rel, fnmap in SITES.items():
        p = REPO / rel
        if not p.exists():
            problems.append(f"{rel} 不存在")
            continue

        text = p.read_text(encoding="utf-8")
        text = ensure_logger(text)          # 可能加 2 行, 所以先做
        lines = text.splitlines(keepends=True)
        tree = ast.parse(text)

        inserts: list[tuple[int, str, str]] = []   # (行号, 内容, 说明)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in SKIP_FUNCS or node.name not in fnmap:
                continue
            hs = silent_handlers(node)
            want = fnmap[node.name]
            if len(hs) < max(want) + 1:
                problems.append(
                    f"{rel}::{node.name} 只找到 {len(hs)} 个静默位点, "
                    f"但配置需要 {max(want) + 1} 个")
                continue
            for idx, lvl in want.items():
                h = hs[idx]
                passline = h.body[0].lineno          # pass 所在行
                indent = " " * (h.col_offset + 4)
                tag = LABEL.get(rel, rel)
                ins = (f'{indent}log.{lvl}("{tag} · {node.name} 第{idx + 1}处'
                       f'兜底步骤失败 (非致命)", exc_info=True)\n')
                inserts.append((passline, ins, f"{rel}:{passline} {node.name}[{idx}]"))

        if not inserts:
            continue

        # 倒序插入, 避免行号偏移
        for ln, ins, desc in sorted(inserts, key=lambda x: -x[0]):
            lines.insert(ln - 1, ins)
            total += 1
            print(f"  [log] {desc}")

        new = "".join(lines)
        try:
            ast.parse(new)
        except SyntaxError as e:
            problems.append(f"{rel} 改后语法错误: {e}")
            continue
        p.write_text(new, encoding="utf-8", newline="")

    print()
    print(f"已补 {total} 处日志")
    if problems:
        print(f"问题 {len(problems)} 项:")
        for x in problems:
            print(f"  {x}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())