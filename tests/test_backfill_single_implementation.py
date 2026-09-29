"""tests/test_backfill_single_implementation.py — backfill 只能有一份实现。

事件: 6dd453b 修了 novel.py 里「损坏的 self_check 被当成「没有自检」→
写成 AUTO_PASSED」这个 bug, 以为修完了。后来
tools/audit_corrupt_default_write.py 在 review_ui/bp/review.py 报出同一形态
——同一段逻辑的第二份拷贝, 原封不动地带着同一个 bug。

两份拷贝的 docstring 都自认过重复:
  novel.py        "Backfill review records for chapters that don't have one yet"
  review.py       "Same backfill as cmd_review_queue."

两份甚至已经漂移: review.py 那份少了 append_audit 调用。

重复实现不是风格问题, 它让「只修一半」成为默认结果。本文件把结构锁住:
  1. 实现只在 review_service.backfill_missing_reviews 一处
  2. 两个调用方都只是委派, 不含逻辑
  3. 损坏分支的语义没在搬家过程中丢掉
  4. 审计脚本保持 0 处
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from lib import review_service as revserv  # noqa: E402

CANONICAL = "backfill_missing_reviews"


def _fn_source(rel: str, name: str) -> str:
    src = (REPO / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.unparse(n)
    pytest.fail(f"{rel} 里找不到 {name}()")


class TestSingleImplementation:
    def test_canonical_lives_in_review_service(self):
        assert hasattr(revserv, CANONICAL), \
            f"规范实现必须在 lib/review_service.py 的 {CANONICAL}()"

    def test_no_logic_left_in_novel_py(self):
        """CLI 入口只能委派。留下任何读取逻辑就意味着又一份拷贝."""
        src = _fn_source("novel.py", "_ensure_review_for_existing")
        assert CANONICAL in src, "novel.py 应委派到规范实现"
        for leaked in ("auto_flag", "save_review", "_empty_record",
                       "self_checks"):
            assert leaked not in src, (
                f"novel.py 的 _ensure_review_for_existing 里仍残留 {leaked} —— "
                f"实现又分叉了"
            )

    def test_no_logic_left_in_review_ui(self):
        src = _fn_source("review_ui/bp/review.py", "_ensure_review_backfill")
        assert CANONICAL in src, "Web UI 应委派到规范实现"
        for leaked in ("auto_flag", "save_review", "_empty_record",
                       "self_checks"):
            assert leaked not in src, (
                f"review.py 的 _ensure_review_backfill 里仍残留 {leaked} —— "
                f"实现又分叉了"
            )

    def test_exactly_one_function_reads_selfcheck_and_writes_review(self):
        """全仓只应有一处: 读 self_checks 做判定 + 写评审记录.

        用 AST 而非字符串匹配。两个教训都在这里:
          1. 源码是 f"{ch['id']}.json", 子串 "ch['id'].json" 不连续存在
          2. 只看 "self_checks" 会误伤 chapter.py —— 那里只是 print 出路径
             提醒用户去看, 并不读取。判据必须是【真的读了】。
        """
        hits = []
        for base in (REPO / "lib", REPO / "review_ui"):
            for p in sorted(base.rglob("*.py")):
                if "__pycache__" in p.parts:
                    continue
                rel = str(p.relative_to(REPO)).replace("\\", "/")
                if rel.startswith("tests/"):
                    continue
                tree = ast.parse(p.read_text(encoding="utf-8"))
                for fn in ast.walk(tree):
                    if not isinstance(fn, ast.FunctionDef):
                        continue
                    try:
                        src = ast.unparse(fn)
                    except Exception:
                        continue
                    mentions_sc = "self_checks" in src
                    writes_review = any(
                        w in src for w in ("auto_flag", "save_review",
                                           "_empty_record"))
                    # 真的读了: 有 read_text / loads / open
                    actually_reads = any(
                        r in src for r in (".read_text(", "json.loads(",
                                           "loads(", ".open("))
                    if mentions_sc and actually_reads and writes_review:
                        hits.append(f"{rel}::{fn.name}")
        assert hits == [f"lib/review_service.py::{CANONICAL}"], (
            f"「读 self_checks 做判定 → 写评审」应只存在于规范实现, 实得 {hits}"
        )

    def test_chapter_py_is_not_a_second_copy(self):
        """chapter.py 提到 self_checks 只是打印提示, 不构成第二份实现."""
        src = _fn_source("lib/chapter.py", "run_post_write_pipeline")
        assert "self_checks" in src, "预期它会打印 self_checks 路径"
        read_lines = [l for l in src.splitlines()
                      if "self_checks" in l and ("read_text" in l or "open(" in l)]
        assert not read_lines, (
            f"chapter.py 若开始真读 self_checks, 就是第二份 backfill 实现: {read_lines}"
        )


class TestSemanticSurvivedTheMove:
    """搬家不能把 6dd453b 的语义弄丢。"""

    def test_corrupt_branch_catches_only_parse_and_io(self):
        src = inspect.getsource(revserv.backfill_missing_reviews)
        assert "JSONDecodeError" in src and "OSError" in src, \
            "损坏分支应收窄到解析与 IO 错误"
        assert "except Exception" not in src, \
            "不该捕获宽泛 Exception —— 6dd453b 特意收窄过"

    def test_corrupt_branch_continues_without_writing(self):
        src = inspect.getsource(revserv.backfill_missing_reviews)
        assert "continue" in src, "损坏章节应跳过, 不落任何评审结论"
        assert "log.error" in src, "损坏必须记 ERROR"

    def test_missing_file_still_auto_passes(self):
        """文件不存在仍默认通过 —— 那是既定语义, 不是 bug."""
        src = inspect.getsource(revserv.backfill_missing_reviews)
        assert 'REVIEW_STATUS["AUTO_PASSED"]' in src

    def test_audit_script_reports_zero(self):
        """审计保持 0 处 —— 合并后不应再有「污染值流入写入」的路径."""
        import audit_corrupt_default_write as detector
        findings = []
        for base in (REPO / "lib", REPO / "review_ui"):
            for p in base.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                findings.extend(detector.scan_source(
                    p.read_text(encoding="utf-8"),
                    str(p.relative_to(REPO))))
        assert not findings, (
            f"审计报了 {len(findings)} 处, 合并后应为 0:\n"
            + "\n".join(f"  {f['file']}:{f['line']} {f['func']} -> {f['call']}"
                        for f in findings[:8])
        )