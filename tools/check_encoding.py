"""tools/check_encoding.py — strict gate; non-zero exit on any non-UTF-8-no-BOM.

Intended use: a CI step that runs after every push, or a pre-commit hook
that gates dirty commits. The audit (audit_encoding.py) is the same scan
but exits 0 always; this one exits 1 on failure so CI can fail.

Usage:
  python tools/check_encoding.py            # exits 1 if dirty
  python tools/check_encoding.py --verbose  # also print clean list"""
from __future__ import annotations

import sys

# Reuse the classifier so the two scripts never disagree.
from audit_encoding import classify, iter_files

REPO_NAME = "novel_workflow"


def main() -> int:
    files = iter_files()
    clean: list[str] = []
    dirty: list[tuple[str, str]] = []

    # 集合而非 startswith —— "utf-8 (BOM)" 不该被当成干净.
    # 反向测试发现过这个假阳性 (幂中毒文件守卫返 clean=0 ).
    CLEAN_LABELS = {"utf-8 (no BOM)", "utf-8 (ASCII-only)"}

    for f in files:
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        label = classify(raw)
        if label in CLEAN_LABELS:
            clean.append(str(f))
        else:
            dirty.append((str(f), label))

    print(f"[{REPO_NAME}] scanned {len(files)}, "
          f"clean={len(clean)}, dirty={len(dirty)}")
    if dirty:
        print(f"FAIL: {len(dirty)} file(s) not UTF-8 (no BOM):")
        for path, label in dirty:
            print(f"  [{label:14s}] {path}")
        print()
        print("Fix with:  python tools/normalize_encoding.py --apply")
        return 1
    if "--verbose" in sys.argv:
        print(f"All {len(clean)} files are UTF-8 (no BOM).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())