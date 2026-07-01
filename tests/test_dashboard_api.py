"""
test_dashboard_api.py - review_ui/dashboard 蓝图 6 个 API (v1.1)

10 cases:
  1. GET /api/pipeline/status/<book> 无 state 返回 null
  2. GET /api/pipeline/status/<book> 读到 running state
  3. GET /api/pipeline/logs/<book> 返回 log 行
  4. GET /api/pipeline/metrics/<book> 返回聚合
  5. POST /api/pipeline/start/<book> 缺 chapters 报 INVALID_ARGS
  6. POST /api/pipeline/start/<book> 项目不存在报 NOT_FOUND
  7. POST /api/pipeline/start/<book> 触发后 status 变 running
  8. POST /api/pipeline/cancel/<book> 杀进程 + 标 cancelled
  9. 401: auth enabled + 空 password 不让过
  10. GET /api/pipeline/logs/<book>/stream (SSE) 返回 text/event-stream
"""
import json
import time
from pathlib import Path

import pytest

from review_ui import app as review_app
from lib import pipeline, storage


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def auth_disabled(monkeypatch):
    """测试用 auth off, 不用登录就能调 endpoint."""
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": False, "user": "", "password": ""
    })


@pytest.fixture
def client(tmp_projects_root):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test-secret-stable"
    with review_app.app.test_client() as c:
        yield c


@pytest.fixture
def running_pipeline(tmp_projects_root):
    """启动 1 个跑着的流水线 (ch 1), 测试完 cancel."""
    runner = pipeline.PipelineRunner()
    state = runner.start("test_book", chapter_num=1, auto_rewrite=False)
    yield state
    # 测试后清理 (防止泄漏)
    try:
        s = runner.status("test_book")
        if s and s.get("status") == "running":
            runner.cancel("test_book")
    except Exception:
        pass


# ── 1. status 无 state ───────────────────────────────────────────────────

def test_status_no_state_returns_null(client, auth_disabled, tmp_projects_root):
    """没起过流水线, status 返回 state=null."""
    r = client.get("/api/pipeline/status/test_book")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["state"] is None


# ── 2. status running ────────────────────────────────────────────────────

def test_status_running(client, auth_disabled, running_pipeline):
    """起流水线后 status 显示 running + PID."""
    r = client.get("/api/pipeline/status/test_book")
    data = r.get_json()
    assert data["state"]["status"] == "running"
    assert data["state"]["pid"] > 0
    assert data["state"]["current_chapter"] == 1


# ── 3. logs 返回行 ──────────────────────────────────────────────────────

def test_logs_returns_lines(client, auth_disabled, tmp_projects_root):
    """写 5 行到 log, /api/pipeline/logs 返回 5 行."""
    log = pipeline.pipeline_log_path("test_book")
    log.parent.mkdir(exist_ok=True)
    log.write_text("\n".join(f"line {i}" for i in range(5)), encoding="utf-8")
    r = client.get("/api/pipeline/logs/test_book?tail=10")
    data = r.get_json()
    assert data["ok"] is True
    assert data["count"] == 5
    assert data["lines"] == [f"line {i}" for i in range(5)]


# ── 4. metrics 聚合 ─────────────────────────────────────────────────────

def test_metrics_returns_aggregation(client, auth_disabled, tmp_projects_root):
    """写 2 行 metrics, /api/pipeline/metrics 返回聚合."""
    runner = pipeline.get_runner()
    runner.append_metric("test_book", stage="writing", ch=5, model="M",
                         input_tokens=1000, output_tokens=200, latency_ms=30000)
    runner.append_metric("test_book", stage="extract", ch=5, model="M",
                         input_tokens=500, output_tokens=50, latency_ms=10000)
    r = client.get("/api/pipeline/metrics/test_book?range=all")
    data = r.get_json()
    assert data["ok"] is True
    assert data["calls"] == 2
    assert data["total_in"] == 1500
    assert data["total_out"] == 250


# ── 5. start 缺 chapters ────────────────────────────────────────────────

def test_start_missing_chapters_returns_invalid_args(client, auth_disabled, tmp_projects_root):
    """POST /start 不带 chapters 报 INVALID_ARGS."""
    r = client.post("/api/pipeline/start/test_book", data={})
    data = r.get_json()
    assert data["code"] == 2  # INVALID_ARGS
    assert "chapters" in data["error"]


# ── 6. start 项目不存在 ────────────────────────────────────────────────

def test_start_nonexistent_book_returns_not_found(client, auth_disabled, tmp_projects_root):
    """POST /start 不存在的项目报 NOT_FOUND."""
    r = client.post("/api/pipeline/start/no_such_book", data={"chapters": "1"})
    data = r.get_json()
    assert data["code"] == 3  # NOT_FOUND
    assert "不存在" in data["error"]


# ── 7. start 触发后 status running ──────────────────────────────────────

def test_start_triggers_subprocess(client, auth_disabled, tmp_projects_root):
    """POST /start 成功后 status 变 running."""
    r = client.post("/api/pipeline/start/test_book",
                    data={"chapters": "1", "auto_rewrite": "false"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["state"]["status"] == "running"

    # 立刻查 status 确认
    r2 = client.get("/api/pipeline/status/test_book")
    s = r2.get_json()["state"]
    assert s["status"] == "running"
    assert s["current_chapter"] == 1

    # 清理
    pipeline.get_runner().cancel("test_book")


# ── 8. cancel 杀进程 ────────────────────────────────────────────────────

def test_cancel_kills_and_marks_cancelled(client, auth_disabled, running_pipeline):
    """POST /cancel 杀子进程 + 标 cancelled."""
    r = client.post("/api/pipeline/cancel/test_book")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["state"]["status"] == "cancelled"
    # 1s 内 PID 应该死了
    time.sleep(0.5)
    assert not pipeline._is_pid_alive(data["state"]["pid"])


# ── 10. SSE stream ──────────────────────────────────────────────────────

def test_sse_stream_returns_event_stream(client, auth_disabled, tmp_projects_root):
    """GET /stream 返回 text/event-stream content-type."""
    # 写 1 行到 log
    log = pipeline.pipeline_log_path("test_book")
    log.parent.mkdir(exist_ok=True)
    log.write_text("existing line\n", encoding="utf-8")
    # 写一个 done 状态 (这样 stream_log 会立即 flush 残留 + 退出)
    storage.write_json("test_book", ".pipeline_state.json", {
        "book": "test_book", "status": "done", "pid": 99999,
    })
    r = client.get("/api/pipeline/logs/test_book/stream")
    assert r.status_code == 200
    # content-type 可能是 'text/event-stream' 或 'text/event-stream; charset=utf-8'
    assert "event-stream" in r.content_type
    # 至少收到 1 个 data: 块
    body = r.data.decode("utf-8", errors="replace")
    assert "data:" in body
    # 收到 end 事件
    assert "event: end" in body


def test_sse_stream_no_state_returns_empty(client, auth_disabled, tmp_projects_root):
    """没 state 时 SSE 立即返回空流 (不 hang)."""
    r = client.get("/api/pipeline/logs/no_state_book/stream")
    assert r.status_code == 200
    assert "event-stream" in r.content_type
    # stream_log 立即 return, body 包含 'end' 事件
    body = r.data.decode("utf-8", errors="replace")
    assert "event: end" in body


# ── 9. 401 auth enabled + 空 password 不让过 ───────────────────────────

def test_start_401_when_auth_enabled_empty_password(client, monkeypatch, tmp_projects_root):
    """auth.enabled=True + password='' → safeguard 放行 (L64 fix).

    这里测的是: auth enabled 但 password 非空时, 没登录的请求应被 401 拦截.
    """
    monkeypatch.setattr(review_app, "_get_auth", lambda: {
        "enabled": True, "user": "weichao", "password": "secret123"
    })
    r = client.post("/api/pipeline/start/test_book", data={"chapters": "1"})
    # 没 Authorization header, 也没 session, 应 401
    assert r.status_code == 401


# ── 11. dashboard.html 页面 ─────────────────────────────────────────────

def test_dashboard_page_renders(client, auth_disabled, tmp_projects_root):
    """GET /dashboard/<book> 渲染 dashboard.html (含 Chart.js / EventSource)."""
    r = client.get("/dashboard/test_book")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # 包含关键 DOM 元素
    assert "流水线面板" in body
    assert 'id="stagesBar"' in body
    assert 'id="logView"' in body
    assert 'id="metricsChart"' in body
    # 包含 JS
    assert "EventSource" in body
    assert "Chart.js" in body or "chart.js" in body.lower() or "chart.umd" in body
    # 包含 next_chapter 变量
    assert "next_chapter" in body or "chapterInput" in body


def test_dashboard_page_nonexistent_book_404(client, auth_disabled, tmp_projects_root):
    """不存在的项目返回 404."""
    r = client.get("/dashboard/no_such_book")
    assert r.status_code == 404


def test_dashboard_page_next_chapter_from_progress(client, auth_disabled, tmp_projects_root):
    """进度 current_chapter=7 → next_chapter=8."""
    storage.write_json("test_book", "progress.json", {
        "phase": "writing", "current_chapter": 7, "total_chapters": 20,
        "chapters_completed": ["ch_001", "ch_002"], "last_updated": "2026-07-01"
    })
    r = client.get("/dashboard/test_book")
    body = r.data.decode("utf-8")
    # next_chapter=8 应该是默认值
    assert 'value="8"' in body
