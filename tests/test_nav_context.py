"""tests/test_nav_context.py — 导航栏高亮必须跟着当前页面走。

Phase 1 的静默回归: review_ui/app.py 的 _nav_context 用精确匹配
    if endpoint == ep
判断激活哪个导航项, 但 Blueprint 会给 endpoint 加蓝图前缀:
    book_page       -> book_page                  (留在 app.py, 正常)
    dashboard_page  -> dashboard.dashboard_page   (搬进蓝图后失效)
    overview_page   -> dashboard.overview_page    (同上)

后果: 进这两页时导航高亮消失 —— 不抛异常、不影响功能, 只是 UI 不对。
单测几乎不可能注意到这种问题, 所以这里走真实 HTTP 请求, 断言渲染出的 HTML。

修法在 app.py: endpoint 取末段比较 (rsplit('.', 1)[-1])。
本测试在修复前会红: dashboard / overview 两页的 active 标记缺失。
"""
from __future__ import annotations

import re

import pytest

from review_ui import app as review_app

BOOK = "nav_probe_book"


def _payload(name: str = BOOK, **over) -> dict:
    p = {
        "name": name,
        "book_name": "导航探针",
        "genre": "玄幻",
        "main_plot": "一个普通的故事",
        "target_chapters": 3,
        "words_per_chapter": 1000,
        "language": "zh",
    }
    p.update(over)
    return p


@pytest.fixture
def client_with_book(tmp_projects_root, auth_disabled):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-nav"
    with review_app.app.test_client() as c:
        r = c.post("/api/projects", json=_payload())
        assert r.status_code in (200, 201, 409), f"建 book 失败: {r.status_code} {r.data[:200]}"
        yield c


def _active_links(html: str) -> list[str]:
    """取出所有带 active 类的导航链接文本, 便于断言。"""
    out = []
    for m in re.finditer(r'<a[^>]*class="[^"]*\bactive\b[^"]*"[^>]*>(.*?)</a>', html, re.S):
        txt = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        out.append(txt)
    return out


class TestDashboardHighlights:
    def test_dashboard_page_marks_dashboard_active(self, client_with_book):
        r = client_with_book.get(f"/dashboard/{BOOK}")
        assert r.status_code == 200, f"页面返回 {r.status_code}"
        active = _active_links(r.get_data(as_text=True))
        assert active, "导航栏一个 active 都没有 —— book_active 没注入"
        # dashboard 链接是第 4 个 book 链接, 用其 href 判定更稳
        html = r.get_data(as_text=True)
        m = re.search(
            r'<a href="/dashboard/[^"]*"[^>]*class="([^"]*\btopnav-link\b[^"]*)"', html)
        assert m, "找不到 dashboard 导航链接"
        assert "active" in m.group(1), (
            f"进了 /dashboard/{BOOK} 但 dashboard 链接没有 active —— "
            f"endpoint 前缀未被处理。实际 class={m.group(1)!r}"
        )


class TestOverviewHighlights:
    def test_overview_page_marks_overview_active(self, client_with_book):
        r = client_with_book.get("/overview")
        assert r.status_code == 200, f"页面返回 {r.status_code}"
        html = r.get_data(as_text=True)
        m = re.search(r'<a href="/overview"[^>]*class="([^"]*\btopnav-link\b[^"]*)"', html)
        assert m, "找不到 overview 导航链接"
        assert "active" in m.group(1), (
            f"进了 /overview 但该链接没有 active —— "
            f"endpoint 前缀未被处理。实际 class={m.group(1)!r}"
        )


class TestUnprefixedPagesUnaffected:
    """留在 app.py 的页面路由(endpoint 无前缀)不能被改坏。"""

    @pytest.mark.parametrize("url,href", [
        (f"/book/{BOOK}", "/book/"),
        (f"/outline/{BOOK}", "/outline/"),
        (f"/entities/{BOOK}", "/entities/"),
    ])
    def test_book_pages_mark_correct_link(self, client_with_book, url, href):
        r = client_with_book.get(url)
        if r.status_code == 404:
            pytest.skip(f"{url} 需要该书的特定资源, 跳过 (非本测试关注点)")
        html = r.get_data(as_text=True)
        m = re.search(
            rf'<a href="{re.escape(href)}[^"]*"[^>]*class="([^"]*\btopnav-link\b[^"]*)"',
            html)
        assert m, f"{url} 页面里找不到 href={href} 的导航链接"
        assert "active" in m.group(1), (
            f"{url} 的导航高亮丢失, class={m.group(1)!r}"
        )

    def test_home_marks_home_active(self, client_with_book):
        html = client_with_book.get("/").get_data(as_text=True)
        # 注意: 模板里有【两个】href="/", 品牌链接是 topnav-brand,
        # 首页导航是 topnav-link。只认后者, 否则会匹配错对象。
        m = re.search(r'<a href="/"[^>]*class="([^"]*\btopnav-link\b[^"]*)"', html)
        assert m, "找不到 href=/ 的导航链接"
        assert "active" in m.group(1), (
            f"首页应高亮 global 导航, 实际 class={m.group(1)!r}"
        )