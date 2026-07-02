"""
tests/test_review_ui_batch_enhance.py — v1.2 M2 批量 reject + 过滤测试
"""
import json
import pytest

from review_ui import app as review_app
from lib import review_service as revserv


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    from lib import storage
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(storage, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(storage, "ROOT", proj_root)
    storage.init_project("test_book", {"book_name": "test_book"})
    # 写 3 个章节并准备 review
    for i in range(1, 4):
        cid = f"ch_00{i}"
        storage.write_chapter("test_book", cid, f"## 第{i}章\n\n内容。")
    # 准备 review records with different severities
    revserv.auto_flag("test_book", "ch_001", {"severity": "critical", "character_inconsistency": [1, 2]}, by="AI")
    revserv.auto_flag("test_book", "ch_002", {"severity": "moderate", "character_inconsistency": [1]}, by="AI")
    revserv.auto_flag("test_book", "ch_003", {"severity": "critical", "character_inconsistency": [1]}, by="AI")
    return proj_root


@pytest.fixture
def client(setup_book):
    review_app.app.config["TESTING"] = True
    review_app.app.config["SECRET_KEY"] = "test"
    with review_app.app.test_client() as c:
        yield c


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setattr(review_app, "_get_auth",
                        lambda: {"enabled": False, "user": "", "password": ""})


class TestBatchReject:
    def test_batch_reject_success(self, client, auth_off, setup_book):
        r = client.post(
            "/api/batch-reject/test_book",
            data=json.dumps({
                "chapters": ["ch_001", "ch_002"],
                "reason": "批量测试",
                "reviewer": "tester",
            }),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["rejected"] == 2
        assert data["failed"] == 0

    def test_batch_reject_missing_reason_400(self, client, auth_off, setup_book):
        r = client.post(
            "/api/batch-reject/test_book",
            data=json.dumps({"chapters": ["ch_001"]}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_batch_reject_empty_chapters_400(self, client, auth_off, setup_book):
        r = client.post(
            "/api/batch-reject/test_book",
            data=json.dumps({"chapters": [], "reason": "x"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_batch_reject_invalid_id_partial_fail(self, client, auth_off, setup_book):
        r = client.post(
            "/api/batch-reject/test_book",
            data=json.dumps({
                "chapters": ["ch_001", "invalid_id", "ch_002"],
                "reason": "测试",
            }),
            content_type="application/json",
        )
        data = r.get_json()
        assert data["ok"] is False
        assert data["rejected"] == 2
        assert data["failed"] == 1


class TestQueueFilter:
    def test_filter_by_severity(self, client, auth_off, setup_book):
        r = client.get("/api/queue/test_book/filtered?severity=critical")
        items = r.get_json()
        assert len(items) == 2
        assert all(it["auto_severity"] == "critical" for it in items)

    def test_filter_by_severity_moderate(self, client, auth_off, setup_book):
        r = client.get("/api/queue/test_book/filtered?severity=moderate")
        items = r.get_json()
        assert len(items) == 1
        assert items[0]["auto_severity"] == "moderate"

    def test_no_filter_returns_all(self, client, auth_off, setup_book):
        r = client.get("/api/queue/test_book/filtered")
        items = r.get_json()
        # 3 severities, all 3 chapters in queue
        assert len(items) == 3

    def test_filter_by_severity_no_match(self, client, auth_off, setup_book):
        r = client.get("/api/queue/test_book/filtered?severity=unknown")
        items = r.get_json()
        assert items == []
