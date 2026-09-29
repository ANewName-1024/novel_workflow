#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查 fix_pipeline_refs.py 是否误改了行尾符(CRLF -> LF)
======================================================
脚本用了 write_text(..., newline='\\n'),会把原本 CRLF 的文件整体转成 LF,
导致 git diff 显示整文件变更(噪声),把真实改动淹没。
若有此情况,需要恢复原行尾符,只保留实质改动。
"""
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

print("=" * 72)
print("1. 行尾符现状")
print("=" * 72)
FILES = ["novel.py", "review_ui/bp/pipeline.py", "lib/chapter.py",
         "lib/pipeline/state.py", "lib/pipeline/process.py",
         "review_ui/dashboard.py", "tests/test_pipeline.py"]
for f in FILES:
    p = pathlib.Path(f)
    if not p.exists():
        print(f"  {f:<34} (不存在)")
        continue
    b = p.read_bytes()
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n") - crlf
    tag = ""
    if crlf == 0 and lf > 50:
        tag = "  <-- 疑似被归一化为 LF"
    print(f"  {f:<34} CRLF={crlf:<6} LF={lf:<6}{tag}")

print()
print("=" * 72)
print("2. git 视角:忽略空白后的真实改动量")
print("=" * 72)
for f in FILES:
    if not pathlib.Path(f).exists():
        continue
    r1 = subprocess.run(["git", "diff", "--cached", "--numstat", "--", f],
                        capture_output=True)
    r2 = subprocess.run(["git", "diff", "--cached", "-w", "--numstat", "--", f],
                        capture_output=True)
    a = r1.stdout.decode("utf-8", "replace").strip()
    b = r2.stdout.decode("utf-8", "replace").strip()
    n1 = a.split("\t")[0] if a else "0"
    n2 = b.split("\t")[0] if b else "0"
    flag = ""
    if n1.isdigit() and n2.isdigit() and int(n1) > int(n2) * 3 and int(n2) > 0:
        flag = "   <-- 大部分是行尾噪声"
    print(f"  {f:<34} 总 {n1:>5} 行   忽略空白后 {n2:>5} 行{flag}")

print()
print("=" * 72)
print("3. HEAD 版本原本的行尾符")
print("=" * 72)
for f in ["novel.py", "review_ui/bp/pipeline.py", "lib/chapter.py"]:
    r = subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True)
    if r.returncode != 0:
        print(f"  {f:<34} (HEAD 中不存在)")
        continue
    b = r.stdout
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n") - crlf
    tag = "  <-- 原本是 LF" if crlf == 0 and lf > 50 else ""
    print(f"  {f:<34} CRLF={crlf:<6} LF={lf:<6}{tag}")
print("=" * 72)
