"""test_review_ui_auth.py - review_ui M5 Basic Auth + session 单测."""
import base64
import pytest

from review_ui import app as review_app


# ──────────────── fixtures ────────────────

@pytest.fixture
def auth_enabled(monkeypatch):
    """Patch app._get_auth() 模拟 auth.enabled=True."""
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True, "user": "tester", "password": "s3cret"
    })


@pytest.fixture
def auth_disabled(monkeypatch):
    """auth.enabled=False 场景."""
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": False, "user": "", "password": ""
    })


@pytest.fixture
def auth_enabled_but_empty_password(monkeypatch):
    """enabled=True 但 password 空 —— 这是【配置错误】, 不是「不设防」.

    2026-10-01 起行为反转, 见下方 TestAuthEnabledButEmptyPassword 的说明。
    """
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True, "user": "tester", "password": ""
    })


@pytest.fixture
def client(tmp_projects_root):
    """Flask test_client + TESTING 模式 + 稳定 secret_key (session 用)."""
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-secret-stable"
    with review_app.app.test_client() as c:
        yield c


# ──────────────── auth disabled (默认) ────────────────

class TestAuthDisabled:
    def test_index_accessible(self, client, auth_disabled):
        r = client.get("/")
        assert r.status_code == 200

    def test_books_api_accessible(self, client, auth_disabled, tmp_projects_root):
        r = client.get("/api/projects")
        assert r.status_code == 200
        # API 返回 {"ok": True, "projects": {<id>: {...}}} — nested lookup
        body = r.get_json()
        assert "projects" in body and "test_book" in body["projects"]

    def test_login_redirects_when_disabled(self, client, auth_disabled):
        """auth 关了就不让看到登录页."""
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert "/" in r.headers["Location"]


# ──────────────── auth enabled ────────────────

class TestAuthEnabledGate:
    def test_index_redirects_to_login(self, client, auth_enabled):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_static_passes_through(self, client, auth_enabled):
        r = client.get("/static/favicon.ico")
        # 不存在是 404, 不是 401/302 (static 走白名单)
        assert r.status_code in (200, 404)

    def test_login_endpoint_accessible(self, client, auth_enabled):
        r = client.get("/login")
        assert r.status_code == 200
        assert "登录" in r.data.decode("utf-8")

    def test_api_unauthed_returns_401_json(self, client, auth_enabled):
        r = client.get("/api/projects")
        assert r.status_code == 401
        data = r.get_json()
        assert data["error"] == "unauthorized"


class TestLogin:
    def test_valid_credentials_redirects(self, client, auth_enabled):
        r = client.post("/login", data={"user": "tester", "password": "s3cret"},
                        follow_redirects=False)
        assert r.status_code == 302
        assert "/" in r.headers["Location"]

    def test_valid_credentials_session_persists(self, client, auth_enabled):
        """登录后调 API 不再 401."""
        client.post("/login", data={"user": "tester", "password": "s3cret"})
        r = client.get("/api/projects")
        assert r.status_code == 200

    def test_invalid_password_returns_401(self, client, auth_enabled):
        r = client.post("/login", data={"user": "tester", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 401
        # 中文 "用户名或密码错" in body
        assert "密码" in r.data.decode("utf-8") or "\xe5\xaf\x86\xe7\xa0\x81" in r.data

    def test_empty_password_rejected(self, client, auth_enabled):
        r = client.post("/login", data={"user": "tester", "password": ""})
        assert r.status_code == 401

    def test_logout_clears_session(self, client, auth_enabled):
        client.post("/login", data={"user": "tester", "password": "s3cret"})
        # 已登录
        assert client.get("/api/projects").status_code == 200
        # 注销
        client.get("/logout")
        # 回到未登录
        r = client.get("/api/projects")
        assert r.status_code == 401


class TestBasicAuthHeader:
    def test_basic_auth_passes_through(self, client, auth_enabled):
        creds = base64.b64encode(b"tester:s3cret").decode("ascii")
        r = client.get("/api/projects",
                       headers={"Authorization": f"Basic {creds}"})
        assert r.status_code == 200

    def test_basic_auth_wrong_password_rejected(self, client, auth_enabled):
        creds = base64.b64encode(b"tester:NOPE").decode("ascii")
        r = client.get("/api/projects",
                       headers={"Authorization": f"Basic {creds}"})
        assert r.status_code == 401

    def test_basic_auth_malformed_returns_401(self, client, auth_enabled):
        """Authorization 头损坏 (非 base64) 不炸, 落到 401."""
        r = client.get("/api/projects",
                       headers={"Authorization": "Basic not-base64-!!!"})
        assert r.status_code == 401


# ────────── enabled=True 但 password 空 → 拒绝一切(2026-10-01 反转) ──────────

class TestAuthEnabledButEmptyPassword:
    """这个类的契约在 2026-10-01 被【刻意反转】, 请先读理由再改。

    旧契约(L64/L65 时期): enabled=True + password='' → 全部放行。
    出发点是善意的 —— 首次启动时若忘了设密码, 别把自己锁在门外。

    但同一个判断落到公网部署上就是灾难。config.yaml 里密码写的是
    `${REVIEW_UI_PASSWORD:-}`, 环境变量一没设就成空串, 于是:

        配错了  ==  静默关掉鉴权  ==  31 个写端点全部裸奔

    实测那台服务器就是这样: 无凭证从公网 http://8.137.116.121:9080/
    取到了真实小说正文, /api/approve /api/edit /DELETE /api/projects/<book>
    全部无凭证可调, 且服务端没有任何日志。

    现在: enabled=True + password 空 = 配置错误 → 拒绝一切请求 + log.error。
    「我想免密」有专门的显式表达: enabled=False。见 TestAuthDisabled。

    与 test_world_rule_consistency.py:71 同类 —— 那条断言也把错误行为
    锁成了期望值。测试通过不等于行为正确, 取决于契约本身对不对。
    """

    def test_api_is_refused_not_passthrough(self, client, auth_enabled_but_empty_password):
        """过去断言 200 放行; 现在必须拒绝。"""
        r = client.get("/api/projects")
        assert r.status_code == 503, \
            "enabled=True 而 password 为空属于配置错误, 绝不能放行"

    def test_api_refusal_is_json_and_explains(self, client, auth_enabled_but_empty_password):
        r = client.get("/api/projects")
        body = r.get_json()
        assert body is not None, "/api/ 的错误必须是 JSON, 前端要 res.json()"
        assert "REVIEW_UI_PASSWORD" in body["message"], \
            "错误信息要直接告诉运维该设哪个环境变量"

    def test_index_is_refused(self, client, auth_enabled_but_empty_password):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 503

    def test_write_endpoints_are_refused(self, client, auth_enabled_but_empty_password):
        """写端点是最要紧的: 审批 / 改稿 / 删项目。"""
        for path, payload in (
            ("/api/approve/test_book/ch_001", {"reviewer": "x"}),
            ("/api/reject/test_book/ch_001", {"reason": "x"}),
            ("/api/config/test_book", {"genre": "x"}),
        ):
            r = client.post(path, json=payload)
            assert r.status_code == 503, f"{path} 在鉴权配置错误时不得放行"

    def test_login_refuses_rather_than_skipping(self, client, auth_enabled_but_empty_password):
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 503

    def test_error_is_logged_loudly(self, client, auth_enabled_but_empty_password, caplog):
        import logging
        with caplog.at_level(logging.ERROR):
            client.get("/api/projects")
        recs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert recs, "配置错误必须留 ERROR 日志 —— 否则运维永远不知道发生了什么"
        assert any("REVIEW_UI_PASSWORD" in r.getMessage() for r in recs)


class TestAuthDisabledIsTheExplicitOptOut:
    """免密的正确表达是 enabled=False, 而不是 enabled=True + 空密码。"""

    def test_disabled_passes_through(self, client, auth_disabled):
        assert client.get("/api/projects").status_code == 200

    def test_disabled_login_redirects(self, client, auth_disabled):
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302