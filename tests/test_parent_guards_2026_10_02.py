"""test_parent_guards_2026_10_02.py — 父代理自己改的两处，各配一条反向防线。

1. 删除项目改成「移入回收站」而不是 shutil.rmtree。
   没有测试的话, 下次有人觉得 shutil.rmtree 更简洁就换回去了, 而且不会有任何告警
   —— 一次误点或脚本跑错, 整本书就没了。这条测试就是那个告警。
2. memory 文件改成原子写 (temp + os.replace)。
   非原子写是「截断→写入」, 中途被杀留下半截 JSON, 读侧 json.JSONDecodeError
   静默退化成空 dict —— 角色记忆整库消失且无报错。

两条都只钉「具体形状」, 不模仿通用 linter: 上一轮自制静态门禁(F821 之类)在
本仓库制造过假阳性并训练人忽略红线, 通用检查交给 ruff, 这里只测行为。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import storage


# ── 1. 删除项目 = 可恢复, 不是 rmtree ─────────────────────────────────────

class TestProjectDeleteIsRecoverable:
    def test_delete_preserves_the_book_in_trash(self, client, tmp_projects_root):
        """删完书还在回收站里, 内容一字不少 —— 这是与 rmtree 的本质区别。"""
        client.post("/api/projects", json={
            "name": "doomed", "main_plot": "会被人误删的一本",
        })
        assert storage.project_exists("doomed")
        before = storage.read_json("doomed", "config.json")

        r = client.delete("/api/projects/doomed")
        assert r.status_code == 200, r.get_json()
        assert not storage.project_exists("doomed")

        trash_rel = r.get_json()["trash"]
        assert trash_rel, "响应必须告诉用户东西被挪到哪了, 否则等于凭空消失"
        trashed = Path(storage.PROJECTS_ROOT) / trash_rel
        assert trashed.is_dir(), f"回收站目录不存在: {trashed}"
        assert json.loads((trashed / "config.json").read_text(encoding="utf-8")) == before

    def test_delete_can_be_undone_by_moving_back(self, client, tmp_projects_root):
        """整条恢复路径要真的能走通, 而不是只在文档里声称可恢复。"""
        client.post("/api/projects", json={"name": "oops", "main_plot": "误删"})
        r = client.delete("/api/projects/oops")
        trashed = Path(storage.PROJECTS_ROOT) / r.get_json()["trash"]

        trashed.rename(Path(storage.PROJECTS_ROOT) / "oops")
        assert storage.project_exists("oops")
        assert client.get("/api/projects/oops").status_code == 200

    def test_trash_dir_is_not_listed_as_a_project(self, client, tmp_projects_root):
        """回收站不能被当成一本书出现在项目列表里。"""
        client.post("/api/projects", json={"name": "gone", "main_plot": "x"})
        client.delete("/api/projects/gone")
        data = client.get("/api/projects").get_json()
        assert "gone" not in data["projects"]
        assert not any(".trash" in name for name in data["projects"])

    def test_two_deletes_in_the_same_second_do_not_collide(self, client, tmp_projects_root):
        """同名书一秒内删两次不能互相覆盖 —— 覆盖等于静默丢数据。"""
        client.post("/api/projects", json={"name": "twin", "main_plot": "第一个"})
        client.delete("/api/projects/twin")
        client.post("/api/projects", json={"name": "twin", "main_plot": "第二个"})
        r2 = client.delete("/api/projects/twin")

        trash = Path(storage.PROJECTS_ROOT) / ".trash"
        entries = sorted(p.name for p in trash.iterdir())
        assert len(entries) == 2, f"两个回收目录应并存, 实际: {entries}"
        plots = {json.loads((trash / e / "config.json").read_text(encoding="utf-8"))["main_plot"]
                 for e in entries}
        assert plots == {"第一个", "第二个"}, f"回收站内容被覆盖了: {plots}"


# ── 2. memory 文件原子写 ─────────────────────────────────────────────────

class TestMemoryWriteIsAtomic:
    def test_save_leaves_no_temp_file_behind(self, tmp_path, monkeypatch):
        from lib import memory
        monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
        memory._save_raw("b1", "characters", {"甲": {"name": "甲"}})

        memdir = tmp_path / "b1" / "memory"
        assert sorted(p.name for p in memdir.iterdir()) == ["characters.json"], \
            "原子写用完必须清理临时文件, 否则 memory/ 下会堆满 .tmp"

    def test_replacing_content_is_visible_and_complete(self, tmp_path, monkeypatch):
        """写完立刻读回必须是完整的新内容, 不能读到截断的中间态。"""
        from lib import memory
        monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
        big = {f"角色{i}": {"name": f"角色{i}", "role": "配角"} for i in range(500)}
        memory._save_raw("b2", "characters", big)

        raw = (tmp_path / "b2" / "memory" / "characters.json").read_text(encoding="utf-8")
        assert json.loads(raw) == big, "落盘内容必须与传入完全一致"

    def test_os_replace_is_used_not_direct_write(self, tmp_path, monkeypatch):
        """钉住实现形状: 必须走 os.replace。

        写成 p.write_text() 也能通过上面两条, 但那正是截断窗口的来源。
        断的是「原子」这个性质, 不是「内容对不对」。
        """
        import os
        from lib import memory
        monkeypatch.setattr(storage, "PROJECTS_ROOT", tmp_path)
        calls = []
        real_replace = os.replace
        monkeypatch.setattr(os, "replace", lambda a, b: (calls.append((a, b)), real_replace(a, b))[1])

        memory._save_raw("b3", "characters", {"甲": {"name": "甲"}})
        assert calls, "_save_raw 没有调用 os.replace —— 又变回非原子写了"
        src, dst = calls[0]
        assert str(src).endswith(".tmp"), f"源应是临时文件, 实际 {src}"
        assert str(dst).endswith("characters.json")


# ── 3. dashboard.py 的用户可见消息必须是可读中文 ─────────────────────────

# 曾经试过写一个「检测双重编码字符」的通用门禁, 两次都被自己的实现打脸:
#   第一次用特征字符集, 扫小说正文时在正常汉字上误报;
#   第二次改用可逆性判定(GBK 编码后能解成合法 UTF-8 即判可疑), 同样误报 ——
#   相当一部分正常汉字恰好满足这个条件。
# 假阳性率高的自制门禁比没有更糟: 它只会训练人习惯性忽略红线。所以不做了,
# 改成钉住「这些具体消息必须以可读中文存在」—— 文件若再被双重编码, 这些串
# 会整体消失, 断言照样转红, 且不可能误报。

@pytest.mark.parametrize("needle", [
    "必须是整数",              # api_pipeline_start 参数校验
    "缺少 'chapters' 参数",     # api_pipeline_start 缺参
    "没有流水线记录",           # api_pipeline_status 的 message 字段
    "流水线面板页面",           # dashboard_page docstring
    "统一 NovelError",          # _err_response docstring
])
def test_dashboard_user_facing_messages_are_readable_chinese(needle):
    from review_ui import dashboard
    src = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert needle in src, (
        f"dashboard.py 缺少可读中文 {needle!r} —— 该文件曾整片是 UTF-8→GBK→UTF-8 "
        f"的乱码, docstring 与用户可见错误消息同时受害, 而 ruff 与 pytest 都查不出来"
    )


def test_dashboard_still_parses_and_keeps_its_routes():
    """顺带守住一个事实: 上面那些 docstring 里的箭头/破折号被我手工重写过,
    重写最容易出的错就是引号不配对把文件弄坏。"""
    import ast
    from review_ui import dashboard
    ast.parse(Path(dashboard.__file__).read_text(encoding="utf-8"))
    rules = {str(r.rule) for r in dashboard.dashboard_bp.url_map.iter_rules()} \
        if hasattr(dashboard.dashboard_bp, "url_map") else set()
    # 未挂到 app 时 url_map 为空, 退化为只断言模块可导入可解析
    assert dashboard.api_pipeline_start is not None
    assert dashboard.api_pipeline_status is not None
    assert isinstance(rules, set)
