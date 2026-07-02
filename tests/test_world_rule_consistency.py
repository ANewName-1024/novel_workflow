"""
tests/test_world_rule_consistency.py — v1.2 M1.4 一致性扫描测试
"""
import json

import pytest

from lib import self_check, storage, llm as llm_mod
from lib.entity import WorldRule
from lib.memory import EntityStore


class MockLLM:
    def __init__(self, response: str = ""):
        self._response = response
        self.calls = []

    def complete(self, prompt: str, **kwargs) -> str:
        self.calls.append({"prompt": prompt, **kwargs})
        return self._response


@pytest.fixture
def setup_book(tmp_path, monkeypatch):
    from lib import storage as sm
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    monkeypatch.setattr(sm, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(sm, "ROOT", proj_root)
    sm.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})
    sm.write_chapter("test_book", "ch_001", "## 第一章 测试\n\n主角萧炎在斗气大陆的废墟中觉醒。")
    return proj_root


class TestNoActiveRules:
    def test_no_rules_skips_llm(self, setup_book):
        llm = MockLLM()
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result["overall_ok"] is True
        assert result["rules_checked"] == 0
        assert len(llm.calls) == 0

    def test_only_draft_rules_skipped(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="草案", status="草案"))
        store.add_world_rule(WorldRule(name="废弃", status="已废弃"))
        llm = MockLLM()
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result["rules_checked"] == 0
        assert len(llm.calls) == 0


class TestLLMNoViolations:
    def test_ok_result(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="灵根等级", category="体系",
                                       constraints=["灵根品级先天决定"]))
        llm = MockLLM(response=json.dumps({"violations": [], "overall_ok": True,
                                            "summary": "无违反"}))
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result["overall_ok"] is True
        assert result["rules_checked"] == 1
        assert "灵根等级" in llm.calls[0]["prompt"]
        assert "灵根品级先天决定" in llm.calls[0]["prompt"]

    def test_saves_to_file(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="规则X", constraints=["约束"]))
        llm = MockLLM(response='{"violations":[],"overall_ok":true,"summary":"OK"}')
        self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        sc_path = storage.selfcheck_path("test_book", "ch_001")
        assert sc_path.exists()
        saved = json.loads(sc_path.read_text(encoding="utf-8"))
        assert saved["overall_ok"] is True


class TestLLMWithViolations:
    def test_parses_violations(self, setup_book):
        store = EntityStore("test_book")
        rule = store.add_world_rule(WorldRule(name="灵根等级", category="体系",
                                              constraints=["不可后天改变"]))
        llm = MockLLM(response=json.dumps({
            "violations": [{"rule_id": rule.id, "rule_name": "灵根等级",
                            "constraint": "不可后天改变",
                            "evidence": "主角服丹药提升品级",
                            "severity": "critical"}],
            "overall_ok": False, "summary": "1处严重违反",
        }))
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result["overall_ok"] is False
        assert len(result["violations"]) == 1
        assert result["violations"][0]["severity"] == "critical"

    def test_markdown_fence_stripped(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="X", constraints=["约束"]))
        llm = MockLLM(response="```json\n" + json.dumps(
            {"violations": [], "overall_ok": True, "summary": "OK"}) + "\n```")
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result["overall_ok"] is True

    def test_malformed_output(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="X"))
        llm = MockLLM(response="not json at all")
        result = self_check.world_rule_consistency("test_book", "ch_001", llm=llm)
        assert result.get("parse_error") is True
        assert result["violations"] == []


class TestErrorPaths:
    def test_chapter_not_found(self, setup_book):
        llm = MockLLM()
        with pytest.raises(FileNotFoundError):
            self_check.world_rule_consistency("test_book", "ch_999", llm=llm)

    def test_save_false_no_file(self, setup_book):
        store = EntityStore("test_book")
        store.add_world_rule(WorldRule(name="X"))
        llm = MockLLM(response='{"violations":[],"overall_ok":true,"summary":"OK"}')
        self_check.world_rule_consistency("test_book", "ch_001", llm=llm, save=False)
        assert not storage.selfcheck_path("test_book", "ch_001").exists()


class TestCheckConsistencyAPI:
    @pytest.fixture
    def client(self, setup_book):
        from review_ui import app as ra
        ra.app.config["TESTING"] = True
        ra.app.config["SECRET_KEY"] = "test"
        with ra.app.test_client() as c:
            yield c

    @pytest.fixture
    def auth_off(self, monkeypatch):
        from review_ui import app as ra
        monkeypatch.setattr(ra, "_get_auth", lambda: {"enabled": False, "user": "", "password": ""})

    def test_no_rules_api(self, client, auth_off, setup_book):
        r = client.post("/api/entities/test_book/check-consistency",
                        data=json.dumps({"chapter_id": "ch_001"}),
                        content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["overall_ok"] is True
        assert data["rules_checked"] == 0

    def test_with_violation_api(self, client, auth_off, setup_book, monkeypatch):
        store = EntityStore("test_book")
        rule = store.add_world_rule(WorldRule(name="灵根等级", category="体系",
                                              constraints=["不可后天改变"]))
        mock_llm = MockLLM(response=json.dumps({
            "violations": [{"rule_id": rule.id, "rule_name": "灵根等级",
                            "constraint": "不可后天改变",
                            "evidence": "主角服丹药提升品级", "severity": "critical"}],
            "overall_ok": False, "summary": "违反",
        }))
        monkeypatch.setattr(llm_mod, "get_llm", lambda: mock_llm)

        r = client.post("/api/entities/test_book/check-consistency",
                        data=json.dumps({"chapter_id": "ch_001"}),
                        content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        assert data["overall_ok"] is False
        assert len(data["violations"]) == 1
        assert data["violations"][0]["severity"] == "critical"

    def test_missing_params_400(self, client, auth_off, setup_book):
        r = client.post("/api/entities/test_book/check-consistency",
                        data=json.dumps({}), content_type="application/json")
        assert r.status_code == 400

    def test_scan_all(self, client, auth_off, setup_book):
        storage.write_chapter("test_book", "ch_002", "## 第二章\n\n内容。")
        r = client.post("/api/entities/test_book/check-consistency",
                        data=json.dumps({"all": True}), content_type="application/json")
        assert r.status_code == 200
        assert r.get_json()["total"] == 2
