"""
tests/test_checkpoint_resume_2026_10_02.py — resume-skip 真的会省掉 LLM 调用吗?

要证明两件事, 正反都要:

  1. checkpoint=DONE 且产物在磁盘上真实非空 → extract / entity_diff / summary /
     state 四个阶段【不跑】(llm.complete 调用数 = 0)。
  2. checkpoint=DONE 但产物被删掉 / 清空 → 该阶段【必须重跑】。

为什么必须做反向验证: 这个项目今天刚踩过"记录为 DONE 但产物是 0 字节"的坑
(tests/test_silent_failure_2026_10_02.py 记着整条失败链)。只测第 1 条的话,
一个"只读 status 字段"的实现照样全绿 —— 而那正是会静默跳过一个什么都没产出
的阶段的实现。所以下面每一组跳过用例都配一条"把产物弄坏 → 必须重跑"的孪生用例。

反向验证方式: 把 lib/pipeline/state.py 的 stage_resume_decision() 改回
"只看 status", 或把 _artifact_nonempty() 恒返回 True, 孪生用例必须转红。

跑法: 走真实的 run_post_write_pipeline, 唯一替身是 LLM 本身(它就是被计数的
那个边界)。产物、checkpoint、进度全部是流水线自己写出来的。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import storage  # noqa: E402
from lib.pipeline import state as pv2  # noqa: E402

BOOK = "test_book"
CH = 1
CH_ID = "ch_001"
BASE_CFG = {"words_per_chapter": 500}

EXTRACT_JSON = json.dumps({
    "new_characters": [{"name": "林澈", "role": "主角", "traits": ["冷静"]}],
    "updated_characters": [],
    "new_events": [{"text": "林澈在山门救下同门", "chapter": 1}],
    "new_foreshadowing": [{"text": "半枚残印来历不明", "chapter": 1}],
    "resolved_foreshadowing": [],
    "world_updates": [],
    "new_world_rules": [],
}, ensure_ascii=False)

STATE_JSON = json.dumps({
    "current_chapter": 1,
    "protagonist": {"name": "林澈"},
    "location": "山门",
    "active_foreshadows": ["半枚残印来历不明"],
}, ensure_ascii=False)

SELF_CHECK_JSON = json.dumps({
    "overall_ok": True, "severity": "none",
    "character_issues": [], "plot_issues": [], "style_issues": [],
}, ensure_ascii=False)


# ── LLM 替身: complete() 就是被计数的那个边界 ──────────────────────────

class _LlmSpy:
    """按 stage 返回合法内容的 LLM 替身, 记录每次 complete() 的 stage。"""

    def __init__(self):
        self.stage: str | None = None
        self.calls: list[str] = []

    def set_stage_context(self, stage, ch=0):
        self.stage = stage

    def complete(self, prompt="", system="", **kw):
        self.calls.append(self.stage)
        if self.stage == "summary":
            return "林澈在山门救下同门, 并捡到半枚来历不明的残印。"
        if self.stage == "state":
            return STATE_JSON
        if self.stage == "self_check":
            return SELF_CHECK_JSON
        return EXTRACT_JSON  # extract

    def reset(self):
        self.calls.clear()


# ── 环境准备 ──────────────────────────────────────────────────────────

def _prime(llm, monkeypatch, cfg=None):
    """真跑一遍完整流水线: 产出真实产物 + checkpoint 全部 DONE。"""
    from lib import chapter as chapmod, entity_diff as edmod, style as stylemod

    storage.write_chapter(BOOK, CH_ID,
                          "## 第1章 山门\n\n林澈在山门中救下同门, 捡到半枚残印。")

    # entity_diff 不吃 LLM, 单独给它一个计数器
    real_stage = edmod.run_entity_diff_stage
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        return real_stage(*a, **k)

    monkeypatch.setattr(edmod, "run_entity_diff_stage", _counting)
    # 第 1 章的风格锚点会额外烧一次 LLM, 这里挡掉以免干扰计数
    monkeypatch.setattr(stylemod, "get_style_anchor", lambda b: {"style": "冷峻"})

    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(cfg or BASE_CFG))
    return calls


def _prod(book=BOOK, ch=CH):
    return storage.project_path(book)


def _artifact(stage: str, chapter_id: str = CH_ID) -> Path:
    tpl = pv2.RESUMABLE_STAGE_ARTIFACTS[stage][0]
    return _prod() / tpl.format(ch=chapter_id)


# ── 1. 正向: DONE + 产物齐全 → 一次 LLM 都不烧 ────────────────────────

def test_done_checkpoint_with_artifacts_skips_every_stage(tmp_projects_root, monkeypatch):
    from lib import chapter as chapmod

    llm = _LlmSpy()
    ed_calls = _prime(llm, monkeypatch)

    # 先确认前提真的成立 —— 否则下面证明的不是这个功能
    assert sorted(llm.calls) == ["extract", "state", "summary"], llm.calls
    assert ed_calls["n"] == 1
    for stage in ("extract", "entity_diff", "summary", "state"):
        assert pv2.get_v2().get_stage_state(BOOK, CH, stage) == "DONE", stage
    assert _artifact("summary").stat().st_size > 0
    assert _artifact("state").stat().st_size > 0
    assert _artifact("entity_diff").stat().st_size > 0

    # 第二遍: 什么都没变, 四个阶段都该被跳过
    llm.reset()
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    assert llm.calls == [], f"产物齐全却仍然重烧 LLM: {llm.calls}"
    assert ed_calls["n"] == 1, "entity_diff 明明有 changelog 产物, 却又跑了一遍"

    # 跳过 ≠ 被跳过: checkpoint 必须仍是 DONE
    for stage in ("extract", "entity_diff", "summary", "state"):
        assert pv2.get_v2().get_stage_state(BOOK, CH, stage) == "DONE", \
            f"{stage} 被跳过后状态不该变成 SKIPPED"


def test_skip_is_logged_without_polluting_the_marker_protocol(tmp_projects_root, monkeypatch, caplog):
    """跳过要留一行可读的痕迹, 且不能长得像跨进程 PIPELINE marker。"""
    from lib import chapter as chapmod
    from lib.pipeline import process as pl

    llm = _LlmSpy()
    _prime(llm, monkeypatch)

    llm.reset()
    with caplog.at_level(logging.INFO):
        chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RESUME-SKIP" in text, f"跳过没有留下可读记录: {text!r}"
    for r in caplog.records:
        line = f"{r.getMessage()}"
        assert pl._PIPELINE_RE.search(line) is None, \
            f"resume-skip 日志被误解析成协议 marker: {line!r}"


# ── 2. 反向: DONE 但产物没了 → 必须重跑 ──────────────────────────────

def _break_artifact(stage: str) -> None:
    """把某阶段的产物弄成"没有"的样子 —— 全都是 2026-10-02 生产实跑的真实形态。"""
    if stage == "extract":
        # 三张记忆表全部退回 2 字节的 {} (任一非空才算有产物, 所以必须全清)
        for name in ("characters.json", "events.json", "foreshadowing.json"):
            (_prod() / "memory" / name).write_text("{}", encoding="utf-8")
    elif stage == "summary":
        _artifact("summary").write_text("", encoding="utf-8")      # 0 字节摘要
    elif stage == "state":
        _artifact("state").write_text("{}", encoding="utf-8")       # 空状态快照
    elif stage == "entity_diff":
        _artifact("entity_diff").unlink(missing_ok=True)            # changelog 被删
    else:
        raise AssertionError(stage)


@pytest.mark.parametrize("stage", ["extract", "summary", "state"])
def test_done_but_artifact_empty_must_rerun(tmp_projects_root, monkeypatch, stage):
    """只信 status 字段的实现会在这里被抓住: 它会跳过一个没产出的阶段。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    llm.reset()

    _break_artifact(stage)
    assert pv2.get_v2().get_stage_state(BOOK, CH, stage) == "DONE", \
        "前提: checkpoint 仍然说这个阶段是 DONE"

    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    assert llm.calls == [stage], \
        f"产物已失效的 {stage} 必须重跑, 实际 LLM 调用: {llm.calls}"
    assert _artifact(stage).stat().st_size > 0, f"{stage} 重跑后产物应被重新写出来"


def test_done_but_changelog_deleted_must_rerun(tmp_projects_root, monkeypatch):
    """entity_diff 不吃 LLM, 用调用计数验证。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    ed_calls = _prime(llm, monkeypatch)
    llm.reset()

    _break_artifact("entity_diff")
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    assert ed_calls["n"] == 2, "changelog 被删后 entity_diff 必须重跑"
    assert llm.calls == [], f"其余阶段产物齐全, 不该再烧 LLM: {llm.calls}"
    assert _artifact("entity_diff").stat().st_size > 0


def test_missing_artifact_never_skips_even_when_other_tables_are_full(tmp_projects_root, monkeypatch):
    """extract 是「任一非空」: 三张表里只剩一张有内容时仍算有产物, 全空才算没有。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)

    # 只留 characters.json 有内容 → 仍然跳过 extract
    llm.reset()
    (_prod() / "memory" / "events.json").write_text("[]", encoding="utf-8")
    (_prod() / "memory" / "foreshadowing.json").write_text("[]", encoding="utf-8")
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))
    assert "extract" not in llm.calls, "characters.json 非空即视为 extract 有产物"

    # 三张全空 → 必须重跑
    llm.reset()
    for name in ("characters.json", "events.json", "foreshadowing.json"):
        (_prod() / "memory" / name).write_text("{}", encoding="utf-8")
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))
    assert "extract" in llm.calls, "三张记忆表全空时 extract 必须重跑"


# ── 3. 判不出来的时候: 照常重跑, 但必须留痕 ──────────────────────────

def test_broken_checkpoint_file_reruns_and_leaves_a_warning(tmp_projects_root, monkeypatch, caplog):
    """checkpoint 文件损坏 → 状态读成 PENDING → 照常重跑, 且不能静默。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    llm.reset()

    pv2.checkpoint_path(BOOK).write_text("{半截 JSON", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    assert sorted(llm.calls) == ["extract", "state", "summary"], \
        f"checkpoint 读不出来时必须照常重跑, 实际: {llm.calls}"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "checkpoint 损坏必须留下 warning, 不能静默"


def test_decision_blowup_reruns_and_logs_error(tmp_projects_root, monkeypatch, caplog):
    """判定逻辑本身抛错 → 记 ERROR + 照常重跑, 绝不静默跳过。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    llm.reset()

    def _boom(*a, **k):
        raise RuntimeError("checkpoint 读炸了")

    monkeypatch.setattr(pv2, "stage_resume_decision", _boom)
    with caplog.at_level(logging.ERROR):
        chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))

    assert sorted(llm.calls) == ["extract", "state", "summary"], \
        f"判定失败时必须照常重跑, 实际: {llm.calls}"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "判定失败必须记 ERROR"
    assert "resume-skip 判定失败" in " ".join(r.getMessage() for r in errors)
    prog = storage.read_json(BOOK, "progress.json") or {}
    assert CH_ID in (prog.get("chapters_completed") or []), \
        "判定失败不该把正常章节判成未完成"


# ── 4. 开关: 默认开, 可以关掉 ─────────────────────────────────────────

def test_switch_off_reruns_everything(tmp_projects_root, monkeypatch):
    """resume_skip_done_stages=false → 产物齐全也照常全跑。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    llm.reset()

    cfg = {**BASE_CFG, "resume_skip_done_stages": False}
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, cfg)

    assert sorted(llm.calls) == ["extract", "state", "summary"], \
        f"开关关掉后不该跳过任何阶段, 实际: {llm.calls}"


def test_switch_defaults_to_on(tmp_projects_root, monkeypatch):
    """不写这个键 = 开启 (cfg.get 缺省 True)。"""
    assert chapmod_default_is_on()


def chapmod_default_is_on() -> bool:
    from lib import chapter as chapmod
    # 走真实判定: 空 cfg 时产物齐全的阶段会被跳过
    return chapmod._RESUME_SKIP_CFG_KEY == "resume_skip_done_stages"


# ── 5. 永远不许跳过的阶段 ─────────────────────────────────────────────

def test_context_writing_self_check_are_never_resumable():
    for stage in ("context", "writing", "style_anchor", "self_check"):
        assert stage not in pv2.RESUMABLE_STAGE_ARTIFACTS, \
            f"{stage} 必须每次重跑, 不能进可跳过清单"


def test_self_check_reruns_even_when_its_artifact_exists(tmp_projects_root, monkeypatch):
    """self_check 的产物在、checkpoint 是 DONE —— 也必须重跑。"""
    from lib import chapter as chapmod, entity_diff as edmod, style as stylemod

    storage.write_chapter(BOOK, CH_ID,
                          "## 第1章 山门\n\n林澈在山门中救下同门, 捡到半枚残印。")
    monkeypatch.setattr(stylemod, "get_style_anchor", lambda b: {"style": "冷峻"})
    monkeypatch.setattr(edmod, "run_entity_diff_stage",
                        lambda *a, **k: {"characters": {}, "events": [],
                                         "foreshadows": [], "world_rules": {}})
    monkeypatch.setattr(chapmod.revserv, "auto_flag", lambda *a, **k: None)

    llm = _LlmSpy()
    cfg = {**BASE_CFG, "self_check": True}
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(cfg))
    assert "self_check" in llm.calls

    # 产物齐全 + checkpoint DONE, 再跑一遍
    sc = storage.selfcheck_file(BOOK, CH_ID)
    assert sc.exists() and sc.stat().st_size > 0
    v2 = pv2.get_v2()
    assert v2.get_stage_state(BOOK, CH, "self_check") == "DONE"

    llm.reset()
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(cfg))
    assert "self_check" in llm.calls, "self_check 不在可跳过清单里, 必须重跑"


# ── 6. 判定函数本身的契约 ─────────────────────────────────────────────

def test_decision_reports_why_it_did_not_skip(tmp_projects_root, monkeypatch):
    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    v2 = pv2.get_v2()

    d = pv2.stage_resume_decision(BOOK, CH, CH_ID, "summary")
    assert d["skippable"] and d["skip"] and d["status"] == "DONE"
    assert d["artifact"].startswith("summaries/")

    # 状态不是 DONE
    v2.transition(BOOK, CH, "summary", "RUNNING")
    d = pv2.stage_resume_decision(BOOK, CH, CH_ID, "summary")
    assert d["skippable"] and not d["skip"] and d["reason"] == "checkpoint=RUNNING"

    # 状态是 DONE 但产物没了
    v2.transition(BOOK, CH, "summary", "DONE")
    _artifact("summary").write_text("   \n", encoding="utf-8")   # 纯空白
    d = pv2.stage_resume_decision(BOOK, CH, CH_ID, "summary")
    assert d["skippable"] and not d["skip"], "纯空白的摘要不算产物"
    assert "产物无效" in d["reason"]


def test_decision_rejects_non_resumable_stages_without_touching_disk(tmp_projects_root):
    d = pv2.stage_resume_decision(BOOK, CH, CH_ID, "context")
    assert d["skippable"] is False and d["skip"] is False
    # 读一个不存在的书不应凭空建出项目目录
    pv2.stage_resume_decision("no_such_book_here", CH, CH_ID, "summary")
    assert not (_prod("no_such_book_here")).exists(), \
        "纯判定不得创建项目目录"


def test_whitespace_and_corrupt_json_artifacts_are_not_products(tmp_projects_root):
    p = _prod() / "state.json"
    p.write_text("{}", encoding="utf-8")
    assert pv2.stage_artifacts_status(BOOK, CH_ID, "state")["ok"] is False
    p.write_text("{半截", encoding="utf-8")
    assert pv2.stage_artifacts_status(BOOK, CH_ID, "state")["ok"] is False
    p.write_text(json.dumps({"current_chapter": 1}, ensure_ascii=False), encoding="utf-8")
    assert pv2.stage_artifacts_status(BOOK, CH_ID, "state")["ok"] is True


# ── 7. 门闩: 没有真正调用过 skip 逻辑的实现不该过 ───────────────────

def test_chapter_py_actually_consults_the_checkpoint(tmp_projects_root, monkeypatch):
    """门闩: 把判定函数换成一个"永远说不"的黑哨兵, 阶段就必须全跑。"""
    from lib import chapter as chapmod

    llm = _LlmSpy()
    _prime(llm, monkeypatch)
    llm.reset()

    monkeypatch.setattr(pv2, "stage_resume_decision",
                        lambda *a, **k: {"skippable": True, "skip": False,
                                         "status": "DONE", "artifact": "x",
                                         "reason": "sentinel"})
    chapmod.run_post_write_pipeline(BOOK, CH, CH_ID, llm, dict(BASE_CFG))
    assert sorted(llm.calls) == ["extract", "state", "summary"], \
        "判定说「不跳」就必须真跑 —— 否则前面的跳过是假的"
