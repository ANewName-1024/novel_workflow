"""
2026-10-01 一致性 / 资源 / 输入校验修复的反向防线
===============================================
"""
import json
import os
import re
import pytest


# ── self_check 与世界规则扫描曾写同一个文件 ───────────────────────────────

class TestWorldRuleScanDoesNotClobberSelfCheck:
    """world_rule_consistency 与 self_check_chapter 过去都写
    self_checks/<ch>.json, 而两者结构完全不同。

    Web 端 entities.py:192 用 save=True 调一致性扫描 -> 覆盖自检报告 ->
    backfill_missing_reviews 把它当自检结果喂给 auto_flag -> severity 取不到
    -> 落到 PENDING_REVIEW。用户点一下「检查一致性」, 该章自检结论就没了。

    而 test_world_rule_consistency.py:71 曾把这个覆盖行为锁成期望值。
    """

    def test_two_results_use_different_files(self, tmp_projects_root):
        from lib import storage
        a = storage.selfcheck_file("test_book", "ch_001")
        b = storage.world_rule_file("test_book", "ch_001")
        assert a != b, "自检与世界规则扫描仍在写同一个文件"
        assert a.name.endswith("ch_001.json")
        assert b.name.endswith("ch_001.world_rules.json")

    def test_world_rule_scan_preserves_self_check(self, tmp_projects_root):
        from lib import self_check, storage
        from lib.entity import WorldRule
        from lib.memory import EntityStore

        # 一致性扫描要求章节正文存在
        storage.write_chapter("test_book", "ch_001", "## 第一章\n\n主角服丹药提升品级。\n")

        sentinel = {"character_inconsistency": [], "severity": "critical",
                    "overall_ok": False, "marker": "ORIGINAL_SELF_CHECK"}
        sc_file = storage.selfcheck_path("test_book", "ch_001")
        sc_file.write_text(json.dumps(sentinel, ensure_ascii=False), encoding="utf-8")

        EntityStore("test_book").add_world_rule(
            WorldRule(name="规则X", constraints=["约束"]))

        class FakeLLM:
            calls = []

            def complete(self, **kw):
                return json.dumps({"violations": [], "overall_ok": True,
                                   "summary": "OK"})

        self_check.world_rule_consistency("test_book", "ch_001", llm=FakeLLM())

        after = json.loads(sc_file.read_text(encoding="utf-8"))
        assert after == sentinel, \
            "跑一次一致性扫描就把自检报告覆盖了 —— severity 结论随之消失"
        assert storage.world_rule_file("test_book", "ch_001").exists(), \
            "一致性扫描自己的结果应当另存一份"


# ── 进程启动的文件句柄泄漏 ────────────────────────────────────────────────

class TestNoFileHandleLeak:
    """process.start() 过去只在 except 分支里 log_fp.close(),
    成功路径没人关 —— 每 start() 一次泄漏一个父进程句柄。"""

    def test_log_fp_closed_on_success(self, tmp_projects_root, monkeypatch):
        import subprocess as sp
        from lib.pipeline import process as pl

        opened = []
        real_open = open
        closed = []

        class TrackingFP:
            def __init__(self, f):
                self._f = f
            def __getattr__(self, n):
                return getattr(self._f, n)
            def fileno(self):
                return self._f.fileno()
            def close(self):
                closed.append(True)
                return self._f.close()

        def fake_open(path, mode="r", *a, **k):
            if "ab" in mode:
                fp = TrackingFP(real_open(path, mode, *a, **k))
                opened.append(fp)
                return fp
            return real_open(path, mode, *a, **k)

        class FakeProc:
            pid = 424242
            def poll(self):
                return None
        monkeypatch.setattr(pl, "open", fake_open, raising=False)
        monkeypatch.setattr(sp, "Popen", lambda *a, **k: FakeProc())
        monkeypatch.setattr(pl, "_write_state", lambda *a, **k: None)

        pl.PipelineRunner().start("test_book", 1)
        assert opened, "没捕获到 log_fp"
        assert closed, "成功路径没有关闭 log_fp —— 每次 start 泄漏一个句柄"


# ── STAGES 两份副本已分叉 ────────────────────────────────────────────────

class TestSingleSourceOfStages:
    """process.py 曾有一份 7 个阶段的 STAGES(缺 entity_diff),
    state.py 是 8 个。当前 process 那份无引用, 尚未出事, 但是定时炸弹。"""

    def test_process_reuses_state_stages(self):
        from lib.pipeline import process as pl
        from lib.pipeline import state as st
        assert pl.STAGES is st.STAGES, \
            "process.STAGES 与 state.STAGES 必须是同一个对象, 不能各写一份"

    def test_no_literal_stage_list_in_process(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "pipeline", "process.py")
        src = open(path, "rb").read().decode("utf-8")
        m = re.search(r'^STAGES\s*=\s*\[', src, re.M)
        assert m is None, "process.py 又出现了一份字面量 STAGES 列表"

    def test_all_stages_including_entity_diff(self):
        from lib.pipeline import state as st
        assert "entity_diff" in st.STAGES


# ── version_id 路径穿越 ──────────────────────────────────────────────────

class TestVersionIdValidation:
    """outline 的 diff 端点从 query string 取 v1/v2, 不经 nginx 路径规范化,
    过去直接 f"{v1_id}.json" 拼进路径。"""

    GOOD = ["v001", "v002", "20260930-120000-a1b2c3", "a.b_c-1"]
    BAD = ["../../../etc/passwd", "..", ".", "a/b", "a\\b", "", "x" * 65,
           None, 123, "a b", "%2e%2e%2f"]

    @pytest.mark.parametrize("vid", GOOD)
    def test_accepts_normal_ids(self, vid):
        from lib.version import validate_version_id
        assert validate_version_id(vid) == vid

    @pytest.mark.parametrize("vid", BAD)
    def test_rejects_traversal(self, vid):
        from lib.version import validate_version_id
        with pytest.raises(ValueError):
            validate_version_id(vid)

    def test_diff_endpoint_rejects_traversal(self, client, tmp_projects_root):
        r = client.get("/api/outline/test_book/diff?v1=../../../../etc/passwd&v2=v001")
        assert r.status_code in (400, 404), \
            f"穿越型 version_id 未被拦下, 实得 {r.status_code}"
        assert "passwd" not in r.get_data(as_text=True)[:400]

    def test_version_path_validates(self, tmp_projects_root):
        from lib.version import _version_path
        with pytest.raises(ValueError):
            _version_path("test_book", "ch_001", "../../escape")


# ── 字数口径单一实现 ─────────────────────────────────────────────────────

class TestNoDuplicateWordCountRegex:
    """tools/migrate_to_sqlite.py 曾是第二份错误正则。
    字数口径必须只有 storage.count_words 一处。"""

    def test_migrate_tool_uses_shared_helper(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "migrate_to_sqlite.py")
        src = open(path, "rb").read().decode("utf-8")
        assert "count_words" in src, "migrate_to_sqlite.py 仍在自己算字数"
        assert not re.search(r'findall\(r"\[\\u4e00-\\u9fff\]\+"', src), \
            "migrate_to_sqlite.py 仍带着那个多一个 + 的错误正则"
