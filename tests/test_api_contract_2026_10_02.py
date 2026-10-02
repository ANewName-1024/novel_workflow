"""tests/test_api_contract_2026_10_02.py — 四条「报成功但没做事 / 照单全收」的接口.

2026-10-02 对全部 89 条路由做了一次带鉴权的实跑扫描, 找出同一类病:
**接口在没做事的时候报成功, 或者把不该收的输入照单全收**。四处:

  P1  POST /api/config/<book>   cfg.update(body) —— 任何 key 直写 config.json,
                                 然后回一句「已保存」。llm_model / api_base /
                                 llm_provider 是生成流水线要读的字段, 界面上一处
                                 拼写错误就静默落盘、静默改变后面章节的生成方式。
                                 同一个操作在 PUT /api/projects/<book> 上本来就有
                                 白名单 —— 两份名单迟早漂移, 所以这次只留一份定义。
  P4  POST /api/outline/<book>/node   `{}` 返回 201, 建出「未命名章节」; 大纲没有
                                 卷时还会顺手建一个「默认卷」。一次空请求改两处。
  P6  PUT /api/projects/<book>   `{}` 返回 200「已更新」, 实际只盖了一个
                                 updated_at。
  P7  GET /api/llm/providers     current_model = os.environ["MODEL"], 而生产环境
                                 从来没设这个变量 —— 这个字段恒为空。

公共约定: 用 conftest 的 client fixture(tmp_projects_root + auth_disabled)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import llm_providers as lp  # noqa: E402
from lib import outline_editor as oe  # noqa: E402
from lib import storage  # noqa: E402
from review_ui.core import EDITABLE_CONFIG_FIELDS  # noqa: E402

_BOOK = "test_book"

# 白名单里每类字段各取一个能落盘的样例值(整数字段故意用字符串,
# 两条路由都必须强转成 int —— UI 表单就是发字符串的)。
_SAMPLE_VALUE = {
    "book_name": "新书名",
    "genre": "科幻",
    "tone": "冷硬",
    "protagonist": "王五",
    "antagonist": "赵六",
    "main_plot": "一条主线",
    "style": "简洁",
    "target_chapters": "42",
    "words_per_chapter": 1800,
    "language": "zh",
    "llm_model": "model-x",
    "api_base": "http://example.invalid/v1",
    "llm_provider": "local",
    "self_check": False,
    "auto_rewrite_on_critical": True,
    "self_check_strict": False,
}

# 白名单外的 key: 曾出现在真实调用里(created_at/name 是历史 body 的字段),
# 以及纯粹编的拼写错误。
_UNKNOWN_KEYS = ("bogus_field", "created_at", "evil_field", "name", "id", "llm-mode1")


def _config_path() -> Path:
    return storage.project_path(_BOOK) / "config.json"


def _config_bytes() -> bytes:
    return _config_path().read_bytes()


def _read_config() -> dict:
    return storage.read_json(_BOOK, "config.json") or {}


# ── P1: config 写入没有字段白名单 ───────────────────────────────────────────

class TestConfigWriteHasWhitelist:
    """POST /api/config/<book> 曾经把调用方发的任何 key 直接写进 config.json。"""

    def test_unknown_key_is_not_written(self, client, tmp_projects_root):
        r = client.post(f"/api/config/{_BOOK}", json={"bogus_field": 1})

        assert r.status_code == 400, \
            f"白名单外的 key 竟被当成功保存: {r.status_code} {r.get_json()}"
        assert "bogus_field" not in _read_config(), \
            "白名单外的 key 落进了 config.json"
        # GET 也不该把它读回来 —— 落盘那一刻就已经是坏数据了
        echoed = client.get(f"/api/config/{_BOOK}").get_json()
        assert "bogus_field" not in echoed

    def test_recognised_key_is_written(self, client, tmp_projects_root):
        r = client.post(f"/api/config/{_BOOK}", json={"llm_model": "model-x"})

        assert r.status_code == 200, r.get_json()
        assert r.get_json()["ok"] is True
        assert _read_config()["llm_model"] == "model-x", \
            "白名单内的 key 反而没写进去 —— 白名单把正常编辑也挡住了"

    def test_int_field_is_coerced_like_the_other_route(self, client, tmp_projects_root):
        """UI 发的是字符串; 落盘必须是 int(与 PUT /api/projects 同一套规则)。"""
        r = client.post(f"/api/config/{_BOOK}", json={"target_chapters": "42"})

        assert r.status_code == 200, r.get_json()
        assert _read_config()["target_chapters"] == 42
        assert isinstance(_read_config()["target_chapters"], int)

    def test_all_unknown_body_is_400_and_config_is_byte_identical(self, client, tmp_projects_root):
        before = _config_bytes()

        r = client.post(f"/api/config/{_BOOK}", json={"nope": 1, "also_nope": 2})

        assert r.status_code == 400, \
            f"全白名单外的 body 被当成功保存: {r.status_code} {r.get_json()}"
        assert r.get_json()["ok"] is False
        assert _config_bytes() == before, \
            "被拒绝的请求改了 config.json(哪怕只多盖一个 updated_at)"


class TestWhitelistIsOneDefinition:
    """反漂移: 两条路由必须共用同一份名单定义。"""

    def test_both_modules_use_the_same_helper_object(self):
        from review_ui import core
        from review_ui.bp import llm_config, projects

        assert projects.pick_config_updates is core.pick_config_updates, \
            "projects.py 又自己抄了一份可写字段名单"
        assert llm_config.pick_config_updates is core.pick_config_updates, \
            "llm_config.py 又自己抄了一份可写字段名单"

    @pytest.mark.parametrize("key", sorted(EDITABLE_CONFIG_FIELDS))
    def test_both_routes_accept_the_same_key(self, client, tmp_projects_root, key):
        value = _SAMPLE_VALUE[key]

        r_cfg = client.post(f"/api/config/{_BOOK}", json={key: value})
        r_put = client.put(f"/api/projects/{_BOOK}", json={key: value})

        assert r_cfg.status_code == 200, f"config 路由拒了白名单内的 {key}: {r_cfg.get_json()}"
        assert r_put.status_code == 200, f"projects 路由拒了白名单内的 {key}: {r_put.get_json()}"
        stored = _read_config()[key]
        expected = int(value) if key in ("target_chapters", "words_per_chapter") else value
        assert stored == expected, f"{key} 落盘值 {stored!r} != {expected!r}"

    @pytest.mark.parametrize("key", _UNKNOWN_KEYS)
    def test_both_routes_reject_the_same_key(self, client, tmp_projects_root, key):
        before = _config_bytes()
        before_value = _read_config().get(key, None)

        r_cfg = client.post(f"/api/config/{_BOOK}", json={key: "x"})
        r_put = client.put(f"/api/projects/{_BOOK}", json={key: "x"})

        # created_at 是 init_project 本来就写进去的, 所以这里比的是「值有没有被
        # 改掉」, 不是「这个 key 存不存在」。
        assert _read_config().get(key, None) == before_value, \
            f"白名单外的 {key} 被改写了"
        assert _config_bytes() == before, f"{key} 让 config.json 变了"
        # 两条路由对「拒绝」的表达不同(见 P6: projects 保持 200 + changed: []),
        # 但白名单只有一份: 都不写。
        assert r_cfg.status_code == 400, r_cfg.get_json()
        assert r_put.status_code == 200, r_put.get_json()
        assert r_put.get_json()["changed"] == []


# ── P4: outline node add 空 body 建垃圾节点 + 顺手建卷 ──────────────────────

class TestOutlineNodeAddRejectsEmpty:
    """POST /api/outline/<book>/node 曾经接受 `{}` 并返回 201。"""

    def test_empty_body_is_400(self, client, tmp_projects_root):
        r = client.post(f"/api/outline/{_BOOK}/node", json={})

        assert r.status_code == 400, \
            f"空 body 建出了节点并报成功: {r.status_code} {r.get_json()}"

    def test_no_default_volume_is_autocreated(self, client, tmp_projects_root):
        """回归重点: 一次空请求曾经同时改两处 —— 建「未命名章节」+ 建「默认卷」。"""
        outline_path = storage.project_path(_BOOK) / "outline.json"
        before = outline_path.read_bytes()

        assert client.post(f"/api/outline/{_BOOK}/node", json={}).status_code == 400

        outline = oe.load_outline_or_empty(_BOOK)
        assert outline["volumes"] == [], \
            f"被拒绝的请求还是建出了卷: {outline['volumes']}"
        assert outline["chapters"] == [], \
            f"被拒绝的请求还是建出了章节: {outline['chapters']}"
        assert all(v.get("title") != "默认卷" for v in outline["volumes"]), \
            "兜底卷「默认卷」在拒绝路径上仍然被建了出来"
        assert outline_path.read_bytes() == before, "被拒绝的请求写了 outline.json"

    def test_served_outline_also_has_no_volume(self, client, tmp_projects_root):
        client.post(f"/api/outline/{_BOOK}/node", json={})

        served = client.get(f"/api/outline/{_BOOK}").get_json()
        assert served["volumes"] == []
        assert served["chapters"] == []

    @pytest.mark.parametrize("body", [
        {"title": "   "},      # 只有空白
        {"title": ""},
        {"summary": "只有摘要"},
        {"parent_vol": "vol_1", "position": 0},
    ])
    def test_blank_or_missing_title_is_400(self, client, tmp_projects_root, body):
        r = client.post(f"/api/outline/{_BOOK}/node", json=body)

        assert r.status_code == 400, \
            f"没有可用标题却报成功: {body!r} -> {r.status_code} {r.get_json()}"
        assert oe.load_outline_or_empty(_BOOK)["chapters"] == []

    def test_real_title_still_creates_the_node(self, client, tmp_projects_root):
        r = client.post(f"/api/outline/{_BOOK}/node",
                        json={"title": "  第一章  ", "summary": "摘要"})

        assert r.status_code == 201, r.get_json()
        node = r.get_json()["node"]
        assert node["title"] == "第一章", "标题应去掉首尾空白后落盘"
        assert node["summary"] == "摘要"
        chapters = oe.load_outline_or_empty(_BOOK)["chapters"]
        assert [c["title"] for c in chapters] == ["第一章"]

    def test_summary_is_optional(self, client, tmp_projects_root):
        """只要求 title: 采纳 AI 建议时 summary 本来就可能是空的。"""
        r = client.post(f"/api/outline/{_BOOK}/node", json={"title": "只有标题"})

        assert r.status_code == 201, r.get_json()
        assert r.get_json()["node"]["summary"] == ""

    def test_non_dict_body_is_400(self, client, tmp_projects_root):
        r = client.post(f"/api/outline/{_BOOK}/node", json=["title"])

        assert r.status_code == 400, f"数组 body 应当 400, 实得 {r.status_code}"


# ── P6: 空更新报「已更新」 ─────────────────────────────────────────────────

class TestEmptyUpdateDoesNotClaimSuccess:
    def test_empty_body_does_not_report_success(self, client, tmp_projects_root):
        r = client.put(f"/api/projects/{_BOOK}", json={})

        assert r.status_code == 400, \
            f"空 body 仍返回成功: {r.status_code} {r.get_json()}"
        body = r.get_json()
        assert body["ok"] is False
        assert "已更新" not in json_text(body), "空更新仍然宣称已更新"

    def test_one_real_field_reports_success_and_applies(self, client, tmp_projects_root):
        r = client.put(f"/api/projects/{_BOOK}", json={"genre": "科幻"})

        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["ok"] is True
        assert "已更新" in json_text(body)
        assert body["changed"] == ["genre"]
        assert _read_config()["genre"] == "科幻"
        assert _read_config().get("updated_at"), "有真改动时才盖 updated_at"

    def test_all_unknown_body_claims_nothing_and_writes_nothing(self, client, tmp_projects_root):
        before = _config_bytes()

        r = client.put(f"/api/projects/{_BOOK}",
                       json={"created_at": "FAKE", "evil_field": "x"})

        assert "已更新" not in json_text(r.get_json()), \
            f"零改动却宣称已更新: {r.get_json()}"
        assert r.get_json()["changed"] == []
        assert _config_bytes() == before, "零改动的请求仍然写了 config.json"

    def test_int_field_still_coerced(self, client, tmp_projects_root):
        r = client.put(f"/api/projects/{_BOOK}", json={"target_chapters": "60"})

        assert r.status_code == 200, r.get_json()
        assert _read_config()["target_chapters"] == 60
        assert isinstance(_read_config()["target_chapters"], int)

    def test_bad_int_is_400_not_500(self, client, tmp_projects_root):
        r = client.put(f"/api/projects/{_BOOK}", json={"target_chapters": "abc"})

        assert r.status_code == 400, f"非法的整数字段应当 400, 实得 {r.status_code}"


# ── P7: current_model 恒为空 ───────────────────────────────────────────────

class TestCurrentModelIsReal:
    def test_current_model_key_still_present(self, client, tmp_projects_root):
        data = client.get("/api/llm/providers").get_json()

        assert set(data) >= {"ok", "providers", "default_provider", "current_model"}, \
            f"响应形状变了: {sorted(data)}"
        assert isinstance(data["providers"], dict) and data["providers"]

    def test_current_model_is_not_empty_without_book(self, client, tmp_projects_root, monkeypatch):
        """生产缺陷本体: MODEL 环境变量从未设置, 于是 current_model 恒为 ""。"""
        monkeypatch.delenv("MODEL", raising=False)

        data = client.get("/api/llm/providers").get_json()

        assert data["current_model"], \
            "current_model 又变回空字符串 —— 它现在必须走真实解析路径"

    def test_current_model_matches_book_resolution(self, client, tmp_projects_root):
        model = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        r = client.post(f"/api/config/{_BOOK}",
                        json={"llm_provider": "local", "llm_model": model})
        assert r.status_code == 200, r.get_json()

        data = client.get(f"/api/llm/providers?book={_BOOK}").get_json()

        assert data["current_model"] == model, \
            f"current_model={data['current_model']!r} != 书里配置的 {model!r}"
        assert data["current_model"] == lp.resolve_for_book(_BOOK)["model"], \
            "current_model 与 llm_providers.resolve_for_book 的真实解析不一致"

    def test_per_book_model_differs_from_global(self, client, tmp_projects_root):
        client.post(f"/api/config/{_BOOK}",
                    json={"llm_provider": "deepseek", "llm_model": "deepseek-coder"})

        per_book = client.get(f"/api/llm/providers?book={_BOOK}").get_json()["current_model"]
        global_model = client.get("/api/llm/providers").get_json()["current_model"]

        assert per_book == "deepseek-coder"
        assert per_book != global_model, \
            "?book= 没起作用, 书籍级覆盖没进解析"

    def test_broken_book_config_does_not_500(self, client, tmp_projects_root):
        """书里写了个没注册的 provider: 回退, 但接口不能 500。"""
        client.post(f"/api/config/{_BOOK}", json={"llm_provider": "no_such_provider"})

        r = client.get(f"/api/llm/providers?book={_BOOK}")

        assert r.status_code == 200, f"解析失败不该整条接口 500: {r.get_json()}"


def json_text(payload) -> str:
    """把响应体摊成一段可搜的文本(避免在断言里 dump 中文到控制台)。"""
    import json as _json
    return _json.dumps(payload, ensure_ascii=False)
