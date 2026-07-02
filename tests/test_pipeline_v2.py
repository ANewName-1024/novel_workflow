"""
test_pipeline_v2.py — lib/pipeline_v2.PipelineV2 (v1.2 M5)

~30 tests:
- Checkpoint 持久化 (load/save/reset/atomic)
- FSM transitions (合法 + 非法)
- Skip / Rerun API
- 状态汇总 get_pipeline_view
- 多章独立 + 并发 (last-write-wins)
- v1 兼容 (老 state.json 不破坏)
"""
import json
from pathlib import Path

import pytest

from lib import pipeline_v2 as pv2
from lib import storage
from lib.errors import ErrorCode


@pytest.fixture
def book(tmp_projects_root):
    return "test_book"


# ── 1-3. Checkpoint 持久化 ──────────────────────────────────────────────

def test_load_empty_returns_empty_doc(tmp_projects_root, book):
    """项目无 checkpoint 文件 → 返回空 doc."""
    v2 = pv2.PipelineV2()
    doc = v2.load(book)
    assert doc.book == book
    assert doc.chapters == {}


def test_save_and_load_roundtrip(tmp_projects_root, book):
    """save 后 load 出来数据一致."""
    v2 = pv2.PipelineV2()
    ch_doc = pv2.ChapterCheckpoint(book=book, chapter=5)
    ch_doc.stages["context"].status = pv2.StageState.DONE.value
    ch_doc.stages["context"].tokens = {"in": 4200, "out": 0}
    v2.save_chapter(book, ch_doc)

    loaded = v2.get_chapter(book, 5)
    assert loaded.chapter == 5
    assert loaded.stages["context"].status == "DONE"
    assert loaded.stages["context"].tokens == {"in": 4200, "out": 0}
    # 其他 stage 还是 PENDING
    assert loaded.stages["writing"].status == "PENDING"


def test_reset_chapter_clears_all_stages(tmp_projects_root, book):
    """reset_chapter 把所有 stage 重置为 PENDING."""
    v2 = pv2.PipelineV2()
    ch_doc = pv2.ChapterCheckpoint(book=book, chapter=3)
    ch_doc.stages["context"].status = "DONE"
    ch_doc.stages["writing"].status = "FAILED"
    v2.save_chapter(book, ch_doc)

    new_ch = v2.reset_chapter(book, 3)
    assert all(s.status == "PENDING" for s in new_ch.stages.values())


def test_get_chapter_creates_if_missing(tmp_projects_root, book):
    """不存在的章节返回新的 PENDING chapter."""
    v2 = pv2.PipelineV2()
    ch = v2.get_chapter(book, 99)
    assert ch.chapter == 99
    assert ch.stages["context"].status == "PENDING"
    # 不应写盘
    assert not pv2.checkpoint_path(book).exists()


def test_corrupt_checkpoint_returns_empty(tmp_projects_root, book):
    """checkpoint 文件损坏 → load 返回空 doc, 不抛."""
    path = pv2.checkpoint_path(book)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ invalid json ::}", encoding="utf-8")
    v2 = pv2.PipelineV2()
    doc = v2.load(book)
    assert doc.chapters == {}


# ── 4-12. FSM transitions ──────────────────────────────────────────────

def test_transition_pending_to_running(tmp_projects_root, book):
    """PENDING → RUNNING 合法."""
    v2 = pv2.PipelineV2()
    sc = v2.transition(book, 1, "context", "RUNNING")
    assert sc.status == "RUNNING"
    assert sc.started_at is not None


def test_transition_running_to_done(tmp_projects_root, book):
    """RUNNING → DONE 合法, 自动设 ended_at."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    sc = v2.transition(book, 1, "context", "DONE", artifacts={"context_file": "x.json"})
    assert sc.status == "DONE"
    assert sc.ended_at is not None
    assert sc.artifacts == {"context_file": "x.json"}
    assert sc.error is None  # DONE 清空 error


def test_transition_running_to_failed(tmp_projects_root, book):
    """RUNNING → FAILED, 保留 error."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "extract", "RUNNING")
    sc = v2.transition(book, 1, "extract", "FAILED", error="JSON parse error")
    assert sc.status == "FAILED"
    assert sc.error == "JSON parse error"


def test_transition_done_to_running_for_rerun(tmp_projects_root, book):
    """DONE → RUNNING (rerun)."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    sc = v2.transition(book, 1, "context", "RUNNING")
    assert sc.status == "RUNNING"


def test_transition_pending_to_skipped_raises(tmp_projects_root, book):
    """PENDING → SKIPPED 是合法的 (允许主动 skip 未跑的 stage)."""
    v2 = pv2.PipelineV2()
    sc = v2.transition(book, 1, "self_check", "SKIPPED", artifacts={"skip_reason": "skip from pending"})
    assert sc.status == "SKIPPED"


def test_transition_done_to_done_raises(tmp_projects_root, book):
    """DONE → DONE 非法."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    with pytest.raises(pv2.PipelineError) as exc:
        v2.transition(book, 1, "context", "DONE")
    assert exc.value.code == ErrorCode.GENERIC
    assert "非法 FSM 转换" in exc.value.message


def test_transition_unknown_stage_raises(tmp_projects_root, book):
    """未知 stage → INVALID_ARGS."""
    v2 = pv2.PipelineV2()
    with pytest.raises(pv2.PipelineError) as exc:
        v2.transition(book, 1, "no_such_stage", "RUNNING")
    assert exc.value.code == ErrorCode.INVALID_ARGS


def test_transition_unknown_state_raises(tmp_projects_root, book):
    """未知 stage state → INVALID_ARGS."""
    v2 = pv2.PipelineV2()
    with pytest.raises(pv2.PipelineError) as exc:
        v2.transition(book, 1, "context", "NO_SUCH_STATE")
    assert exc.value.code == ErrorCode.INVALID_ARGS


def test_get_stage_state_returns_string(tmp_projects_root, book):
    """get_stage_state 返回字符串."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    assert v2.get_stage_state(book, 1, "context") == "RUNNING"
    assert v2.get_stage_state(book, 1, "writing") == "PENDING"


# ── 13-17. Skip API ─────────────────────────────────────────────────────

def test_skip_pending_stage(tmp_projects_root, book):
    """PENDING → SKIPPED 直接."""
    v2 = pv2.PipelineV2()
    sc = v2.skip_stage(book, 1, "self_check", reason="manual skip")
    assert sc.status == "SKIPPED"
    assert sc.artifacts.get("skip_reason") == "manual skip"


def test_skip_failed_stage(tmp_projects_root, book):
    """FAILED → SKIPPED, 不抛."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "extract", "RUNNING")
    v2.transition(book, 1, "extract", "FAILED", error="x")
    sc = v2.skip_stage(book, 1, "extract")
    assert sc.status == "SKIPPED"


def test_skip_done_stage(tmp_projects_root, book):
    """DONE → SKIPPED (允许跳过已完成)."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    sc = v2.skip_stage(book, 1, "context")
    assert sc.status == "SKIPPED"


def test_skip_running_stage_raises(tmp_projects_root, book):
    """RUNNING → SKIPPED 非法."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "writing", "RUNNING")
    with pytest.raises(pv2.PipelineError) as exc:
        v2.skip_stage(book, 1, "writing")
    assert "正在 RUNNING" in exc.value.message


# ── 18-22. Rerun API ────────────────────────────────────────────────────

def test_rerun_resets_downstream(tmp_projects_root, book):
    """rerun from_stage: 下游全 PENDING."""
    v2 = pv2.PipelineV2()
    # 模拟 ch 1 全完成
    for s in pv2.STAGES:
        v2.transition(book, 1, s, "RUNNING")
        v2.transition(book, 1, s, "DONE")

    v2.rerun_from(book, 1, "extract")

    ch_doc = v2.get_chapter(book, 1)
    # 上游 context/writing 保留 DONE
    assert ch_doc.stages["context"].status == "DONE"
    assert ch_doc.stages["writing"].status == "DONE"
    # 下游 extract 之后全 PENDING
    for s in ["extract", "summary", "state", "self_check", "done"]:
        assert ch_doc.stages[s].status == "PENDING", f"{s} should be PENDING"


def test_rerun_from_first_stage(tmp_projects_root, book):
    """rerun from context: 全部 reset."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "writing", "RUNNING")
    v2.transition(book, 1, "writing", "DONE")

    v2.rerun_from(book, 1, "context")
    ch_doc = v2.get_chapter(book, 1)
    for s in pv2.STAGES:
        assert ch_doc.stages[s].status == "PENDING"


def test_rerun_from_last_stage_no_op(tmp_projects_root, book):
    """rerun from done (最后阶段): 只 reset done 本身."""
    v2 = pv2.PipelineV2()
    for s in pv2.STAGES:
        v2.transition(book, 1, s, "RUNNING")
        v2.transition(book, 1, s, "DONE")

    v2.rerun_from(book, 1, "done")
    ch_doc = v2.get_chapter(book, 1)
    assert ch_doc.stages["context"].status == "DONE"
    assert ch_doc.stages["done"].status == "PENDING"


def test_rerun_unknown_stage_raises(tmp_projects_root, book):
    """rerun 不存在的 stage → INVALID_ARGS."""
    v2 = pv2.PipelineV2()
    with pytest.raises(pv2.PipelineError) as exc:
        v2.rerun_from(book, 1, "no_such")
    assert exc.value.code == ErrorCode.INVALID_ARGS


# ── 23-26. 状态汇总 ─────────────────────────────────────────────────────

def test_pipeline_view_returns_all_stages(tmp_projects_root, book):
    """get_pipeline_view 返回 7 个 stage 列表."""
    v2 = pv2.PipelineV2()
    view = v2.get_pipeline_view(book, 1)
    assert len(view["stages"]) == 7
    assert [s["name"] for s in view["stages"]] == pv2.STAGES


def test_pipeline_view_current_stage(tmp_projects_root, book):
    """current_stage = 第一个非 DONE/SKIPPED 的 stage."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    v2.transition(book, 1, "writing", "RUNNING")
    view = v2.get_pipeline_view(book, 1)
    assert view["current_stage"] == "writing"
    assert view["is_complete"] is False


def test_pipeline_view_is_complete(tmp_projects_root, book):
    """所有 stage DONE 或 SKIPPED → is_complete=True."""
    v2 = pv2.PipelineV2()
    for s in pv2.STAGES[:-1]:
        v2.transition(book, 1, s, "RUNNING")
        v2.transition(book, 1, s, "DONE")
    v2.skip_stage(book, 1, "done")  # 用 skip 模拟 done
    view = v2.get_pipeline_view(book, 1)
    assert view["is_complete"] is True
    assert view["current_stage"] is None


def test_pipeline_view_failed_stage(tmp_projects_root, book):
    """failed_stage = 第一个 FAILED 的 stage."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    v2.transition(book, 1, "writing", "RUNNING")
    v2.transition(book, 1, "writing", "DONE")
    v2.transition(book, 1, "extract", "RUNNING")
    v2.transition(book, 1, "extract", "FAILED", error="JSON parse error")
    view = v2.get_pipeline_view(book, 1)
    assert view["failed_stage"] == "extract"
    assert view["current_stage"] == "extract"  # FAILED 也算"未完成", 是 current


# ── 27-30. 多章 + 边界 ──────────────────────────────────────────────────

def test_multiple_chapters_independent(tmp_projects_root, book):
    """ch 1 和 ch 2 互不影响."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    v2.transition(book, 2, "context", "RUNNING")
    # ch 2 context RUNNING, ch 1 context DONE
    assert v2.get_stage_state(book, 1, "context") == "DONE"
    assert v2.get_stage_state(book, 2, "context") == "RUNNING"


def test_atomic_save_no_partial_file(tmp_projects_root, book):
    """atomic write: 不留 .tmp 残留."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    parent = pv2.checkpoint_path(book).parent
    tmp_files = list(parent.glob(".pipeline_checkpoints.json.*.tmp"))
    assert tmp_files == []


def test_v1_state_json_unchanged(tmp_projects_root, book):
    """v2 不写 v1 的 .pipeline_state.json."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2_path = pv2.checkpoint_path(book)
    assert v2_path.exists()
    # v1 文件不应被创建
    v1_path = pv2.storage.project_root(book) / ".pipeline_state.json"
    assert not v1_path.exists()


def test_skip_then_rerun_chain(tmp_projects_root, book):
    """skip → rerun → 重新 PENDING."""
    v2 = pv2.PipelineV2()
    v2.skip_stage(book, 1, "self_check", reason="test")
    assert v2.get_stage_state(book, 1, "self_check") == "SKIPPED"

    v2.rerun_from(book, 1, "self_check")
    assert v2.get_stage_state(book, 1, "self_check") == "PENDING"


def test_skip_preserves_skip_reason_in_artifacts(tmp_projects_root, book):
    """skip 带 reason → artifacts 保留 skip_reason."""
    v2 = pv2.PipelineV2()
    sc = v2.skip_stage(book, 1, "extract", reason="manual review ok")
    ch_doc = v2.get_chapter(book, 1)
    assert ch_doc.stages["extract"].artifacts.get("skip_reason") == "manual review ok"


def test_concurrent_save_last_write_wins(tmp_projects_root, book):
    """并发 save: 后写覆盖先写 (last-write-wins, 文档化)."""
    v2 = pv2.PipelineV2()
    # 模拟: save A → save B → load 应该是 B
    doc_a = pv2.CheckpointDoc(book=book)
    ch_a = pv2.ChapterCheckpoint(book=book, chapter=1)
    ch_a.stages["context"].status = "RUNNING"
    doc_a.chapters[1] = ch_a
    v2.save(doc_a)

    ch_b = pv2.ChapterCheckpoint(book=book, chapter=1)
    ch_b.stages["context"].status = "DONE"
    v2.save_chapter(book, ch_b)

    loaded = v2.get_chapter(book, 1)
    assert loaded.stages["context"].status == "DONE"


def test_singleton_get_v2():
    """get_v2() 返回单例."""
    a = pv2.get_v2()
    b = pv2.get_v2()
    assert a is b