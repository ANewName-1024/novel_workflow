"""
读路径不得产生副作用 (2026-10-01 安全修复的反向防线)
=====================================================

背景
----
`storage.project_root()` 原本无条件 `mkdir`。而 `project_exists()` 调的就是它,
于是「这本书存不存在」这个检查动作本身会把书目录创建出来。

生产后果不是理论: 服务器 projects/ 下躺着一个字面量名为 `${BOOK}` 的空目录
(某处环境变量没展开), 而 Web 层每一条 404 路径(比如 `/dashboard/<不存在的书>`)
都会再盖一个空目录。叠加「公网可达 + 鉴权关闭」, 这是一个可被外部无限造目录的接口。

修复
----
拆成两族:
  project_path() / chapters_path() / memory_path() / summaries_path()
      纯计算, 绝不创建任何东西 —— 读路径与存在性检查只用这一族
  project_root() / chapters_dir() / memory_dir() / summaries_dir() / selfcheck_path()
      顺带 mkdir —— 只给写路径用

本文件的作用: 把「读路径无副作用」这条约束钉死。故意改坏任一处即红。
"""
import json
import pytest

from lib import storage


@pytest.fixture
def bare_root(tmp_path, monkeypatch):
    """空的 projects 根, 里面【没有】任何项目目录。"""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", root)
    monkeypatch.setattr(storage, "ROOT", root)
    return root


# ── 存在性检查 ─────────────────────────────────────────────────────────────

class TestProjectExistsHasNoSideEffect:
    def test_project_exists_does_not_create_dir(self, bare_root):
        assert storage.project_exists("ghost_book") is False
        assert not (bare_root / "ghost_book").exists(), \
            "project_exists() 建出了目录 —— 404 请求每打一次就多一个空项目"

    def test_read_json_does_not_create_dir(self, bare_root):
        assert storage.read_json("ghost_book", "config.json") is None
        assert not (bare_root / "ghost_book").exists(), \
            "read_json() 建出了目录"

    def test_list_chapters_does_not_create_dir(self, bare_root):
        assert storage.list_chapters("ghost_book") == []
        assert not (bare_root / "ghost_book").exists(), \
            "list_chapters() 建出了目录"

    def test_read_chapter_does_not_create_dir(self, bare_root):
        assert storage.read_chapter("ghost_book", "ch_001") is None
        assert not (bare_root / "ghost_book").exists()

    def test_project_path_is_pure(self, bare_root):
        p = storage.project_path("ghost_book")
        assert p == bare_root / "ghost_book"
        assert not p.exists(), "project_path() 竟创建了目录 —— 名字与语义已不符"


# ── 读侧子目录 helper 同样无副作用 ────────────────────────────────────────

class TestReadHelpersArePure:
    def test_chapters_path_pure(self, bare_root):
        storage.chapters_path("ghost_book")
        assert not (bare_root / "ghost_book").exists()

    def test_memory_path_pure(self, bare_root):
        storage.memory_path("ghost_book")
        assert not (bare_root / "ghost_book").exists()

    def test_summaries_path_pure(self, bare_root):
        storage.summaries_path("ghost_book")
        assert not (bare_root / "ghost_book").exists()

    def test_selfcheck_file_pure(self, bare_root):
        storage.selfcheck_file("ghost_book", "ch_001")
        assert not (bare_root / "ghost_book").exists()

    def test_summary_reads_do_not_create_dir(self, bare_root, tmp_path):
        """lib.summary 的两个读函数曾经经 summaries_dir 顺带建目录。"""
        from lib import summary
        assert summary.get_chapter_summary("ghost_book", "ch_001") is None
        assert summary.get_all_chapter_summaries("ghost_book") == []
        assert not (bare_root / "ghost_book").exists()

    def test_comments_reads_do_not_create_dir(self, bare_root):
        """lib.comments 的 path helper 同时供 load/save, 曾走 project_root。"""
        from lib import comments
        assert comments.list_comments("ghost_book") == []
        assert comments.list_notifications("ghost_book") == []
        assert not (bare_root / "ghost_book").exists()


# ── 写路径必须仍然能建目录(别把功能改没了) ────────────────────────────────

class TestWritePathsStillCreate:
    def test_project_root_still_creates(self, bare_root):
        p = storage.project_root("new_book")
        assert p.is_dir()

    def test_write_json_creates_parents(self, bare_root):
        storage.write_json("deep_book", "memory/entities.json", {"a": 1})
        f = bare_root / "deep_book" / "memory" / "entities.json"
        assert f.is_file()
        assert json.loads(f.read_text(encoding="utf-8")) == {"a": 1}

    def test_init_project_still_works(self, bare_root):
        storage.init_project("fresh", {"book_name": "fresh", "genre": "都市"})
        assert storage.project_exists("fresh")
        assert (bare_root / "fresh" / "progress.json").is_file()
        assert (bare_root / "fresh" / "outline.json").is_file()

    def test_write_chapter_still_creates_chapters_dir(self, bare_root):
        storage.write_chapter("wbook", "ch_001", "## 标题\n\n正文")
        assert (bare_root / "wbook" / "chapters" / "ch_001.md").is_file()


# ── 原子写 ────────────────────────────────────────────────────────────────

class TestWriteJsonIsAtomic:
    def test_no_temp_file_left_behind(self, bare_root):
        storage.write_json("abook", "config.json", {"x": 1})
        leftovers = [p.name for p in (bare_root / "abook").iterdir()
                     if p.name != "config.json"]
        assert leftovers == [], f"原子写留下了临时文件: {leftovers}"

    def test_previous_content_survives_a_failed_write(self, bare_root, monkeypatch):
        """写失败时旧内容必须还在 —— 这是原子写的全部意义。"""
        storage.write_json("cbook", "config.json", {"good": 1})

        import os
        real_replace = os.replace

        def boom(src, dst, *a, **k):
            raise OSError("模拟提交阶段崩溃")

        monkeypatch.setattr(storage.os, "replace", boom)
        with pytest.raises(OSError):
            storage.write_json("cbook", "config.json", {"good": 2})
        monkeypatch.setattr(storage.os, "replace", real_replace)

        assert json.loads(
            (bare_root / "cbook" / "config.json").read_text(encoding="utf-8")
        ) == {"good": 1}, "写失败把旧内容也弄没了"
        leftovers = [p.name for p in (bare_root / "cbook").iterdir()
                     if p.name != "config.json"]
        assert leftovers == [], f"失败路径留下了临时文件: {leftovers}"

    def test_never_writes_partial_json(self, bare_root):
        """中途读到的 config.json 永远应该是完整 JSON。"""
        storage.write_json("dbook", "progress.json",
                           {"chapters_completed": ["ch_001", "ch_002"]})
        data = json.loads(
            (bare_root / "dbook" / "progress.json").read_text(encoding="utf-8"))
        assert data["chapters_completed"] == ["ch_001", "ch_002"]


# ── 字数口径 ──────────────────────────────────────────────────────────────

class TestCountWords:
    def test_counts_cjk_characters_not_runs(self):
        """旧实现用 [\\u4e00-\\u9fff]+ , 那个 + 数的是连续片段不是字,
        实测偏低 8.3 倍。这里 6 字 × 100 遍 = 600 个汉字, 旧口径只会给 1。"""
        text = "苏晴蹲在溪边" * 100
        assert len(text) == 600
        assert storage.count_words(text) == 600

    def test_old_regex_would_have_been_wrong(self):
        """反向证明: 旧口径在同样输入下确实错得离谱。"""
        import re
        text = "苏晴蹲在溪边" * 100
        old = len(re.findall(r"[\u4e00-\u9fff]+", text))
        assert old == 1, "若这条断言失效, 说明『旧实现错』的前提需要重新审视"
        assert storage.count_words(text) == 600

    def test_matches_manual_count_on_real_chapter(self, tmp_path):
        import re
        text = "第一章 天降奇缘\n\n苏晴蹲在溪边，手指拨开一丛蕨草，眼睛一亮。\n"
        expected_cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        got = storage.count_words(text)
        assert got >= expected_cjk, "字数不该低于真实汉字数"

    def test_english_words_counted(self):
        assert storage.count_words("hello wonderful world") == 3
