"""tests/test_doctor_false_alarms_2026_10_02.py — doctor 自己的两条假警报。

2026-10-02 生产实跑, doctor 长期输出:

    ❌ LLM (llama-server): https://api.deepseek.com/v1 不可达: HTTP 401
    ❌ Python 版本: 3.11.15 < 3.12
    汇总: ✅ 5  ⚠ 1  ❌ 2        (退出码 1)

而同一时刻: 三本书全部配 minimax、端点实测可达、模型名正确、流水线正常写章节。
**两条都是假的**, 每一轮都报, 退出码永远非零 —— 于是 doctor 变成一个永远
在说"有问题"的工具, 真出问题时反而没人看它。

根因是同一个毛病: 这两项检查**绕过了项目真正的配置解析**。
  - check_llm 读 `cfg["llm"]["api_base"]`（legacy 全局地址）发一个**不带
    Authorization 头**的 GET /v1/models。任何云端 provider 都会回 401。
  - check_python 把 CI 门禁下限(3.12)当成了代码的实际下限, 而生产 systemd
    明确跑的是 python3.11。

本文件把两条钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import doctor  # noqa: E402


# ── Python 版本 ───────────────────────────────────────────────────────────

class _Ver(tuple):
    """真 tuple, 带 major/minor/micro 属性。

    不能用 MagicMock: check_python 之外, pytest 自己的 skipping 插件也会读
    sys.version_info 并和 (3, 12, 0) 比较, MagicMock 不支持与 tuple 比较会直接
    把收集器搞崩 —— 那是测试自己造的噪音, 不是被测代码的问题。
    """

    def __new__(cls, major, minor, micro=0):
        return super().__new__(cls, (major, minor, micro, "final", 0))

    @property
    def major(self): return self[0]

    @property
    def minor(self): return self[1]

    @property
    def micro(self): return self[2]


def _fake_version(major, minor, micro=0):
    return _Ver(major, minor, micro)


class TestPythonVersionIsNotFatalWhenItRuns:
    def test_below_ci_floor_is_warn_not_fail(self):
        """3.11 在这套部署上正常工作, 不该被判死。"""
        with patch.object(doctor.sys, "version_info", _fake_version(3, 11, 15)):
            r = doctor.check_python({"runtime": {"min_python": "3.12"}})
        assert r.status == "warn", "低于 CI 门禁不代表跑不了, 不该 fail"
        assert "3.12" in r.detail, "要���清它低于哪个下限"

    def test_configured_floor_makes_it_ok(self):
        """下限可配 —— 部署用 3.11 就配 3.11, 不产生噪音。"""
        with patch.object(doctor.sys, "version_info", _fake_version(3, 11, 15)):
            r = doctor.check_python({"runtime": {"min_python": "3.11"}})
        assert r.status == "ok"

    def test_meeting_floor_is_ok(self):
        with patch.object(doctor.sys, "version_info", _fake_version(3, 12, 0)):
            r = doctor.check_python({})
        assert r.status == "ok"
        assert "3.12" in r.detail

    def test_below_hard_floor_still_fails(self):
        """放软下限不等于放软到底 —— 真跑不动的版本仍然必须 fail。"""
        with patch.object(doctor.sys, "version_info", _fake_version(3, 7, 0)):
            r = doctor.check_python({})
        assert r.status == "fail", "硬地板以下依赖装不上, 必须判死"

    def test_garbage_config_falls_back_to_default(self):
        """配置写坏了不能让 doctor 自己崩掉。"""
        with patch.object(doctor.sys, "version_info", _fake_version(3, 12, 0)):
            r = doctor.check_python({"runtime": {"min_python": "不是版本号"}})
        assert r.status in ("ok", "warn")

    def test_no_config_at_all_does_not_crash(self):
        with patch.object(doctor.sys, "version_info", _fake_version(3, 12, 0)):
            r = doctor.check_python({})
        assert r.status == "ok"


# ── LLM 端点 ─────────────────────────────────────────────────────────────

class TestLlmEndpointCheckIsHonest:
    def _resp(self, body=b'{"data":[]}'):
        r = MagicMock()
        r.read.return_value = body
        r.__enter__ = lambda s: r
        r.__exit__ = lambda s, *a: False
        return r

    def test_uses_provider_registry_not_legacy_api_base(self):
        """必须探 provider 注册表里的地址, 而不是 config.yaml 的 legacy 字段。

        生产实测的假警报正是这里: legacy 地址是 deepseek, 实际用的是 minimax。
        """
        seen = {}

        class _Req:
            def __init__(self, url, method=None):
                seen["url"] = url
                self._headers = {}

            def add_header(self, k, v):
                self._headers[k] = v
                seen.setdefault("headers", {})[k] = v

        def _fake_resolve(provider):
            return {"provider": provider, "model": "m",
                    "api_base": "https://api.minimax.chat/v1",
                    "api_key": "sk-test"}

        with patch("urllib.request.Request", _Req), \
             patch("urllib.request.urlopen", lambda *a, **k: self._resp()), \
             patch.object(doctor.llm_providers_mod if hasattr(doctor, "llm_providers_mod")
                          else __import__("lib.llm_providers", fromlist=["x"]),
                          "resolve_model", _fake_resolve):
            r = doctor.check_llm({"llm": {"api_base": "https://api.deepseek.com/v1",
                                          "provider": "minimax"}})

        assert seen["url"] == "https://api.minimax.chat/v1/models", \
            "探的是实际生效的 provider 端点, 不是 legacy 全局地址"
        assert r.status == "ok"

    def test_sends_authorization_header(self):
        """不带 key 的话任何云端 provider 都回 401 —— 这正是原来的 bug。"""
        seen = {}

        class _Req:
            def __init__(self, url, method=None):
                seen["url"] = url
                self.h = {}

            def add_header(self, k, v):
                self.h[k] = v
                seen["auth"] = v

        def _fake_resolve(provider):
            return {"provider": provider, "model": "m",
                    "api_base": "https://api.minimax.chat/v1",
                    "api_key": "sk-secret"}

        import lib.llm_providers as lp
        with patch("urllib.request.Request", _Req), \
             patch("urllib.request.urlopen", lambda *a, **k: self._resp()), \
             patch.object(lp, "resolve_model", _fake_resolve):
            doctor.check_llm({"llm": {"provider": "minimax"}})

        assert seen.get("auth") == "Bearer sk-secret", \
            "云端端点必须带鉴权头, 否则一律 401"

    def test_unreachable_is_warn_not_fail(self):
        """默认 provider 不通不代表这本书写不了, 不该让 doctor 永远非零退出。"""
        import lib.llm_providers as lp
        import urllib.error

        def _fake_resolve(provider):
            return {"provider": provider, "model": "m",
                    "api_base": "http://127.0.0.1:60443/v1", "api_key": ""}

        def _boom(*a, **k):
            raise urllib.error.URLError("Connection refused")

        with patch("urllib.request.urlopen", _boom), \
             patch.object(lp, "resolve_model", _fake_resolve):
            r = doctor.check_llm({"llm": {"provider": "local"}})

        assert r.status == "warn", "端点不通要 warn; 判死交给逐本漂移检测"

    def test_unknown_provider_does_not_crash(self):
        import lib.llm_providers as lp
        def _raise(p):
            raise KeyError(p)
        with patch.object(lp, "resolve_model", _raise):
            r = doctor.check_llm({"llm": {"provider": "不存在的provider"}})
        assert r.status == "warn"


class TestDoctorNoLongerAlwaysFailsOnAHealthyBox:
    """整体回归: 一台配置正确的机器上, doctor 不该永远退出码非零。

    刻意**不**去 patch 全局 sys.version_info: 那个对象被 pytest 自己的
    skipping 插件和若干依赖的导入链读取, 一旦换成一个假对象, 报出来的会是
    导入期的 pydantic 错误, 与被测代码无关(踩过一次)。
    版本相关的行为由上面的 TestPythonVersionIsNotFatalWhenItRuns 逐条覆盖;
    这里只桩配置, 验证"默认 provider 通畅时不该有任何 fail"。
    """

    def test_healthy_environment_has_no_fail(self, monkeypatch):
        import lib.llm_providers as lp

        class _Resp:
            def read(self): return b'{"data":[]}'
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Req:
            def __init__(self, url, method=None): pass
            def add_header(self, k, v): pass

        def _fake_resolve(provider):
            return {"provider": provider, "model": "m",
                    "api_base": "https://api.minimax.chat/v1", "api_key": "k"}

        monkeypatch.setattr(doctor, "get_config", lambda: {
            "llm": {"provider": "minimax"},
            "runtime": {"min_python": "3.9"},
        })
        monkeypatch.setattr("urllib.request.Request", _Req)
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
        monkeypatch.setattr(lp, "resolve_model", _fake_resolve)

        results = doctor.run_all()
        failed = [r.name for r in results if r.status == "fail"]
        assert not any("LLM" in n for n in failed), \
            f"LLM 端点通了就不该被判死, 但这些项 fail 了: {failed}"
        assert "Python 版本" not in failed, "版本不该被判死"
