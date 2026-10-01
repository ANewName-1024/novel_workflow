"""
CI 门禁回归防线 (2026-10-01)
===========================

背景
----
这次 CI 从第 1 次运行起就一直是红的, 5 次全红。逐个归因后, 三个失败 job 里有
两个是**真缺陷**, 而且都跟运行环境有关 —— 这正是本地全绿、CI 全红的原因。

1. `ruff check ... --select=E9,F63,F7,F82` 失败
   F821 未定义名, 两条:
     - `novel.py` 的 `cmd_pipeline_resume()` 用了 `re`, 而 `import re` 只写在
       另一个函数的函数体里(而且那处根本没用到 re, 是死代码)。
       → `python novel.py pipeline resume <书> --chapter ch_5` 必然 NameError。
       命令从加进来那天起就没能通过。
     - `review_ui/dashboard.py` 的 `_to_iso()` 标注用了 `Optional` 但没 import。
       被 `from __future__ import annotations` 掩盖, 运行时不炸, 但任何
       `get_type_hints()` / 注解求值的工具都会 NameError。

2. `pytest (py3.12)` 失败, 而 `pytest (py3.14)` 通过
   `tests/test_llm_metrics.py::test_complete_invokes_metrics_callback`
   断言 `latency_ms > 0`, 实测拿到 0.0。

   根因在产品代码而不是测试: `lib/llm.py` 用 `time.time()` 测耗时。`time.time()`
   是【挂钟】, 既不单调(系统时间被 NTP/DST 校正时会倒退 → 负延迟), 在 Windows 上
   粒度也极粗。本机实测相邻两次取值:

       CPython 3.12  time.time()  199984/200000 次完全相同 (100.0%)
       CPython 3.14  time.time()  109179/200000 次完全相同 ( 54.6%)

   3.13 起 Windows 换掉了 `time.time()` 的实现, 所以这个 bug 在 3.14 上几乎看不见,
   在 3.12 上必现 —— py3.12 那个 job 红了 5 次, 每次都是它。

   注意这里的修法: 是把 `time.time()` 换成 `time.perf_counter()`, 而不是把断言
   从 `> 0` 改成 `>= 0`。断言是对的, 产品是错的; 让测试迁就 bug 是本项目历史上
   已经栽过两次的坑(见 `test_review_ui_auth.py::TestAuthEnabledButEmptyPassword`
   和 `test_world_rule_consistency.py`)。

本文件把这两条钉死, 防止换台机器又变红。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── 1. 测耗时不能用挂钟 ────────────────────────────────────────────────────

#: 测耗时的文件。这些文件里允许出现 time.time() 当【时间戳】用
#: (app_log.py / backup.py / dashboard.py 都合法), 但绝不允许拿它当秒表。
STOPWATCH_FILES = [
    REPO_ROOT / "lib" / "llm.py",
    REPO_ROOT / "lib" / "llm_providers.py",
]


@pytest.mark.parametrize("path", STOPWATCH_FILES, ids=lambda p: p.name)
class TestElapsedTimeUsesMonotonicClock:
    def test_never_subtracts_two_wallclock_timestamps(self, path):
        """`time.time() - t0` 是本 bug 的签名动作 —— 直接禁掉。

        文本匹配在这里是安全的: 我们要找的是一个【表达式形状】而不是某个名字,
        所以注释里讨论 time.time() 不会误报(对比 test_pipeline_read_paths_are_pure
        里那条按名字匹配的防线, 那个就必须走 AST)。
        """
        src = path.read_text(encoding="utf-8")
        offenders = [
            (i, line.strip())
            for i, line in enumerate(src.splitlines(), 1)
            if re.search(r"time\.time\(\)\s*-", line)
        ]
        assert not offenders, (
            f"{path.name} 又用挂钟测耗时了:\n"
            + "\n".join(f"  L{i}: {txt}" for i, txt in offenders)
            + "\n耗时请用 time.perf_counter(): 它单调, 且不受 NTP/DST 校正与"
              "Windows 时钟粒度影响。"
        )

    def test_t0_is_perf_counter(self, path):
        """秒表的起点也必须是单调钟, 否则只有终点换了钟等于没换。"""
        src = path.read_text(encoding="utf-8")
        t0_lines = [
            line.strip()
            for line in src.splitlines()
            if re.match(r"\s*t0\s*=\s*time\.", line)
        ]
        assert t0_lines, f"{path.name} 里没找到 t0 起点, 结构调整过请同步本测试"
        for line in t0_lines:
            assert "perf_counter" in line, (
                f"{path.name}: 秒表起点不是单调钟 -> {line}"
            )


def test_perf_counter_actually_reports_nonzero_for_instant_call():
    """行为防线: 一次瞬间完成的调用必须测出非零耗时。

    这是上面那条规则的可见后果。没有它, 有人把 perf_counter 换回 time.time()
    时, 只有在 3.12 上才现形 —— 而 3.14 会安静地放行。
    """
    from lib import llm

    captured = []
    obj = llm.LLM(model="test-model")
    obj.enc = None
    obj.client = type(
        "_C", (), {
            "chat": type("_Chat", (), {
                "completions": type("_Comp", (), {
                    "create": staticmethod(lambda **kw: type(
                        "_R", (), {
                            "choices": [type("_Ch", (), {
                                "message": type("_M", (), {"content": "x"})(),
                            })()],
                            "usage": None,
                        })()),
                })(),
            })(),
        }
    )()
    obj.set_metrics_callback(lambda **kw: captured.append(kw))
    obj.complete(prompt="hi")

    assert len(captured) == 1
    latency = captured[0]["latency_ms"]
    assert isinstance(latency, float)
    assert latency > 0, (
        f"瞬间完成的调用测出 latency_ms={latency} —— 秒表分辨率不够, "
        "或者又换回了挂钟"
    )


# ── 2. 关于 F821 这条防线, 为什么这里什么都不写 ──────────────────────────

# 本文件最初带了两条自制的 F821 静态检查, 两条都是假阳性, 已删除:
#
#   1) "except E as e: 块里引用 e 会被清除" —— except ... as e 的全部意义就是
#      在块内使用 e。这条检查会把全项目 50+ 处正确写法判成违规。
#   2) "except 块用了 chapter_id 但没绑定" —— 靠"往后 400 字符"猜作用域。
#      review_service.py:96 的 chapter_id 是 get_review() 的【函数参数】,
#      合法可见, 被判成违规。
#
# 这不是笔误, 是方法论错误: 在这里重造一个比 ruff 弱的 F821 近似品, 只会
# 稳定地产出噪声, 然后让人习惯性地忽略红线 —— 那正是本项目历史上
# "报了干净但其实有问题" / "报了有问题但其实没问题" 两类假象的来源。
#
# F821 的结构性防线只有一个, 就是 CI 里那条真的 ruff 门禁:
#     ruff check lib review_ui novel.py --select=E9,F63,F7,F82
# 它有完整的作用域分析, 跑得比任何自制近似品都快且准。本文件不再重复它 ——
# 如果哪天 ruff 门禁被摘掉, 该恢复的是那条 CI, 不是这里。
