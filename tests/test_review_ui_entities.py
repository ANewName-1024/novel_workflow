"""
test_review_ui_entities.py — review_ui 实体 CRUD API (v1.2 M1.2)

6 端点 × 4 实体类型 = ~25 cases:
  - GET /api/entities/<book>                  list
  - GET /api/entities/<book>/counts           counts
  - GET /api/entities/<book>/<type>/<id>      get
  - POST /api/entities/<book>                 create
  - PUT /api/entities/<book>/<type>/<id>      update
  - DELETE /api/entities/<book>/<type>/<id>   delete
"""
import json

import pytest

from review_ui import app as review_app


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


# ── 1. counts ────────────────────────────────────────────────────────────

class TestEntitiesCounts:
    def test_counts_empty(self, client, auth_disabled):
        r = client.get("/api/entities/test_book/counts")
        assert r.status_code == 200
        data = r.get_json()
        assert data == {"character": 0, "event": 0, "foreshadow": 0, "world_rule": 0}

    def test_counts_after_adding(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        store = EntityStore("test_book")
        from lib.entity import Character, Event, Foreshadow, WorldRule
        store.add_character(Character(name="A"))
        store.add_character(Character(name="B"))
        store.add_event(Event(event="E1"))
        store.add_foreshadow(Foreshadow(foreshadow="F1"))
        store.add_world_rule(WorldRule(name="R1"))
        r = client.get("/api/entities/test_book/counts")
        data = r.get_json()
        assert data["character"] == 2
        assert data["event"] == 1
        assert data["foreshadow"] == 1
        assert data["world_rule"] == 1


# ── 2. list by type ─────────────────────────────────────────────────────

class TestEntitiesList:
    def test_list_world_rules(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import WorldRule
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="灵根等级", category="体系"))
        store.add_world_rule(WorldRule(name="斗技等级", category="体系"))

        r = client.get("/api/entities/test_book?type=world_rule")
        assert r.status_code == 200
        data = r.get_json()
        assert data["type"] == "world_rule"
        assert data["count"] == 2
        names = {e["data"]["name"] for e in data["entities"]}
        assert names == {"灵根等级", "斗技等级"}

    def test_list_characters(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import Character
        store = EntityStore("test_book")
        store.add_character(Character(name="主角"))

        r = client.get("/api/entities/test_book?type=character")
        data = r.get_json()
        assert data["count"] == 1
        assert data["entities"][0]["data"]["name"] == "主角"

    def test_list_invalid_type_400(self, client, auth_disabled):
        r = client.get("/api/entities/test_book?type=invalid")
        assert r.status_code == 400

    def test_list_without_type_returns_counts(self, client, auth_disabled):
        r = client.get("/api/entities/test_book")
        assert r.status_code == 200
        data = r.get_json()
        assert "counts" in data


# ── 3. get single ──────────────────────────────────────────────────────

class TestEntitiesGet:
    def test_get_world_rule(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import WorldRule
        store = EntityStore("test_book")
        r = store.add_world_rule(WorldRule(name="灵根等级", category="体系"))

        resp = client.get(f"/api/entities/test_book/world_rule/{r.id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["type"] == "world_rule"
        assert data["id"] == r.id
        assert data["data"]["name"] == "灵根等级"
        assert data["data"]["category"] == "体系"

    def test_get_nonexistent_404(self, client, auth_disabled):
        resp = client.get("/api/entities/test_book/world_rule/rule_xxx")
        assert resp.status_code == 404


# ── 4. create ───────────────────────────────────────────────────────────

class TestEntitiesCreate:
    def test_create_character(self, client, auth_disabled):
        body = {
            "type": "character",
            "data": {"name": "萧炎", "role": "主角", "importance": "高"}
        }
        r = client.post(
            "/api/entities/test_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert r.status_code == 201
        data = r.get_json()
        assert data["ok"] is True
        assert data["entity"]["id"] == "萧炎"
        assert data["entity"]["data"]["role"] == "主角"

    def test_create_world_rule_with_constraints(self, client, auth_disabled):
        body = {
            "type": "world_rule",
            "data": {
                "name": "灵根等级",
                "category": "体系",
                "description": "修士天赋分天/地/人三等",
                "constraints": ["灵根品级先天决定", "高品压低品"],
            }
        }
        r = client.post(
            "/api/entities/test_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert r.status_code == 201
        data = r.get_json()
        rid = data["entity"]["id"]
        assert rid.startswith("rule_")
        assert len(data["entity"]["data"]["constraints"]) == 2

    def test_create_missing_type_400(self, client, auth_disabled):
        body = {"data": {"name": "X"}}
        r = client.post(
            "/api/entities/test_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_create_missing_name_400(self, client, auth_disabled):
        body = {"type": "character", "data": {"role": "主角"}}
        r = client.post(
            "/api/entities/test_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_create_book_not_exists_404(self, client, auth_disabled):
        body = {"type": "character", "data": {"name": "X"}}
        r = client.post(
            "/api/entities/nonexistent_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert r.status_code == 404


# ── 5. update ───────────────────────────────────────────────────────────

class TestEntitiesUpdate:
    def test_update_world_rule_status(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import WorldRule
        store = EntityStore("test_book")
        r = store.add_world_rule(WorldRule(name="X"))

        body = {"fields": {"status": "已废弃", "notes": "与新设定冲突"}}
        resp = client.put(
            f"/api/entities/test_book/world_rule/{r.id}",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entity"]["data"]["status"] == "已废弃"
        assert data["entity"]["data"]["notes"] == "与新设定冲突"

    def test_update_character_arc(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import Character
        store = EntityStore("test_book")
        store.add_character(Character(name="主角"))

        body = {"fields": {"arc": "废材 → 斗帝"}}
        resp = client.put(
            "/api/entities/test_book/character/主角",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["entity"]["data"]["arc"] == "废材 → 斗帝"

    def test_update_nonexistent_404(self, client, auth_disabled):
        body = {"fields": {"status": "已废弃"}}
        resp = client.put(
            "/api/entities/test_book/world_rule/rule_xxx",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_update_empty_fields_400(self, client, auth_disabled):
        body = {"fields": {}}
        resp = client.put(
            "/api/entities/test_book/world_rule/rule_xxx",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ── 6. delete ───────────────────────────────────────────────────────────

class TestEntitiesDelete:
    def test_delete_world_rule(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import WorldRule
        store = EntityStore("test_book")
        r = store.add_world_rule(WorldRule(name="X"))

        resp = client.delete(f"/api/entities/test_book/world_rule/{r.id}")
        assert resp.status_code == 204
        # 验证真的删了
        assert store.get_world_rule(r.id) is None

    def test_delete_character(self, client, auth_disabled, tmp_projects_root):
        from lib.memory import EntityStore
        from lib.entity import Character
        store = EntityStore("test_book")
        store.add_character(Character(name="X"))

        resp = client.delete("/api/entities/test_book/character/X")
        assert resp.status_code == 204
        assert store.get_character("X") is None

    def test_delete_nonexistent_404(self, client, auth_disabled):
        resp = client.delete("/api/entities/test_book/world_rule/rule_xxx")
        assert resp.status_code == 404


# ── 7. 综合 ─────────────────────────────────────────────────────────────

class TestEntitiesIntegration:
    def test_full_workflow_create_list_update_delete(self, client, auth_disabled):
        """端到端: create → list → update → delete → list (空)."""
        # 1. create
        body = {
            "type": "world_rule",
            "data": {
                "name": "灵根等级",
                "category": "体系",
                "description": "修士天赋分天/地/人三等",
                "constraints": ["灵根品级先天决定"],
            }
        }
        r = client.post(
            "/api/entities/test_book",
            data=json.dumps(body),
            content_type="application/json",
        )
        rid = r.get_json()["entity"]["id"]

        # 2. list
        r = client.get("/api/entities/test_book?type=world_rule")
        assert r.get_json()["count"] == 1

        # 3. update
        r = client.put(
            f"/api/entities/test_book/world_rule/{rid}",
            data=json.dumps({"fields": {"status": "已废弃"}}),
            content_type="application/json",
        )
        assert r.get_json()["entity"]["data"]["status"] == "已废弃"

        # 4. delete
        r = client.delete(f"/api/entities/test_book/world_rule/{rid}")
        assert r.status_code == 204

        # 5. list (空)
        r = client.get("/api/entities/test_book?type=world_rule")
        assert r.get_json()["count"] == 0