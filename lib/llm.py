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

# 推理模型(reasoning model)的 token 预算下限。
#
# 2026-10-02 实测: MiniMax M3.1 在 extract 阶段 max_tokens=4096 时,
#   finish_reason=length  reasoning_tokens=4096  content_len=0
# —— 4096 全部被思维链吃掉, 正文预算为 0, API 正常返回 200。
# 而旧代码是 `content or ""`, 于是这个空串被当成成功往下传, 最终
# 整章"完成"而记忆库/摘要/审校全是空的 —— 全程没有任何一处报错。
#
# 所以推理模型的 max_tokens 必须在**思维链之外**再留出正文预算。
# 16384 是实测能稳定返回正文的值(同一提示词: 4096→空, 16384→3236 字符)。
#
# 按 provider 配置而非硬编码: 非推理模型/本地小上下文模型设 0 即关闭该下限,
# 免得给它们报 context too long。配置项: providers.<name>.min_max_tokens
DEFAULT_MIN_MAX_TOKENS = 0

# 空响应时的升级上限: 首次失败后按 4 倍递增重试, 但不超过这个值
EMPTY_RETRY_CEILING = 65536


class LLMEmptyResponse(RuntimeError):
    """LLM 返回了空正文。

    这**不是**成功。单独一个异常类型, 是为了让 complete() 能把它和
    「网络抖动」区分开: 空正文重发同样的参数不会有任何变化, 必须
    换更大的 max_tokens 才有意义。
    """

    def __init__(self, finish_reason, model: str, max_tokens: int, usage_note: str = ""):
        self.finish_reason = finish_reason
        self.model = model
        self.max_tokens = max_tokens
        self.usage_note = usage_note
        hint = ""
        if finish_reason == "length":
            hint = (
                f" —— 模型把 max_tokens={max_tokens} 全部用在了推理链上, "
                f"正文没拿到预算。若该模型是推理模型, 请调大 "
                f"providers.<name>.min_max_tokens (实测 16384 可解)。"
            )
        super().__init__(
            f"LLM 返回空正文 (finish_reason={finish_reason!r}, model={model!r}, "
            f"max_tokens={max_tokens}){usage_note}{hint}"
        )


def _extract_text(resp, max_tokens: int) -> str:
    """从 chat.completion 响应里取正文, 并检查它是否真的存在。

    旧实现在这里写的是 `resp.choices[0].message.content or ""`:
    content 为 None 时被 `or ""` 吞成空串, 既不报错也不看 finish_reason,
    于是「被 max_tokens 截断、什么都没生成」和「正常返回」长得一模一样。
    下游 json.loads("") 抛错被 catch 掉, 阶段照样写 DONE —— 这就是
    流水线「静默地成功」的起点。
    """
    choice = resp.choices[0]
    msg = getattr(choice, "message", None)
    text = (getattr(msg, "content", None) or "") if msg is not None else ""
    if text.strip():
        return text

    finish = getattr(choice, "finish_reason", None)
    model = getattr(resp, "model", "?")
    note = ""
    usage = getattr(resp, "usage", None)
    if usage is not None:
        comp = getattr(usage, "completion_tokens", None)
        det = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(det, "reasoning_tokens", None) if det is not None else None
        note = f" (completion_tokens={comp}, reasoning_tokens={reasoning})"
    raise LLMEmptyResponse(finish, model, max_tokens, note)


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
        min_max_tokens: int = 0,
    ):
        """
        Create an LLM client.

        Resolution order (model/api_base/api_key):
        1. Explicit kwargs (highest)
        2. provider config from llm_providers (if provider given)
        3. Global config.yaml llm.* (legacy)
        4. Hardcoded defaults (back-compat)

        min_max_tokens: 推理模型的 token 预算下限(见 DEFAULT_MIN_MAX_TOKENS)。
        0 = 不施加下限。provider 配置里给了就优先用。
        """
        timeout_sec = 600          # 下面可能被 config.yaml 覆盖
        # Provider-based resolution
        if provider:
            cfg = resolve_model(provider, model)
            self.provider = provider
            self.model = cfg["model"]
            api_base = api_base or cfg["api_base"]
            api_key = api_key or cfg["api_key"]
            if not min_max_tokens:
                min_max_tokens = int(cfg.get("min_max_tokens") or 0)
        else:
            # Legacy / direct args
            self.provider = "local"  # default
            if model is None and api_base is None:
                # Use global config
                from .config_loader import get_config
                gcfg = get_config()
                cfg = gcfg.get("llm", {})
                model = cfg.get("default_model", DEFAULT_MODEL)
                api_base = cfg.get("api_base", DEFAULT_API_BASE)
                # timeout_sec 之前在 config.yaml 里配着但**从未被读取**,
                # LLM 一直用硬编码的 600。这里是它第一次真正被接线。
                timeout_sec = int(cfg.get("timeout_sec", 600) or 600)
                if not min_max_tokens:
                    min_max_tokens = int(cfg.get("min_max_tokens", DEFAULT_MIN_MAX_TOKENS) or 0)
            self.model = model or DEFAULT_MODEL
            api_base = api_base or DEFAULT_API_BASE
            api_key = api_key or "no-key-needed"
        if not min_max_tokens:
            min_max_tokens = DEFAULT_MIN_MAX_TOKENS
        self.min_max_tokens = int(min_max_tokens)
        self.timeout_sec = int(timeout_sec or 600)

        self.api_base = api_base
        self.api_key = api_key
        self.client = OpenAI(base_url=api_base, api_key=api_key, timeout=self.timeout_sec)
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

    def ping(self, prompt: str = "ping", timeout_sec: int = 15) -> dict:
        """最小连通性探测: 只回答「端点可达吗 / key 有效吗」。

        **不能**走 complete()。complete() 会施加 min_max_tokens 下限, 并在空
        响应时按 4 倍升级重试 —— 对推理模型(min_max_tokens=16384)来说, 一次
        只想确认"通不通"的探测会变成 16384 → 65536 token 的又慢又贵的请求,
        上限 EMPTY_RETRY_CEILING。预检要的是"有没有人应答", 不是"写得好不好",
        所以这里直接打底层客户端, 且**刻意不检查正文**: 推理模型在
        max_tokens=1 下正文必然是空的, 那是正常的, 不是故障。

        HTTP 层能应答 (200) 就算通过。401/403/404/连接失败会照常抛。
        """
        client = OpenAI(
            base_url=self.api_base, api_key=self.api_key, timeout=timeout_sec
        )
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1,
            temperature=0,
        )
        return {
            "ok": True,
            "model": getattr(resp, "model", self.model),
            "api_base": self.api_base,
            "finish_reason": getattr(resp.choices[0], "finish_reason", None),
        }

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

    def _effective_max_tokens(self, requested: Optional[int]) -> int:
        """把调用点要的 max_tokens 抬到 provider 配的推理下限之上。

        调用点按「正文大概多少字」拍脑袋写 max_tokens(summary 写 400、
        state 写 1500), 那是按**非推理**模型的心智模型算的。推理模型
        还要先花一大截预算写思维链, 于是正文预算被挤成 0。
        这里统一兜底: 请求值和 provider 下限取大者。

        下限默认 0(不干预), 避免给小上下文模型凭空造 context too long。
        """
        want = int(requested or 0)
        return max(want, self.min_max_tokens)

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

        # 测耗时刻意用 perf_counter() 而不是 time.time():
        #   time.time() 是【挂钟】, 既不单调(系统时间被 NTP/DST 校正时会倒退, 算出
        #   负延迟), 在 Windows 上分辨率也极粗 —— 实测 CPython 3.12 下相邻两次
        #   time.time() 100% 返回同一个值(200000/200000), 于是任何快于时钟粒度的
        #   调用(本地 llama-server 命中缓存、mock 调用)一律上报 latency_ms = 0。
        #   3.13 起 Windows 的 time.time() 换了更精确的实现, 于是这个 bug 在 3.14
        #   上看不见, 在 3.12 上必现 —— CI 的 py3.12 门禁红了 5 次就是它。
        t0 = time.perf_counter()
        eff_max = self._effective_max_tokens(max_tokens)
        attempt = 0
        while True:
            try:
                if stream:
                    text = self._stream_completion(messages, eff_max, stop)
                    resp = None
                    if not text.strip():
                        raise LLMEmptyResponse("stop", self.model, eff_max, " (流式响应)")
                else:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=eff_max,
                        stop=stop or None,
                    )
                    text = _extract_text(resp, eff_max)
                latency_ms = (time.perf_counter() - t0) * 1000
                usage = getattr(resp, "usage", None) if not stream else None
                if usage is not None:
                    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
                else:
                    in_tok = self._count_tokens(prompt + system)
                    out_tok = self._count_tokens(text)
                self._emit_metrics(eff_stage, eff_ch, in_tok, out_tok, latency_ms)
                return text
            except LLMEmptyResponse as e:
                # 空正文 + finish_reason=length = 思维链把预算吃光了。
                # 原样重发不会有任何变化, 所以不走下面的通用重试, 而是
                # 单独把额度翻几倍再试一次(不消耗 max_retries 预算)。
                if e.finish_reason == "length" and eff_max < EMPTY_RETRY_CEILING:
                    new_max = min(eff_max * 4, EMPTY_RETRY_CEILING)
                    log.warning(
                        "LLM 返回空正文(max_tokens=%d, finish_reason=length), "
                        "判定为推理模型预算不足, 提高到 %d 重试", eff_max, new_max)
                    eff_max = new_max
                    continue
                raise
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
                    attempt += 1
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
        t0 = time.perf_counter()
        eff_max = self._effective_max_tokens(max_tokens)
        attempt = 0
        while True:
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=eff_max,
                )
                text = _extract_text(resp, eff_max)
                latency_ms = (time.perf_counter() - t0) * 1000
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
            except LLMEmptyResponse as e:
                if e.finish_reason == "length" and eff_max < EMPTY_RETRY_CEILING:
                    new_max = min(eff_max * 4, EMPTY_RETRY_CEILING)
                    log.warning(
                        "LLM 返回空正文(max_tokens=%d, finish_reason=length), "
                        "判定为推理模型预算不足, 提高到 %d 重试", eff_max, new_max)
                    eff_max = new_max
                    continue
                raise
            except Exception as e:
                if not _is_retryable(e):
                    raise RuntimeError(
                        f"LLM API error (不可重试, {_err_desc(e)}): {e}") from e
                if attempt < self.max_retries:
                    _backoff(self.retry_delay, attempt)
                    log.warning("LLM 调用失败, %.1fs 后重试 (%d/%d): %s: %s",
                                self.retry_delay * (2 ** attempt), attempt + 1,
                                self.max_retries, type(e).__name__, e)
                    attempt += 1
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