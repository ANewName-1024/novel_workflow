"""tools/apply_ambiguity_logging.py — 给「失败伪装成成功」的位点补日志。

审计结论(tools/audit_ambiguity.py):95 处 except 里只有 7 处真该改 ——
它们的返回值 None/[] 与「合法的空」不可区分, 调用方分不清「本来就没有」
和「读坏了」。UI 层面这会显示成「无评审 / 无记录」, 而实际是文件损坏。

按 L98: bool 返回 (_is_pid_alive / _kill_pid_tree) 本身是精确答案, 不动;
刻意 best-effort 的 SWALLOW 也不动。本脚本只碰这 7 处。

返回契约保持不变(None/[]), 避免破坏调用方; 改的是「失败是否可见」。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 1) 需要 logger 的文件: (相对路径, 锚点 import 行, 要在其后插入)
NEEDS_LOGGER = {
    "lib/review_service.py": "import ",
    "lib/self_check.py": "import ",
    "lib/entity_diff.py": "import ",
    "lib/pipeline/process.py": "import ",
}

# 2) 7 处位点: (文件, 唯一上下文, 替换文本)
#    上下文必须唯一 —— 脚本会校验唯一性, 匹配不到就报错退出。
SITES = [
    (
        "lib/review_service.py",
        "    try:\n"
        "        return json.loads(p.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return None\n",
        "    try:\n"
        "        return json.loads(p.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError) as e:\n"
        "        # 评审文件损坏 != 「本章没有评审」。返回 None 一样会被 UI 读成\n"
        "        # 「无评审」, 所以这里必须留痕。\n"
        "        log.warning(\"评审记录损坏,按「无评审」处理: %s | %s: %s\",\n"
        "                    p, type(e).__name__, e)\n"
        "        return None\n",
    ),
    (
        "lib/self_check.py",
        "    try:\n"
        "        return json.loads(p.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return None\n",
        "    try:\n"
        "        return json.loads(p.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError) as e:\n"
        "        log.warning(\"自检记录损坏,按「无自检」处理: %s | %s: %s\",\n"
        "                    p, type(e).__name__, e)\n"
        "        return None\n",
    ),
    (
        "lib/entity_diff.py",
        "    try:\n"
        "        return json.loads(path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return None\n",
        "    try:\n"
        "        return json.loads(path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError) as e:\n"
        "        log.warning(\"实体变更记录损坏,按「无变更」处理: %s | %s: %s\",\n"
        "                    path, type(e).__name__, e)\n"
        "        return None\n",
    ),
    (
        "lib/pipeline/process.py",
        "    try:\n"
        "        return json.loads(path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return None\n",
        "    try:\n"
        "        return json.loads(path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError) as e:\n"
        "        # 状态文件损坏 != 「流水线没跑过」. status() 会据此把状态判错.\n"
        "        log.warning(\"流水线状态文件损坏,按「无状态」处理: %s | %s: %s\",\n"
        "                    path, type(e).__name__, e)\n"
        "        return None\n",
    ),
    (
        "lib/pipeline/process.py",
        "    except OSError:\n"
        "        return None\n"
        "    # 倒序找最近的 PIPELINE marker\n",
        "    except OSError as e:\n"
        "        # 日志读不到 != 「日志里没有 marker」—— 后者才是正常情况.\n"
        "        log.warning(\"流水线日志不可读,无法解析当前阶段: %s | %s: %s\",\n"
        "                    path, type(e).__name__, e)\n"
        "        return None\n"
        "    # 倒序找最近的 PIPELINE marker\n",
    ),
    (
        "lib/pipeline/process.py",
        "        except OSError:\n"
        "            return []\n",
        "        except OSError as e:\n"
        "            # 空日志是正常的; 读不到日志不是. 不记的话面板会显示「无日志」.\n"
        "            log.warning(\"流水线日志不可读,返回空列表: %s | %s: %s\",\n"
        "                        path, type(e).__name__, e)\n"
        "            return []\n",
    ),
    (
        "lib/pipeline/state.py",
        "    try:\n"
        "        return json.loads(snap_path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return None\n",
        "    try:\n"
        "        return json.loads(snap_path.read_text(encoding=\"utf-8\"))\n"
        "    except (json.JSONDecodeError, OSError) as e:\n"
        "        log.warning(\"快照文件损坏,按「无快照」处理: %s | %s: %s\",\n"
        "                    snap_path, type(e).__name__, e)\n"
        "        return None\n",
    ),
]


def add_logger(text: str) -> str:
    """在最后一个顶层 import 之后插入 logging 导入与 logger 定义。"""
    tree = ast.parse(text)
    last_import_end = 0
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = max(last_import_end, node.end_lineno or 0)
    if last_import_end == 0:
        raise RuntimeError("找不到顶层 import,无法安全插入")
    if "\nimport logging\n" in text:
        return text
    inject = [
        "import logging\n",
        "\n",
        'log = logging.getLogger(__name__)\n',
    ]
    lines[last_import_end:last_import_end] = inject
    return "".join(lines)


def main() -> int:
    failures: list[str] = []

    # 步骤 1: 加 logger
    for rel in NEEDS_LOGGER:
        p = REPO / rel
        text = p.read_text(encoding="utf-8")
        if "log = logging.getLogger" in text:
            print(f"  [跳过] {rel} 已有 logger")
            continue
        try:
            new = add_logger(text)
            ast.parse(new)          # 语法自检
            p.write_text(new, encoding="utf-8", newline="")
            print(f"  [加]  {rel}  logger 已注入")
        except Exception as e:
            failures.append(f"{rel}: {e}")
            print(f"  [失败] {rel}: {e}")

    # 步骤 2: 改 7 处位点
    print()
    for rel, old, new in SITES:
        p = REPO / rel
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n == 0:
            failures.append(f"{rel}: 锚点未命中")
            print(f"  [失败] {rel}: 锚点未命中")
            continue
        if n > 1:
            failures.append(f"{rel}: 锚点出现 {n} 次,非唯一")
            print(f"  [失败] {rel}: 锚点出现 {n} 次")
            continue
        updated = text.replace(old, new, 1)
        try:
            ast.parse(updated)
        except SyntaxError as e:
            failures.append(f"{rel}: 改后语法错误 {e}")
            print(f"  [失败] {rel}: 改后语法错误 {e}")
            continue
        p.write_text(updated, encoding="utf-8", newline="")
        print(f"  [改]  {rel}  1 处")

    print()
    if failures:
        print(f"完成, 但有 {len(failures)} 处失败:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部成功。返回契约未变, 仅补日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())