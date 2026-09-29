"""tools/compare_db_vs_file_shape.py — 修 get_review 前, 先比对两侧结构。

db.get_review() 一直是坏的(NameError), 所以它的返回值从未被任何生产路径消费。
一修, 它会第一次真正生效 —— 那么「DB 行」和「文件 record」对调用方的
可替代性就成了问题。本脚本在动手前把差异列清楚。

看三件事:
  1. DB 行的列 vs 文件 record 的键 —— 哪些键对不上
  2. 现有代码有没有 chapter_id / ch_id 的兼容处理
  3. 哪些调用方直接按 key 取值(缺键会 KeyError)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "lib" / "db.py"


def table_columns(src: str) -> dict[str, list[str]]:
    """从 CREATE TABLE 语句里抠出列名。"""
    import re
    out = {}
    for m in re.finditer(
                r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)\s*\((.*?)\n\s*\)",
                src, re.S):
            cols = []
            depth = 0
            cur = ""
            for ch in m.group(2):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    cols.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            if cur.strip():
                cols.append(cur.strip())
            names = []
            for c in cols:
                c = c.strip()
                if not c or c.upper().startswith(("PRIMARY", "FOREIGN", "UNIQUE")):
                    continue
                names.append(c.split()[0])
            out[m.group(1)] = names
    return out


def empty_record_keys() -> set[str]:
    import re
    src = (REPO / "lib" / "review_service.py").read_text(encoding="utf-8")
    m = re.search(r"def _empty_record\(.*?\n(?=\ndef |\nclass )", src, re.S)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)":', m.group(0)))


def main() -> int:
    src = DB.read_text(encoding="utf-8")
    cols = table_columns(src)
    print("=" * 76)
    print("1. reviews 表列 vs _empty_record 的键")
    print("=" * 76)
    review_cols = set(cols.get("reviews", []))
    empty = empty_record_keys()
    print(f"  DB reviews 列 ({len(review_cols)}): {sorted(review_cols)}")
    print()
    print(f"  _empty_record 键 ({len(empty)}): {sorted(empty)}")
    print()
    only_db = sorted(review_cols - empty)
    only_file = sorted(empty - review_cols)
    print(f"  仅 DB 有  ({len(only_db)}): {only_db}")
    print(f"  仅文件有  ({len(only_file)}): {only_file}")
    print()

    print("=" * 76)
    print("2. chapter_id / ch_id 兼容处理在哪些地方")
    print("=" * 76)
    for rel in ("lib/review_service.py", "lib/db.py",
                "review_ui/bp/review.py", "review_ui/app.py"):
        p = REPO / rel
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "ch_id" in line and ("chapter_id" in line or 'ch_id" in' in line
                                   or "ch_id'" in line):
                print(f"  {rel}:{i:<5} {line.strip()[:66]}")
    print()

    print("=" * 76)
    print("3. 按 key 直取(record[...] / record.get)的地方")
    print("=" * 76)
    for rel in ("lib/review_service.py", "review_ui/bp/review.py",
                "review_ui/app.py"):
        p = REPO / rel
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Subscript):
                    try:
                        t = ast.unparse(n)
                    except Exception:
                        continue
                    if t.startswith(("record[", "rec[", "r[")):
                        inner = t.split("[")[1].rstrip("]")
                        is_str = inner.startswith(("'", '"'))
                        mark = "  <-- 缺键会 KeyError" if is_str else ""
                        print(f"  {rel}::{fn.name}()  L{n.lineno}  {t}{mark}")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())