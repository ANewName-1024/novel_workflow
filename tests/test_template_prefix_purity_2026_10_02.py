"""
2026-10-02: 模板硬编码根路径 —— 一整类 bug 的结构性防线
=====================================================

**已确认存在的线上故障** (用户报「登录显示报错」)

背景
----
小说 UI 早期寄生在 nginx 的 `/novel/` 前缀后
(`proxy_pass .../9180/` 带尾斜杠 => 剥掉前缀)。
Flask 侧有 `ProxyFix(x_prefix=1)`, 所以 `url_for()` 生成的链接是带前缀的。
但模板里有 **22 处** `href/action/src="/..."` 和 **35 处** `fetch("/api/...")`
写死了根相对路径 —— 这些不经过 `url_for()`, 前缀一丢就打到源站根目录,
而那里是 nginx 上**另一个** Node 应用(21112), 返回 404 Error。

最致命的一处是 `login.html` 的 `action="/login"`: 表单提交到源站根目录,
落在别的应用上, **看起来完全像"密码输错了"**。

2026-10-02 的处置是双管齐下:
1. nginx 给小说 UI 分配独立端口 9081, 独占根目录, 结构上消灭这一类问题
2. 把模板里所有硬编码改成 `url_for`, app 变得可移植
3. `_base.html` 加一层 fetch 前缀兼容, 一处盖住全部 35 个调用点,
   以后新写的 fetch 也不会重新踩坑

为什么值得钉死
--------------
这些不是"某个页面的一个链接坏了", 是**一整类**。只测登录页会漏掉
章节翻页、大纲、实体、通知、流水线面板……而它们的表现同样是
"404 / 白屏 / 点了没反应", 排查成本极高。

反向验证: 把某个模板改回 `href="/xxx"`, 对应用例必须转红。
"""
import re
from pathlib import Path

import pytest

from review_ui import app as review_app

TEMPLATES = Path(review_app.__file__).resolve().parent / "templates"
PREFIX = "/novel"
BOOK = "test_book"


# href/action/src 后面紧跟一个以 / 开头的引号串 = 写死的根相对路径。
# 只匹配这三个属性 —— 它们是浏览器唯一会直接发起导航的标记。
HARDCODED = re.compile(r"""\b(?:href|action|src)\s*=\s*["'](/[^"'/][^"']*)["']""")

# 渲染后的 HTML 已经有了前缀, 所以要判的是"**没带**前缀"的那些。
# 上面那条 HARDCODED 只适合扫源码 —— 直接拿去扫渲染结果会把
# href="/novel/..." 这种**完全正确**的链接也判成违规(实测踩过)。
HARDCODED_RENDERED = re.compile(
    r"""\b(?:href|action|src)\s*=\s*["'](/(?!novel/)[^"'/][^"']*)["']"""
)


def _templates():
    return sorted(TEMPLATES.glob("*.html"))


# ── 防线 1: 模板里不允许再出现硬编码根路径 ───────────────────────────────
#
# 这条断言的是**一个具体形状** (href="/..." 这个 token),
# 不是模仿通用 linter: 这个 app 的模板全部由我们控制, 已全部转换完,
# 断言"零出现"是有确定答案的。真要写裸路径时, 这里的失败会明确指出
# 是哪个文件哪一处, 促使人改用 url_for。

class TestNoHardcodedRootPathsInTemplates:
    def test_which_templates_exist(self):
        """先确认 glob 真的匹配到了文件, 避免下面全部空跑变绿。"""
        names = {p.name for p in _templates()}
        assert {"_base.html", "_navbar.html", "login.html", "book.html",
                "chapter.html", "index.html", "outline.html",
                "entities.html", "notifications.html", "overview.html"} <= names

    @pytest.mark.parametrize("tpl", _templates(), ids=lambda p: p.name)
    def test_no_hardcoded_absolute_href(self, tpl):
        html = tpl.read_text(encoding="utf-8")
        hits = []
        for m in HARDCODED.finditer(html):
            line = html[:m.start()].count("\n") + 1
            hits.append(f"L{line}: {m.group(0)}")
        assert not hits, (
            f"{tpl.name} 里有写死的根相对路径, 带前缀部署下会打到源站根目录:\n"
            + "\n".join(hits)
            + "\n  改用 {{ url_for('endpoint', ...) }}"
        )


# ── 防线 2: 带前缀渲染时, 页面里不能出现未加前缀的链接 ────────────────────
#
# 防线 1 是静态的(扫源码)。这条是**功能性的**: 真把页面渲染出来看。
# 两者互补 —— 静态扫不到 url_for 用错端点的情况, 渲染扫不到
# 以后有人绕过 url_for 直接拼字符串拼出来的新情况。

@pytest.fixture
def client(monkeypatch, tmp_projects_root):
    """已登录的客户端。fixture 必须放模块级 —— 放类里时 self.client
    拿到的是 FixtureFunctionDefinition 而不是实例(实测踩过)。"""
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True, "user": "t", "password": "p",
    })
    review_app.app.config["TESTING"] = True
    with review_app.app.test_client() as c:
        c.post("/login", data={"user": "t", "password": "p"},
               headers={"X-Forwarded-Prefix": PREFIX})
        yield c


def _assert_clean(client, path, headers=None):
    """渲染一页并断言 HTML 里没有裸的 href="/"。返回 HTML 供进一步断言。"""
    if headers is None:
        headers = {"X-Forwarded-Prefix": PREFIX}
    r = client.get(path, headers=headers)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    html = r.get_data(as_text=True)
    # 只看真正的标记属性; 正文里出现 /api/xxx 是给人看的说明文字, 无关
    bad = [m.group(0) for m in HARDCODED_RENDERED.finditer(html)]
    assert not bad, f"{path} 渲染出了未加前缀的链接: {bad}"
    return html


class TestRenderedPagesArePrefixClean:
    """把每个主要页面都渲染一遍, 断言 HTML 里没有裸的 href="/"。"""

    def test_index(self, client):
        html = _assert_clean(client, "/")
        assert f'href="{PREFIX}/book/{BOOK}"' in html, "书籍卡片链接没带前缀"

    def test_navbar_links(self, client):
        html = _assert_clean(client, f"/book/{BOOK}")
        for frag in (f'href="{PREFIX}/overview"',
                     f'href="{PREFIX}/llm"',
                     f'href="{PREFIX}/book/{BOOK}"',
                     f'href="{PREFIX}/outline/{BOOK}"',
                     f'href="{PREFIX}/entities/{BOOK}',
                     f'href="{PREFIX}/dashboard/{BOOK}"',
                     f'href="{PREFIX}/notifications/{BOOK}"'):
            assert frag in html, f"导航缺少 {frag}"

    def test_static_assets(self, client):
        html = _assert_clean(client, "/")
        assert f'href="{PREFIX}/static/css/main.css"' in html
        assert f'src="{PREFIX}/static/js/common.js"' in html


# ── 防线 3: fetch 兼容层必须存在且逻辑正确 ───────────────────────────────
#
# 35 处 fetch("/api/...") 没法逐个改成 url_for (在 JS 里), 也不该逐个改 ——
# 以后新加的 fetch 还是会写根相对路径。所以用一层 shim 统一处理。
# shim 必须早于 common.js 加载, 否则 common.js 里的 fetch 拿不到包装。

class TestFetchPrefixShim:
    def _base(self):
        return (TEMPLATES / "_base.html").read_text(encoding="utf-8")

    def test_shim_present(self):
        assert "window.fetch = function" in self._base()

    def test_shim_uses_script_root(self):
        assert "request.script_root" in self._base()

    def test_shim_skips_protocol_relative_and_double_prefix(self):
        """//example.com 是协议相对 URL, 补前缀会把它变成跨站请求。"""
        b = self._base()
        assert 'input.charAt(1) !== "/"' in b, "没排除协议相对 URL"
        assert "input.indexOf(ROOT) !== 0" in b, "没排除重复补前缀"

    def test_shim_loaded_before_common_js(self):
        """顺序错了 shim 形同虚设 —— common.js 里的 fetch 不会被包装。"""
        b = self._base()
        shim_at = b.index("window.fetch = function")
        common_at = b.index("js/common.js")
        assert shim_at < common_at, "fetch 兼容层必须排在 common.js 之前"

    def test_shim_is_noop_without_prefix(self, client):
        """根部署(现在的 9081)下 ROOT 为空, shim 直接 return, 不包装 fetch。

        注意: 必须请求一个真正渲染 _base.html 的页面。`/` 未登录会被鉴权
        闸门 302 走, 拿到的是 Flask 的重定向页而不是模板 —— 实测踩过。
        login.html 也不继承 _base.html, 所以用已登录的书籍页。
        """
        r = client.get(f"/book/{BOOK}")   # 不带 X-Forwarded-Prefix
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "var ROOT =" in html
        assert "var ROOT = \"\";" in html, \
            "无前缀部署时 ROOT 应为空字符串, shim 直接 return"
