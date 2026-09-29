"""tests/test_pipeline_marker_protocol.py — [PIPELINE] marker 是跨进程协议。

这是本次重构中最危险的一处, 原因:

  lib/chapter.py 生产 19 行 `[PIPELINE] book=... ch=... stage=... status=...`
  lib/pipeline/process.py:226 用 _PIPELINE_RE 正则从日志文件里抓回来
  → 解析出流水线当前阶段 → 崩溃恢复 / status() 校准依赖它

两者隔着进程边界, 靠正则文本耦合。如果有人把这些 print 改成 log.info(),
或调整字段顺序, 后果是:
  - 崩溃恢复静默失效(流水线崩了, UI 显示「没跑过」)
  - test_chapter_markers.py 依然全绿 —— 它只测正则本身, 不测生产者

所以本文件测**生产者与消费者的一致性**, 而不是重复测正则。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.pipeline import process as pl

CHAPTER_PY = Path(__file__).resolve().parent.parent / "lib" / "chapter.py"
PIPELINE_PREFIX = "[PIPELINE] book="


def chapter_source() -> str:
    return CHAPTER_PY.read_text(encoding="utf-8")


# ── 生产者:chapter.py 仍用 print 输出 marker ──────────────────────────────
class TestProducerStillPrints:
    def test_marker_lines_are_print_not_logging(self):
        """marker 必须走 print(落进日志文件), 不能走 log.*。

        log.* 的输出经过 handler 格式化, 会被加上时间戳/前缀/级别,
        虽然正则仍能匹配, 但一旦 root logger 配了非 stdout 的 handler
        (比如只写文件), pipeline 的日志抓取就拿不到了。
        """
        src = chapter_source()
        for i, line in enumerate(src.splitlines(), 1):
            if PIPELINE_PREFIX in line:
                assert "print(" in line, (
                    f"chapter.py:L{i} 的 [PIPELINE] marker 被改成了非 print —— "
                    f"崩溃恢复会静默失效。\n  {line.strip()}"
                )

    def test_marker_count_unchanged(self):
        """marker 数量没有因本次重构丢失。"""
        src = chapter_source()
        count = src.count(PIPELINE_PREFIX)
        assert count == 19, f"预期 19 个 marker, 实得 {count}"

    def test_every_marker_matches_the_consumer_regex(self):
        """最强的一条: 每一个生产者写出的字面量, 消费者都能解析。

        这条直接连起协议两端 —— 之前没有任何测试做过这件事。
        """
        # 只取 f-string 里的字面部分。
        # 注意: stage/status 在生产端是【字面量】(stage=extract) 而非变量,
        # 且末尾可能跟额外字段 (severity={sev}), 所以不做结尾锚定。
        literal = re.compile(
            r'print\(f"\[PIPELINE\] book=\{book\} ch=\{chapter_num\} '
            r'stage=([a-z_]+) status=([a-z]+)'
        )
        found = literal.findall(chapter_source())
        assert found, "没匹配到任何 marker 生产点 —— 正则或代码形态变了"
        for stage, status in found:
            line = f"[PIPELINE] book=B ch=1 stage={stage} status={status}"
            m = pl._PIPELINE_RE.search(line)
            assert m is not None, f"消费者解析不了生产者输出: {line!r}"
            # 分组序号与 process.py:228-229 的取值方式保持一致 (位置分组, 非命名)
            assert m.group(3) == stage, f"stage 分组位置变了, 消费者会取错: {m.groups()}"
            assert m.group(4) == status, f"status 分组位置变了: {m.groups()}"


# ── 消费者:正则仍能解析 ───────────────────────────────────────────────────
class TestConsumerParses:
    @pytest.mark.parametrize("stage", [
        "extract", "entity_diff", "summary", "state", "self_check", "context",
    ])
    def test_consumer_parses_each_stage(self, stage):
        line = f"[PIPELINE] book=my_book ch=42 stage={stage} status=done"
        m = pl._PIPELINE_RE.search(line)
        assert m is not None, f"解析失败: {line!r}"
        # 位置分组, 与 process.py:228-229 一致
        assert m.group(1) == "my_book"
        assert int(m.group(2)) == 42
        assert m.group(3) == stage
        assert m.group(4) == "done"

    def test_consumer_ignores_normal_log_lines(self):
        """普通日志行不应被误判成 marker。"""
        for line in [
            "2026-09-29 17:00:00 WARNING lib.chapter: extract 失败 (非致命): boom",
            "  ✓ 章节摘要生成",
            "  ⚠ 状态更新失败 (非致命): disk full",
        ]:
            assert pl._PIPELINE_RE.search(line) is None, \
                f"普通行被误解析成 marker: {line!r}"


# ── 防线:协议锁注释存在 ───────────────────────────────────────────────────
class TestProtocolIsDocumented:
    def test_lock_comments_present(self):
        """每个 marker 上方应有「勿改」注释, 防止后来者手贱。"""
        lines = chapter_source().splitlines()
        unmarked = []
        for i, line in enumerate(lines):
            if PIPELINE_PREFIX not in line:
                continue
            prev2 = lines[max(0, i - 2):i]
            if not any("协议" in p for p in prev2):
                unmarked.append(i + 1)
        assert not unmarked, (
            f"这些 marker 行缺少协议锁注释: {unmarked}\n"
            f"  协议说明应写在紧邻的上两行内"
        )