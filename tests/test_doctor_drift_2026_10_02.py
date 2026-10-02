"""
test_doctor_drift_2026_10_02.py — 配置漂移检测 (lib/doctor.py)

背景: 2026-10-02 生产实跑踩到两类 doctor 测不出来的坑 ——
  1) 书配 deepseek-chat, 账号只收 deepseek-flash / deepseek-v4-pro → HTTP 400,
     而 resolve_model 只发一条 WARNING 就照发(设计如此);
  2) init 把新书绑到 http://127.0.0.1:60443/v1, 那台机器上没有进程在监听。

这些用例全部 mock 掉 LLM.ping(), **不打任何真实网络请求**。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# 2026-10-02 生产 400 的原文, 一字不改地贴进来: 解析逻辑必须认得这句式。
REAL_400_BODY = (
    '{"error": {"message": "The supported API model names are '
    'deepseek-flash, deepseek-v4-pro, but you passed deepseek-chat.", '
    '"type": "invalid_request_error"}}'
)


def _api_error(status: int, body: str, cls_name: str = "BadRequestError"):
    """造一个形状和 openai SDK 异常一致的异常。

    刻意不 import openai 的异常类: 那些类的 __init__ 要求传 httpx.Request/
    Response, 拼起来比这里直接造一个还啰嗦, 而且分类逻辑真正依赖的只有
    形状(status_code + response.text + 类型名), 不依赖真的继承 openai 基类。
    注意别去改 exc.__class__ —— CPython 不允许给异常换类。
    """
    exc = type(cls_name, (Exception,), {})(body)
    exc.status_code = status          # type: ignore[attr-defined]
    exc.response = MagicMock()         # type: ignore[attr-defined]
    exc.response.text = body           # type: ignore[attr-defined]
    return exc


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_tiktoken_download(monkeypatch):
    """LLM.__init__ 会 tiktoken.get_encoding(), 那是可能触发下载的网络调用。"""
    from lib import llm as llm_mod
    monkeypatch.setattr(llm_mod, "tiktoken", MagicMock())


@pytest.fixture
def drift_env(monkeypatch):
    """把 book 的 LLM 配置解析固定成测试要的样子, 不依赖真实 projects/。"""
    from lib import llm_providers

    def _install(resolved: dict):
        monkeypatch.setattr(llm_providers, "resolve_for_book",
                            lambda book: dict(resolved))
    return _install


def _resolved(**over):
    """resolve_for_book() 的返回形状(含 2026-10-02 新加的两个字段)。"""
    base = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
        "type": "openai-compat",
        "min_max_tokens": 0,
    }
    base.update(over)
    return base


# ── 1) 模型名不在 provider 的已知列表 → 告警 ───────────────────────────────

def test_model_not_in_known_list_reports_drift(drift_env):
    from lib import doctor
    from lib import llm_providers

    # 账号实际只收 flash / v4-pro, 但注册表里还留着老的 deepseek-chat。
    monkey_models = ["deepseek-chat", "deepseek-coder", "deepseek-reasoner",
                     "deepseek-flash", "deepseek-v4-pro"]
    drift_env(_resolved(model="deepseek-v4-turbo"))
    orig = llm_providers.get_provider_config
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": monkey_models}):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)

    hits = [f for f in rep.findings if f.kind == "model_unknown"]
    assert len(hits) == 1, [f.as_dict() for f in rep.findings]
    assert hits[0].book == "test_book"
    assert hits[0].data["provider"] == "deepseek"
    assert "deepseek-v4-turbo" in hits[0].detail
    # 期望的候选模型必须原样列出来, 用户照着改就行
    assert hits[0].data["candidates"] == monkey_models
    assert "deepseek-flash" in hits[0].hint
    assert rep.by_kind["model_unknown"] == "warn"
    assert rep.ok is True          # 只是告警, 不是致命 —— 真正判死看 400


def test_model_in_known_list_has_no_drift(drift_env):
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved(model="deepseek-flash"))
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["deepseek-chat", "deepseek-flash"]}):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)
    assert rep.findings == []
    assert rep.ok is True


# ── 2) ping() 400 + "supported API model names" → 提取可用模型名 ────────────

def test_ping_400_extracts_supported_model_names(drift_env):
    from lib import doctor
    drift_env(_resolved())
    with patch("lib.llm.LLM.ping", side_effect=_api_error(400, REAL_400_BODY)):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)

    hits = [f for f in rep.findings if f.kind == "model_rejected"]
    assert len(hits) == 1, [f.as_dict() for f in rep.findings]
    f = hits[0]
    # API 返回的可用模型名必须原样出现在报告里 —— 这条的价值全在于此
    assert f.data["supported_models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert "deepseek-flash" in f.detail and "deepseek-v4-pro" in f.detail
    assert f.severity == "fatal"
    assert rep.ok is False


def test_extract_supported_models_from_exception_text():
    """只认得 body 不够 —— 有的 SDK 版本把报文放在 str(e) 里。"""
    from lib import doctor
    exc = _api_error(400, "Error code: 400")
    exc.response.text = ""
    exc.args = ("The supported API model names are m-a, m-b, but you passed m-c.",)
    assert doctor._extract_supported_models(exc) == ["m-a", "m-b"]


def test_ping_400_without_model_list_is_only_warn(drift_env):
    """400 但报文里没有模型清单 → 降级成告警, 别硬编模型名。"""
    from lib import doctor
    drift_env(_resolved())
    with patch("lib.llm.LLM.ping", side_effect=_api_error(400, '{"error":"bad ctx"}')):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)
    assert [f.kind for f in rep.findings] == ["endpoint_bad_request"]
    assert rep.findings[0].severity == "warn"
    assert rep.ok is True


# ── 3) ping() 连接失败 → 端点不可达(要和"模型名不对"分开) ──────────────────

def test_ping_connection_refused_reports_unreachable_endpoint(drift_env):
    from lib import doctor
    drift_env(_resolved(provider="local",
                        api_base="http://127.0.0.1:60443/v1",
                        model="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"))
    with patch("lib.llm.LLM.ping", side_effect=ConnectionRefusedError(10061, "拒绝连接")):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)

    hits = [f for f in rep.findings if f.kind == "endpoint_unreachable"]
    assert len(hits) == 1
    f = hits[0]
    assert f.severity == "fatal"
    # 端口要点出来: "连不上"和"没人监听这个端口"对用户是两件事
    assert "60443" in f.detail
    assert "监听" in f.hint
    assert rep.ok is False


def test_ping_timeout_reports_unreachable(drift_env):
    from lib import doctor
    drift_env(_resolved())
    with patch("lib.llm.LLM.ping", side_effect=TimeoutError("timed out")):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)
    assert rep.findings[0].kind == "endpoint_unreachable"


def test_network_problem_is_not_reported_as_model_problem(drift_env):
    """反向断言: 网络不通绝不能被归到 model_* 那一类(修法完全相反)。"""
    from lib import doctor
    drift_env(_resolved())
    with patch("lib.llm.LLM.ping", side_effect=ConnectionRefusedError(10061, "x")):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)
    assert not [f for f in rep.findings if f.kind.startswith("model_")]


# ── 4) 401 / 403 → key 问题 ────────────────────────────────────────────────

def test_ping_401_reports_api_key_problem(drift_env):
    from lib import doctor
    drift_env(_resolved())
    with patch("lib.llm.LLM.ping", side_effect=_api_error(401, "invalid api key")):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)
    f = rep.findings[0]
    assert f.kind == "api_key"
    assert f.severity == "fatal"
    assert f.data["env_key"] == "DEEPSEEK_API_KEY"
    assert ".env" in f.hint


def test_ping_403_reports_api_key_problem(drift_env):
    from lib import doctor
    drift_env(_resolved(provider="minimax", model="MiniMax-M3"))
    with patch("lib.llm.LLM.ping", side_effect=_api_error(403, "forbidden")):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)
    assert rep.findings[0].kind == "api_key"
    assert rep.findings[0].data["env_key"] == "MINIMAX_API_KEY"


# ── 5) 正常配置 → 无漂移 ────────────────────────────────────────────────────

def test_healthy_config_reports_no_drift(drift_env):
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved())
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["deepseek-chat", "deepseek-coder"]}), \
         patch("lib.llm.LLM.ping", return_value={"ok": True, "model": "deepseek-chat"}):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=True)

    assert rep.findings == []
    assert rep.ok is True
    assert rep.probes == 1
    assert rep.by_kind["endpoint_unreachable"] == "ok"
    assert "novel doctor" not in doctor.format_drift_report(rep)   # 顺带能渲染
    assert "没有发现配置漂移" in doctor.format_drift_report(rep)


def test_probe_false_makes_no_request(drift_env):
    """probe=False 必须真的零请求 —— CLI 侧靠这个做默认行为。"""
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved())
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["deepseek-chat"]}), \
         patch("lib.llm.LLM.ping") as ping:
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)
    ping.assert_not_called()
    assert rep.probes == 0
    assert "未发任何网络请求" in rep.time_note


# ── 6) 推理模型 token 预算 / 端点覆盖 ──────────────────────────────────────

def test_reasoning_provider_reports_token_budget(drift_env):
    from lib import doctor
    drift_env(_resolved(provider="minimax", model="MiniMax-M3.1-Flash-Preview",
                        api_base="https://api.minimax.chat/v1", min_max_tokens=16384))
    rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)
    f = [x for x in rep.findings if x.kind == "token_budget"][0]
    assert f.data["min_max_tokens"] == 16384
    assert "16384" in f.detail
    assert f.severity == "info"


def test_model_drift_hint_includes_token_floor(drift_env):
    """模型名不对 且 provider 是推理模型 → 提示里要带上 max_tokens 下限。"""
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved(provider="minimax", model="MiniMax-M9",
                        min_max_tokens=16384))
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["MiniMax-M3"]}):
        rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)
    hint = [f for f in rep.findings if f.kind == "model_unknown"][0].hint
    assert "16384" in hint


def test_api_base_overridden_is_reported(drift_env):
    from lib import doctor
    drift_env(_resolved(api_base="http://127.0.0.1:60443/v1",
                        api_base_overridden=True))
    rep = doctor.check_llm_config_drift(books=["test_book"], probe=False)
    f = [x for x in rep.findings if x.kind == "endpoint_overridden"][0]
    assert "60443" in f.detail


# ── 7) 只读 / 预算 / 汇总 ───────────────────────────────────────────────────

def test_drift_check_never_writes_book_config(tmp_path):
    """硬性要求: 只读。跑完一本 config.json 必须逐字节不变。"""
    from lib import doctor
    from lib import storage
    import json

    proj = tmp_path / "projects"
    (proj / "ro_book").mkdir(parents=True)
    cfg = {"book_name": "ro", "llm_provider": "deepseek", "llm_model": "deepseek-chat"}
    (proj / "ro_book" / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    before = (proj / "ro_book" / "config.json").read_bytes()

    with patch.object(storage, "PROJECTS_ROOT", proj), \
         patch("lib.llm.LLM.ping", side_effect=_api_error(400, REAL_400_BODY)):
        rep = doctor.check_llm_config_drift(probe=True)
    assert rep.books == 1
    assert rep.ok is False                       # 确实检出了漂移
    assert (proj / "ro_book" / "config.json").read_bytes() == before


def test_identical_endpoints_probed_once(drift_env):
    """多本书共用同一端点 → 只探一次(否则 20 本书就是 20 次计费请求)。"""
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved())
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["deepseek-chat"]}), \
         patch("lib.llm.LLM.ping", return_value={"ok": True}) as ping:
        rep = doctor.check_llm_config_drift(
            books=["a", "b", "c", "d"], probe=True)
    assert ping.call_count == 1
    assert rep.probes == 1
    assert rep.books == 4


def test_budget_exhaustion_is_reported_not_ignored(drift_env):
    """预算用尽要如实说"没探到", 而不是静默当成通过。"""
    from lib import doctor
    from lib import llm_providers as lp

    calls = []

    def _ping(self, *a, **kw):
        calls.append(1)
        return {"ok": True}

    drift_env(_resolved())
    with patch.object(lp, "resolve_for_book",
                      lambda book: _resolved(model=f"m-{book}")), \
         patch.object(lp, "get_provider_config", lambda p: {"models": []}), \
         patch("lib.llm.LLM.ping", _ping):
        rep = doctor.check_llm_config_drift(
            books=["a", "b", "c"], probe=True, budget_sec=0.0)
    assert calls == []                              # 预算 0 = 一个请求都不发
    assert len([f for f in rep.findings if f.kind == "probe_skipped"]) == 3


def test_summary_separates_fatal_from_warn(drift_env):
    from lib import doctor
    from lib import llm_providers as lp

    def _resolve(book):
        # bad_key 用合法模型名但 key 被拒(致命); odd_model 模型名不在表里(告警)
        return _resolved() if book == "bad_key" else _resolved(model="nope")

    def _ping(self, *a, **kw):
        if self.model == "deepseek-chat":
            raise _api_error(401, "invalid api key")
        return {"ok": True}

    with patch.object(lp, "resolve_for_book", _resolve), \
         patch.object(lp, "get_provider_config", lambda p: {"models": ["deepseek-chat"]}), \
         patch("lib.llm.LLM.ping", _ping):
        rep = doctor.check_llm_config_drift(books=["bad_key", "odd_model"], probe=True)
    assert rep.ok is False
    assert len(rep.fatal) == 1 and rep.fatal[0].book == "bad_key"
    assert any(f.book == "odd_model" for f in rep.warns)
    assert "❌ 1" in doctor.format_drift_report(rep)


def test_time_note_mentions_budget(drift_env):
    from lib import doctor
    from lib import llm_providers
    drift_env(_resolved())
    with patch.object(llm_providers, "get_provider_config",
                      lambda p: {"models": ["deepseek-chat"]}), \
         patch("lib.llm.LLM.ping", return_value={"ok": True}):
        rep = doctor.check_llm_config_drift(books=["a"], probe=True,
                                           timeout_sec=15, budget_sec=60)
    assert "15s" in rep.time_note and "60s" in rep.time_note
    assert rep.elapsed_ms >= 0


def test_unresolvable_provider_is_fatal(drift_env):
    """书里写了注册表里没有的 llm_provider → resolve_for_book 抛 KeyError。"""
    from lib import doctor
    from lib import llm_providers
    with patch.object(llm_providers, "resolve_for_book",
                      side_effect=KeyError("Unknown provider: 'nope'")):
        rep = doctor.check_llm_config_drift(books=["weird"], probe=False)
    f = rep.findings[0]
    assert f.kind == "resolve_error"
    assert f.severity == "fatal"
    assert rep.ok is False
