#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修测试对 pipeline 私有符号的引用
=================================
测试摸的是 process.py 的内部实现(_is_pid_alive / _parse_current_stage_from_log /
_PIPELINE_RE)。这些不是包级公开 API,不该塞进 __init__.py —— 那样包就欠了
一层契约。正确做法:测试显式引用子模块 process。

四处都是只读调用,没有 monkeypatch,所以改指向不影响行为。
"""
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 文件 -> (需要 import 的别名, 私有名列表)
TARGETS = {
    "tests/test_pipeline.py":          ("pipeline", ["_is_pid_alive", "_parse_current_stage_from_log"]),
    "tests/test_dashboard_api.py":     ("pipeline", ["_is_pid_alive"]),
    "tests/test_chapter_markers.py":   ("pipeline", ["_PIPELINE_RE"]),
    "tests/test_pipeline_is_pid_alive.py": ("pl", ["_is_pid_alive"]),
}

for f, (alias, privs) in TARGETS.items():
    p = pathlib.Path(f)
    t = p.read_text(encoding="utf-8")
    o = t
    used = [s for s in privs if re.search(rf"\b{alias}\.{s}\b", t)]
    if not used:
        print(f"  [跳过] {f} —— 未发现私有引用")
        continue

    for s in used:
        t = re.sub(rf"\b{alias}\.{s}\b", f"{alias}.process.{s}", t)

    # 补 import:紧跟已有的 pipeline import
    if not re.search(rf"from lib\.pipeline import process as {alias}\.process", t):
        anchor = None
        for pat in (rf"^from lib import pipeline as {alias}\b.*$",
                    rf"^from lib\.pipeline import .*$",
                    rf"^from lib import {alias}\b.*$"):
            m = re.search(pat, t, re.M)
            if m:
                anchor = m
                break
        if anchor:
            t = (t[:anchor.end()]
                 + f"\nfrom lib.pipeline import process as {alias}.process  # noqa: E999 内部实现,单元测试直接引层"
                 + t[anchor.end():])
        else:
            # 插在第一个 import 区之后
            m = re.search(r"^import .+$|^from .+$", t, re.M)
            if m:
                t = (t[:m.end()]
                     + f"\nfrom lib.pipeline import process as {alias}.process  # 内部实现"
                     + t[m.end():])
    p.write_text(t, encoding="utf-8", newline="\n")
    print(f"  [修] {f}  -> {', '.join(used)}")
