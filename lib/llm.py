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
    ) -> str:
        """
        Send a chat-completion request. Returns full text.
        Raises on repeated failure.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(self.max_retries + 1):
            try:
                if stream:
                    return self._stream_completion(messages, max_tokens, stop)
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=stop or None,
                )
                return resp.choices[0].message.content or ""
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

    def call(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
        """Low-level messages-based call (for extracted memory prompts)."""
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content or ""
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
