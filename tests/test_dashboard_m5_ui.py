"""
test_dashboard_m5_ui.py — dashboard.html M5 控件渲染测试 (M5.4)

4 tests:
1. dashboard.html 含 m5Stage select 7 个 stage 选项
2. dashboard.html 含 m5SkipBtn + m5RerunBtn + m5ResetBtn
3. dashboard.html 含 API.checkpoints/skip/rerun/reset 调用
4. dashboard 页面 GET 含 M5 HTML (集成测试)
"""
import pytest


@pytest.fixture
def app(tmp_projects_root):
    from review_ui.app import app as flask_app
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def book(tmp_projects_root):
    return "test_book"


@pytest.fixture
def client(app):
    return app.test_client()


# ── 1. HTML 包含 7 stage 选项 ──────────────────────────────────────────

def test_html_has_7_stage_options(tmp_projects_root):
    from pathlib import Path
    html = Path("review_ui/templates/dashboard.html").read_text(encoding="utf-8")
    for stage in ["context", "writing", "extract", "summary", "state", "self_check", "done"]:
        assert f'value="{stage}"' in html, f"missing stage option: {stage}"


# ── 2. HTML 含 3 个按钮 ────────────────────────────────────────────────

def test_html_has_m5_buttons(tmp_projects_root):
    from pathlib import Path
    html = Path("review_ui/templates/dashboard.html").read_text(encoding="utf-8")
    for btn_id in ["m5SkipBtn", "m5RerunBtn", "m5ResetBtn", "m5RefreshBtn"]:
        assert f'id="{btn_id}"' in html, f"missing button: {btn_id}"


# ── 3. JS 含 4 个 API 调用 ────────────────────────────────────────────

def test_js_has_v2_api_calls(tmp_projects_root):
    from pathlib import Path
    html = Path("review_ui/templates/dashboard.html").read_text(encoding="utf-8")
    # M5 API 端点
    for endpoint in [
        "/api/pipeline/checkpoints/",
        "/api/pipeline/skip/",
        "/api/pipeline/rerun/",
        "/api/pipeline/reset/",
    ]:
        assert endpoint in html, f"missing API endpoint in JS: {endpoint}"


# ── 4. dashboard 页 GET 含 M5 区域 ────────────────────────────────────

def test_dashboard_page_renders_m5_section(client, book):
    """GET /dashboard/<book> → 200 + 含 M5 卡片."""
    resp = client.get(f"/dashboard/{book}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pipeline 阶段控制" in body or "M5" in body
    # 7 stage 选项
    for stage in ["context", "writing", "extract", "summary", "state", "self_check", "done"]:
        assert f'value="{stage}"' in body
    # 按钮 id
    for btn in ["m5SkipBtn", "m5RerunBtn", "m5ResetBtn", "m5RefreshBtn"]:
        assert btn in body


def test_dashboard_m5_init_loads_checkpoint_via_api(client, book):
    """前端 init 时会调 /api/pipeline/checkpoints?ch=N (M5.3 API)."""
    resp = client.get(f"/api/pipeline/checkpoints/{book}?ch=1")
    # 没写 checkpoint → 返回空 view, 但 200
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["chapter"] == 1
    assert len(data["stages"]) == 7
    # 默认全 PENDING
    for s in data["stages"]:
        assert s["status"] == "PENDING"