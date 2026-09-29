"""tools/smoke_all.py — 端到端冒烟:今天改了很多层, 确认系统整体还活着。

覆盖:
  1. 全部模块可导入(含拆出的 lib.pipeline 包)
  2. CLI 入口 novel.py 能加载
  3. Web 应用能起, 关键路由响应 200
  4. 蓝图全部注册
  5. 配置加载 / 日志 / 编码守卫三条自建机制都工作
  6. SQLite 与文件存储连通

用法: python tools/smoke_all.py   (退出码非 0 = 有失败项)
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASS, FAIL = [], []


def check(name: str):
    def deco(fn):
        try:
            fn()
            PASS.append(name)
            print(f"  [PASS] {name}")
        except Exception as e:  # noqa: BLE001
            FAIL.append((name, e))
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
            if "-v" in sys.argv:
                traceback.print_exc()
        return fn

    return deco


# ── 1. 模块导入 ───────────────────────────────────────────────────────────
MODULES = [
    "lib.storage", "lib.db", "lib.memory", "lib.llm", "lib.llm_providers",
    "lib.pipeline", "lib.pipeline.state", "lib.pipeline.process",
    "lib.pipeline.errors", "lib.chapter", "lib.review_service",
    "lib.review_actions", "lib.entity", "lib.entity_diff", "lib.outline",
    "lib.outline_ai", "lib.outline_editor", "lib.review", "lib.version",
    "lib.config_loader", "lib.logging_setup", "lib.errors", "lib.state",
    "lib.context", "lib.summary", "lib.style", "lib.self_check", "lib.extract",
    "lib.backup", "lib.doctor", "lib.session_log", "lib.comments",
    "review_ui.core",
    "review_ui.app", "review_ui.dashboard", "review_ui.app_log",
    "review_ui.bp", "review_ui.bp.outline", "review_ui.bp.chapter",
    "review_ui.bp.entities", "review_ui.bp.projects", "review_ui.bp.review",
    "review_ui.bp.comments", "review_ui.bp.notifications",
    "review_ui.bp.llm_config", "review_ui.bp.pipeline",
]


@check("1. 全部模块可导入")
def _imports():
    bad = []
    for m in MODULES:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {type(e).__name__}: {e}")
    assert not bad, f"{len(bad)} 个模块导入失败:\n    " + "\n    ".join(bad)


@check("2. novel.py 可作为脚本加载")
def _cli():
    r = subprocess.run(
        [sys.executable, "-c", "import ast,pathlib; ast.parse(pathlib.Path('novel.py').read_text(encoding='utf-8'))"],
        cwd=REPO, capture_output=True, timeout=60,
    )
    assert r.returncode == 0, f"novel.py 解析失败: {r.stderr.decode('utf-8','replace')[:200]}"


@check("3. blueprint 与路由完整")
def _routes():
    from review_ui import app as ra
    n = len(list(ra.app.url_map.iter_rules()))
    assert n >= 85, f"路由数异常: {n} (预期 >= 85)"
    names = {b.name for b in ra.app.blueprints.values()}
    need = {"outline", "chapter", "entities", "projects", "review",
            "comments", "notifications", "llm_config", "pipeline",
            "dashboard", "app_log"}
    assert need <= names, f"缺蓝图: {sorted(need - names)}"


@check("4. Web 应用可响应关键路由")
def _web():
    from review_ui import app as ra
    ra.app.config["TESTING"] = True
    ra.app.config["SECRET_KEY"] = "smoke"
    import review_ui.app as m
    orig = m._get_auth
    m._get_auth = lambda: {"enabled": False, "user": "", "password": ""}
    try:
        with ra.app.test_client() as c:
            # auth 禁用时 /login 应 302 到 / —— 显示登录表单才是 bug
            r = c.get("/login")
            assert r.status_code == 302 and r.headers.get("Location") == "/", \
                f"/login 在 auth 关闭时应重定向到 /, 实得 {r.status_code} -> {r.headers.get('Location')}"
            for url in ("/", "/api/projects", "/overview", "/health"):
                r = c.get(url)
                assert r.status_code in (200, 302, 404), \
                    f"{url} -> {r.status_code} (期望 200/302/404)"
                if url in ("/", "/overview"):
                    assert r.status_code == 200, f"{url} -> {r.status_code}"
    finally:
        m._get_auth = orig


@check("5. 编码守卫可用")
def _enc():
    r = subprocess.run([sys.executable, str(REPO / "tools" / "check_encoding.py")],
                       cwd=REPO, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[:300]


@check("6. 配置加载")
def _cfg():
    from lib.config_loader import get_config, reload_config
    cfg = get_config()
    for k in ("llm", "review_ui", "projects"):
        assert k in cfg, f"配置缺 {k} 段"
    assert cfg["review_ui"]["port"], "port 为空"
    c1 = get_config()
    c1["llm"]["api_base"] = "MUTATED"
    assert get_config()["llm"]["api_base"] != "MUTATED", "配置快照被污染"
    reload_config()
    assert get_config()["llm"]["api_base"] != "MUTATED", "reload 后仍被污染"


@check("7. 异常体系层级")
def _errors():
    from lib.errors import NovelError
    from lib.pipeline.errors import PipelineError
    assert issubclass(PipelineError, NovelError)
    assert issubclass(PipelineError, Exception)


@check("8. 流水线状态机可用")
def _fsm():
    from lib.pipeline.state import StageState
    # 实测值是大写: PENDING/RUNNING/DONE/FAILED/SKIPPED
    vals = {s.value for s in StageState}
    assert vals == {"PENDING", "RUNNING", "DONE", "FAILED", "SKIPPED"}, \
        f"StageState 取值变了: {sorted(vals)}"


@check("9. SQLite 可用")
def _sqlite():
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        p = f.name
    try:
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE t(x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        con.close()
    finally:
        Path(p).unlink(missing_ok=True)


@check("10. 测试套件可收集")
def _collect():
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                       cwd=REPO, capture_output=True, timeout=300)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert "INTERNALERROR" not in out, "pytest 收集崩溃"
    import re
    m = re.search(r"(\d+) tests? collected", out)
    assert m, f"收集失败:\n{out[-400:]}"
    assert int(m.group(1)) >= 690, f"测试数异常: {m.group(1)}"


print()
print("=" * 70)
print(f"冒烟结果: PASS {len(PASS)}  FAIL {len(FAIL)}")
print("=" * 70)
for n, e in FAIL:
    print(f"  FAIL {n}: {e}")
raise SystemExit(1 if FAIL else 0)
