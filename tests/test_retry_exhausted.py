"""
test_retry_exhausted.py — mock OpenAI client 让 LLM 连续失败, 验证重试耗尽时抛 RuntimeError

不依赖真实网络/LLM, 用 unittest.mock 模拟 openai.RateLimitError.
"""
import time
import pytest
from unittest.mock import MagicMock, patch
from openai import RateLimitError


def _make_llm(max_retries: int = 2, retry_delay: float = 0.0):
    """快速构造 LLM 实例, 短 delay 跑测试快."""
    from lib.llm import LLM
    return LLM(model="test-model", api_base="http://localhost:9999/v1",
               api_key="dummy", max_retries=max_retries, retry_delay=retry_delay)


def _fake_rate_limit_error():
    """构造一个 RateLimitError 实例 (OpenAI SDK 的错误需要 mock response)."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    return RateLimitError(
        message="Rate limit exceeded",
        response=mock_response,
        body=None,
    )


def test_retry_exhausted_raises_runtime_error():
    """连续 N+1 次 RateLimitError → RuntimeError (而不是把 RateLimitError 透传)."""
    llm = _make_llm(max_retries=2, retry_delay=0.0)

    # mock client.chat.completions.create 永远抛 RateLimitError
    with patch.object(llm.client.chat.completions, "create",
                      side_effect=_fake_rate_limit_error()):
        with pytest.raises(RuntimeError, match="LLM API error"):
            llm.call([{"role": "user", "content": "hi"}], max_tokens=10)


def test_retry_then_success():
    """失败 1 次后第 2 次成功, 应该返回内容."""
    llm = _make_llm(max_retries=3, retry_delay=0.0)

    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = "hello world"

    side_effects = [
        _fake_rate_limit_error(),  # 第 1 次失败
        fake_response,              # 第 2 次成功
    ]
    with patch.object(llm.client.chat.completions, "create",
                      side_effect=side_effects):
        result = llm.call([{"role": "user", "content": "hi"}], max_tokens=10)
        assert result == "hello world"


def test_retry_count_respects_max_retries(monkeypatch):
    """调用次数 = max_retries + 1 (initial + retries)."""
    llm = _make_llm(max_retries=3, retry_delay=0.0)
    call_counter = {"n": 0}

    def counting_create(*args, **kwargs):
        call_counter["n"] += 1
        raise _fake_rate_limit_error()

    with patch.object(llm.client.chat.completions, "create", side_effect=counting_create):
        with pytest.raises(RuntimeError):
            llm.call([{"role": "user", "content": "hi"}], max_tokens=10)

    # max_retries=3, 初始 1 + retry 3 = 4 次
    assert call_counter["n"] == 4, f"expected 4 calls (1+3 retries), got {call_counter['n']}"


def test_zero_retries_single_attempt():
    """max_retries=0 只尝试 1 次, 立刻抛 RuntimeError."""
    llm = _make_llm(max_retries=0, retry_delay=0.0)
    call_counter = {"n": 0}

    def counting_create(*args, **kwargs):
        call_counter["n"] += 1
        raise _fake_rate_limit_error()

    with patch.object(llm.client.chat.completions, "create", side_effect=counting_create):
        with pytest.raises(RuntimeError):
            llm.call([{"role": "user", "content": "hi"}], max_tokens=10)

    assert call_counter["n"] == 1