"""
errors.py - 统一异常 + 错误码

设计:
- ErrorCode 是 IntEnum, 值即为 sys.exit() 的退出码
- NovelError 携带 code/message/detail, novel.py 顶层捕获后 sys.exit(code)
- 子命令 / lib 模块不要直接 sys.exit(), 一律 raise NovelError

新增错误码:
- 7 (AUTH_ERROR)    M5 review_ui Basic Auth 用
- 8 (REVIEW_FAILED) M5 review_service 业务异常用
"""
from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    """novel_workflow 进程退出码."""
    OK = 0
    GENERIC = 1
    INVALID_ARGS = 2   # argparse 错 / 缺必填
    NOT_FOUND = 3      # 项目/章节/文件不存在
    LLM_FAILURE = 4    # LLM 调用失败 (RuntimeError 重试耗尽)
    CONFIG_ERROR = 5   # config.yaml 解析失败 / 缺字段
    IO_ERROR = 6       # 文件读写失败
    AUTH_ERROR = 7     # M5 review_ui Basic Auth 失败
    REVIEW_FAILED = 8  # M5 review_service 业务异常


class NovelError(Exception):
    """带 ErrorCode 的异常, novel.py main() 顶层捕获后 sys.exit(code).

    Args:
        code: ErrorCode 枚举, 即退出码
        message: 用户可见的简短消息 (会打到 stderr)
        detail: 调试细节 (默认隐藏, --verbose 时显示)
    """
    def __init__(self, code, message, *, detail=""):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

    def __str__(self):
        if self.detail:
            return f"[{self.code.name}] {self.message} ({self.detail})"
        return f"[{self.code.name}] {self.message}"


def exit_code_from_error(code):
    """把 ErrorCode / int 规整为 sys.exit() 接受的 int."""
    if isinstance(code, ErrorCode):
        return int(code)
    return int(code)