"""tests/test_pipeline_errors.py — 异常体系契约

Phase 3 新增 lib/pipeline/errors.py。这些测试锁住三件事:
  1. 向后兼容 —— state.py 那 6 处 raise PipelineError(code, message) 仍成立
  2. 语义正确 —— Transient 该重试,Permanent 不该重试
  3. 失败可观测 —— best_effort 记日志而不是静默吞掉
"""
from __future__ import annotations

import logging
import time

import pytest

from lib.errors import ErrorCode, NovelError
from lib.pipeline.errors import (
    PermanentError,
    PipelineError,
    TransientError,
    best_effort,
    classify_os_error,
    retry,
)


# ── 1. 继承与向后兼容 ─────────────────────────────────────────────────────
class TestHierarchy:
    def test_inherits_novel_error(self):
        """必须继承 NovelError,否则上层错误码/HTTP 映射会失效."""
        assert issubclass(PipelineError, NovelError)

    def test_subclasses(self):
        assert issubclass(TransientError, PipelineError)
        assert issubclass(PermanentError, PipelineError)

    def test_positional_code_message(self):
        """state.py 用的是 PipelineError(code, message) 位置参数,不能变签名."""
        e = PipelineError(ErrorCode.INVALID_ARGS, "未知 stage")
        assert e.code == ErrorCode.INVALID_ARGS
        assert e.message == "未知 stage"

    def test_code_defaults_to_generic(self):
        e = PipelineError(message="x")
        assert e.code == ErrorCode.GENERIC

    def test_str_keeps_novel_error_format(self):
        e = PipelineError(ErrorCode.GENERIC, "非法 FSM 转换")
        assert "非法 FSM 转换" in str(e)
        assert "GENERIC" in str(e)

    def test_context_carries_structured_fields(self):
        e = PipelineError(ErrorCode.GENERIC, "boom",
                          book="b1", chapter="ch_001", stage="write")
        ctx = e.context()
        assert ctx == {"book": "b1", "chapter": "ch_001", "stage": "write"}

    def test_context_empty_when_unset(self):
        assert PipelineError(ErrorCode.GENERIC, "x").context() == {}


# ── 2. OSError 归类 ───────────────────────────────────────────────────────
class TestClassify:
    @pytest.mark.parametrize("exc", [
        ConnectionResetError("peer gone"),
        TimeoutError("slow"),
        FileNotFoundError("not yet written"),
        BlockingIOError("busy"),
    ])
    def test_transient(self, exc):
        assert classify_os_error(exc) is TransientError

    @pytest.mark.parametrize("exc", [
        PermissionError("denied"),
        ValueError("bad"),
        KeyError("missing"),
        IsADirectoryError("wrong kind"),
    ])
    def test_permanent(self, exc):
        assert classify_os_error(exc) is PermanentError


# ── 3. retry ─────────────────────────────────────────────────────────────
class TestRetry:
    def test_succeeds_first_try(self):
        calls = []

        @retry(times=2, base_delay=0)
        def f():
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1

    def test_retries_transient_then_succeeds(self):
        calls = []

        @retry(times=2, base_delay=0)
        def f():
            calls.append(1)
            if len(calls) < 3:
                raise TransientError(ErrorCode.GENERIC, "网络抖动")
            return "ok"

        assert f() == "ok"
        assert len(calls) == 3

    def test_raises_after_exhausting(self):
        calls = []

        @retry(times=2, base_delay=0)
        def f():
            calls.append(1)
            raise TransientError(ErrorCode.GENERIC, "一直失败")

        with pytest.raises(TransientError):
            f()
        # times=2 -> 共尝试 3 次(初次 + 2 次重试)
        assert len(calls) == 3

    def test_permanent_is_never_retried(self):
        """重试一个校验失败只是浪费时间 —— 必须一次就抛."""
        calls = []

        @retry(times=3, base_delay=0)
        def f():
            calls.append(1)
            raise PermanentError(ErrorCode.INVALID_ARGS, "参数非法")

        with pytest.raises(PermanentError):
            f()
        assert len(calls) == 1, "PermanentError 不应重试"

    def test_unrelated_exception_passes_through(self):
        calls = []

        @retry(times=3, base_delay=0)
        def f():
            calls.append(1)
            raise TypeError("编程错误")

        with pytest.raises(TypeError):
            f()
        assert len(calls) == 1

    def test_backoff_grows(self):
        delays = []
        real_sleep = time.sleep

        def fake_sleep(d):
            delays.append(d)
            real_sleep(0)

        time.sleep = fake_sleep
        try:
            @retry(times=2, base_delay=0.01, factor=2.0)
            def f():
                raise TransientError(ErrorCode.GENERIC, "x")

            with pytest.raises(TransientError):
                f()
        finally:
            time.sleep = real_sleep

        assert len(delays) == 2
        assert delays[1] > delays[0], "退避间隔必须递增"
        assert delays[0] == pytest.approx(0.01)
        assert delays[1] == pytest.approx(0.02)


# ── 4. best_effort ───────────────────────────────────────────────────────
class TestBestEffort:
    def test_returns_value_on_success(self):
        @best_effort("加一")
        def f(x):
            return x + 1

        assert f(1) == 2

    def test_swallows_and_returns_none(self):
        @best_effort("故意炸")
        def f():
            raise ValueError("boom")

        assert f() is None, "best-effort 失败应返回 None 而不是抛出"

    def test_logs_with_context(self, caplog):
        @best_effort("写检查点", log_level=logging.WARNING)
        def f():
            raise RuntimeError("磁盘炸了")

        with caplog.at_level(logging.WARNING):
            f()

        recs = [r for r in caplog.records if "写检查点" in r.getMessage()]
        assert recs, "失败必须留日志,不能静默"
        assert "磁盘炸了" in recs[0].getMessage()

    def test_reraise_lets_through(self):
        """TypeError 这类不该被吞 —— reraise 指定后必须穿透."""

        @best_effort("开搞", reraise=TypeError)
        def f():
            raise TypeError("签名错了")

        with pytest.raises(TypeError):
            f()

    def test_default_reraise_empty_swallows_everything(self):
        """默认不 reraise —— 这正是 best_effort 的语义,由调用方显式声明。"""

        @best_effort("兜底")
        def f():
            raise TypeError("签名错了")

        assert f() is None
