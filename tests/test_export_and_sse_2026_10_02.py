"""tests/test_export_and_sse_2026_10_02.py — 成稿出得去, 铃铛亮得起来, SSE 收得住。

三处断层, 都是「不报错、只是功能不存在」那一类:

P2  导出: 全仓 89 条路由里, review_ui/ 下 grep `export|download|导出|下载`
    零命中。用户能在浏览器里 init -> outline -> write -> review -> approve
    一路走完, 却拿不走成稿, 只能 SSH 跑 `novel.py export`。而那段拼 Markdown
    的逻辑原先死死长在 cmd_export 里, 写盘副作用缠着, 想复用就得继承副作用。

P3  通知徽标: _nav_context 里 `unread_count` 硬编码 0 (带 TODO), 于是
    _navbar.html 的铃铛永远不亮 —— 通知 API 本身是好的, 实测能返回正确
    unread_count, 断的是最后 200 米的接线。

P8  overview SSE: 裸 `while True`, 无心跳 / 无 retry / 无寿命上限。实测挂 45s
    不超时也不出数据(签名没变), nginx proxy_read_timeout 300s 到点静默掐断,
    浏览器每 5 分钟被迫重连一次。

导出刻意不动 LLM, 纯读 + 一次写 —— 所以这个"缺口"是白捡的。
"""
from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from lib import exporter, storage
from review_ui import app as review_app
from review_ui import dashboard

_TEMPLATES = Path(review_app.__file__).resolve().parent / "templates"

# ── 1. lib/exporter.py: 与抽离前的 CLI 逐字节一致 ────────────────────────
#
# 为什么用「测试里重写一遍老算法」而不是硬编码一段期望字符串: 硬编码只能
# 锁住这一本书的形状, 换一本书就露馅; 重写老算法锁的是**格式本身**,
# 以后谁改了分隔符/字段顺序/标题层级都会红。


def _legacy_build(book: str) -> str:
    """抽离前 novel.py cmd_export 里的拼装逻辑, 原样搬过来当基准。"""
    chapters = storage.list_chapters(book)
    assert chapters, "基准函数只在有章节的书上跑"
    cfg = storage.read_json(book, "config.json") or {}
    lines = [f"# {cfg.get('book_name', book)}\n",
             f"\n## 基本信息\n",
             f"- 题材：{cfg.get('genre')}\n",
             f"- 基调：{cfg.get('tone')}\n",
             f"- 主角：{cfg.get('protagonist')}\n",
             f"- 字数：{sum(c['word_count'] for c in chapters)} 字\n",
             f"\n---\n"]
    for ch in chapters:
        text = storage.read_chapter(book, ch["id"]) or ""
        lines.append(f"\n{text}\n\n---\n")
    return "".join(lines)


_CHAPTERS = {
    "ch_001": "## 第一章 雨夜\n\n裴照推开门。",
    "ch_002": "## 第二章 北岸\n\n灯塔灭了。",
    "ch_003": "## 第三章 回声\n\n有人应了一声。",
}


@pytest.fixture
def zh_book(tmp_projects_root):
    """一本中文书名的测试书 —— 专门用来打 RFC 5987 那条路径。"""
    storage.init_project("zh_book", {
        "book_name": "长安夜行录",
        "genre": "悬疑",
        "tone": "冷峻",
        "protagonist": "裴照",
        "target_chapters": 3,
    })
    for ch_id, text in _CHAPTERS.items():
        storage.write_chapter("zh_book", ch_id, text)
    return "zh_book"


class TestExporterMatchesLegacyCli:
    def test_markdown_is_byte_identical_to_legacy(self, zh_book):
        md, n_ch, n_words = exporter.build_full_book_markdown(zh_book)

        assert md == _legacy_build(zh_book), (
            "抽出来的拼装结果与抽离前的 CLI 不一致 —— 有脚本按这个格式抓稿子"
        )
        assert n_ch == 3
        assert n_words > 0

    def test_heading_and_info_block_present(self, zh_book):
        md, _n, _w = exporter.build_full_book_markdown(zh_book)

        assert md.startswith("# 长安夜行录\n"), "书名必须是 H1 开头"
        assert "\n## 基本信息\n" in md
        for field in ("题材：悬疑", "基调：冷峻", "主角：裴照"):
            assert field in md, f"基本信息缺 {field}"

    def test_chapter_headings_in_order(self, zh_book):
        md, _n, _w = exporter.build_full_book_markdown(zh_book)

        positions = [md.index(f"## 第{n}章") for n in ("一", "二", "三")]
        assert positions == sorted(positions), "章节顺序必须与 ch_001/002/003 一致"

    def test_separators_present_and_ordered(self, zh_book):
        """`---` 的个数与位置是格式的一部分: 1 处分隔基本信息 + 每章 1 处。"""
        md, n_ch, _w = exporter.build_full_book_markdown(zh_book)

        assert md.count("\n---\n") == 1 + n_ch, (
            f"分隔符应有 {1 + n_ch} 处, 实际 {md.count(chr(10) + '---' + chr(10))} 处"
        )
        # 分隔符依次夹在各章之间, 最后一处收尾
        sep = [i for i in range(len(md)) if md.startswith("\n---\n", i)]
        first_text = md.index("## 第一章")
        last_text = md.index("## 第三章")
        assert sep[0] < first_text, "基本信息之后应先有一处分隔"
        assert sep[-1] > last_text, "最后一章之后应还有收尾分隔"

    def test_word_count_is_sum_of_chapters(self, zh_book):
        _md, _n, words = exporter.build_full_book_markdown(zh_book)
        expected = sum(storage.list_chapters(zh_book)[i]["word_count"] for i in range(3))
        assert words == expected

    def test_empty_book_raises_not_found(self, tmp_projects_root):
        """没有章节不是「导出空文件」, 是明确的 NOT_FOUND。"""
        from lib.errors import ErrorCode, NovelError

        with pytest.raises(NovelError) as ei:
            exporter.build_full_book_markdown("test_book")
        assert ei.value.code == ErrorCode.NOT_FOUND

    def test_default_filename_is_the_cli_convention(self):
        assert exporter.default_filename("zh_book") == "zh_book_全书.md"


class TestCliExportUnchanged:
    """CLI 必须保持原样 —— 有用户脚本按文件名和两行控制台输出抓稿子。"""

    def test_writes_expected_file(self, zh_book, capsys):
        from novel import cmd_export

        cmd_export(argparse.Namespace(book=zh_book))
        out = capsys.readouterr().out

        p = storage.project_root(zh_book) / f"{zh_book}_全书.md"
        assert p.exists(), f"必须仍写 {zh_book}_全书.md"
        assert p.read_text(encoding="utf-8") == _legacy_build(zh_book)

    def test_prints_same_two_lines(self, zh_book, capsys):
        from novel import cmd_export

        cmd_export(argparse.Namespace(book=zh_book))
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]

        assert len(lines) == 2, f"控制台应仍是两行, 实际 {len(lines)}: {lines}"
        assert lines[0].startswith("✓ 已导出: "), f"首行变了: {lines[0]!r}"
        assert lines[0].endswith(f"{zh_book}_全书.md"), f"首行没带文件名: {lines[0]!r}"
        assert re.fullmatch(r"  3 章 \| \d+ 字", lines[1]), f"次行变了: {lines[1]!r}"


# ── 2. 导出路由 ─────────────────────────────────────────────────────────

_CD_RE = re.compile(r"filename\*=UTF-8''(.*?)(?:\s*;|\s*$)", re.IGNORECASE)


class TestExportApi:
    def test_returns_markdown_and_stats(self, client, zh_book):
        r = client.get(f"/api/export/{zh_book}")

        assert r.status_code == 200
        data = r.get_json()
        assert set(data) == {"ok", "filename", "chapters", "words", "markdown"}
        assert data["ok"] is True
        assert data["filename"] == f"{zh_book}_全书.md"
        assert data["chapters"] == 3
        assert data["words"] > 0
        assert data["markdown"] == _legacy_build(zh_book)

    def test_no_chapters_is_a_sane_error_not_500(self, client):
        """test_book 由 conftest 建好但没有章节 —— 必须是 404 JSON。"""
        r = client.get("/api/export/test_book")

        assert r.status_code != 500, "没有章节是正常业务状态, 不该 500"
        assert r.status_code == 404
        assert r.get_json()["error"] == "not_found"

    def test_unknown_book_uses_standard_error_contract(self, client):
        r = client.get("/api/export/does_not_exist")

        assert r.status_code == 404
        body = r.get_json()
        # 与 app.py err_404 的 /api/ 分支同一套契约
        assert body["error"] == "not_found"
        assert "does_not_exist" in body["message"]
        assert body["path"].endswith("/does_not_exist")


class TestExportDownload:
    def test_download_is_markdown(self, client, zh_book):
        r = client.get(f"/api/export/{zh_book}/download")

        assert r.status_code == 200
        assert r.mimetype == "text/markdown"
        assert r.get_data(as_text=True) == _legacy_build(zh_book)

    def test_content_disposition_is_rfc5987(self, client, zh_book):
        r = client.get(f"/api/export/{zh_book}/download")
        assert r.status_code == 200, "拿不到响应就先别谈响应头"
        cd = r.headers["Content-Disposition"]

        assert cd.startswith("attachment;")
        m = _CD_RE.search(cd)
        assert m, f"缺少 RFC 5987 的 filename*=: {cd!r}"
        # 裸的非 ASCII filename= 会被中间层截成乱码, 这里只允许 ASCII 回退名
        bare = re.search(r'(?<![\*\w])filename="([^"]*)"', cd)
        assert bare, f"缺少 ASCII 回退 filename=: {cd!r}"
        assert bare.group(1).isascii(), f"回退名里混进了非 ASCII: {bare.group(1)!r}"

    def test_chinese_filename_round_trips(self, client, zh_book):
        """中文书名经 percent-decode 后必须与原名逐字符相等。"""
        r = client.get(f"/api/export/{zh_book}/download")
        assert r.status_code == 200, "拿不到响应就先别谈响应头"
        quoted = _CD_RE.search(r.headers["Content-Disposition"]).group(1)

        assert "%" in quoted, "含中文的文件名必须被 percent-encode"
        assert unquote(quoted, encoding="utf-8") == f"{zh_book}_全书.md"

    def test_download_also_404s_on_empty_book(self, client):
        assert client.get("/api/export/test_book/download").status_code == 404


# ── 2b. 导出路由确实在全局鉴权闸门后面 ──────────────────────────────────
#
# 需求写的是"before_request 已经兜住了, 不用另写 auth"。这句得验, 不能假定:
# 导出是「把成稿整本拿走」的出口, 漏在闸门外面 = 整本书对外公开。


@pytest.fixture
def gated_client(tmp_projects_root, monkeypatch):
    """auth.enabled=True 的 client(conftest 的 client 是关鉴权的)。"""
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True, "user": "tester", "password": "s3cret",
    })
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-secret-stable"
    with review_app.app.test_client() as c:
        yield c


class TestExportRoutesAreBehindTheAuthGate:
    @pytest.mark.parametrize("path", [
        "/api/export/{book}",
        "/api/export/{book}/download",
    ])
    def test_unauthenticated_is_401(self, gated_client, zh_book, path):
        r = gated_client.get(path.format(book=zh_book))

        assert r.status_code == 401, "新端点没被 _auth_gate 兜住"
        assert r.get_json()["error"] == "unauthorized"

    def test_basic_auth_header_gets_through(self, gated_client, zh_book):
        hdr = base64.b64encode(b"tester:s3cret").decode()
        r = gated_client.get(f"/api/export/{zh_book}",
                             headers={"Authorization": f"Basic {hdr}"})

        assert r.status_code == 200
        assert r.get_json()["chapters"] == 3


# ── 3. P3: 导航栏未读徽标 ───────────────────────────────────────────────


def _add_unread(book: str, user: str = "wei_chao", n: int = 1) -> None:
    from lib import comments as comm
    for i in range(n):
        comm.add_notification(book, user, "comment", f"未读 {i}", ref_chapter="ch_001")


class TestNavUnreadBadge:
    def test_badge_renders_when_unread(self, client, zh_book):
        _add_unread(zh_book, n=3)

        html = client.get(f"/book/{zh_book}").get_data(as_text=True)
        assert '<span class="topnav-badge">3</span>' in html, (
            "铃铛徽标没渲染 —— nav.unread_count 还是硬编码的 0"
        )

    def test_no_badge_when_all_read(self, client, zh_book):
        html = client.get(f"/book/{zh_book}").get_data(as_text=True)

        assert '<span class="topnav-badge">' not in html

    def test_badge_uses_cfg_reviewer_identity(self, client, zh_book):
        """徽标数的是 cfg.reviewer 那个人的未读, 不是别人的。"""
        cfg = storage.read_json(zh_book, "config.json") or {}
        cfg["reviewer"] = "wang_yan"
        storage.write_json(zh_book, "config.json", cfg)
        _add_unread(zh_book, user="wei_chao", n=2)
        _add_unread(zh_book, user="wang_yan", n=5)

        html = client.get(f"/book/{zh_book}").get_data(as_text=True)
        assert '<span class="topnav-badge">5</span>' in html, (
            "徽标应按 cfg.reviewer=wang_yan 计数"
        )

    def test_nav_call_failure_does_not_blank_the_page(self, client, zh_book, monkeypatch):
        """context_processor 每页都跑 —— 这里抛出去就是整站白屏。"""
        from lib import comments as comm

        def boom(*_a, **_k):
            raise RuntimeError("notifications store is on fire")

        monkeypatch.setattr(comm, "unread_count", boom)
        # monkeypatch 到 lib 模块上, _nav_context 走的是 `from lib import comments`
        _add_unread(zh_book, n=1)

        r = client.get(f"/book/{zh_book}")
        assert r.status_code == 200, "通知计数失败不得让页面 500"
        assert "长安夜行录" in r.get_data(as_text=True)
        assert '<span class="topnav-badge">' not in r.get_data(as_text=True)


# ── 4. P8: overview SSE 必须收得住 ─────────────────────────────────────


def _fast_stream_cfg(**over) -> dict:
    """把 overview SSE 的时钟调到测试尺度(秒级)。"""
    cfg = {
        "stream_poll_interval": 0.01,   # ×3 = 0.03s 一轮
        "stream_max_lifetime": 0.3,
        "stream_heartbeat_interval": 0.05,
        "stream_retry_ms": 1000,
    }
    cfg.update(over)
    return cfg


def _drain(resp, limit: int = 400) -> list[str]:
    """逐块迭代响应流(不是 get_data), 这才是「连着读」的真实姿势。"""
    frames = []
    for chunk in resp.response:
        frames.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        assert len(frames) < limit, (
            f"读了 {limit} 帧还没结束 —— 这次连接根本没有上限, 会一直占着 worker"
        )
    return frames


def _events(frames: list[str]) -> list[str]:
    """从帧里抽出 event 名; 无 event 的 data 帧记作 'message'。"""
    out = []
    for f in frames:
        m = re.search(r"^event: (\S+)", f, re.M)
        out.append(m.group(1) if m else "message")
    return out


class TestOverviewStreamTerminates:
    def test_stream_ends_within_max_lifetime(self, client, monkeypatch):
        monkeypatch.setattr(dashboard, "_dashboard_cfg", lambda: _fast_stream_cfg())

        r = client.get("/api/overview/stream")
        assert r.status_code == 200
        assert r.mimetype == "text/event-stream"

        frames = _drain(r)
        assert "end" in _events(frames), (
            f"到点必须发 `event: end` 主动收尾, 实际事件: {_events(frames)}"
        )
        assert frames[-1].startswith("event: end"), "end 必须是最后一帧"

    def test_sends_explicit_retry_field(self, client, monkeypatch):
        monkeypatch.setattr(dashboard, "_dashboard_cfg",
                            lambda: _fast_stream_cfg(stream_retry_ms=2500))

        frames = _drain(client.get("/api/overview/stream"))
        assert "retry: 2500" in frames[0], (
            f"首帧应显式下发重连间隔, 实际 {frames[0]!r}"
        )

    def test_heartbeat_when_state_does_not_change(self, client, monkeypatch):
        """签名不变时也得定期说话 —— 静默的连接会被中间层当成死连接。"""
        monkeypatch.setattr(dashboard, "_dashboard_cfg", lambda: _fast_stream_cfg())

        frames = _drain(client.get("/api/overview/stream"))
        heartbeats = [f for f in frames if f.startswith(": keepalive")]

        assert heartbeats, (
            f"全程无心跳帧 —— 状态没变时连接完全静默。收到的帧: {_events(frames)}"
        )

    def test_data_still_pushed_on_first_frame(self, client, monkeypatch):
        """补心跳不能顺手把原有的变更推送弄坏。"""
        monkeypatch.setattr(dashboard, "_dashboard_cfg", lambda: _fast_stream_cfg())

        frames = _drain(client.get("/api/overview/stream"))
        assert any(f.startswith("data: ") for f in frames), "首帧仍应推一次状态"

    def test_bad_config_falls_back_instead_of_500(self, client, monkeypatch):
        """手写 yaml 写成 "15s" / null 都不能让端点崩。

        故意只把 heartbeat 配坏: 寿命保持有效短值, 否则回落的是 1800s 默认,
        测试就得等 30 分钟。回落本身正确 —— 断言的是「不 500 且仍会收尾」。
        """
        monkeypatch.setattr(dashboard, "_dashboard_cfg",
                            lambda: _fast_stream_cfg(stream_heartbeat_interval="15s"))

        r = client.get("/api/overview/stream")
        assert r.status_code == 200
        assert "end" in _events(_drain(r)), "配置非法时应回落到默认值, 而不是炸掉"


class TestOverviewStreamClientJs:
    """前端必须认得 `end`, 否则服务端收尾后页面就永远停在"连接断开"。"""

    def test_client_handles_end_event(self):
        html = (_TEMPLATES / "overview.html").read_text(encoding="utf-8")

        assert 'addEventListener("end"' in html, \
            "overview.html 没有监听 `end` 事件 —— 服务端收尾后前端会一直显示断开"
        assert "connectSSE" in html, "重连仍应走既有的 connectSSE"
