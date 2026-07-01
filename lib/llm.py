"""
LLM wrapper: OpenAI-compat API (local llama-server).
Handles streaming, retries, token counting, context window management.
"""
from __future__ import annotations

import time, json, tiktoken
from typing import Generator, Optional
from openai import OpenAI, RateLimitError, APIError

DEFAULT_API_BASE = "http://127.0.0.1:60443/v1"

class LLM:
    def __init__(
        self,
        model: str = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        api_base: str = DEFAULT_API_BASE,
        api_key: str = "no-key-needed",
        max_retries: int = 3,
        retry_delay: float = 10.0,
    ):
        self.client = OpenAI(base_url=api_base, api_key=api_key, timeout=600)
        self.model = model
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

    def set_metrics_callback(self, cb) -> None:
        """设置指标回调. cb 签名: cb(stage, ch, model, in_tok, out_tok, latency_ms)."""
        self._metrics_cb = cb

    def set_stage_context(self, stage: str, ch: int = 0) -> None:
        """设置当前 stage/ch. 后续 complete() / call() 会带上这些字段调用回调.

        用法: llm.set_stage_context("extract", 8); llm.complete(...); llm.set_stage_context("summary", 8); ...
        """
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
        # 隐式 stage/ch 上下文 (参数未传时回退到 context)
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
                else:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stop=stop or None,
                    )
                    text = resp.choices[0].message.content or ""
                # 成功后: 调 metrics callback
                latency_ms = (time.time() - t0) * 1000
                usage = getattr(resp, "usage", None) if not stream else None
                if usage is not None:
                    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
                else:
                    # fallback: 用 tiktoken 估算 (不依赖 server 返 usage)
                    in_tok = self._count_tokens(prompt + system)
                    out_tok = self._count_tokens(text)
                self._emit_metrics(eff_stage, eff_ch, in_tok, out_tok, latency_ms)
                return text
            except (RateLimitError, APIError) as e:
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
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
        """Low-level messages-based call (for extracted memory prompts). v1.1 加 stage/ch (隐式 context)."""
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
                    # fallback 估算
                    all_input = " ".join(m.get("content", "") for m in messages)
                    in_tok = self._count_tokens(all_input)
                    out_tok = self._count_tokens(text)
                self._emit_metrics(eff_stage, eff_ch, in_tok, out_tok, latency_ms)
                return text
            except (RateLimitError, APIError) as e:
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
                raise RuntimeError(f"LLM API error: {e}") from e

    def estimate_input_tokens(self, text: str) -> int:
        return self._count_tokens(text)

    def estimate_cost(self, input_text: str, output_text: str) -> dict:
        """Rough cost estimate. For local models this is always $0."""
        in_tok  = self.estimate_input_tokens(input_text)
        out_tok = self.estimate_input_tokens(output_text)
        return {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": 0.0}

# Singleton (lazy – init on first use)
_llm: Optional[LLM] = None

def get_llm(api_base: str = DEFAULT_API_BASE, model: str = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf") -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM(model=model, api_base=api_base)
    return _llm
