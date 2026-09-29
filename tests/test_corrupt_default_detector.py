"""tests/test_corrupt_default_detector.py — 验证审计脚本本身能抓到已知 bug。

背景: tools/audit_corrupt_default_write.py 一开始报 0 处。0 处可能意味着
「真的没有」, 也可能意味着「detector 坏了」—— 这正是今天 pyflakes 空输出
被误读成「检查通过」的同一类错误。

已知实例: novel.py 的 _ensure_review_for_existing() (6dd453b 修复前)

    try:    sc_result = json.loads(...)
    except Exception: sc_result = None              # 损坏
    if sc_result:  auto_flag(book, ch, sc_result, ...)   # 写入
    else:          save_review(book, empty)              # 写入结论

注意 sc_result 是在 handler 内部被赋值的 —— detector 若排除「handler 里
赋值过的变量」, 恰好会漏掉这个真实存在的 bug。开发过程中真的发生过:
第一版就带这个排除, 会对已知 bug 报 0 处。

本测试直接调用脚本的 scan_source(), 不重写判定 —— 测副本没有意义。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import audit_corrupt_default_write as detector  # noqa: E402


KNOWN_BUG = '''
def backfill(book, ch_id):
    sc_result = None
    sc_path = make_path(book, ch_id)
    if sc_path.exists():
        try:
            sc_result = json.loads(sc_path.read_text(encoding="utf-8"))
        except Exception:
            sc_result = None              # 损坏 == 「没有」
    if sc_result:
        auto_flag(book, ch_id, sc_result, by="AI-backfill")   # 写入
    else:
        empty = make_empty(ch_id)
        empty["status"] = "AUTO_PASSED"
        save_review(book, empty)          # 写入结论
'''

CLEAN_READ_ONLY = '''
def read_only(book, ch_id):
    """只读, 不写 —— 损坏顶多显示过期, 不该判为危险."""
    rec = None
    p = path_of(book, ch_id)
    if p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            rec = None
    return rec
'''

CLEAN_WITH_DIFFERENT_DEFAULT = '''
def parses_int(text):
    n = None
    try:
        n = int(text)
    except ValueError:
        n = None
    save_counter(n)          # n 会被写入, 但它确实是「解析失败」, 应报出
    return n
'''


class TestDetectorCatchesKnownBug:
    def test_known_bug_is_detected(self):
        hits = detector.scan_source(KNOWN_BUG, "novel.py")
        assert hits, (
            "detector 抓不到 6dd453b 修的那个 bug —— 它不可信, "
            "0 处结果不能作为「干净」的依据"
        )
        assert "sc_result" in {h["var"] for h in hits}

    def test_flagged_write_call_is_auto_flag(self):
        hits = detector.scan_source(KNOWN_BUG, "novel.py")
        calls = {h["call"] for h in hits}
        assert "auto_flag" in calls, f"应报出 auto_flag, 实得 {calls}"

    def test_variable_assigned_inside_handler_is_not_excluded(self):
        """关键回归: sc_result 在 handler 里赋值, 不能因此被排除。

        这是开发中真实出现的缺陷 —— 第一版带 handler 排除逻辑, 对已知
        bug 报 0 处, 而那正是本模式最典型的形态。
        """
        hits = detector.scan_source(KNOWN_BUG, "novel.py")
        assert any(h["assigned_in_handler"] for h in hits), \
            "detector 排除了 handler 内赋值的变量, 会漏报最典型的形态"


class TestDetectorPrecision:
    def test_read_only_code_is_not_flagged(self):
        hits = detector.scan_source(CLEAN_READ_ONLY, "x.py")
        assert not hits, f"纯读取不应判危险, 实得 {hits}"

    def test_different_default_still_analyzed(self):
        """默认值不同不影响判定链, ValueError 也该被覆盖."""
        hits = detector.scan_source(CLEAN_WITH_DIFFERENT_DEFAULT, "x.py")
        assert hits, "非 None 的 falsy 默认值同样应进入判定"


class TestIsWriteCall:
    @pytest.mark.parametrize("name,expect", [
        ("save_review", True),
        ("upsert_review", True),
        ("mark_chapter_completed", True),
        ("auto_flag", True),
        ("write_text", True),
        ("get_review", False),
        ("read_json", False),
        ("list_chapters", False),
        ("list_reviews", False),
    ])
    def test_bare_name_call(self, name, expect):
        # 必须构造 Call 节点(f(...)), is_write_call 读的是 node.func
        node = ast.parse(f"{name}()").body[0].value
        ok, _ = detector.is_write_call(node)
        assert ok == expect, f"{name} 判定为 {ok}, 预期 {expect}"

    def test_method_call_detected(self):
        node = ast.parse("obj.save_review()").body[0].value
        ok, name = detector.is_write_call(node)
        assert ok and name == "save_review"

    def test_read_prefix_beats_write_prefix(self):
        """get_/list_ 优先 —— 否则 list_reviews 之类会被前缀规则误判."""
        node = ast.parse("db.get_review()").body[0].value
        assert detector.is_write_call(node)[0] is False
        node = ast.parse("db.save_review()").body[0].value
        assert detector.is_write_call(node)[0] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))