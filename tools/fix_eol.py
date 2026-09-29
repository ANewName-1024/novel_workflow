#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把被误改行尾符的文件恢复成 CRLF
================================
fix_pipeline_refs.py 用 write_text(..., newline='\\n') 写回,把原本 CRLF 的
文件整体转成 LF。git 因此显示整文件变更(noise),掩盖真实改动。

这里只改行尾符,不动任何内容 —— 用二进制读写,避免二次转码。
"""
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 原本是 CRLF、被我误转成 LF 的文件
TO_CRLF = ["novel.py", "review_ui/bp/pipeline.py"]

for f in TO_CRLF:
    p = pathlib.Path(f)
    if not p.exists():
        print(f"  [跳过] {f} 不存在")
        continue
    b = p.read_bytes()
    crlf = b.count(b"\r\n")
    lf_total = b.count(b"\n")
    if crlf > 0:
        print(f"  [已OK] {f}  CRLF={crlf} 无需改")
        continue
    # 二进制层面:LF -> CRLF(先归一,避免重复)
    fixed = b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    p.write_bytes(fixed)
    print(f"  [修] {f}  LF {lf_total} -> CRLF "
          f"{fixed.count(b'\r\n')}  (内容字节未变,仅行尾)")

print()
print("=== 校验:忽略空白后的 diff 应只含真实改动 ===")
import subprocess
for f in TO_CRLF:
    r = subprocess.run(["git", "diff", "-w", "--numstat", "--", f],
                       capture_output=True)
    print(f"  {f:<32} {r.stdout.decode('utf-8','replace').strip() or '(无变化)'}")
