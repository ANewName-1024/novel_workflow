"""
test_dashboard_v2_api.py — review_ui/dashboard.py 4 个 v2 API 测试 (M5.3)

4 个端点 × 1-2 场景 = 6 tests:
1. GET /api/pipeline/checkpoints/<book>?ch=N
2. POST /api/pipeline/skip/<book> {ch, stage, reason?}
3. POST /api/pipeline/rerun/<book> {ch, from_stage}
4. POST /api/pipeline/reset/<book> {ch}
"""
import json
import pytest

from lib import pipeline_v2 as pv2


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_projects_root):
    """Create Flask test app with dashboard blueprint."""
    from review_ui.app import app as flask_app
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def book(tmp_projects_root):
    return "test_book"


@pytest.fixture
def client(app):
    return app.test_client()


# ── 1. GET checkpoints ───────────────────────────────────────────────────

def test_api_checkpoints_returns_view(client, book):
    """GET checkpoints → 返回 7 stage 列表 + summary."""
    # 先写一些 v2 状态
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "context", "RUNNING")
    v2.transition(book, 1, "context", "DONE")
    v2.transition(book, 1, "writing", "RUNNING")
    v2.transition(book, 1, "writing", "FAILED", error="LLM timeout")

    resp = client.get(f"/api/pipeline/checkpoints/{book}?ch=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["book"] == book
    assert data["chapter"] == 1
    assert len(data["stages"]) == 7
    assert data["failed_stage"] == "writing"
    assert data["current_stage"] == "writing"


def test_api_checkpoints_missing_ch(client, book):
    """缺 ch 参数 → 400."""
    resp = client.get(f"/api/pipeline/checkpoints/{book}")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code_name"] == "INVALID_ARGS"


def test_api_checkpoints_invalid_ch(client, book):
    """ch=0 → 400."""
    resp = client.get(f"/api/pipeline/checkpoints/{book}?ch=0")
    assert resp.status_code == 400


def test_api_checkpoints_nonexistent_book(client):
    """Project not found - returns error (status may be 400 due to NOT_FOUND=3<400)."""
    resp = client.get("/api/pipeline/checkpoints/no_such_book?ch=1")
    # NOT_FOUND code = 3, dashboard._err_response maps <400 to 400
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code_name"] == "NOT_FOUND"


# ── 2. POST skip ─────────────────────────────────────────────────────────

def test_api_skip_marks_stage(client, book):
    """POST skip → stage 标 SKIPPED."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "self_check", "RUNNING")
    v2.transition(book, 1, "self_check", "FAILED", error="x")

    resp = client.post(
        f"/api/pipeline/skip/{book}",
        json={"ch": 1, "stage": "self_check", "reason": "manual ok"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["new_state"] == "SKIPPED"
    # 验证 v2 状态
    assert v2.get_stage_state(book, 1, "self_check") == "SKIPPED"


def test_api_skip_running_raises(client, book):
    """skip RUNNING stage → 400 + error."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 1, "writing", "RUNNING")

    resp = client.post(
        f"/api/pipeline/skip/{book}",
        json={"ch": 1, "stage": "writing"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "RUNNING" in data["error"]


def test_api_skip_missing_stage_param(client, book):
    """缺 stage → 400."""
    resp = client.post(
        f"/api/pipeline/skip/{book}",
        json={"ch": 1},
    )
    assert resp.status_code == 400


# ── 3. POST rerun ────────────────────────────────────────────────────────

def test_api_rerun_resets_downstream(client, book):
    """POST rerun → 下游 reset + 启动 v1 subprocess."""
    v2 = pv2.PipelineV2()
    # 模拟 ch 5 已全 DONE
    for s in pv2.STAGES:
        v2.transition(book, 5, s, "RUNNING")
        v2.transition(book, 5, s, "DONE")

    resp = client.post(
        f"/api/pipeline/rerun/{book}",
        json={"ch": 5, "from_stage": "extract"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["from_stage"] == "extract"
    # 验证 v2: 上游保留, 下游 PENDING
    assert v2.get_stage_state(book, 5, "context") == "DONE"
    assert v2.get_stage_state(book, 5, "writing") == "DONE"
    assert v2.get_stage_state(book, 5, "extract") == "PENDING"
    assert v2.get_stage_state(book, 5, "done") == "PENDING"
    # v1 启动了新 subprocess (有 state 字段)
    assert "state" in data
    assert data["state"]["status"] == "running"


def test_api_rerun_invalid_stage(client, book):
    """from_stage 非法 → 400."""
    resp = client.post(
        f"/api/pipeline/rerun/{book}",
        json={"ch": 1, "from_stage": "no_such"},
    )
    assert resp.status_code == 400


# ── 4. POST reset ────────────────────────────────────────────────────────

def test_api_reset_clears_chapter(client, book):
    """POST reset → ch 全部 PENDING."""
    v2 = pv2.PipelineV2()
    v2.transition(book, 3, "context", "RUNNING")
    v2.transition(book, 3, "context", "DONE")
    v2.transition(book, 3, "writing", "RUNNING")
    v2.transition(book, 3, "writing", "FAILED", error="x")

    resp = client.post(
        f"/api/pipeline/reset/{book}",
        json={"ch": 3},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    # 全部 PENDING
    for s in pv2.STAGES:
        assert v2.get_stage_state(book, 3, s) == "PENDING"


def test_api_reset_invalid_ch(client, book):
    """ch=0 → 400."""
    resp = client.post(
        f"/api/pipeline/reset/{book}",
        json={"ch": 0},
    )
    assert resp.status_code == 400


# ── 5. v1 API 兼容 (回归) ──────────────────────────────────────────────

def test_v1_status_endpoint_still_works(client, book):
    """v1 /api/pipeline/status 仍正常 (不破坏 v1 API)."""
    resp = client.get(f"/api/pipeline/status/{book}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    # 没跑过 → state=None
    assert data["state"] is None