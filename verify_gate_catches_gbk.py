"""verify_gate_catches_gbk.py — 定论: 门禁到底抓不抓 .gitignore 的 GBK。

背景矛盾:
  check_encoding.py 的逻辑是「非 CLEAN_LABELS 一律 dirty」, gbk 不在 CLEAN_LABELS 里,
  理论上必然被抓。但实测塞入 GBK 字节后仍报 clean=290 dirty=0。
  两种可能: (a) iter_files() 没把 .gitignore 算进去  (b) 变异没真正生效
  必须查实, 不能猜。

本脚本逐步打印每一步的实际状态, 最后无条件恢复原文件。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
GITIGNORE = ROOT / ".gitignore"


def run_gate() -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(TOOLS / "check_encoding.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""


def main() -> int:
    from audit_encoding import classify, iter_files

    orig = GITIGNORE.read_bytes()
    print("=" * 70)
    print("1. 初始状态")
    print("=" * 70)
    print(f"  .gitignore {len(orig)} bytes, classify -> {classify(orig)!r}")
    scanned = [f for f in iter_files() if f.name == ".gitignore"]
    print(f"  iter_files() 是否包含 .gitignore: {bool(scanned)}  {scanned}")
    code, last = run_gate()
    print(f"  门禁: exit={code}  {last}")

    try:
        print()
        print("=" * 70)
        print("2. 注入 GBK 字节(2 个: '单元')")
        print("=" * 70)
        mutated = orig + "单元".encode("gbk")
        GITIGNORE.write_bytes(mutated)
        check = GITIGNORE.read_bytes()
        print(f"  写入后 {len(check)} bytes (原 {len(orig)})")
        try:
            check.decode("utf-8", errors="strict")
            print("  ★ 变异体仍能严格 UTF-8 解码 -> 变异无效")
            return 2
        except UnicodeDecodeError as e:
            print(f"  严格解码失败 @{e.start} -> 变异有效")
        print(f"  classify(变异体) -> {classify(check)!r}")
        code, last = run_gate()
        print(f"  门禁: exit={code}  {last}")
        print(f"  判定: {'✅ 门禁抓到了' if code != 0 else '❌ 门禁漏报 —— 这是真缺陷'}")
    finally:
        GITIGNORE.write_bytes(orig)
        print()
        print("=" * 70)
        print("3. 已恢复")
        print("=" * 70)
        print(f"  .gitignore {len(GITIGNORE.read_bytes())} bytes, "
              f"classify -> {classify(GITIGNORE.read_bytes())!r}")
        code, last = run_gate()
        print(f"  门禁: exit={code}  {last}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())