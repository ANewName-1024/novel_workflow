"""
test_outline_api.py - review_ui/app.py 大纲编辑器 API (v1.2 M4)

7 cases covering the 9 routes:
  - GET   /api/outline/<book>            sync'd
  - PUT   /api/outline/<book>            validate + persist
  - POST  /api/outline/<book>/node       add
  - PUT   /api/outline/<book>/node/<id>  update
  - DEL   /api/outline/<book>/node/<id>  remove
  - POST  /api/outline/<book>/reorder    batch moves
  - GET   /api/outline/<book>/diff       between two saved versions
  + failure paths: missing book, bad payload, validation errors
"""
import json
from pathlib import Path

import pytest

from review_ui import app as review_app
from lib import outline_editor as oe, storage


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def auth_disabled(monkeypatch):
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
def seeded_book(tmp_projects_root):
    """test_book with 2 vols + 4 chapters (ch_001..ch_004)."""
    storage.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})
    outline = {
        "meta": {"title": "test_book", "target_chapters": 4, "summary": ""},
        "volumes": [
            {"id": "vol_1", "title": "卷1", "summary": "vs1", "chapters": []},
            {"id": "vol_2", "title": "卷2", "summary": "vs2", "chapters": []},
        ],
        "chapters": [
            {"id": "ch_001", "vol": "vol_1", "title": "C1", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_002", "vol": "vol_1", "title": "C2", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_003", "vol": "vol_2", "title": "C3", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_004", "vol": "vol_2", "title": "C4", "summary": "",
             "pov": "P", "key_events": [], "foreshadow": []},
        ],
        "generated_at": "2026-07-01T00:00:00",
    }
    oe.save_outline("test_book", outline, auto_snapshot=False)
    return "test_book"


# ── 1. GET /api/outline/<book> ────────────────────────────────────────────

class TestGet:
    def test_returns_synced_outline(self, client, auth_disabled, seeded_book):
        r = client.get("/api/outline/test_book")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["volumes"]) == 2
        assert len(data["chapters"]) == 4
        # volumes[].chapters must be synced
        v1 = next(v for v in data["volumes"] if v["id"] == "vol_1")
        assert "第1章 C1" in v1["chapters"]
        assert "第2章 C2" in v1["chapters"]

    def test_missing_book_404(self, client, auth_disabled):
        r = client.get("/api/outline/no_such_book")
        assert r.status_code == 404


# ── 2. PUT /api/outline/<book> ────────────────────────────────────────────

class TestReplace:
    def test_replace_full_outline(self, client, auth_disabled, seeded_book):
        new_outline = {
            "meta": {"title": "test_book", "target_chapters": 1, "summary": ""},
            "volumes": [
                {"id": "vol_1", "title": "Only Vol", "summary": "", "chapters": []},
            ],
            "chapters": [
                {"id": "ch_001", "vol": "vol_1", "title": "Sole", "summary": "",
                 "pov": "P", "key_events": [], "foreshadow": []},
            ],
            "generated_at": "2026-07-01T00:00:00",
        }
        r = client.put("/api/outline/test_book", json=new_outline)
        assert r.status_code == 200
        # Verify persisted
        loaded = oe.load_outline_or_empty("test_book")
        assert len(loaded["chapters"]) == 1
        assert loaded["chapters"][0]["title"] == "Sole"

    def test_replace_rejects_duplicate_chapter_id(self, client, auth_disabled, seeded_book):
        bad = {
            "meta": {}, "volumes": [],
            "chapters": [
                {"id": "ch_X", "vol": "vol_1", "title": "A", "summary": "",
                 "pov": "", "key_events": [], "foreshadow": []},
                {"id": "ch_X", "vol": "vol_1", "title": "B", "summary": "",
                 "pov": "", "key_events": [], "foreshadow": []},
            ],
            "generated_at": "",
        }
        r = client.put("/api/outline/test_book", json=bad)
        assert r.status_code == 400
        # App's custom error handler returns {error, message, ...}
        body = r.get_json()
        assert "duplicate" in body["message"].lower()

    def test_replace_rejects_unknown_vol_ref(self, client, auth_disabled, seeded_book):
        bad = {
            "meta": {}, "volumes": [{"id": "vol_1", "title": "", "summary": "", "chapters": []}],
            "chapters": [
                {"id": "ch_001", "vol": "vol_999", "title": "X", "summary": "",
                 "pov": "", "key_events": [], "foreshadow": []},
            ],
            "generated_at": "",
        }
        r = client.put("/api/outline/test_book", json=bad)
        assert r.status_code == 400


# ── 3. POST /api/outline/<book>/node ─────────────────────────────────────

class TestNodeAdd:
    def test_add_node(self, client, auth_disabled, seeded_book):
        r = client.post("/api/outline/test_book/node", json={
            "parent_vol": "vol_1", "position": 99,
            "title": "新章", "summary": "新摘要", "pov": "Q",
        })
        assert r.status_code == 201
        node = r.get_json()["node"]
        assert node["title"] == "新章"
        assert node["vol"] == "vol_1"
        # Persisted
        loaded = oe.load_outline_or_empty("test_book")
        assert any(c["title"] == "新章" for c in loaded["chapters"])

    def test_add_node_missing_parent_vol_400(self, client, auth_disabled, seeded_book):
        r = client.post("/api/outline/test_book/node", json={"position": 0})
        assert r.status_code == 400


# ── 4. PUT /api/outline/<book>/node/<id> ─────────────────────────────────

class TestNodeUpdate:
    def test_update_node_fields(self, client, auth_disabled, seeded_book):
        r = client.put("/api/outline/test_book/node/ch_001", json={
            "title": "新C1", "summary": "新摘要", "key_events": ["事件A"]
        })
        assert r.status_code == 200
        loaded = oe.load_outline_or_empty("test_book")
        ch1 = next(c for c in loaded["chapters"] if c["id"] == "ch_001")
        assert ch1["title"] == "新C1"
        assert ch1["summary"] == "新摘要"
        assert ch1["key_events"] == ["事件A"]
        # Untouched
        assert ch1["pov"] == "P"

    def test_update_unknown_chapter_404(self, client, auth_disabled, seeded_book):
        r = client.put("/api/outline/test_book/node/ch_999", json={"title": "x"})
        assert r.status_code == 404

    def test_update_empty_fields_400(self, client, auth_disabled, seeded_book):
        r = client.put("/api/outline/test_book/node/ch_001", json={})
        assert r.status_code == 400


# ── 5. DELETE /api/outline/<book>/node/<id> ──────────────────────────────

class TestNodeDelete:
    def test_delete_node(self, client, auth_disabled, seeded_book):
        r = client.delete("/api/outline/test_book/node/ch_002")
        assert r.status_code == 204
        loaded = oe.load_outline_or_empty("test_book")
        ids = {c["id"] for c in loaded["chapters"]}
        assert "ch_002" not in ids

    def test_delete_unknown_chapter_404(self, client, auth_disabled, seeded_book):
        r = client.delete("/api/outline/test_book/node/ch_999")
        assert r.status_code == 404


# ── 6. POST /api/outline/<book>/reorder ──────────────────────────────────

class TestReorder:
    def test_reorder_moves_chapter(self, client, auth_disabled, seeded_book):
        r = client.post("/api/outline/test_book/reorder", json={
            "moves": [{"ch_id": "ch_001", "new_vol": "vol_2", "new_position": 0}],
        })
        assert r.status_code == 200
        loaded = oe.load_outline_or_empty("test_book")
        v2 = [c for c in loaded["chapters"] if c["vol"] == "vol_2"]
        assert v2[0]["id"] == "ch_001"

    def test_reorder_empty_moves_400(self, client, auth_disabled, seeded_book):
        r = client.post("/api/outline/test_book/reorder", json={"moves": []})
        assert r.status_code == 400

    def test_reorder_missing_chapter_400(self, client, auth_disabled, seeded_book):
        r = client.post("/api/outline/test_book/reorder", json={
            "moves": [{"ch_id": "ch_999", "new_vol": "vol_1", "new_position": 0}],
        })
        assert r.status_code == 400


# ── 7. GET /api/outline/<book>/diff ──────────────────────────────────────

class TestDiff:
    def test_diff_between_saved_versions(self, client, auth_disabled, seeded_book):
        # First edit to establish v001 (after save)
        r1 = client.put("/api/outline/test_book/node/ch_001", json={"title": "edit-1"})
        assert r1.status_code == 200
        # Second edit to establish v002 (different content)
        r2 = client.put("/api/outline/test_book/node/ch_001", json={"title": "edit-2"})
        assert r2.status_code == 200

        from lib import version as ver_serv
        versions = ver_serv.list_versions("test_book", "outline.json")
        assert len(versions) >= 2, f"need ≥2 versions for diff, got {len(versions)}"
        # Diff the two oldest snapshots
        v1 = versions[-1]["version_id"]
        v2 = versions[-2]["version_id"] if len(versions) >= 2 else versions[0]["version_id"]
        r = client.get(f"/api/outline/test_book/diff?v1={v1}&v2={v2}")
        assert r.status_code == 200
        diff = r.get_json()
        # Should detect the title edits on ch_001
        edited_ids = {e["ch_id"] for e in diff["chapters_edited"]}
        assert "ch_001" in edited_ids

    def test_diff_missing_versions_400(self, client, auth_disabled, seeded_book):
        r = client.get("/api/outline/test_book/diff")
        assert r.status_code == 400
