"""tools/convert_chapter_logging.py — chapter.py: 诊断 print -> logging

分级依据(见 MEMORY: novel-workflow L98, 及本次会话的判断):
  A. `⚠ X 失败 (非致命)` 与 `v2-checkpoint 写入失败` —— 纯诊断信息, 触发时
     没有对应的用户动作。转 logging: 落文件、可按 book/chapter 过滤、带时间戳。
  B. `[PIPELINE] book=... stage=... status=...` —— **跨进程协议, 绝对不能转**。
     lib/pipeline/process.py:226 用 _PIPELINE_RE 正则从日志文件里抓回来,
     解析流水线当前阶段与崩溃恢复。改了就静默失效, 且现有测试会全绿
     (test_chapter_markers.py 只测正则, 不测生产者)。
  C. `✓ / ✗ / →` 进度与指引 —— CLI 面向用户, 保持 print。

终端可见性: lib/logging_setup.py 的 setup_logging() 默认 console=True,
同时输出 stdout + 轮转文件。所以 A 类转 logging 后终端仍然可见。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "lib" / "chapter.py"

# (原始行, 替换行) —— 每条必须唯一, 脚本会校验
CONVERSIONS = [
    # ── A. 纯诊断 -> log.warning ──
    ('        print(f"  [v2-checkpoint] {stage}→{status} 写入失败: {_e}", file=sys.stderr)',
     '        log.warning("[v2-checkpoint] %s→%s 写入失败: %s", stage, status, _e)'),
    ('        print(f"  ⚠ extract 失败 (非致命): {e}")',
     '        log.warning("extract 失败 (非致命): %s", e)'),
    ('        print(f"  ⚠ entity_diff 失败 (非致命): {e}")',
     '        log.warning("entity_diff 失败 (非致命): %s", e)'),
    ('        print(f"  ⚠ 摘要生成失败 (非致命): {e}")',
     '        log.warning("摘要生成失败 (非致命): %s", e)'),
    ('        print(f"  ⚠ 状态更新失败 (非致命): {e}")',
     '        log.warning("状态更新失败 (非致命): %s", e)'),
    ('            print(f"  ⚠ 风格锚点提取失败 (非致命): {e}")',
     '            log.warning("风格锚点提取失败 (非致命): %s", e)'),
    ('                print(f"  ⚠ 评审记录创建失败 (非致命): {flag_err}")',
     '                log.warning("评审记录创建失败 (非致命): %s", flag_err)'),
    ('            print(f"  ⚠ 自检失败 (非致命): {e}")',
     '            log.warning("自检失败 (非致命): %s", e)'),
]

# [PIPELINE] 行前缀 —— 转换后加锁注释, 防止后来者手贱
PIPELINE_PREFIX = '[PIPELINE] book='


def ensure_logger(text: str) -> str:
    if "log = logging.getLogger" in text:
        return text
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    last_import = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import = max(last_import, node.end_lineno or 0)
    if last_import == 0:
        raise RuntimeError("找不到顶层 import")
    lines[last_import:last_import] = [
        "import logging\n",
        "\n",
        'log = logging.getLogger(__name__)\n',
    ]
    return "".join(lines)


def lock_pipeline_markers(text: str) -> tuple[str, int]:
    """在每个 [PIPELINE] marker 上方加协议锁注释。"""
    out, added, in_except = [], 0, False
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 粗略判断 except 块: 以 except 开头, 缩进 4 的倍数
        if stripped.startswith("except "):
            in_except = True
        if PIPELINE_PREFIX in line and in_except:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + "# ⚠ 协议行: process._PIPELINE_RE 从日志里正则解析它来恢复阶段状态。\n")
            out.append(indent + "#   改格式 = 崩溃恢复静默失效, 且 test_chapter_markers.py 测不出来。勿改成 log.*。\n")
            added += 1
        out.append(line)
        i += 1
    return "".join(out), added


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    orig = text

    text = ensure_logger(text)

    problems = []
    for old, new in CONVERSIONS:
        n = text.count(old)
        if n != 1:
            problems.append(f"锚点出现 {n} 次(应为 1): {old.strip()[:60]}")
            continue
        text = text.replace(old, new, 1)

    if problems:
        print("转换中止, 以下锚点有问题:")
        for p in problems:
            print(f"  - {p}")
        return 1

    try:
        ast.parse(text)
    except SyntaxError as e:
        print(f"改后语法错误: {e}")
        return 1

    text, n_locked = lock_pipeline_markers(text)

    TARGET.write_text(text, encoding="utf-8", newline="")

    # 校验: 协议行数量必须不变
    new_count = text.count(PIPELINE_PREFIX)
    old_count = orig.count(PIPELINE_PREFIX)
    print(f"已转换诊断 print: {len(CONVERSIONS)} 处")
    print(f"已加协议锁注释  : {n_locked} 处")
    print(f"[PIPELINE] 行数 : {old_count} -> {new_count}  "
          f"{'✓ 未损失' if old_count == new_count else '✗ 变了!'}")
    print()
    print("剩余 print(应为 CLI 进度/指引):")
    ast.parse(text)  # 再确认一次
    tree = ast.parse(text)
    left = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            left += 1
    print(f"  {left} 处 print 保留")
    return 0 if old_count == new_count else 1


if __name__ == "__main__":
    raise SystemExit(main())