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
    """L64/L65 坑: enabled=True 但 password 空 → 视为配置错误, 全部放行."""
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


# ──────────────── L64/L65 补救: enabled 但 password 空 → 放行 ────────────────

class TestAuthEnabledButEmptyPassword:
    """L64/L65 第一次启动默认 config 把 review_ui 锁死的补救测试."""

    def test_api_passes_through(self, client, auth_enabled_but_empty_password, tmp_projects_root):
        """enabled=True + password='' → 全部放行, 不应该 401."""
        r = client.get("/api/projects")
        assert r.status_code == 200

    def test_index_passes_through(self, client, auth_enabled_but_empty_password):
        r = client.get("/", follow_redirects=False)
        # 放行 → 200 (不走 auth gate)
        assert r.status_code in (200, 302)

    def test_login_skipped(self, client, auth_enabled_but_empty_password):
        """password 空 → /login 跳到首页, 不让看到登录表单."""
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert "/" in r.headers["Location"]