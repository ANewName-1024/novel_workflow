"""tests/test_resume_autodetect_2026_10_02.py — resume 自动探测不能把下游清空。

背景
----
2026-10-02 给流水线加了「checkpoint DONE 且产物完好就跳过整个阶段」的机制
(lib/chapter.py::_resume_skip_stage)。但恢复入口 `recover_stage()` 的自动探测
让这个功能等于白做:

    write_chapter 每次运行开头都把 context 标 RUNNING
      -> 自动探测第一个非 DONE 的阶段, 几乎总是命中 context
      -> rerun_from("context") 把**全部 8 个阶段**重置成 PENDING
      -> 下游那些「DONE + 产物完好」的阶段被一起清掉
      -> resume-skip 一个都触发不了

而 context 和 writing 本来就**无条件重跑**, 重置它们没有任何收益。

修法: 自动探测时跳过这两个头部阶段, 直接找真正值得重置的那一个。
若只剩它们非 DONE, 说明上游本来就是坏的 —— 什么都不用重置, 直接重跑即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import storage                      # noqa: E402
from lib.pipeline import state as pstate     # noqa: E402
from lib.pipeline.state import StageState    # noqa: E402


def _seed(book: str, statuses: dict) -> None:
    """直接写一份 checkpoint, 绕开真的跑流水线。"""
    v2 = pstate.get_v2()
    ch_doc = pstate.ChapterCheckpoint(book=book, chapter=1)
    for stage, st in statuses.items():
        assert stage in ch_doc.stages, f"未知 stage: {stage}"
        ch_doc.stages[stage].status = st
    doc = pstate.CheckpointDoc(book=book)
    doc.chapters[1] = ch_doc
    v2.save(doc)


class TestAutoDetectSkipsHeadStages:
    def test_does_not_wipe_completed_downstream(self, tmp_projects_root):
        """最典型的中断形态: context 挂在 RUNNING, 下游已经做完且产物齐全。"""
        _seed("test_book", {
            "context":  StageState.RUNNING.value,
            "writing":   StageState.DONE.value,
            "extract":   StageState.DONE.value,
            "entity_diff": StageState.DONE.value,
            "summary":   StageState.DONE.value,
            "state":     StageState.DONE.value,
        })
        v2 = pstate.get_v2()

        res = pstate.recover_stage("test_book", 1)      # 不给 from_stage -> 自动探测

        assert res["ok"] is True
        stages = v2.get_chapter("test_book", 1).stages
        # 下游三个关键阶段必须保持 DONE, 否则 resume-skip 永远触发不了
        for s in ("extract", "summary", "state"):
            assert stages[s].status == StageState.DONE.value, \
                f"{s} 被自动探测连带清空了 —— resume-skip 会完全失效"

    def test_reports_head_stage_without_resetting(self, tmp_projects_root):
        """只剩 context/writing 没做完时, 什么都不该重置, 只需重跑。

        刻意把下游全标 DONE —— 否则下游那一个 PENDING 就是真正值得重置的
        阶段, 自动探测命中它是正确行为(见下一个测试类)。
        """
        _seed("test_book", {
            "context":  StageState.RUNNING.value,
            "writing":   StageState.PENDING.value,
            "extract":   StageState.DONE.value,
            "entity_diff": StageState.DONE.value,
            "summary":   StageState.DONE.value,
            "state":     StageState.DONE.value,
            "self_check": StageState.SKIPPED.value,
            "done":      StageState.DONE.value,
        })
        v2 = pstate.get_v2()

        res = pstate.recover_stage("test_book", 1)

        assert res["ok"] is True
        assert "context" in res["message"] or "writing" in res["message"]
        stages = v2.get_chapter("test_book", 1).stages
        assert stages["extract"].status == StageState.DONE.value, \
            "没有真正值得重置的阶段时, 不应动任何状态"


class TestAutoDetectStillResetsRealBreakage:
    """跳过头部阶段不能变成「什么都不恢复」—— 真断了的下游仍要能恢复。"""

    def test_failed_downstream_is_still_reset(self, tmp_projects_root):
        # entity_diff 必须显式标 DONE: 它默认是 PENDING 且排在 summary 之前,
        # 不标的话自动探测命中它是**正确**行为, 测不到我们想测的那条。
        _seed("test_book", {
            "context":  StageState.DONE.value,
            "writing":   StageState.DONE.value,
            "extract":   StageState.DONE.value,
            "entity_diff": StageState.DONE.value,
            "summary":   StageState.FAILED.value,
            "state":     StageState.PENDING.value,
        })
        v2 = pstate.get_v2()

        res = pstate.recover_stage("test_book", 1)

        assert res["ok"] is True
        assert res["recovered_stage"] == "summary", \
            "下游 FAILED 必须仍被自动探测命中, 否则恢复功能形同虚设"
        stages = v2.get_chapter("test_book", 1).stages
        assert stages["summary"].status == StageState.PENDING.value
        # 已完成的 extract 在 summary 之前, 不该被重置
        assert stages["extract"].status == StageState.DONE.value

    def test_pending_downstream_is_still_reachable(self, tmp_projects_root):
        """上游全好、下游第一个非 DONE 是 entity_diff 时, 也要能恢复。"""
        _seed("test_book", {
            "context":  StageState.DONE.value,
            "writing":   StageState.DONE.value,
            "extract":   StageState.DONE.value,
            "entity_diff": StageState.PENDING.value,
            "summary":   StageState.PENDING.value,
        })
        res = pstate.recover_stage("test_book", 1)
        assert res["ok"] is True
        assert res["recovered_stage"] == "entity_diff"

    def test_explicit_from_stage_still_works(self, tmp_projects_root):
        """显式指定阶段时行为不变 —— 自动探测的改动不能影响它。"""
        _seed("test_book", {
            "context": StageState.DONE.value,
            "writing":  StageState.DONE.value,
            "extract":  StageState.DONE.value,
        })
        v2 = pstate.get_v2()

        res = pstate.recover_stage("test_book", 1, from_stage="extract")

        assert res["ok"] is True
        assert res["recovered_stage"] == "extract"
        assert v2.get_chapter("test_book", 1).stages["extract"].status \
            == StageState.PENDING.value
