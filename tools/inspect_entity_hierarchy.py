"""tools/inspect_entity_hierarchy.py — Entity 基类是否被真正复用。

现象: entity.py 里 class Entity 只有 2 个方法, 而 Character/Event/
Foreshadow/WorldRule 各有 3-4 个。若 Entity 真是基类, 共用的
updated_at/touch 这类样板应该在那里, 而不是每个子类各写一遍。

本脚本看:
  1. 各类的继承关系
  2. 每个方法名出现在几个类里(共有的 vs 独有的)
  3. Entity 上到底放了什么, 子类有没有绕过它
"""
from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REL = "lib/entity.py"


def main() -> int:
    src = (REPO / REL).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]

    print("=" * 74)
    print("继承关系")
    print("=" * 74)
    for c in classes:
        bases = [ast.unparse(b) for b in c.bases]
        methods = [m.name for m in c.body if isinstance(m, ast.FunctionDef)]
        print(f"  L{c.lineno:<5} class {c.name:<18} bases={bases or '(无)'}  "
              f"{len(methods)} 方法")
        for m in c.body:
            if isinstance(m, ast.FunctionDef):
                first = ast.get_source_segment(src, m).splitlines()
                body = " / ".join(x.strip() for x in first[1:3])
                print(f"          L{m.lineno:<5} {m.name}()   {body[:58]}")

    print()
    print("=" * 74)
    print("方法名分布(出现在几个类里)")
    print("=" * 74)
    where = defaultdict(list)
    for c in classes:
        for m in c.body:
            if isinstance(m, ast.FunctionDef):
                where[m.name].append(c.name)
    for name, owners in sorted(where.items(), key=lambda x: -len(x[1])):
        mark = "  ← 共用" if len(owners) > 1 else ""
        print(f"  {name:<24} {len(owners)} 处  {owners}{mark}")

    print()
    print("=" * 74)
    print("Entity 基类上有什么, 子类是否绕过了它")
    print("=" * 74)
    ent = next((c for c in classes if c.name == "Entity"), None)
    if ent is None:
        print("  没有 Entity 类")
        return 0
    ent_methods = {m.name for m in ent.body if isinstance(m, ast.FunctionDef)}
    print(f"  Entity 方法: {sorted(ent_methods) or '(无)'}")
    for c in classes:
        if c is ent:
            continue
        bases = [ast.unparse(b) for b in c.bases]
        inherits_entity = any("Entity" in b for b in bases)
        own = {m.name for m in c.body if isinstance(m, ast.FunctionDef)}
        dup_with_entity = own & ent_methods
        print(f"  {c.name:<16} 继承Entity={str(inherits_entity):<6} "
              f"自有方法={sorted(own)}")
        if dup_with_entity:
            # 同名不等于同语义 —— 必须逐个看实现再下结论。
            # 已核查: entity.py 的 Entity 的 bases 为空, 它不是基类而是独立容器;
            # Character.to_dict() 是 asdict(self), Entity.to_dict() 是
            # {"type","id","data"} 信封 —— 同名, 语义相反, 合并会弄坏结构。
            print(f"       ⚠ 同名方法: {sorted(dup_with_entity)} "
                  f"—— 需逐个比对实现, 同名≠同语义")
    print("=" * 74)
    print("结论: entity.py 不动")
    print("=" * 74)
    print("""
  touch()   三处, 各只有一行 self.updated_at = _now_iso()
           -> 为一行代码抽 mixin 是负收益, 间接层比重复更贵

  to_dict  Character/Event/Foreshadow/WorldRule 都是 asdict(self)
           Entity 是 {"type","id","data"} 信封
           -> 同名不同语义, 合并会破坏 Entity 的信封结构

  Entity 的 bases 为空 —— 它不是基类, 而是独立包装容器; 子类也不继承它。
  「基类没被真正复用」是我从方法数量误读出来的怀疑, 不成立。

  这条是负面结论, 写进来是为了下次不再顺着同名去合并。
""".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())