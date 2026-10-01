"""
2026-10-01 安全修复的反向防线
=============================

这些测试的存在理由: 每一条都对应一个**已确认存在过**的漏洞或缺陷。
故意把修复改回去, 对应用例必须变红。

背景见 docs/ 诊断报告。核心事实: 诊断当天那台服务器
`review_ui.auth.enabled=false` + 公网可达 + 无主机防火墙,
无凭证即可读到真实小说正文, 并调用 31 个 POST / 5 个 DELETE 端点。
"""
import base64
import pytest

from review_ui import app as review_app


# ── 开放重定向 ────────────────────────────────────────────────────────────

class TestNoOpenRedirect:
    """login() 过去直接 redirect(request.args.get('next'))。

    /login?next=https://evil.com  → 登录成功后被弹到站外。
    配合钓鱼页做 credential phishing。"""

    @pytest.fixture(autouse=True)
    def _ctx(self):
        # _safe_next 兜底时调 url_for("index"), 需要 request context
        with review_app.app.test_request_context("/login"):
            yield

    def test_external_url_is_refused(self):
        assert review_app._safe_next("https://evil.com") == "/"
        assert review_app._safe_next("http://evil.com/x") == "/"

    def test_protocol_relative_url_is_refused(self):
        """//evil.com 在浏览器里等价于跳到 evil.com。"""
        assert review_app._safe_next("//evil.com") == "/"
        assert review_app._safe_next("//evil.com/path") == "/"

    def test_backslash_is_refused(self):
        r"""/\evil.com 与 /\evil.com 会被部分浏览器当协议相对 URL。"""
        assert review_app._safe_next("/\\evil.com") == "/"
        assert review_app._safe_next("\\evil.com") == "/"

    def test_control_chars_refused(self):
        assert review_app._safe_next("/a\nb") == "/"
        assert review_app._safe_next("/a\rb") == "/"

    def test_empty_and_none_refused(self):
        assert review_app._safe_next("") == "/"
        assert review_app._safe_next(None) == "/"

    def test_relative_paths_still_allowed(self):
        """正常站内跳转不能被这个修复误伤。"""
        assert review_app._safe_next("/book/测试书籍") == "/book/测试书籍"
        assert review_app._safe_next("/") == "/"


# ── 凭空造书 ──────────────────────────────────────────────────────────────

class TestConfigEndpointCannotInventProjects:
    """POST /api/config/<book> 过去无 slug 校验、不要求书存在。

    read_json→None→{}→update(body)→write_json, 于是任意 POST 造出一本书,
    且完全绕开 bp/projects.py 里唯一的 _validate_book_slug。"""

    def test_rejects_bad_slug(self, client):
        for bad in ("__pycache__", ".git", "a" * 100, "has space", ""):
            r = client.post(f"/api/config/{bad}", json={"genre": "x"})
            assert r.status_code in (400, 404), f"slug={bad!r} 竟被放行"

    def test_rejects_nonexistent_book(self, client, tmp_projects_root):
        r = client.post("/api/config/never_created", json={"genre": "仙侠"})
        assert r.status_code == 404

    def test_nonexistent_book_dir_is_not_created(self, client, tmp_projects_root):
        client.post("/api/config/ghost_xyz", json={"genre": "仙侠"})
        assert not (tmp_projects_root / "ghost_xyz").exists(), \
            "一个本该 404 的请求把项目目录建出来了"

    def test_existing_book_still_writable(self, client, tmp_projects_root):
        r = client.post("/api/config/test_book", json={"genre": "科幻"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_empty_body_still_400(self, client, tmp_projects_root):
        assert client.post("/api/config/test_book", json={}).status_code == 400


# ── 反射型 XSS ────────────────────────────────────────────────────────────

class TestNoReflectedXSS:
    """dashboard_page 的 404 分支过去是 f"<h1>项目 [{book}] 不存在</h1>",
    book 直接来自 URL 且未经转义。"""

    PAYLOAD = '<img src=x onerror=alert(1)>'

    def test_dashboard_404_does_not_reflect_raw_html(self, client, tmp_projects_root):
        r = client.get(f"/dashboard/{self.PAYLOAD}")
        assert r.status_code == 404
        body = r.get_data(as_text=True)
        assert "<img src=x onerror" not in body, \
            "book 被原样拼进 HTML —— 反射型 XSS 回来了"
        assert "&lt;img" in body or "不存在" in body, \
            "应当走模板转义输出"

    def test_script_tag_payload_escaped(self, client, tmp_projects_root):
        r = client.get("/dashboard/<script>alert(1)</script>")
        body = r.get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body

    def test_no_bare_html_fstrings_in_blueprints(self):
        """结构性防线: review_ui/ 下不允许再出现 return f"<..>" 这种裸拼接。"""
        import os, re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, "review_ui")):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                for i, ln in enumerate(
                        open(fp, "rb").read().decode("utf-8").splitlines(), 1):
                    if re.search(r'return\s+f?["\']<\w', ln):
                        bad.append(f"{os.path.relpath(fp, root)}:{i}")
        assert not bad, f"又出现裸 HTML 拼接: {bad}"


# ── 请求参数脏值 ──────────────────────────────────────────────────────────

class TestIntArgValidation:
    """过去 10 处直接 int(request.args.get(...)), 脏输入 → ValueError →
    err_500, 用户看到「服务器错误」而不是「参数非法」。"""

    def test_helper_rejects_garbage(self):
        from review_ui.core import _int_arg
        from werkzeug.exceptions import BadRequest
        app_ctx = review_app.app.test_request_context("/?n=abc")
        with app_ctx:
            with pytest.raises(BadRequest):
                _int_arg("n", 0)

    def test_helper_respects_bounds(self):
        from review_ui.core import _int_arg
        from werkzeug.exceptions import BadRequest
        with review_app.app.test_request_context("/?n=9999"):
            with pytest.raises(BadRequest):
                _int_arg("n", 0, lo=0, hi=100)

    def test_helper_returns_default_when_absent(self):
        from review_ui.core import _int_arg
        with review_app.app.test_request_context("/"):
            assert _int_arg("missing", 42) == 42

    def test_chapter_window_garbage_is_400_not_500(self, client, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 第一章\n\n正文一行\n正文二行\n")
        r = client.get("/api/chapter/test_book/ch_001/context?window=abc")
        assert r.status_code == 400, f"期望 400, 实得 {r.status_code}"

    def test_chapter_line_garbage_is_400_not_500(self, client, tmp_projects_root):
        from lib import storage
        storage.write_chapter("test_book", "ch_001", "## 第一章\n\n正文\n")
        r = client.get("/api/chapter/test_book/ch_001/context?line=abc")
        assert r.status_code == 400

    def test_no_bare_int_request_arg_left(self):
        """结构性防线: 不允许再直接 int()/float() 取请求参数。"""
        import os, re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bad = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, "review_ui")):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py") or fn == "core.py":
                    continue
                fp = os.path.join(dirpath, fn)
                for i, ln in enumerate(
                        open(fp, "rb").read().decode("utf-8").splitlines(), 1):
                    if re.search(r'\b(int|float)\(\s*(request\.|payload\.|body\.)', ln):
                        bad.append(f"{os.path.relpath(fp, root)}:{i}")
        assert not bad, f"仍有裸取参: {bad}"


# ── 500 处理器与 /api/ 的响应约定 ─────────────────────────────────────────

class TestApiErrorsAreJson:
    """404 与 400 都给 /api/ 返回 JSON, 只有 500 返回 HTML ——
    前端 fetch 拿到 HTML 却在 res.json() 上炸, 真因被二次错误盖掉。"""

    def test_500_on_api_path_is_json(self, client, tmp_projects_root, monkeypatch):
        from lib import review_service

        def boom(*a, **k):
            raise RuntimeError("内部炸了")

        monkeypatch.setattr(review_service, "get_review_queue", boom)
        # TESTING=True 时 Flask 直接把异常抛给调用方, 不经过 errorhandler,
        # 所以这里必须关掉它才能验证 err_500 的行为 (与生产一致)
        review_app.app.config["TESTING"] = False
        review_app.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            r = client.get("/api/queue/test_book")
            assert r.status_code == 500
            assert r.is_json, "/api/ 的 500 必须是 JSON"
            assert r.get_json()["error"] == "internal_error"
        finally:
            review_app.app.config["TESTING"] = True
            review_app.app.config["PROPAGATE_EXCEPTIONS"] = None


# ── 鉴权方向 ──────────────────────────────────────────────────────────────

class TestAuthDirection:
    """enabled=True + password 空 = 配置错误, 拒绝一切; 免密必须显式写 enabled=false。"""

    def test_misconfigured_is_detected(self, monkeypatch):
        monkeypatch.setattr(review_app, "_get_auth", lambda: {
            "enabled": True, "user": "u", "password": ""})
        assert review_app._auth_misconfigured(review_app._get_auth()) is True

    def test_disabled_is_not_misconfigured(self, monkeypatch):
        monkeypatch.setattr(review_app, "_get_auth", lambda: {
            "enabled": False, "user": "", "password": ""})
        assert review_app._auth_misconfigured(review_app._get_auth()) is False

    def test_properly_configured_is_not_misconfigured(self, monkeypatch):
        monkeypatch.setattr(review_app, "_get_auth", lambda: {
            "enabled": True, "user": "u", "password": "p"})
        assert review_app._auth_misconfigured(review_app._get_auth()) is False


# ── 错误信息不得外泄内部结构 ──────────────────────────────────────────────

class TestNoInternalLeak:
    def test_llm_health_does_not_echo_raw_exception(self, client, tmp_projects_root,
                                                      monkeypatch):
        import requests

        def raiser(*a, **k):
            raise requests.exceptions.ConnectionError(
                "连接 http://10.0.0.5:60443/v1 失败 —— 内网地址")

        # api_llm_health_check 内部是 `import requests` 后再调, 所以 patch
        # requests.post 本身即可命中
        monkeypatch.setattr(requests, "post", raiser)
        r = client.post("/api/llm/health", json={"provider": "local"})
        body = r.get_data(as_text=True)
        assert "10.0.0.5" not in body, f"响应里带了内网地址: {body[:200]}"

    def test_health_endpoint_never_hits_arbitrary_url(self, client, tmp_projects_root,
                                                      monkeypatch):
        """provider 由调用方指定但 api_base 来自 config.yaml, 不是 SSRF。

        这里钉住两件事: (1) 不会把用户给的字符串直接当 URL 用;
        (2) 未知 provider 是输入错误, 应当 400 而不是 500 ——
        get_provider_config 抛 KeyError, 而原代码 `if not pcfg: return 400`
        因此成了死代码。"""
        import requests
        called = {}

        def spy(url, *a, **k):
            called["url"] = url
            raise RuntimeError("stop")

        monkeypatch.setattr(requests, "post", spy)
        r = client.post("/api/llm/health", json={"provider": "http://evil.com"})
        assert r.status_code == 400, f"未知 provider 应为 400, 实得 {r.status_code}"
        assert not called, f"不该发出任何出网请求, 实际打了 {called}"
