"""tools/probe_openai_errors.py — 查 OpenAI SDK 异常层级, 为重试分类打底。

背景: lib/llm.py 的两处重试都写成
    except (RateLimitError, APIError) as e:  ->  sleep -> 继续
问题在于 APIError 是所有 API 异常的基类, 认证失败(401)、参数错误(400)
也会走同一条路重试。max_retries=3 + retry_delay=10s 意味着一个错误密钥
要白等 30 秒才报错。

分类错了比重试更糟, 所以先测出 SDK 的真实继承关系, 不靠记忆。
"""
from __future__ import annotations

import inspect
import sys

try:
    import openai
except ImportError:
    print("openai 未安装")
    raise SystemExit(0)

NAMES = [
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "RateLimitError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "UnprocessableEntityError",
    "InternalServerError",
]

print("=" * 74)
print("openai SDK 异常继承关系")
print("=" * 74)
for name in NAMES:
    cls = getattr(openai, name, None)
    if cls is None:
        print(f"  {name:<28} (此版本无)")
        continue
    mro = [c.__name__ for c in inspect.getmro(cls)]
    # 只显示到 APIError 为止, 后面是 Exception/BaseException 没信息量
    chain = []
    for c in mro:
        chain.append(c)
        if c == "APIError":
            break
    print(f"  {name:<28} <- {' <- '.join(chain)}")

print()
print("=" * 74)
print("分类建议")
print("=" * 74)


def status_of(name: str) -> int | None:
    cls = getattr(openai, name, None)
    if cls is None or not issubclass(cls, openai.APIStatusError):
        return None
    return getattr(cls, "status_code", None)


TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}
transient, permanent = [], []
for name in NAMES:
    st = status_of(name)
    if st is None:
        continue
    (transient if st in TRANSIENT_STATUS else permanent).append((name, st))

print("  可重试 (瞬时):")
for n, st in transient:
    print(f"      {n:<28} HTTP {st}")
if not transient:
    print("      (无)")

print("  不可重试 (确定性):")
for n, st in permanent:
    print(f"      {n:<28} HTTP {st}")
if not permanent:
    print("      (无)")

print()
print("  无状态码(连接层, 归为可重试):")
for n in ("APIConnectionError", "APITimeoutError"):
    if getattr(openai, n, None) is not None:
        print(f"      {n}")
print("=" * 74)