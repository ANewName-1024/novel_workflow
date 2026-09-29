"""tools_encode_check.py — 检测 self-improving 域文件的真实编码, 并验证 GBK→UTF-8 是否无损。

背景: 尝试编辑 D:\\self-improving\\domains\\test-design.md 时报
「not valid UTF-8」—— 那是 novel_workflow 今天做的编码归一(b8c09ef)在
另一个目录的同类问题。

转换前必须先证明:
  1. 它确实是 GBK(而不是别的编码, 或已损坏)
  2. 能严格解码(不靠 errors='replace' 蒙混)
  3. 往返一致(GBK→str→GBK 字节完全相同)
只满足 1-2 不够: 若原文件混了编码, 严格解码也可能「成功」但内容已错。
往返一致才是真无损。
"""
from __future__ import annotations

import sys
from pathlib import Path

FILES = [
    Path(r"D:\self-improving\memory.md"),
    Path(r"D:\self-improving\domains\novel-workflow.md"),
    Path(r"D:\self-improving\domains\test-design.md"),
    Path(r"D:\self-improving\domains\audit.md"),
    Path(r"D:\self-improving\corrections.md"),
]


def probe(p: Path) -> dict:
    raw = p.read_bytes()
    info = {"path": str(p), "bytes": len(raw), "bom": False, "enc": None,
            "lossless": None, "reason": ""}
    if raw[:3] == b"\xef\xbb\xbf":
        info["bom"] = True
    for enc in ("utf-8", "gbk", "gb18030", "big5", "shift_jis", "cp1252"):
        try:
            text = raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
        if enc == "utf-8":
            info["enc"] = "utf-8"
            info["lossless"] = True
            break
        # 非 UTF-8: 必须能往返编码回同样的字节, 否则说明混了编码
        try:
            back = text.encode(enc, errors="strict")
        except UnicodeEncodeError:
            info["reason"] = f"{enc} 往返编码失败(内容含该编码无法表示的字符)"
            continue
        if back == raw:
            info["enc"] = enc
            info["lossless"] = True
            break
        else:
            info["enc"] = enc
            info["lossless"] = False
            info["reason"] = (f"{enc} 往返后字节不同({len(back)} vs {len(raw)})"
                              f" —— 可能混了多种编码, 禁止转换")
    return info


def main() -> int:
    print("=" * 78)
    print("self-improving 域文件编码探测")
    print("=" * 78)
    need = []
    for p in FILES:
        if not p.exists():
            print(f"  (不存在) {p}")
            continue
        i = probe(p)
        bom = " +BOM" if i["bom"] else ""
        if i["enc"] is None:
            print(f"  ✗ {p.name:<22} 无法确定编码")
            continue
        ok = "无损" if i["lossless"] else "有风险"
        print(f"  {'✓' if i['lossless'] else '✗'} {p.name:<22} {i['enc']:<9}{bom:<5}"
              f" {i['bytes']:>7} bytes  {ok}")
        if i["reason"]:
            print(f"      → {i['reason']}")
        if i["enc"] != "utf-8" and i["lossless"]:
            need.append(p)
    print()
    print("=" * 78)
    if need:
        print("可安全转换为 UTF-8 的文件:")
        for p in need:
            print(f"  {p}")
        print()
        print("转换前请确认: 这不在 git 管理下, 且已确认往返一致。")
    else:
        print("无需转换。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())