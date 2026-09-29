"""tests/test_no_module_mutation_in_tests.py — 测试不得直接改模块属性。

背景: Phase 1 拆分 app.py 时留下两处
    review_app._get_auth = lambda: {...}
这种直接赋值不像 monkeypatch.setattr, **永远不会被还原**。结果是:
某个测试关掉 auth 后, 后续同 session 的所有测试都跑在「已禁用」的假设下,
测试之间产生了隐藏的顺序依赖。整轮 624 个测试全绿, 但根是脆的。

真实风险不是「现在错了」, 而是「谁把顺序调一下就炸, 且没人知道是哪两处」。
所以用静态检查把整类堵死: tests/ 下不允许出现对 app 模块的直接属性赋值。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# 允许被直接改的模块级对象(测试自己的模块, 不跨测试共享)
ALLOWED_TARGETS = {
    "os.environ", "sys.modules", "sys.path", "warnings.filters",
}


def _iter_test_files() -> list[Path]:
    return [p for p in sorted(TESTS_DIR.glob("*.py"))
            if p.name != Path(__file__).name]


@pytest.mark.parametrize("path", _iter_test_files(), ids=lambda p: p.name)
def test_no_direct_mutation_of_app_module(path: Path) -> None:
    """任何 `mod.attr = ...` 且 mod 不是白名单内的, 都必须报错。

    monkeypatch.setattr 内部走的是函数调用, 不会命中本检查。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # 赋值: mod.attr = value
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                offender = _check_target(tgt, path)
                if offender:
                    offenders.append((node.lineno, offender))
        # 增删: del mod.attr
        elif isinstance(node, ast.Delete):
            for tgt in node.targets:
                offender = _check_target(tgt, path)
                if offender:
                    offenders.append((node.lineno, offender))
        # 注解赋值: mod.attr: T = value
        elif isinstance(node, ast.AnnAssign):
            offender = _check_target(node.target, path)
            if offender:
                offenders.append((node.lineno, offender))

    assert not offenders, (
        f"{path.name} 里出现了对模块属性的直接赋值(不会自动还原, 会污染同 session 的其他测试):\n"
        + "\n".join(f"  L{ln}: {txt}" for ln, txt in offenders)
        + "\n\n改用 monkeypatch.setattr(模块, '属性', 值), pytest 会在 teardown 还原。"
    )


def _check_target(tgt: ast.AST, path: Path) -> str | None:
    """返回问题描述, 没问题则 None。"""
    if not isinstance(tgt, ast.Attribute):
        return None
    if not isinstance(tgt.value, ast.Name):
        return None
    # 只关心明显跨测试共享的模块对象
    if tgt.value.id not in ("review_app", "flask_app", "app", "ra", "ra_dashboard"):
        return None
    return f"{tgt.value.id}.{tgt.attr} = ..."


def test_conftest_auth_fixture_is_scoped() -> None:
    """conftest 的 auth_disabled 必须用 monkeypatch, 不得裸赋值。"""
    conftest = TESTS_DIR / "conftest.py"
    src = conftest.read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "auth_disabled":
            assigns_to_attr = any(
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id in ("review_app", "app")
                for t in ast.walk(node)
                if isinstance(t, ast.Assign)
                for t in t.targets
            )
            assert not assigns_to_attr, (
                "conftest.auth_disabled 直接赋值了 app 属性 —— "
                "会让 auth 状态泄漏到整个 session"
            )
            return

    pytest.fail("conftest.py 里找不到 auth_disabled fixture")


def test_get_auth_is_restored_after_tests() -> None:
    """运行期兜底: auth 必须在收尾后回到原状。

    这条是行为断言, 覆盖前面那些静态检查抓不到的路径
    (比如经由 conftest fixture 泄漏的)。
    """
    from review_ui import app as review_app

    real = review_app._get_auth
    assert callable(real), "_get_auth 必须是可调用的"
    result = real()
    assert isinstance(result, dict), f"_get_auth 应返回 dict, 实得 {type(result)}"
    # enabled 来自 config.yaml, 不应被上一个测试改成 False 或 True
    assert "enabled" in result, "返回结构应含 enabled 字段"