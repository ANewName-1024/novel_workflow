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
    """第二次调用拿同一对象 (cache hit)"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("backup:\n  retention_days: 7\n", encoding="utf-8")
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", cfg_path)
    cfg1 = config_loader.get_config(reload=True)
    cfg2 = config_loader.get_config()  # cache hit
    assert cfg1 is cfg2