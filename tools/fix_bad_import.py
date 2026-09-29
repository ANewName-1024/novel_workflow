#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修掉 fix_test_privates.py 生成的非法 import
=============================================
上一版生成了 `from lib.pipeline import process as pl.process` —— import 别名
不能是属性路径,是语法错误,导致 4 个测试文件收集失败。

正确的做法:不需要额外 import。lib/pipeline/__init__.py 里有
`from . import process, state`,所以 `pl.process` 本身就能通过已存在的
`from lib import pipeline as pl` 访问到 —— 直接删掉那行坏 import 即可。
"""
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FILES = [
    "tests/test_pipeline.py",
    "tests/test_dashboard_api.py",
    "tests/test_chapter_markers.py",
    "tests/test_pipeline_is_pid_alive.py",
]

BAD = re.compile(
    r"^from lib\.pipeline import process as (\w+)\.process.*\n", re.M)

for f in FILES:
    p = pathlib.Path(f)
    t = p.read_text(encoding="utf-8")
    m = BAD.search(t)
    if not m:
        print(f"  [跳过] {f} —— 无坏 import")
        continue
    alias = m.group(1)
    t2 = BAD.sub("", t, count=1)
    # 确认 alias 确实已绑定到 lib.pipeline(否则 pl.process 不可达)
    has_pkg = re.search(rf"^from lib import pipeline as {alias}\b", t2, re.M) or \
        re.search(rf"^from lib\.pipeline import\b", t2, re.M)
    p.write_text(t2, encoding="utf-8", newline="\n")
    print(f"  [修] {f}  删除坏 import;  {alias} 已绑定包: {bool(has_pkg)}")

print()
print("=== 语法自检 ===")
import ast
bad = 0
for f in FILES:
    p = pathlib.Path(f)
    try:
        ast.parse(p.read_text(encoding="utf-8"))
        print(f"  OK  {f}")
    except SyntaxError as e:
        bad += 1
        print(f"  ERR {f}: {e}")
print(f"  语法错误 {bad} 个")
