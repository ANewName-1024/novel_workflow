"""fix_gitignore_encoding.py — 把 .gitignore 里的 GBK 片段转成 UTF-8。

发现: tools/check_encoding.py 报 clean=286, dirty=0, 但 .gitignore 里有
14 个 GBK 字节。守卫只扫 *.py, 没覆盖 .gitignore —— 这是它自己的盲区。

难点: 文件是【混合】的(大部分 UTF-8 + 少量 GBK 注释)。整体按 GBK 解码会
毁掉 UTF-8 部分, 所以必须逐段处理:

  1. 找出所有非 UTF-8 的字节区间
  2. 每段单独试 GBK / GB18030, 能解就替换成对应 UTF-8
  3. 解不出来的原样保留, 绝不猜测
  4. 输出前后对照, 人工确认后再写回

只处理 .gitignore, 不碰其他文件。
"""
from __future__ import annotations

import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parent / ".gitignore"


def bad_runs(raw: bytes) -> list[tuple[int, int]]:
    """返回所有非 UTF-8 字节区间 [(start, end), ...]"""
    runs = []
    i = 0
    n = len(raw)
    while i < n:
        try:
            raw[i:].decode("utf-8", errors="strict")
            break
        except UnicodeDecodeError as e:
            start = i + e.start
            # 向后扩展, 把连续的坏字节并成一段(在下一个合法 UTF-8 边界处停)
            j = start
            while j < n:
                try:
                    raw[j:].decode("utf-8", errors="strict")
                    break
                except UnicodeDecodeError as e2:
                    j = j + e2.start + max(1, e2.end - e2.start)
            runs.append((start, j))
            i = j
    return runs


def main() -> int:
    raw = TARGET.read_bytes()
    print(f"{TARGET.name}: {len(raw)} bytes")
    runs = bad_runs(raw)
    print(f"非 UTF-8 区间: {len(runs)} 段")
    if not runs:
        print("已经是合法 UTF-8, 无需处理")
        return 0

    for s, e in runs:
        chunk = raw[s:e]
        print(f"  [{s}:{e}] {len(chunk)} bytes  {chunk!r}")
        for enc in ("gb18030", "gbk", "big5"):
            try:
                txt = chunk.decode(enc, errors="strict")
                print(f"      {enc:<9} → {txt!r}")
            except UnicodeDecodeError:
                print(f"      {enc:<9} ✗")

    if "--apply" not in sys.argv:
        print("\n(加 --apply 执行替换)")
        return 0

    out = bytearray()
    prev = 0
    for s, e in runs:
        out += raw[prev:s]
        chunk = raw[s:e]
        done = False
        for enc in ("gb18030", "gbk"):
            try:
                txt = chunk.decode(enc, errors="strict")
                out += txt.encode("utf-8")
                print(f"  [{s}:{e}] 按 {enc} 转出: {txt!r}")
                done = True
                break
            except UnicodeDecodeError:
                continue
        if not done:
            print(f"  [{s}:{e}] ★ 无法解码, 原样保留")
            out += chunk
        prev = e
    out += raw[prev:]

    try:
        out.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        print(f"\n结果仍非合法 UTF-8 @{e.start}, 已放弃写入")
        return 1

    TARGET.write_bytes(bytes(out))
    print(f"\n已写入 {TARGET.name}, {len(raw)} → {len(out)} bytes")
    print("校验: 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())