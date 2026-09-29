"""
LLM wrapper: OpenAI-compat API (local llama-server / DeepSeek / MiniMax / OpenAI).
Handles streaming, retries, token counting, context window management.

v1.3: Multi-provider support via llm_providers.py
"""
from __future__ import annotations

import time, json, tiktoken
import logging
from typing import Any, Callable, Generator, Optional, TYPE_CHECKING
from openai import OpenAI, RateLimitError, APIError
try:
    from openai import APIConnectionError, APITimeoutError
except ImportError:      # 老版本 SDK 没有细分类
    APIConnectionError = APITimeoutError = None

from .llm_providers import resolve_model, resolve_for_book, get_provider_config, BUILTIN_PROVIDERS

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "http://127.0.0.1:60443/v1"
DEFAULT_MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"

# ── 重试分类 ──────────────────────────────────────────────────────────────
# 依据 tools/probe_openai_errors.py 对当前 SDK 实测的继承关系, 不是凭记忆:
#   APIError                  所有 API 异常的基类 —— catch 它等于什么都重试
#   APIConnectionError        连接层, 无状态码          -> 可重试
#   APITimeoutError           <- APIConnectionError      -> 可重试
#   APIStatusError            有 status_code
#     RateLimitError  429 / ConflictError 409            -> 可重试
#     BadRequestError 400 / AuthenticationError 401
#     PermissionDeniedError 403 / NotFoundError 404
#     UnprocessableEntityError 422                       -> 不可重试
#
# 原实现 catch (RateLimitError, APIError), 而 APIError 是上面所有确定性错误
# 的基类, 于是填错 API key 也会重试满 max_retries 次 —— 按默认
# retry_delay=10s 算, 一个 401 要白等 30 秒才报错。
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    """该失败是否值得重试。只有瞬时错误才值得。"""
    if APIConnectionError is not None and isinstance(
            exc, (APIConnectionError, APITimeoutError)):
        return True          # 连不上 / 超时, 必然是瞬时的
    status = getattr(exc, "status_code", None)
    if status is None:
        # 非 openai 异常(如 _stream_completion 抛的 RuntimeError): 保守不重试,
        # 否则一个编程错误会被当成网络抖动反复重试。
        return False
    return status in _RETRYABLE_STATUS



def _err_desc(exc: BaseException) -> str:
    """给错误一句人能读懂的原因, 写进 RuntimeError 消息里。"""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return f"HTTP {status} {name}" if status else name


def _backoff(base: float, attempt: int, cap: float = 60.0) -> None:
    """指数退避, 并设上限。

    固定间隔在上游限流时会持续加压, 指数退避给它喘息时间; 上限避免
    第 5 次重试睡十几分钟。
    """
    delay = min(base * (2 ** attempt), cap)
    if delay > 0:
        time.sleep(delay)


class LLM:
    def __init__(
        self,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 10.0,
    ):
        """
        Create an LLM client.

        Resolution order (model/api_base/api_key):
        1. Explicit kwargs (highest)
        2. provider config from llm_providers (if provider given)
        3. Global config.yaml llm.* (legacy)
        4. Hardcoded defaults (back-compat)
        """
        # Provider-based resolution
        if provider:
            cfg = resolve_model(provider, model)
            self.provider = provider
            self.model = cfg["model"]
            api_base = api_base or cfg["api_base"]
            api_key = api_key or cfg["api_key"]
        else:
            # Legacy / direct args
            self.provider = "local"  # default
            if model is None and api_base is None:
                # Use global config
                from .config_loader import get_config
                cfg = get_config().get("llm", {})
                model = cfg.get("default_model", DEFAULT_MODEL)
                api_base = cfg.get("api_base", DEFAULT_API_BASE)
            self.model = model or DEFAULT_MODEL
            api_base = api_base or DEFAULT_API_BASE
            api_key = api_key or "no-key-needed"
        
        self.api_base = api_base
        self.api_key = api_key
        self.client = OpenAI(base_url=api_base, api_key=api_key, timeout=600)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        #cl100k_base is used for GPT-4 context; use the same for rough token estimation
        try:
            self.enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.enc = None
        # v1.1: 指标回调 (为 None 则不调用). 调用方设:
        #   cb(stage, ch, model, input_tokens, output_tokens, latency_ms)
        self._metrics_cb = None
        # v1.1: 上下文 stage/ch (供模块侧调 complete() 时隐式提供)
        self._current_stage = "unknown"
        self._current_ch = 0

    def describe(self) -> dict:
        """Return a human-readable summary of this LLM instance."""
        return {
            "provider": self.provider,
            "model": self.model,
            "api_base": self.api_base,
            "has_api_key": bool(self.api_key) and self.api_key != "no-key-needed",
        }

    def set_metrics_callback(self, cb) -> None:
        """设置指标回调. cb 签名: cb(stage, ch, model, in_tok, out_tok, latency_ms)."""
        self._metrics_cb = cb

    def set_stage_context(self, stage: str, ch: int = 0) -> None:
        """设置当前 stage/ch. 后续 complete() / call() 会带上这些字段调用回调."""
        self._current_stage = stage
        self._current_ch = ch

    def _emit_metrics(self, stage: str, ch: int, in_tok: int, out_tok: int, latency_ms: float) -> None:
        """调用 _metrics_cb (如有). 异常不传出去."""
        if self._metrics_cb is None:
            return
        try:
            self._metrics_cb(
                stage=stage, ch=ch, model=self.model,
                input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency_ms,
            )
        except Exception:
            pass  # 回调异常不传, 不破坏 LLM 调用

    def _count_tokens(self, text: str) -> int:
        if self.enc:
            return len(self.enc.encode(text))
        # Fallback: ~0.73 chars per token for Chinese-heavy text
        return int(len(text) * 0.73)

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stop: Optional[list[str]] = None,
        stream: bool = False,
        stage: Optional[str] = None,
        ch: Optional[int] = None,
    ) -> str:
        """
        Send a chat-completion request. Returns full text.
        Raises on repeated failure.

        v1.1: stage/ch 可隐式通过 set_stage_context() 上下文设, 显式参数覆盖隐式.
        """
        eff_stage = stage if stage is not None else self._current_stage
        eff_ch = ch if ch is not None else self._current_ch

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.time()
        for attempt in range(self.max_retries + 1):
            try:
                if stream:
                    text = self._stream_completion(messages, max_tokens, stop)
                    resp = None
                else:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stop=stop or None,
                    )
                    text = resp.choices[0].message.content or ""
                latency_ms = (time.time() - t0) * 1000
                usage = getattr(resp, "usage", None) if not stream else None
                if usage is not None:
                    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
                else:
                    in_tok = self._count_tokens(prompt + system)
                    out_tok = self._count_tokens(text)
                self._emit_metrics(eff_stage, eff_ch, in_tok, out_tok, latency_ms)
                return text
            except Exception as e:
                if not _is_retryable(e):
                    # 确定性失败(400/401/403/404 等): 重试没有意义,
                    # 否则只是把参数错误重试到超时才报错。
                    raise RuntimeError(
                        f"LLM API error (不可重试, {_err_desc(e)}): {e}") from e
                if attempt < self.max_retries:
                    _backoff(self.retry_delay, attempt)
                    log.warning("LLM 调用失败, %.1fs 后重试 (%d/%d): %s: %s",
                                self.retry_delay * (2 ** attempt), attempt + 1,
                                self.max_retries, type(e).__name__, e)
                    continue
                raise RuntimeError(f"LLM API error after {self.max_retries} retries: {e}") from e

    def _stream_completion(
        self,
        messages: list[dict],
        max_tokens: int,
        stop: Optional[list[str]],
    ) -> str:
        full = []
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                stop=stop or None,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full.append(delta)
        except Exception as e:
            raise RuntimeError(f"LLM stream error: {e}") from e
        return "".join(full)

    def call(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096,
             stage: Optional[str] = None, ch: Optional[int] = None) -> str:
        """Low-level messages-based call."""
        eff_stage = stage if stage is not None else self._current_stage
        eff_ch = ch if ch is not None else self._current_ch
        t0 = time.time()
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = resp.choices[0].message.content or ""
                latency_ms = (time.time() - t0) * 1000
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
                else:
                    all_input = " ".join(m.get("content", "") for m in messages)
                    in_tok = self._count_tokens(all_input)
                    out_tok = self._count_tokens(text)
                self._emit_metrics(eff_stage, eff_ch, in_tok, out_tok, latency_ms)
                return text
            except Exception as e:
                if not _is_retryable(e):
                    raise RuntimeError(
                        f"LLM API error (不可重试, {_err_desc(e)}): {e}") from e
                if attempt < self.max_retries:
                    _backoff(self.retry_delay, attempt)
                    log.warning("LLM 调用失败, %.1fs 后重试 (%d/%d): %s: %s",
                                self.retry_delay * (2 ** attempt), attempt + 1,
                                self.max_retries, type(e).__name__, e)
                    continue
                raise RuntimeError(f"LLM API error: {e}") from e

    def estimate_input_tokens(self, text: str) -> int:
        return self._count_tokens(text)

    def estimate_cost(self, input_text: str, output_text: str) -> dict:
        """Rough cost estimate. For local models this is always $0."""
        in_tok  = self.estimate_input_tokens(input_text)
        out_tok = self.estimate_input_tokens(output_text)
        return {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": 0.0}


# ── Factory helpers ────────────────────────────────────────────────

# Singleton (lazy – init on first use)
_llm: Optional[LLM] = None

def get_llm(
    api_base: str = DEFAULT_API_BASE,
    model: str = DEFAULT_MODEL,
    provider: Optional[str] = None,
    book: Optional[str] = None,
) -> LLM:
    """
    Get an LLM instance.
    
    v1.3: Supports provider-based routing. Order of priority:
    1. provider + model explicit args
    2. book (looks up book's config.json llm_provider/llm_model)
    3. Legacy api_base/model args
    4. Global config.yaml llm.* 
    5. Hardcoded defaults
    """
    global _llm
    
    # Book-based resolution
    if book:
        cfg = resolve_for_book(book)
        return LLM(
            model=cfg["model"],
            api_base=cfg["api_base"],
            api_key=cfg["api_key"],
            provider=cfg["provider"],
        )
    
    # Provider explicit
    if provider:
        return LLM(provider=provider, model=model)
    
    # Legacy singleton path (back-compat)
    if _llm is None:
        _llm = LLM(model=model, api_base=api_base)
    return _llm


def reset_singleton() -> None:
    """Reset the global singleton (for testing or after config change)."""
    global _llm
    _llm = None