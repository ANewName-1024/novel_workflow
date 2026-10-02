"""
2026-10-02: 带前缀部署下登录成功后跳到别的应用
=============================================

**已确认存在的线上故障** (用户报「登录显示报错」)

现象
----
VPS 上小说 UI 挂在 nginx 的 `/novel/` 前缀后面:

    location ^~ /novel/ {
        proxy_pass http://127.0.0.1:9180/;      # 尾斜杠 => 剥掉 /novel 前缀
        proxy_set_header X-Forwarded-Prefix /novel;
    }
    location / { proxy_pass http://127.0.0.1:21112; }   # 源站根目录是**另一个**应用

`app.py` 里 `ProxyFix(x_prefix=1)` 把 X-Forwarded-Prefix 写进 SCRIPT_NAME,
所以 `url_for()` 生成的链接是带前缀的 —— 鉴权拦截那一步没问题:

    GET /novel/        -> 302 Location: /novel/login?next=/     ✅

但 login() 成功后走的是 `_safe_next()` + `redirect(nxt)`, 那里是**裸字符串
重定向**, 不经过 url_for(), 于是前缀丢失:

    POST /novel/login  -> 302 Location: /                       ❌

浏览器跟到 `/`, 那是 nginx 上 21112 的 Node 应用, 返回 404 Error 页。
于是: **密码是对的、session 也签发了, 但用户被弹到一个 Error 页面**,
看起来就像「登录失败」。

修法
----
`_safe_next()` 在已通过站内校验的相对路径前补回 `request.script_root`。
不放宽任何原有拒绝条件 (外链、`//`、反斜杠、控制字符一律仍被拒)。

反向验证: 把 `_safe_next` 里的 script_root 拼接删掉, 用例必须变红。
"""
import pytest

from review_ui import app as review_app


PREFIX = "/novel"


def _password() -> str:
    """用一个非空密码, 让 _auth_misconfigured() 不成立。"""
    return "test-only-password"


@pytest.fixture
def client(monkeypatch):
    # 必须直接打桩 _get_auth: 它读的是 config 里的 review_ui.auth,
    # 而 enabled 默认 False —— 只设环境变量的话 login() 会在鉴权分支之前
    # 就短路 redirect(url_for("index")), next 参数根本没被读到, 用例会假绿。
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True,
        "user": "weichao",
        "password": _password(),
    })
    review_app.app.config["TESTING"] = True
    with review_app.app.test_client() as c:
        yield c


# ── 端到端: 登录成功后的 Location 必须带前缀 ──────────────────────────────

class TestLoginRedirectHonorsForwardedPrefix:
    """这是用户实际撞到的那条路径。"""

    def test_post_login_redirect_keeps_prefix(self, client):
        r = client.post(
            "/login?next=/",
            data={"user": "weichao", "password": _password()},
            headers={"X-Forwarded-Prefix": PREFIX},
        )
        assert r.status_code == 302
        # 修复前是 "/", 浏览器会落到 nginx 上另一个应用 -> 404 Error
        assert r.headers["Location"] == f"{PREFIX}/"

    def test_post_login_redirect_keeps_prefix_on_deep_path(self, client):
        r = client.post(
            "/login?next=/book/some-book",
            data={"user": "weichao", "password": _password()},
            headers={"X-Forwarded-Prefix": PREFIX},
        )
        assert r.status_code == 302
        assert r.headers["Location"] == f"{PREFIX}/book/some-book"

    def test_auth_gate_next_param_carries_prefix(self, client):
        """鉴权拦截时塞进 next= 的路径也要带前缀, 否则登录后回不到原处。"""
        r = client.get("/book/some-book", headers={"X-Forwarded-Prefix": PREFIX})
        assert r.status_code == 302
        assert r.headers["Location"] == f"{PREFIX}/login?next={PREFIX}/book/some-book"


# ── 表单 action 本身必须带前缀 (第一轮修复漏掉的就是这里) ────────────────
#
# 背景: 第一轮只修了服务端 `_safe_next`, 测试是 `client.post("/login?next=/")`
# —— 直接打路由, **绕过了浏览器实际依据的那份 HTML**。真实故障是
# login.html 里 `action="/login"` 写死, 浏览器 POST 到源站根目录,
# 被 nginx 派给 21112 那个 Node 应用 -> 404 Error。
# 所以防线必须钉**渲染结果**, 不是钉路由行为。

class TestLoginFormActionHonorsPrefix:
    def test_form_action_is_prefixed(self, client):
        r = client.get("/login?next=/book/x",
                       headers={"X-Forwarded-Prefix": PREFIX})
        html = r.get_data(as_text=True)
        assert 'action="/login"' not in html, \
            "表单 action 写死 /login, 带前缀部署下会跳到源站根目录"
        assert f'action="{PREFIX}/login' in html

    def test_form_action_keeps_next_param(self, client):
        r = client.get("/login?next=/book/x",
                       headers={"X-Forwarded-Prefix": PREFIX})
        html = r.get_data(as_text=True)
        assert "next=" in html, "next 参数丢了, 登录后回不到原页面"

    def test_form_action_without_prefix_is_plain(self, client):
        """本地开发(无前缀)不能被带坏。"""
        r = client.get("/login")
        html = r.get_data(as_text=True)
        assert 'action="/login"' in html
        assert f'action="{PREFIX}/login' not in html

    def test_logout_link_is_prefixed(self, client):
        # 注销链接在 book.html 的 {% if session.auth_user %} 块里,
        # 不先登录的话 book.html 根本不会渲染, 会被鉴权闸门先跳去登录页。
        client.post("/login",
                    data={"user": "weichao", "password": _password()},
                    headers={"X-Forwarded-Prefix": PREFIX})
        r = client.get("/book/test_book", headers={"X-Forwarded-Prefix": PREFIX})
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'href="/logout"' not in html, \
            "注销链接写死 /logout, 带前缀部署下点不动"
        assert f'href="{PREFIX}/logout"' in html


# ── 不带前缀的本地开发场景不能被带坏 ─────────────────────────────────────

class TestNoPrefixDeploymentUnchanged:
    def test_plain_redirect_without_prefix_header(self, client):
        r = client.post(
            "/login?next=/book/some-book",
            data={"user": "weichao", "password": _password()},
        )
        assert r.status_code == 302
        assert r.headers["Location"] == "/book/some-book"

    def test_root_without_prefix_header(self, client):
        r = client.post(
            "/login?next=/",
            data={"user": "weichao", "password": _password()},
        )
        assert r.status_code == 302
        assert r.headers["Location"] == "/"


# ── 幂等: 已经有前缀的不能被加成 /novel/novel/... ────────────────────────

class TestPrefixIsNotDoubled:
    def test_already_prefixed_candidate_untouched(self, client):
        r = client.post(
            f"/login?next={PREFIX}/book/x",
            data={"user": "weichao", "password": _password()},
            headers={"X-Forwarded-Prefix": PREFIX},
        )
        assert r.status_code == 302
        assert r.headers["Location"] == f"{PREFIX}/book/x"

    def test_candidate_equal_to_root_untouched(self):
        with review_app.app.test_request_context("/login",
                                                  environ_overrides={
                                                      "SCRIPT_NAME": PREFIX}):
            assert review_app._safe_next(PREFIX) == PREFIX


# ── 安全性不能因为这次修复而退步 ─────────────────────────────────────────

class TestOpenRedirectProtectionStillHolds:
    """_safe_next 现在会拼前缀, 拼之前必须仍然拒绝站外。"""

    def _ctx(self):
        return review_app.app.test_request_context(
            "/login", environ_overrides={"SCRIPT_NAME": PREFIX})

    def test_external_url_still_refused(self):
        with self._ctx():
            assert review_app._safe_next("https://evil.com") == f"{PREFIX}/"

    def test_protocol_relative_still_refused(self):
        """前缀拼接绝不能把 //evil.com 变成 /novel///evil.com 之外的任何东西。"""
        with self._ctx():
            assert review_app._safe_next("//evil.com") == f"{PREFIX}/"

    def test_backslash_still_refused(self):
        with self._ctx():
            assert review_app._safe_next("/\\evil.com") == f"{PREFIX}/"

    def test_control_chars_still_refused(self):
        with self._ctx():
            assert review_app._safe_next("/a\nb") == f"{PREFIX}/"

    def test_empty_still_refused(self):
        with self._ctx():
            assert review_app._safe_next("") == f"{PREFIX}/"
            assert review_app._safe_next(None) == f"{PREFIX}/"
