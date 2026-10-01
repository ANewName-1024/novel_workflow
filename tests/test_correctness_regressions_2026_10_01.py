"""
2026-10-01 正确性修复的反向防线
=============================

每条对应一个【已确认存在过】的 bug。改回去即红。

这一批的共同特征: 它们全都躲过了现有测试。原因写在各自的类 docstring 里,
而这正是本文件要钉住的东西 —— 「测试全绿」不等于「行为正确」。
"""
import ast
import json
import os
import re
import threading
import pytest


# ── 1. bp/pipeline.py 漏 import re ────────────────────────────────────────

class TestResumeEndpointIsReachable:
    """review_ui/bp/pipeline.py 用了 re.match 却没 import re。

    这个 NameError 能活下来, 是因为 test_route_completeness.py 只把路由规则
    字符串放进集合比对, **从不发请求**。路由完整性 ≠ 行为完整性。
    """

    def test_module_actually_imports_re(self):
        """静态防线: 本模块用到 re.* 就必须有 import re。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "review_ui", "bp", "pipeline.py")
        tree = ast.parse(open(path, "rb").read().decode("utf-8"))
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    bound.add(a.asname or a.name)
        used = {n.value.id for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "re"}
        assert "re" in bound, "bp/pipeline.py 用到 re.* 却没有 import re"

    def test_resume_returns_json_not_500(self, client, tmp_projects_root):
        """真正发一次请求 —— 这是 test_route_completeness 永远不会做的事。"""
        from lib.pipeline import state as pv
        orig = pv.recover_stage
        try:
            pv.recover_stage = lambda book, n: {
                "ok": True, "chapter": n, "recovered_stage": "context",
                "message": "stub"}
            r = client.post("/api/pipeline/test_book/ch_001/resume")
            assert r.status_code == 200, f"期望 200, 实得 {r.status_code} (NameError 会是 500)"
            assert r.get_json()["ok"] is True
        finally:
            pv.recover_stage = orig

    def test_resume_rejects_bad_chapter_id(self, client, tmp_projects_root):
        r = client.post("/api/pipeline/test_book/not-a-chapter/resume")
        assert r.status_code == 400, f"期望 400, 实得 {r.status_code}"

    def test_resume_on_missing_book_is_404(self, client, tmp_projects_root):
        r = client.post("/api/pipeline/no_such_book/ch_001/resume")
        assert r.status_code == 404


# ── 2. review_service except 块引用未定义的 chapter_id ───────────────────

class TestSQLiteFallbackPathsAreReachable:
    """get_review_queue / get_review_stats 的 except 块过去写 chapter_id,
    但函数签名只有 (book)。SQLite 一挂, 优雅回退自己先 NameError。

    测试盲区: 现有用例全在 SQLite 正常时跑。
    """

    @pytest.fixture
    def sqlite_down(self, monkeypatch):
        import sqlite3
        from lib import review_service as revserv

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        real = revserv.db.list_reviews if hasattr(revserv, "db") else None
        import lib.db as dbmod
        monkeypatch.setattr(dbmod, "list_reviews", boom)
        monkeypatch.setattr(dbmod, "review_stats", boom)
        yield

    def test_queue_falls_back_to_files(self, client, tmp_projects_root, sqlite_down):
        """SQLite 不可用时应回退读文件, 而不是抛 NameError。"""
        r = client.get("/api/queue/test_book")
        assert r.status_code == 200, f"期望 200, 实得 {r.status_code}"
        assert r.is_json

    def test_stats_falls_back_to_files(self, client, tmp_projects_root, sqlite_down):
        r = client.get("/api/stats/test_book")
        assert r.status_code == 200, f"期望 200, 实得 {r.status_code}"

    def test_no_chapter_id_in_those_except_blocks(self):
        """静态防线: 那两个 except 块里不得出现 chapter_id。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "review_service.py")
        src = open(path, "rb").read().decode("utf-8")
        tree = ast.parse(src)
        bad = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name not in ("get_review_queue", "get_review_stats"):
                continue
            params = {a.arg for a in fn.args.args}
            for handler in ast.walk(fn):
                if isinstance(handler, ast.ExceptHandler):
                    for sub in ast.walk(handler):
                        if (isinstance(sub, ast.Name)
                                and sub.id == "chapter_id"
                                and sub.id not in params):
                            bad.append(f"{fn.name}:{sub.lineno}")
        assert not bad, f"except 块引用了作用域外的 chapter_id: {bad}"


# ── 3. chapter.py 从不对 context/writing 调 _v2_mark ─────────────────────

class TestAllStagesReachedInV2:
    """chapter.py 过去只给 done/extract/entity_diff/summary/state/self_check
    调 _v2_mark, context 与 writing 只有 print([PIPELINE])。

    而 state.STAGES 要求 8 个阶段全部 DONE/SKIPPED, 于是 is_complete()
    永远 False —— 写完的章节被永久列为「中断在 context」, recover_stage()
    于是从 context 重跑 = 重复生成已写好的章节。
    """

    def test_every_stage_in_STAGES_is_marked_in_chapter_py(self):
        from lib.pipeline import state as pv
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "chapter.py")
        src = open(path, "rb").read().decode("utf-8")
        marked = set(re.findall(r'_v2_mark\(\s*book\s*,\s*chapter_num\s*,\s*"(\w+)"', src))
        missing = [s for s in pv.STAGES if s not in marked]
        assert not missing, f"chapter.py 没有为这些阶段调 _v2_mark: {missing}"

    def test_self_check_disabled_is_marked_skipped(self):
        """自检未启用时该阶段原本永远 PENDING, 同样让 is_complete() 为 False。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "chapter.py")
        src = open(path, "rb").read().decode("utf-8")
        assert '"self_check", "SKIPPED"' in src, \
            "自检未启用时必须显式标 SKIPPED, 否则恢复逻辑会一直以为它没跑过"

    def test_protocol_print_lines_are_preserved(self):
        """补 _v2_mark 不能把 [PIPELINE] 协议行弄丢 —— 那是跨进程数据通道。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "chapter.py")
        src = open(path, "rb").read().decode("utf-8")
        for stage in ("context", "writing", "extract", "summary", "state"):
            assert f"stage={stage} status=start" in src, f"{stage} 的 start 协议行丢了"
            assert f"stage={stage} status=done" in src, f"{stage} 的 done 协议行丢了"


# ── 4. entity_diff 快照取在 merge 之后 ────────────────────────────────────

class TestEntityDiffSnapshotOrdering:
    """快照过去在 entity_diff 阶段才取, 那时 extract 早已 merge 完 ——
    拿到的是变更后的状态, 再与 current 比, added/updated/resolved 恒为 0。
    章节页「本章节实体变化」面板于是永远是空的。"""

    def test_snapshot_call_precedes_merge_call(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "chapter.py")
        src = open(path, "rb").read().decode("utf-8")
        snap = src.index("before_snap = _edmod.snapshot_memory(book)")
        merge = src.index("memory.merge_extraction(book, extraction)")
        assert snap < merge, \
            "快照必须在 merge_extraction 之前取, 否则 diff 恒为空"

    def test_before_snap_is_guarded_against_unbound(self):
        """before_snap = None 必须在 try 之外 —— extract 自己抛异常时
        下面的 entity_diff 阶段仍要能安全读到 None, 而不是撞 NameError。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lib", "chapter.py")
        tree = ast.parse(open(path, "rb").read().decode("utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            assigns = [n for n in ast.walk(fn)
                       if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "before_snap"
                               for t in n.targets)]
            if not assigns:
                continue
            first = min(a.lineno for a in assigns)
            # 该赋值所在的 try 块起点
            enclosing_try = None
            for node in ast.walk(fn):
                if isinstance(node, ast.Try) and node.lineno < first <= (node.end_lineno or 0):
                    enclosing_try = node.lineno
            assert enclosing_try is None, \
                f"before_snap 的首次赋值(L{first})落在 try 块内(L{enclosing_try}), " \
                "extract 抛异常时会 NameError"
            return
        pytest.fail("chapter.py 里找不到 before_snap 赋值")


# ── 5. 编辑审计钩子在写入之前触发 ─────────────────────────────────────────

class TestAuditHookRunsAfterWrite:
    """api_edit 过去先 hook_review_action 再 revserv.edit, 且 except: pass。
    edit 失败时审计里已经记了一条「已编辑」, 与事实相反。"""

    def test_hook_not_called_when_edit_fails(self, client, tmp_projects_root, monkeypatch):
        from lib import session_log as slog
        from lib import review_service as revserv

        calls = []
        monkeypatch.setattr(slog, "hook_review_action",
                            lambda *a, **k: calls.append(a))
        monkeypatch.setattr(revserv, "edit",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        review_app_cfg = __import__("review_ui.app", fromlist=["app"])
        review_app_cfg.app.config["PROPAGATE_EXCEPTIONS"] = True
        with pytest.raises(RuntimeError):
            client.post("/api/edit/test_book/ch_001",
                        json={"text": "新正文", "reviewer": "x"})
        assert calls == [], f"写入失败却已记审计: {calls}"

    def test_hook_called_after_successful_edit(self, client, tmp_projects_root, monkeypatch):
        from lib import session_log as slog
        from lib import review_service as revserv

        calls = []

        def fake_edit(book, ch, reviewer, text, notes):
            calls.append(("edit", ch))
            return {"status": "human_edited"}

        monkeypatch.setattr(slog, "hook_review_action",
                            lambda *a, **k: calls.append(("hook", a[1])))
        monkeypatch.setattr(revserv, "edit", fake_edit)
        r = client.post("/api/edit/test_book/ch_001",
                        json={"text": "新正文", "reviewer": "x"})
        assert r.status_code == 200
        assert calls == [("edit", "ch_001"), ("hook", "ch_001")], \
            f"顺序应为 先写后记, 实得 {calls}"

    def test_hook_failure_is_logged_not_swallowed(self, client, tmp_projects_root,
                                                   monkeypatch, caplog):
        import logging
        from lib import session_log as slog
        from lib import review_service as revserv

        def boom(*a, **k):
            raise RuntimeError("hook 挂了")

        monkeypatch.setattr(slog, "hook_review_action", boom)
        monkeypatch.setattr(revserv, "edit",
                            lambda *a, **k: {"status": "human_edited"})
        with caplog.at_level(logging.ERROR):
            r = client.post("/api/edit/test_book/ch_001",
                            json={"text": "新正文", "reviewer": "x"})
        assert r.status_code == 200, "hook 失败不该让主流程 500"
        recs = [x for x in caplog.records if x.levelno >= logging.ERROR]
        assert recs, "hook 失败必须留 ERROR 日志, 不能 except: pass"


# ── 6. GET 路由触发写操作 ────────────────────────────────────────────────

class TestBackfillIsSerializedAndMemoized:
    """backfill 由三条 GET 路由触发, 无锁读改写 → 并发 GET 会各写一份,
    审计轨迹出现重复条目; 且每次页面渲染/浏览器预取都全量遍历一遍。"""

    def test_second_call_is_short_circuited(self, tmp_projects_root):
        from lib import storage, review_service as revserv
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv._backfill_fingerprint.pop("test_book", None)
        first = revserv.backfill_missing_reviews("test_book")
        second = revserv.backfill_missing_reviews("test_book")
        assert first >= 1
        assert second == 0, "章节集合没变时不该重复跑"

    def test_force_bypasses_memo(self, tmp_projects_root):
        from lib import storage, review_service as revserv
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.backfill_missing_reviews("test_book")
        revserv.backfill_missing_reviews("test_book", force=True)
        # force 允许重跑, 幂等保证不会重复建记录
        assert revserv.get_review("test_book", "ch_001") is not None

    def test_new_chapter_invalidates_memo(self, tmp_projects_root):
        from lib import storage, review_service as revserv
        storage.write_chapter("test_book", "ch_001", "## 一\n\n正文")
        revserv.backfill_missing_reviews("test_book")
        storage.write_chapter("test_book", "ch_002", "## 二\n\n正文")
        n = revserv.backfill_missing_reviews("test_book")
        assert n >= 1, "新增章节后必须重新补建"
        assert revserv.get_review("test_book", "ch_002") is not None

    def test_concurrent_calls_do_not_duplicate_audit(self, tmp_projects_root):
        from lib import storage, review_service as revserv
        for i in (1, 2, 3):
            storage.write_chapter("test_book", f"ch_{i:03d}", f"## {i}\n\n正文")
        revserv._backfill_fingerprint.pop("test_book", None)
        errors = []

        def run():
            try:
                revserv.backfill_missing_reviews("test_book")
            except Exception as e:      # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"并发 backfill 抛异常: {errors}"

        # 审计落在 reviews/audit.log(append_audit), 不在 record["history"] 里。
        # 无锁时 6 个线程会各判一次「这章还没记录」, 于是每章多行 backfilled。
        log_text = revserv.audit_log_path("test_book").read_text(encoding="utf-8")
        for i in (1, 2, 3):
            cid = f"ch_{i:03d}"
            lines = [ln for ln in log_text.splitlines() if f"ch={cid} " in ln]
            assert len(lines) == 1, \
                f"{cid} 的 backfill 审计重复 {len(lines)} 次:\n" + "\n".join(lines)
            assert revserv.get_review("test_book", cid) is not None
