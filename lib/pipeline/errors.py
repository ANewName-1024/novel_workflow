"""
lib/pipeline/errors.py — 流水线异常体系(Phase 3)
==============================================
动机
----
Phase 3 审计 71 处 except(tools/audit_excepts.py):20 处「吞掉异常」、
48 处「捕获过宽且不记日志」。

但一刀切改成 raise 是错的 —— 审计显示多数是**刻意的 best-effort 语义**:
    _kill_pid_tree 捕获 psutil.NoSuchProcess
        进程已经没了,这就是「杀成功」。raise 是错的。
    _v2_mark 捕获 Exception
        检查点写失败不该中断正文写入。raise 也是错的。

真正要解决的三件事:
    1. 失败被静默伪装成成功(返回 None/[]/{} 与「确实没有」无法区分)
    2. print(stderr) 而非 logging,无结构化上下文
    3. 可重试的网络/IO 异常没有重试,单次抖动直接判失败

分层
----
PipelineError          领域异常基类(原在 state.py,此处收编为唯一真相)
├── TransientError     可重试:网络抖动、临时占用、并发写冲突
└── PermanentError     不可重试:校验失败、状态非法、资源不存在

向后兼容
--------
state.py 原先 raise PipelineError(ErrorCode.GENERIC, "...") —— 本模块保持
`(code, message)` 位置参数不变,那 5 处 raise 无需改动。
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

from ..errors import ErrorCode, NovelError

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class PipelineError(NovelError):
    """流水线领域异常,继承 NovelError 复用错误码与上层 HTTP 映射。"""

    def __init__(
        self,
        code: ErrorCode = ErrorCode.GENERIC,
        message: str = "",
        *,
        detail: str = "",
        stage: str | None = None,
        book: str | None = None,
        chapter: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(code, message, detail=detail)
        self.stage = stage
        self.book = book
        self.chapter = chapter
        self.__cause__ = cause

    def context(self) -> dict[str, Any]:
        """结构化上下文,供日志与前端展示。"""
        d: dict[str, Any] = {}
        if self.book:
            d["book"] = self.book
        if self.chapter:
            d["chapter"] = self.chapter
        if self.stage:
            d["stage"] = self.stage
        return d


class TransientError(PipelineError):
    """可重试的失败:网络抖动、临时文件占用、并发写冲突。"""


class PermanentError(PipelineError):
    """不可重试的失败:校验失败、状态机非法转移、资源不存在。"""


# ── OSError 归类:决定该不该重试 ───────────────────────────────────────────
TRANSIENT_OS_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,     # 含 ConnectionResetError / ConnectionAbortedError
    TimeoutError,        # 含 socket.timeout
    BlockingIOError,
    FileNotFoundError,   # pipeline 场景下多为「对方还没写完」
)

PERMANENT_OS_ERRORS: tuple[type[BaseException], ...] = (
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
    ValueError,   # json.JSONDecodeError 是 ValueError 子类,一并覆盖
    KeyError,
)


def classify_os_error(e: BaseException) -> type[PipelineError]:
    """把底层异常归到 Transient / Permanent。"""
    if isinstance(e, TRANSIENT_OS_ERRORS):
        return TransientError
    if isinstance(e, PERMANENT_OS_ERRORS):
        return PermanentError
    return PipelineError


# ── best_effort:替代裸 pass ───────────────────────────────────────────────
def best_effort(
    what: str,
    *,
    log_level: int = logging.DEBUG,
    reraise: type[BaseException] | tuple[type[BaseException], ...] = (),
) -> Callable[[F], F]:
    """声明「此处失败是设计内的」,记日志后继续 —— 替代 `except: pass`。

    失败不再是黑洞:至少留下一条带堆栈的记录。reraise 指定哪些仍需向上抛
    (如 TypeError 这类不该被吞的)。
    """

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except reraise:
                raise
            except Exception as e:  # noqa: BLE001 — 声明式 best-effort
                log.log(log_level, "best-effort 失败: %s | %s: %s",
                        what, type(e).__name__, e, exc_info=True)
                return None

        return wrapper  # type: ignore[return-value]

    return deco


# ── retry:只对 TransientError 生效 ────────────────────────────────────────
def retry(
    times: int = 2,
    *,
    base_delay: float = 0.5,
    factor: float = 1.5,
    on: type[BaseException] | tuple[type[BaseException], ...] = TransientError,
) -> Callable[[F], F]:
    """指数退避重试,仅对 on 指定类型生效。

    PermanentError 永不重试 —— 重试一个校验失败只是浪费时间。
    """
    if times < 0:
        raise ValueError("times 必须 >= 0")

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            delay = base_delay
            last: BaseException | None = None
            for attempt in range(times + 1):
                try:
                    return fn(*a, **kw)
                except on as e:
                    last = e
                    if attempt == times:
                        break
                    log.warning(
                        "%s 第 %d/%d 次失败(%s: %s), %.1fs 后重试",
                        fn.__name__, attempt + 1, times, type(e).__name__, e, delay)
                    time.sleep(delay)
                    delay *= factor
            assert last is not None
            raise last

        return wrapper  # type: ignore[return-value]

    return deco
