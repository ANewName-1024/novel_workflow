"""tests/test_silent_failure_2026_10_02.py — 流水线不能"静默地成功"。

2026-10-02 在生产服务器上实跑整条流水线(init → outline → write → review →
export)得到的实测结论:

    每个阶段都报 DONE、整条命令 exit 0、status 里章节列表打勾,
    但 characters.json=2 字节 {}、events.json=2 字节 []、
    summaries/ch_001.txt=0 字节、reviews/ch_001.md=0 字节,
    而 audit.log 里写着 "章节无自检数据，默认通过"。

失败链(每一步单独看都像"正常"):

    1. 推理模型把 max_tokens 全花在思维链上 -> API 正常返回 200, 正文为空
       实测: max_tokens=4096 时 finish_reason=length / reasoning_tokens=4096
             / content_len=0; 换成 16384 就有 3236 字符正文
    2. lib/llm.py 写的是 `resp.choices[0].message.content or ""`
       —— 空 content 被 or "" 吞成空串, 且**从不检查 finish_reason**
    3. extract 的 json.loads("") 抛错 -> catch -> 返回全空 dict
    4. 阶段状态照旧写 DONE
    5. 章节照旧 mark_chapter_completed + 报 "✓ 完成"

所以本文件把每一层都钉住。反向验证方式: 把 lib/llm.py 的 `_extract_text`
改回 `or ""`、或把 chapter.py 的闸门删掉, 对应用例必须转红。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.llm import LLM, LLMEmptyResponse, EMPTY_RETRY_CEILING  # noqa: E402
from lib import entity_diff as edmod  # noqa: E402


# ── 造一个假的 chat.completion 响应 ─────────────────────────────────────

def _resp(content, finish_reason="stop", reasoning_tokens=None, model="m"):
    """构造 openai SDK 那种形状的响应对象。"""
    details = None
    if reasoning_tokens is not None:
        details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=(reasoning_tokens or 0) + (len(content or "")),
        completion_tokens_details=details,
    )
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )],
        usage=usage,
    )


def _llm(min_max_tokens=0, max_retries=0) -> LLM:
    llm = LLM(model="m", api_base="http://localhost:9999/v1", api_key="k",
              max_retries=max_retries, retry_delay=0.0,
              min_max_tokens=min_max_tokens)
    return llm


# ── 第 1 层: LLM 响应健康检查 ───────────────────────────────────────────

class TestEmptyResponseIsNotSuccess:
    """空正文必须抛错, 而不是被 or "" 吞成空串继续往下传。"""

    def test_none_content_raises(self):
        """content=None 是实跑里真实发生过的那一次(content_len=0)。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp(None, "stop")

        with pytest.raises(LLMEmptyResponse):
            llm.complete("写点什么")

    def test_whitespace_only_content_raises(self):
        """"   " 不是正文。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp("   \n ", "stop")

        with pytest.raises(LLMEmptyResponse):
            llm.complete("写点什么")

    def test_error_message_names_the_real_cause(self):
        """报错必须自解释, 否则排查又要从头猜。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp(
            None, "length", reasoning_tokens=4096)

        with pytest.raises(LLMEmptyResponse) as ei:
            llm.complete("写点什么", max_tokens=4096)
        msg = str(ei.value)
        assert "finish_reason" in msg and "length" in msg
        assert "4096" in msg, "报错里要带上被吃光的预算"
        assert "min_max_tokens" in msg, "报错要指向真正的修法"

    def test_normal_response_passes_through(self):
        """正常返回不能被误伤 —— 这是修复的另一半。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp("正文内容", "stop")

        assert llm.complete("写点什么") == "正文内容"


class TestReasoningBudgetEscalation:
    """finish_reason=length + 空正文 = 思维链吃光了预算, 该换额度重试。"""

    def test_escalates_then_succeeds(self):
        llm = _llm()
        llm.client = MagicMock()
        create = llm.client.chat.completions.create
        create.side_effect = [
            _resp(None, "length", reasoning_tokens=4096),
            _resp("真正的正文", "stop"),
        ]

        assert llm.complete("x", max_tokens=4096) == "真正的正文"
        # 第二次请求的 max_tokens 必须是 4 倍, 否则重发没有意义
        assert create.call_args_list[0].kwargs["max_tokens"] == 4096
        assert create.call_args_list[1].kwargs["max_tokens"] == 16384

    def test_escalation_is_bounded(self):
        """一直空也不能无限升 —— 必须停在上限后报错。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp(
            None, "length", reasoning_tokens=999999)

        with pytest.raises(LLMEmptyResponse):
            llm.complete("x", max_tokens=4096)
        # 4096 -> 16384 -> 65536(封顶) 后不再升
        assert llm.client.chat.completions.create.call_count == 3
        last = llm.client.chat.completions.create.call_args_list[-1]
        assert last.kwargs["max_tokens"] == EMPTY_RETRY_CEILING

    def test_stop_reason_does_not_escalate(self):
        """finish_reason=stop 却空 = 真的没内容, 升级重试是浪费钱。"""
        llm = _llm()
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp(None, "stop")

        with pytest.raises(LLMEmptyResponse):
            llm.complete("x", max_tokens=4096)
        assert llm.client.chat.completions.create.call_count == 1, \
            "非 length 的空响应不该被升级重试"

    def test_escalation_does_not_consume_retry_budget(self):
        """max_retries 是给网络抖动用的, 不该被 token 升级吃掉。"""
        llm = _llm(max_retries=1)
        llm.client = MagicMock()
        llm.client.chat.completions.create.side_effect = [
            _resp(None, "length"),            # 第 1 次: 空
            _resp("终于有正文了", "stop"),    # 第 2 次: 升级后成功
        ]
        assert llm.complete("x", max_tokens=4096) == "终于有正文了"


class TestReasoningTokenFloor:
    """min_max_tokens 下限: 调用点按"正文多少字"拍的数字对推理模型不够用。"""

    def test_floor_lifts_small_requests(self):
        llm = _llm(min_max_tokens=16384)
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp("ok", "stop")
        llm.complete("x", max_tokens=400)          # summary 的老数字
        assert llm.client.chat.completions.create.call_args.kwargs["max_tokens"] == 16384

    def test_floor_never_lowers_a_larger_request(self):
        llm = _llm(min_max_tokens=4096)
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp("ok", "stop")
        llm.complete("x", max_tokens=32768)        # 本来就够大
        assert llm.client.chat.completions.create.call_args.kwargs["max_tokens"] == 32768

    def test_zero_floor_means_no_interference(self):
        """本地小上下文模型不能被凭空抬到 16384 而 context too long。"""
        llm = _llm(min_max_tokens=0)
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp("ok", "stop")
        llm.complete("x", max_tokens=400)
        assert llm.client.chat.completions.create.call_args.kwargs["max_tokens"] == 400

    def test_minimax_provider_ships_with_a_floor(self):
        """内置的 minimax 是推理模型, 必须自带下限而不是靠人记得配。"""
        from lib.llm_providers import resolve_model
        assert resolve_model("minimax")["min_max_tokens"] >= 8192


class TestPingDoesNotEscalate:
    """预检要的是"通不通", 不该因为推理模型而变成一次 65536 token 的请求。"""

    def test_ping_uses_tiny_budget_and_skips_floor(self, monkeypatch):
        llm = _llm(min_max_tokens=16384)
        llm.client = MagicMock()
        llm.client.chat.completions.create.return_value = _resp(None, "length")

        seen = {}
        import lib.llm as llmmod

        class _FakeClient:
            def __init__(self, **kw):
                seen.update(kw)
                self.chat = SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **k: _resp(None, "length")))
        monkeypatch.setattr(llmmod, "OpenAI", _FakeClient)

        out = llm.ping("ping", timeout_sec=7)
        assert out["ok"] is True, "HTTP 有应答就算通 —— 推理模型正文为空是正常的"
        assert seen["timeout"] == 7


# ── 第 2 层: 解析失败不许降级成空结果 ───────────────────────────────────

class TestExtractionFailureIsLoud:
    def test_malformed_raises(self):
        from lib.extract import parse_extraction, ExtractionParseError
        with pytest.raises(ExtractionParseError):
            parse_extraction("模型开始跟我聊天而不是给 JSON")

    def test_valid_empty_arrays_are_still_valid(self):
        """抽出来确实是空的, 是合法结果, 不该被当成失败。"""
        from lib.extract import parse_extraction
        data = parse_extraction('{"new_events": [], "new_characters": []}')
        assert data["new_events"] == []


# ── 第 3 层: 阶段闸门 —— 关键阶段失败就不许记账 ─────────────────────────

class TestCriticalStageGateStopsTheChapter:
    """extract/summary 失败时, 本章不得被标记为完成。"""

    def _cfg(self):
        return {"words_per_chapter": 500}

    def test_extract_failure_prevents_completion(self, tmp_projects_root, monkeypatch):
        from lib import chapter as chapmod, storage
        from lib.llm import LLMEmptyResponse

        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        llm = MagicMock()
        llm.set_stage_context = MagicMock()

        def _boom(*a, **k):
            raise LLMEmptyResponse("length", "m", 4096)
        llm.complete.side_effect = _boom

        with pytest.raises(chapmod.CoherenceStageFailed):
            chapmod.run_post_write_pipeline(
                "test_book", 1, "ch_001", llm, self._cfg())

        # 关键断言: 不能记账
        prog = storage.read_json("test_book", "progress.json") or {}
        assert "ch_001" not in (prog.get("chapters_completed") or []), \
            "extract 失败时章节不得被标记完成 —— 否则它对后续章节隐形"

    def test_summary_failure_prevents_completion(self, tmp_projects_root, monkeypatch):
        from lib import chapter as chapmod, storage, extract as extmod, summary as summod

        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        llm = MagicMock()
        llm.set_stage_context = MagicMock()

        # extract 正常, summary 抛错
        monkeypatch.setattr(extmod, "extract_from_chapter",
                            lambda t, l: {"new_events": [], "new_characters": [],
                                          "new_foreshadowing": []})
        def _sum_boom(*a, **k):
            raise RuntimeError("摘要生成炸了")
        monkeypatch.setattr(summod, "generate_chapter_summary", _sum_boom)

        with pytest.raises(chapmod.CoherenceStageFailed):
            chapmod.run_post_write_pipeline(
                "test_book", 1, "ch_001", llm, self._cfg())

        prog = storage.read_json("test_book", "progress.json") or {}
        assert "ch_001" not in (prog.get("chapters_completed") or [])

    def test_happy_path_still_marks_complete(self, tmp_projects_root, monkeypatch):
        """闸门不能把正常流程也拦下来 —— 修复的另一半。"""
        from lib import chapter as chapmod, storage, extract as extmod, summary as summod
        from lib import state as statemod, entity_diff as edmod2, style as stylemod

        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        llm = MagicMock()
        llm.set_stage_context = MagicMock()
        monkeypatch.setattr(extmod, "extract_from_chapter",
                            lambda t, l: {"new_events": [], "new_characters": [],
                                          "new_foreshadowing": []})
        monkeypatch.setattr(summod, "generate_chapter_summary", lambda *a, **k: "摘要")
        monkeypatch.setattr(statemod, "update_state_after_chapter", lambda *a, **k: None)
        monkeypatch.setattr(edmod2, "snapshot_memory", lambda b: {})
        monkeypatch.setattr(stylemod, "get_style_anchor", lambda b: {"x": 1})
        monkeypatch.setattr(stylemod, "extract_style_anchor", lambda *a, **k: {})

        chapmod.run_post_write_pipeline("test_book", 1, "ch_001", llm, self._cfg())
        prog = storage.read_json("test_book", "progress.json") or {}
        assert "ch_001" in (prog.get("chapters_completed") or []), \
            "一切正常时必须照常记账, 否则闸门就成了新的阻断"


# ── 第 4 层: entity_diff 的 sum() 崩溃 ──────────────────────────────────

class TestEntityDiffSummaryDoesNotExplode:
    """`sum(a, b, c)` 会去迭代第一个 int 参数 —— 这个错误每章必现。"""

    def test_empty_diff_summary_works(self):
        entry = {"entities": {
            "character":  {"added": [], "updated": [], "removed": []},
            "event":      {"added": [], "updated": [], "removed": []},
            "foreshadow": {"added": [], "updated": [], "resolved": [], "removed": []},
            "world_rule": {"added": [], "updated": [], "removed": []},
        }}
        s = edmod.summarize_changes(entry)
        assert s["total_changes"] == 0

    def test_counts_are_summed(self):
        entry = {"entities": {
            "character":  {"added": [1, 2], "updated": [3], "removed": []},
            "event":      {"added": [1], "updated": [], "removed": []},
            "foreshadow": {"added": [], "updated": [], "resolved": [1, 1], "removed": []},
            "world_rule": {"added": [], "updated": [], "removed": []},
        }}
        s = edmod.summarize_changes(entry)
        assert s["total_changes"] == 2 + 1 + 1 + 2, "总数必须真的加起来"

    def test_stage_runs_end_to_end(self, tmp_projects_root):
        """整条 entity_diff 阶段能跑通, 不再 FAILED。"""
        out = edmod.run_entity_diff_stage("test_book", 1, "ch_001", edmod.snapshot_memory("test_book"))
        assert edmod.summarize_changes(out)["total_changes"] == 0


# ── 第 5 层: 缺数据不许当成合格 ─────────────────────────────────────────

class TestMissingDataIsNotAPass:
    def test_chapter_without_selfcheck_goes_to_human_queue(self, tmp_projects_root):
        from lib import storage, review_service as revserv
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv._backfill_fingerprint.pop("test_book", None)

        revserv.backfill_missing_reviews("test_book")
        rec = revserv.get_review("test_book", "ch_001")
        assert rec is not None, "仍应建记录"
        assert rec["status"] == revserv.REVIEW_STATUS["PENDING_REVIEW"], \
            "缺自检数据不等于合格 —— 「因为没检查过所以通过」是最坏的降级"
        assert not rec.get("auto_result"), "不得伪造自检结论"


# ── 第 6 层: 进度记账不能被陈旧对象覆盖 ─────────────────────────────────

class TestWriteDoesNotClobberProgress:
    """cmd_write 收尾曾把循环开始前的旧 prog 对象整个写回 progress.json。

    2026-10-02 部署后实跑抓到: 章节写完 1544 字、extract 抽到 5 事件/5 伏笔/
    3 角色、所有产物齐全, progress.json 却是
        {"phase": "writing", "current_chapter": 0, "chapters_completed": []}
    —— mark_chapter_completed 记的账被循环结束时的旧对象整个盖掉了。
    状态说没写, 产物说写好了, 而且进度条会一直显示 0/N。
    """

    def _args(self, book, chapters="1"):
        import argparse
        return argparse.Namespace(
            book=book, chapters=chapters, auto_continue=False,
            auto_rewrite_on_critical=False, self_check_strict=False,
        )

    def test_completed_chapter_survives_the_final_write(self, tmp_projects_root,
                                                        monkeypatch, capsys):
        from lib import storage, chapter as chapmod
        import novel

        storage.write_json("test_book", "outline.json", {
            "meta": {"target_chapters": 1},
            "volumes": [], "chapters": [{"id": "ch_001", "title": "一", "summary": "s"}],
        })
        storage.write_json("test_book", "progress.json", {
            "phase": "init", "current_chapter": 0, "total_chapters": 1,
            "chapters_completed": [],
        })

        # 替身要**真的记账**, 否则测的不是那个覆盖 bug
        def _fake_write(book, num, llm, ol, cfg_override=None):
            storage.mark_chapter_completed(book, f"ch_{num:03d}", num)
            return "## 第一章\n\n正文"

        monkeypatch.setattr(chapmod, "write_chapter", _fake_write)
        monkeypatch.setattr(novel, "get_llm", lambda **kw: _StubLLM())

        novel.cmd_write(self._args("test_book"))

        prog = storage.read_json("test_book", "progress.json") or {}
        assert "ch_001" in (prog.get("chapters_completed") or []), \
            "cmd_write 收尾把 mark_chapter_completed 记的账覆盖掉了"
        assert prog.get("current_chapter") == 1
        assert prog.get("phase") == "done", "全部写完时 phase 应为 done"

    def test_partial_run_keeps_phase_writing(self, tmp_projects_root, monkeypatch):
        """没写完就不能标 done —— 同样不能覆盖已记账的章节。"""
        from lib import storage, chapter as chapmod
        import novel

        storage.write_json("test_book", "outline.json", {
            "meta": {"target_chapters": 5},
            "volumes": [], "chapters": [{"id": "ch_001", "title": "一", "summary": "s"}],
        })
        storage.write_json("test_book", "progress.json", {
            "phase": "init", "current_chapter": 0, "total_chapters": 5,
            "chapters_completed": [],
        })

        def _fake_write(book, num, llm, ol, cfg_override=None):
            storage.mark_chapter_completed(book, f"ch_{num:03d}", num)
            return "## 第一章\n\n正文"

        monkeypatch.setattr(chapmod, "write_chapter", _fake_write)
        monkeypatch.setattr(novel, "get_llm", lambda **kw: _StubLLM())

        novel.cmd_write(self._args("test_book"))

        prog = storage.read_json("test_book", "progress.json") or {}
        assert "ch_001" in (prog.get("chapters_completed") or [])
        assert prog.get("phase") == "writing", "5 章只写 1 章, phase 不该是 done"


class _StubLLM:
    """cmd_write 只需要 describe / 上下文估算, 不发真实请求。"""
    model = "stub"
    api_base = "http://stub/v1"

    def describe(self):
        return {"provider": "stub", "model": self.model, "api_base": self.api_base}

    def estimate_input_tokens(self, text):
        return len(text) // 2

    def set_stage_context(self, *a, **k):
        pass

