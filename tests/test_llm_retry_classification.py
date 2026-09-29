"""tests/test_llm_retry_classification.py — LLM 重试该对哪些错误生效。

原实现 (lib/llm.py):
    except (RateLimitError, APIError) as e:
        if attempt < self.max_retries:
            time.sleep(self.retry_delay); continue

问题: APIError 是所有 API 异常的基类, AuthenticationError(401) /
BadRequestError(400) / NotFoundError(404) 全在下面, 于是这些确定性错误
也走满 max_retries 次重试。按默认 retry_delay=10s 算, 一个填错的
API key 要白等 30 秒才报错。

同时固定间隔在限流时会持续加压, 缺指数退避。

分类依据是 tools/probe_openai_errors.py 对当前 SDK 的实测继承关系, 不是记忆。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    InternalServerError,
)

from lib.llm import LLM, _is_retryable


def _llm(max_retries: int = 3, retry_delay: float = 0.0) -> LLM:
    return LLM(model="m", api_base="http://localhost:9999/v1", api_key="k",
               max_retries=max_retries, retry_delay=retry_delay)


def _status_error(cls, status: int, msg: str = "boom"):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    return cls(message=msg, response=resp, body=None)


def _conn_error(cls):
    return cls(request=MagicMock())


class TestClassification:
    """直接测分类函数 —— 这是重试策略的地基。"""

    @pytest.mark.parametrize("exc", [
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 503),
        _conn_error(APIConnectionError),
        _conn_error(APITimeoutError),
    ])
    def test_transient_is_retryable(self, exc):
        assert _is_retryable(exc) is True, f"{type(exc).__name__} 应可重试"

    @pytest.mark.parametrize("exc", [
        _status_error(AuthenticationError, 401),
        _status_error(BadRequestError, 400),
        _status_error(NotFoundError, 404),
    ])
    def test_deterministic_is_not_retryable(self, exc):
        assert _is_retryable(exc) is False, f"{type(exc).__name__} 不该重试"

    def test_non_openai_exception_is_not_retryable(self):
        """编程错误不该被当成网络抖动反复重试。"""
        assert _is_retryable(ValueError("参数写错了")) is False
        assert _is_retryable(TypeError()) is False


class TestFastFailure:
    """确定性错误必须一次就失败 —— 这是本次改动的核心收益。"""

    @pytest.mark.parametrize("cls,status", [
        (AuthenticationError, 401),
        (BadRequestError, 400),
        (NotFoundError, 404),
    ])
    def test_deterministic_fails_without_retrying(self, cls, status):
        llm = _llm(max_retries=3, retry_delay=0.0)
        calls = {"n": 0}

        def counting_create(*a, **k):
            calls["n"] += 1
            raise _status_error(cls, status)

        with patch.object(llm.client.chat.completions, "create",
                          side_effect=counting_create):
            with pytest.raises(RuntimeError, match="不可重试"):
                llm.call([{"role": "user", "content": "hi"}], max_tokens=10)

        assert calls["n"] == 1, (
            f"HTTP {status} 是确定性错误, 应只调 1 次, 实际调了 {calls['n']} 次"
        )

    def test_auth_error_reports_status(self):
        """错误消息要能一眼看出是 401, 而不是笼统的『重试耗尽』."""
        llm = _llm(max_retries=0, retry_delay=0.0)
        with patch.object(llm.client.chat.completions, "create",
                          side_effect=_status_error(AuthenticationError, 401)):
            with pytest.raises(RuntimeError) as ei:
                llm.call([{"role": "user", "content": "hi"}], max_tokens=10)
        msg = str(ei.value)
        assert "401" in msg, f"消息应含状态码, 实际: {msg}"
        assert "AuthenticationError" in msg, f"消息应含异常类型, 实际: {msg}"

    def test_real_delay_would_not_be_slept(self, monkeypatch):
        """把 retry_delay 调大, 确认快速失败路径根本不 sleep.

        修好前: 401 会 sleep(10) x 3 = 30 秒。
        修好后: 一次都不睡。
        """
        slept = []
        monkeypatch.setattr("lib.llm.time.sleep", lambda d: slept.append(d))

        llm = _llm(max_retries=3, retry_delay=10.0)
        with patch.object(llm.client.chat.completions, "create",
                          side_effect=_status_error(AuthenticationError, 401)):
            with pytest.raises(RuntimeError):
                llm.call([{"role": "user", "content": "hi"}], max_tokens=10)

        assert slept == [], f"401 不该触发任何等待, 实际 sleep 了 {slept}"


class TestTransientStillRetries:
    """可重试错误的行为不能被我改坏。"""

    def test_rate_limit_retries_then_succeeds(self):
        llm = _llm(max_retries=3, retry_delay=0.0)
        ok = MagicMock()
        ok.choices = [MagicMock()]
        ok.choices[0].message.content = "hello"
        with patch.object(llm.client.chat.completions, "create",
                          side_effect=[_status_error(RateLimitError, 429), ok]):
            assert llm.call([{"role": "user", "content": "hi"}],
                            max_tokens=10) == "hello"

    def test_connection_error_retries(self):
        llm = _llm(max_retries=2, retry_delay=0.0)
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _conn_error(APIConnectionError)
            ok = MagicMock()
            ok.choices = [MagicMock()]
            ok.choices[0].message.content = "recovered"
            return ok

        with patch.object(llm.client.chat.completions, "create", side_effect=flaky):
            assert llm.call([{"role": "user", "content": "hi"}],
                            max_tokens=10) == "recovered"
        assert calls["n"] == 3


class TestBackoff:
    def test_delay_grows_exponentially(self, monkeypatch):
        slept = []
        monkeypatch.setattr("lib.llm.time.sleep", lambda d: slept.append(d))
        llm = _llm(max_retries=3, retry_delay=1.0)
        with patch.object(llm.client.chat.completions, "create",
                          side_effect=_status_error(RateLimitError, 429)):
            with pytest.raises(RuntimeError):
                llm.call([{"role": "user", "content": "hi"}], max_tokens=10)
        assert slept == [1.0, 2.0, 4.0], f"退避间隔应翻倍, 实际 {slept}"

    def test_backoff_is_capped(self, monkeypatch):
        """不能无限增长 —— 60s 封顶, 否则第 5 次重试睡十几分钟."""
        slept = []
        monkeypatch.setattr("lib.llm.time.sleep", lambda d: slept.append(d))
        llm = _llm(max_retries=8, retry_delay=10.0)
        with patch.object(llm.client.chat.completions, "create",
                          side_effect=_status_error(RateLimitError, 429)):
            with pytest.raises(RuntimeError):
                llm.call([{"role": "user", "content": "hi"}], max_tokens=10)
        assert max(slept) <= 60.0, f"退避应封顶 60s, 实际最大 {max(slept)}"