"""
test_llm_metrics.py - LLM metrics callback 集成 (v1.1 M5)

4 cases:
  1. set_metrics_callback 后, complete() 调 callback (含 in/out tokens + latency)
  2. set_stage_context 隐式提供 stage/ch
  3. 显式 stage/ch 覆盖隐式 context
  4. callback 异常不破坏 LLM 调用
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from lib import llm


@pytest.fixture
def mock_llm():
    """构造 1 个 LLM, mock 掉 client.chat.completions.create (避免真打 llama-server)."""
    with patch.object(llm, "tiktoken", MagicMock()):
        l = llm.LLM(model="test-model")
    # 替换 client
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "test output"
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    l.client = MagicMock()
    l.client.chat.completions.create.return_value = mock_resp
    return l


# ── 1. callback 被调 ────────────────────────────────────────────────────

def test_complete_invokes_metrics_callback(mock_llm):
    """complete() 后 callback 收到 input_tokens / output_tokens / latency."""
    captured = []

    def cb(stage, ch, model, input_tokens, output_tokens, latency_ms):
        captured.append({
            "stage": stage, "ch": ch, "model": model,
            "in": input_tokens, "out": output_tokens, "ms": latency_ms,
        })

    mock_llm.set_metrics_callback(cb)
    text = mock_llm.complete(prompt="hi", system="you are a bot", stage="writing", ch=8)
    assert text == "test output"
    assert len(captured) == 1
    c = captured[0]
    assert c["stage"] == "writing"
    assert c["ch"] == 8
    assert c["model"] == "test-model"
    assert c["in"] == 100
    assert c["out"] == 50
    assert c["ms"] > 0


# ── 2. set_stage_context 隐式 stage/ch ─────────────────────────────────

def test_set_stage_context_implicit(mock_llm):
    """set_stage_context() 后, complete() 隐式用 stage/ch."""
    captured = []
    mock_llm.set_metrics_callback(
        lambda stage, ch, **kw: captured.append((stage, ch))
    )
    mock_llm.set_stage_context("extract", 5)
    mock_llm.complete(prompt="x")  # 不传 stage/ch
    assert captured == [("extract", 5)]


# ── 3. 显式参数覆盖隐式 ────────────────────────────────────────────────

def test_explicit_stage_overrides_context(mock_llm):
    """complete(stage="X", ch=99) 覆盖 set_stage_context() 的值."""
    captured = []
    mock_llm.set_metrics_callback(
        lambda stage, ch, **kw: captured.append((stage, ch))
    )
    mock_llm.set_stage_context("extract", 5)
    mock_llm.complete(prompt="x", stage="summary", ch=10)
    assert captured == [("summary", 10)]


# ── 4. callback 异常不破坏 ─────────────────────────────────────────────

def test_callback_exception_swallowed(mock_llm):
    """callback 抛异常不破坏 LLM 调用 (返回正常结果)."""
    def bad_cb(**kw):
        raise RuntimeError("callback error")
    mock_llm.set_metrics_callback(bad_cb)
    # 应正常返回, 不抛
    text = mock_llm.complete(prompt="x", stage="writing", ch=1)
    assert text == "test output"


# ── 5. call() 也走 callback ─────────────────────────────────────────────

def test_call_invokes_metrics_callback(mock_llm):
    """call() (low-level messages) 也走 metrics callback."""
    captured = []
    mock_llm.set_metrics_callback(
        lambda stage, ch, **kw: captured.append((stage, ch))
    )
    text = mock_llm.call(messages=[{"role": "user", "content": "hi"}],
                          stage="extract", ch=8)
    assert text == "test output"
    assert captured == [("extract", 8)]


# ── 6. fallback 估算 (无 usage) ─────────────────────────────────────────

def test_complete_uses_fallback_when_no_usage():
    """server 不返 usage 时, 用 chars*0.73 估算 input/output tokens."""
    # 直接构造 LLM, 强制 enc=None (走 fallback)
    l = llm.LLM(model="test-model")
    l.enc = None
    # 用 SimpleNamespace 避免 MagicMock auto-attr
    from types import SimpleNamespace
    mock_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="out text"))],
        # 注意: 不设 usage 属性
    )
    l.client = MagicMock()
    l.client.chat.completions.create.return_value = mock_resp

    captured = []
    l.set_metrics_callback(
        lambda stage, ch, **kw: captured.append(kw)
    )
    l.complete(prompt="hello world input", stage="writing", ch=1)
    assert len(captured) == 1
    # fallback: in_tok > 0, out_tok > 0
    assert captured[0]["input_tokens"] > 0
    assert captured[0]["output_tokens"] > 0
