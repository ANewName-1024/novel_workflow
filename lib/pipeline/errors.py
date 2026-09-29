"""
lib/pipeline/errors.py — 流水线领域异常(Phase 3 收敛后的最终形态)

历史
----
初版(1d0e70c)在这里定义了一整套异常体系: PipelineError / TransientError /
PermanentError, 外加 retry() / best_effort() / classify_os_error() 三个工具,
并配了 26 个测试。

收敛后的事实(tools/ 下的三份审计脚本实测):
  PipelineError        被 state.py 的 FSM 校验 raise 了 6 次  →  保留
  TransientError       只在 import 与 __all__ 里出现, 从未 raise  →  移除
  PermanentError       同上                                        →  移除
  retry()              生产代码零使用(llm.py 有自己的退避)        →  移除
  best_effort()        生产代码零使用, 且形状不匹配:               →  移除
                       那 15 个位点都是内联 try 块, 不是装饰器
  classify_os_error()  生产代码零使用                              →  移除

「可重试性」这个知识没有丢, 它在真正做重试的地方:
lib/llm.py 的 _is_retryable(), 按 SDK 实测的状态码分类。放在这里而不是
pipeline 包里, 是因为重试只发生在 LLM 调用链上。

教训记在 MEMORY: novel-workflow L98 的姊妹条 ——
写了 26 个测试不等于有消费者, 测试锁住的是「被测代码存在」,
不是「被测代码有用」。上线前应该问一句:谁在调用它?
"""
from __future__ import annotations

import logging
from typing import Any

from ..errors import ErrorCode, NovelError

log = logging.getLogger(__name__)

__all__ = ["PipelineError"]


class PipelineError(NovelError):
    """流水线领域异常, 继承 NovelError 复用错误码与上层 HTTP 映射。

    目前用于状态机的校验失败: 未知 stage、非法 FSM 转换。
    位置参数 (code, message) 与 NovelError 保持一致。
    """

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
        """结构化上下文, 供日志与前端展示。"""
        d: dict[str, Any] = {}
        if self.book:
            d["book"] = self.book
        if self.chapter:
            d["chapter"] = self.chapter
        if self.stage:
            d["stage"] = self.stage
        return d