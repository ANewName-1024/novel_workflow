"""
pipeline 层读路径不得产生副作用 (2026-10-01 修复的反向防线)
============================================================

背景
----
`lib/storage.py` 早先已把路径拆成两族(`project_path()` 纯计算 / `project_root()` 顺带
mkdir), 见 `test_storage_read_paths_are_pure.py`。但那次只覆盖了 `storage.py` 本身 ——
**下游调用方并没有跟着一起换**。

`lib/pipeline/state.py` 就漏了:

    def checkpoint_path(book):
        return storage.project_root(book) / CHECKPOINT_FILE   # ← mkdir 的那一族

而 `PipelineV2.load()` / `get_chapter()` 都是纯读, 却经由 `checkpoint_path()` 取路径。
于是对一本**不存在的书**读一次 checkpoint, 就会把 `projects/<book>/` 建出来。

真实触发方式(本地实测复现, 不是推演):

    python novel.py pipeline resume nonexistent_book --chapter ch_5
    → NameError: name 're' is not defined        (见 novel.py 同期修复)

`re` 修好之后这条命令就能跑通了, 于是副作用暴露出来:

    ✅ 自动检测中断于 [context], 已重置为可恢复
    $ ls projects/nonexistent_book/
    .pipeline_checkpoints.json                    ← 凭空多出来的项目目录

书名打错一个字母, 就在磁盘上多一个空项目。Web 层同理: 任何走 `get_pipeline_view()` /
`get_last_snapshot()` 的页面, 对不存在的书都会留下目录。

本文件的作用: 把「读路径无副作用」这条约束钉在 pipeline 层, 并加一条结构性防线
——`lib/pipeline/state.py` 里不允许再出现 `project_root(`。
故意改坏任一处即红。
"""
import ast
from pathlib import Path

import pytest

from lib import storage
from lib.pipeline import state as pv2


@pytest.fixture
def bare_root(tmp_path, monkeypatch):
    """空的 projects 根, 里面【没有】任何项目目录。"""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", root)
    monkeypatch.setattr(storage, "ROOT", root)
    return root


# ── 路径 helper ────────────────────────────────────────────────────────────

class TestCheckpointPathIsPure:
    def test_checkpoint_path_does_not_create_dir(self, bare_root):
        path = pv2.checkpoint_path("ghost_book")
        assert path.name == pv2.CHECKPOINT_FILE
        assert not (bare_root / "ghost_book").exists(), \
            "checkpoint_path() 建出了目录 —— 它是纯路径计算, 写侧由 _atomic_write_json 自己 mkdir"

    def test_checkpoint_path_points_into_projects_root(self, bare_root):
        assert pv2.checkpoint_path("ghost_book").parent == bare_root / "ghost_book"


# ── 纯读 API ───────────────────────────────────────────────────────────────

class TestPipelineReadsHaveNoSideEffect:
    def test_load_does_not_create_dir(self, bare_root):
        doc = pv2.get_v2().load("ghost_book")
        assert doc.book == "ghost_book"
        assert doc.chapters == {}
        assert not (bare_root / "ghost_book").exists(), \
            "PipelineV2.load() 建出了目录"

    def test_get_chapter_does_not_create_dir(self, bare_root):
        ch_doc = pv2.get_v2().get_chapter("ghost_book", 5)
        assert ch_doc.chapter == 5
        assert not (bare_root / "ghost_book").exists(), \
            "get_chapter() 建出了目录 —— 它只是 load() + 一个默认值"

    def test_get_last_snapshot_does_not_create_dir(self, bare_root):
        assert pv2.get_last_snapshot("ghost_book") is None
        assert not (bare_root / "ghost_book").exists(), \
            "get_last_snapshot() 建出了目录 —— '查一下有没有快照' 不该造出 memory/ 两级目录"

    def test_get_last_snapshot_does_not_create_memory_dir(self, bare_root):
        """上一条的加强版: 盯住被顺手 mkdir 出来的那两级。"""
        pv2.get_last_snapshot("ghost_book")
        assert not (bare_root / "ghost_book" / "memory").exists()

    def test_get_interrupted_chapters_does_not_create_dir(self, bare_root):
        assert pv2.get_interrupted_chapters("ghost_book") == []
        assert not (bare_root / "ghost_book").exists()


# ── 写侧必须仍然能建目录 ───────────────────────────────────────────────────

class TestWritePathStillWorks:
    """只把读路径改成纯计算是不够的 —— 写路径要是没跟上, 就是把 bug 换了个方向。"""

    def test_save_then_load_roundtrip(self, bare_root):
        v2 = pv2.get_v2()
        ch = v2.get_chapter("real_book", 1)
        v2.save_chapter("real_book", ch)

        assert (bare_root / "real_book" / pv2.CHECKPOINT_FILE).exists(), \
            "save() 没写出文件 —— 读路径改纯之后, 写路径的 mkdir 丢了"

        again = v2.get_chapter("real_book", 1)
        assert again.book == "real_book"
        assert again.chapter == 1

    def test_checkpoint_snapshot_write_creates_dir(self, bare_root):
        pv2.checkpoint_snapshot("real_book", 2)
        assert (bare_root / "real_book" / "memory" / "pipeline_snapshot.json").exists()


# ── 结构性防线 ─────────────────────────────────────────────────────────────

class TestNoMkdiringHelperLeftInPipelineState:
    def test_state_py_never_calls_project_root(self):
        """`project_root()` 会 mkdir。任何出现在这个文件里的**调用**都是嫌疑点。

        写路径要用目录, 请显式 `path.parent.mkdir(...)` —— checkpoint_snapshot()
        就是这么做的, 这样读/写的边界在代码里一眼可见, 而不是靠"这个函数叫不叫 root"。

        走 AST 而不是文本匹配: 注释里为了说明历史完全应该能提到 project_root(),
        文本匹配会把那些说明也判成违规, 逼着后来者删掉解释性注释。
        """
        path = Path(pv2.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "project_root"
        ]
        assert not offenders, (
            f"lib/pipeline/state.py 在 L{offenders} 调用了 mkdir 语义的 project_root()。"
            "读路径请改用 project_path(); 写路径请显式 path.parent.mkdir(...)。"
        )
