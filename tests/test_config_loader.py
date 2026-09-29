"""
test_config_loader.py — 配置加载: env 替换 + 默认值 + 缓存
"""
import os
import pytest
from pathlib import Path
from lib import config_loader


@pytest.fixture(autouse=True)
def _reset_cache():
    config_loader.reset_cache()
    yield
    config_loader.reset_cache()


def test_get_config_returns_dict():
    cfg = config_loader.get_config(reload=True)
    assert isinstance(cfg, dict)
    for key in ("llm", "review_ui", "backup", "logging"):
        assert key in cfg, f"missing section: {key}"


def test_default_config_used_when_no_file(monkeypatch):
    """无 config.yaml 时, 用 example + defaults 兜底"""
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH",
                        Path("D:/nope/does/not/exist.yaml"))
    cfg = config_loader.get_config(reload=True)
    assert cfg["llm"]["api_base"] == "http://127.0.0.1:60443/v1"


def test_env_var_replacement(monkeypatch, tmp_path):
    """${VAR} 替换为环境变量"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "llm:\n  api_base: '${TEST_BASE_URL}'\n  api_key: '${TEST_KEY:-fallback}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_BASE_URL", "http://my-llm:9999/v1")
    monkeypatch.setenv("TEST_KEY", "secret-xyz")
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)
    cfg = config_loader.get_config(reload=True)
    assert cfg["llm"]["api_base"] == "http://my-llm:9999/v1"
    assert cfg["llm"]["api_key"] == "secret-xyz"


def test_env_var_default_value(monkeypatch, tmp_path):
    """${VAR:-default} 当 VAR 未设时用 default"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "review_ui:\n  password: '${REVIEW_PW:-changeme}'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REVIEW_PW", raising=False)
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)
    cfg = config_loader.get_config(reload=True)
    assert cfg["review_ui"]["password"] == "changeme"


def test_config_caching(monkeypatch, tmp_path):
    """缓存命中: 第二次调用不重读磁盘,内容一致。

    注意这里断言的是「缓存真的生效」,不是「拿到同一个对象」。
    Phase 4 起 get_config() 返回深拷贝 —— 调用方拿到的是自己的快照,
    改不坏全局配置。身份相同是旧的实现细节,不是对外契约。
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("backup:\n  retention_days: 7\n", encoding="utf-8")
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)
    cfg1 = config_loader.get_config(reload=True)
    cfg2 = config_loader.get_config()  # cache hit

    # 内容一致
    assert cfg1 == cfg2
    assert cfg1["backup"]["retention_days"] == 7

    # 确实没重读: 把磁盘内容改掉,缓存期内应无感知
    cfg_path.write_text("backup:\n  retention_days: 999\n", encoding="utf-8")
    assert config_loader.get_config()["backup"]["retention_days"] == 7

    # 显式失效后才看到新内容
    assert config_loader.get_config(reload=True)["backup"]["retention_days"] == 999


def test_config_returns_isolated_copy(monkeypatch, tmp_path):
    """返回的是快照: 调用方就地修改不会污染全局配置。

    这条保证是 Phase 4 引入深拷贝的理由。没有它,任何一处
    `cfg = get_config(); cfg[...] = ...` 都会静默改掉全局。
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("backup:\n  retention_days: 7\n", encoding="utf-8")
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)

    cfg = config_loader.get_config(reload=True)
    cfg["backup"]["retention_days"] = -1
    cfg["llm"]["api_base"] = "http://evil.invalid"

    fresh = config_loader.get_config()
    assert fresh["backup"]["retention_days"] == 7
    assert fresh["llm"]["api_base"] != "http://evil.invalid"


def test_reload_config_is_explicit_invalidation(monkeypatch, tmp_path):
    """reload_config() 是公开的失效入口,与 get_config(reload=True) 等价。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("logging:\n  level: INFO\n", encoding="utf-8")
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)

    assert config_loader.get_config(reload=True)["logging"]["level"] == "INFO"

    cfg_path.write_text("logging:\n  level: DEBUG\n", encoding="utf-8")
    assert config_loader.get_config()["logging"]["level"] == "INFO"   # 仍是缓存

    config_loader.reload_config()
    assert config_loader.get_config()["logging"]["level"] == "DEBUG"   # 已失效