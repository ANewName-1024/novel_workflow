"""
test_doctor.py — 9 项检查 + format_report (names are Chinese in this module)
"""
from lib import doctor


def test_check_python():
    r = doctor.check_python()
    assert r.name == "Python 版本"
    assert r.status in ("ok", "pass", "warn", "fail")
    assert "3." in r.detail


def test_check_deps():
    r = doctor.check_deps()
    assert r.name == "依赖"
    assert r.status in ("ok", "pass", "warn", "fail")


def test_check_git():
    r = doctor.check_git()
    assert r.name == "Git"
    assert r.status in ("ok", "pass", "warn", "fail")


def test_check_disk():
    r = doctor.check_disk()
    assert r.name == "磁盘空间"
    assert r.status in ("ok", "pass", "warn", "fail")
    assert any(unit in r.detail for unit in ("GB", "MB"))


def test_check_llm():
    r = doctor.check_llm({})
    assert r.name == "LLM (llama-server)"
    assert r.status in ("ok", "pass", "warn", "fail")


def test_check_port_free_random_high_port():
    """随机大端口应该 free (从 50000-60000 区间扫一个真的空闲的)."""
    import random
    import socket
    # 在 50000-60000 区间扫一个肯定空闲的端口 (避免随机到被占用的)
    port = None
    for _ in range(20):
        candidate = random.randint(50000, 60000)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if s.connect_ex(("127.0.0.1", candidate)) != 0:
                port = candidate
                break
        finally:
            s.close()
    assert port is not None, "找不到空闲端口, 测试环境异常"
    r = doctor.check_port_free({"review_ui": {"port": port}})
    assert r.name.startswith("端口")
    assert r.status == "ok"


def test_check_paths():
    r = doctor.check_paths()
    assert r.name == "关键文件"
    assert r.status in ("ok", "pass", "warn", "fail")


def test_check_projects_dir():
    r = doctor.check_projects_dir({})
    assert r.name == "项目目录"
    assert r.status in ("ok", "pass", "warn", "fail")


def test_run_all_returns_at_least_7_checks():
    results = doctor.run_all()
    assert isinstance(results, list)
    assert len(results) >= 7
    for r in results:
        assert r.status in ("ok", "pass", "warn", "fail")


def test_format_report_includes_summary():
    results = doctor.run_all()
    output = doctor.format_report(results)
    assert "汇总" in output
    assert any(s in output for s in ("novel doctor", "✅", "⚠", "❌"))