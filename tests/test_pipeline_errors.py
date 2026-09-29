"""tests/test_pipeline_errors.py — PipelineError 的契约

初版这个文件有 26 个测试, 覆盖 TransientError / PermanentError / retry() /
best_effort() / classify_os_error() —— 全部已随投机泛化一起移除
(它们从无生产消费者, 理由见 lib/pipeline/errors.py)。

保留的是 PipelineError 本身: 它被 state.py 的 FSM 校验真实 raise 了 6 次,
所以它的签名与行为必须有测试锁住。

留下的教训: 26 个测试不代表 26 个有用的东西。测试锁住的是「被测代码存在」,
不是「被测代码有用」—— 上线前应该问一句谁在调用它。
"""
from __future__ import annotations

from lib.errors import ErrorCode, NovelError
from lib.pipeline.errors import PipelineError


class TestHierarchy:
    def test_inherits_novel_error(self):
        """必须继承 NovelError, 否则上层错误码/HTTP 映射会失效."""
        assert issubclass(PipelineError, NovelError)

    def test_is_exception(self):
        assert issubclass(PipelineError, Exception)


class TestSignature:
    def test_positional_code_message(self):
        """state.py 用的是 PipelineError(code, message) 位置参数, 不能变签名."""
        e = PipelineError(ErrorCode.INVALID_ARGS, "未知 stage")
        assert e.code == ErrorCode.INVALID_ARGS
        assert e.message == "未知 stage"

    def test_code_defaults_to_generic(self):
        assert PipelineError(message="x").code == ErrorCode.GENERIC

    def test_str_keeps_novel_error_format(self):
        e = PipelineError(ErrorCode.GENERIC, "非法 FSM 转换")
        assert "非法 FSM 转换" in str(e)
        assert "GENERIC" in str(e)

    def test_detail_is_optional(self):
        assert PipelineError(ErrorCode.GENERIC, "msg", detail="上下文").detail == "上下文"
        assert PipelineError(ErrorCode.GENERIC, "msg").detail == ""


class TestStructuredContext:
    def test_context_carries_fields(self):
        e = PipelineError(ErrorCode.GENERIC, "boom",
                          book="b1", chapter="ch_001", stage="write")
        assert e.context() == {"book": "b1", "chapter": "ch_001", "stage": "write"}

    def test_context_empty_when_unset(self):
        assert PipelineError(ErrorCode.GENERIC, "x").context() == {}

    def test_partial_context(self):
        e = PipelineError(ErrorCode.GENERIC, "x", book="b1")
        assert e.context() == {"book": "b1"}

    def test_cause_is_recorded(self):
        src = ValueError("底层炸了")
        e = PipelineError(ErrorCode.GENERIC, "包装", cause=src)
        assert e.__cause__ is src


class TestRemovedAPIStaysRemoved:
    """防止投机泛化悄悄长回来。

    这几个名字曾经存在, 从无生产消费者, 已移除。若有人重新加回,
    应当先回答: 谁在调用它?
    """

    def test_removed_names_not_exported(self):
        import lib.pipeline.errors as mod
        for name in ("TransientError", "PermanentError", "retry",
                     "best_effort", "classify_os_error"):
            assert not hasattr(mod, name), (
                f"{name} 被移除了, 却又出现。若确有生产调用方, 先证明它有用。"
            )

    def test_package_reexports_only_pipeline_error(self):
        import lib.pipeline as pkg
        for name in ("TransientError", "PermanentError", "retry",
                     "best_effort", "classify_os_error"):
            assert not hasattr(pkg, name), f"lib.pipeline 不该再导出 {name}"