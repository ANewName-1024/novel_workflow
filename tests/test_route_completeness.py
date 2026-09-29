"""tests/test_route_completeness.py — 蓝图拆分不得丢路由。

Phase 1 (cda4324) 把 review_ui/app.py 的路由搬进 9 个 Blueprint。单测只覆盖了
其中一部分, 所以「有没有 route 在搬家过程中掉了」一直没被验证。

本文件拿拆分前的源码当基线逐条比对。基线由 tools/gen_route_baseline.py 从
`git show cda4324^:review_ui/app.py` 机器生成 —— 手写基线会失真, 我第一版就
凭空捏造了 9 条不存在的路径(例如写成 /api/outline/<book>/ai/suggest, 实际是
/api/outline/<book>/ai-suggest)。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "cda4324^"
BASELINE_PATH = "review_ui/app.py"

# 57 条, 由 tools/gen_route_baseline.py 从 cda4324^ 实测生成
BASELINE_ROUTES = {
    "/",
    "/api/approve/<book>/<ch>",
    "/api/batch-approve/<book>",
    "/api/batch-reject/<book>",
    "/api/chapter/<book>/<ch>",
    "/api/chapter/<book>/<ch>/apply-feedback",
    "/api/chapter/<book>/<ch>/context",
    "/api/chapter/<book>/<ch>/diff-versions",
    "/api/chapter/<book>/<ch>/entity-diff",
    "/api/chapter/<book>/<ch>/revert/<vid>",
    "/api/chapter/<book>/<ch>/versions",
    "/api/chapter/<book>/<ch>/versions/<vid>",
    "/api/chapter/<book>/diff-chapters/<ch1>/<ch2>",
    "/api/comments/<book>",
    "/api/comments/<book>/<ch>",
    "/api/comments/<book>/<ch>/<cid>",
    "/api/config/<book>",
    "/api/diff/<book>/<ch>",
    "/api/edit/<book>/<ch>",
    "/api/entities/<book>",
    "/api/entities/<book>/<type>/<id>",
    "/api/entities/<book>/check-consistency",
    "/api/entities/<book>/counts",
    "/api/false-positive/<book>/<ch>",
    "/api/history/<book>",
    "/api/llm/health",
    "/api/llm/providers",
    "/api/notifications/<book>",
    "/api/notifications/<book>/<nid>/read",
    "/api/notifications/<book>/read-all",
    "/api/outline/<book>",
    "/api/outline/<book>/ai-expand",
    "/api/outline/<book>/ai-suggest",
    "/api/outline/<book>/diff",
    "/api/outline/<book>/node",
    "/api/outline/<book>/node/<ch_id>",
    "/api/outline/<book>/reorder",
    "/api/outline/<book>/versions",
    "/api/outline/<book>/volumes",
    "/api/outline/<book>/volumes/<vol_id>",
    "/api/pipeline/<book>/<ch>/resume",
    "/api/pipeline/<book>/interruptions",
    "/api/projects",
    "/api/projects/<book>",
    "/api/queue/<book>",
    "/api/queue/<book>/filtered",
    "/api/reject/<book>/<ch>",
    "/api/review/<book>/<ch>",
    "/api/stats/<book>",
    "/book/<book>",
    "/book/<book>/<ch>",
    "/entities/<book>",
    "/llm",
    "/login",
    "/logout",
    "/notifications/<book>",
    "/outline/<book>",
}

BUSINESS_BLOPS = {
    "outline", "chapter", "entities", "projects", "review",
    "comments", "notifications", "llm_config", "pipeline",
}


def _live_routes() -> set[str]:
    from review_ui import app as review_app
    return {str(r.rule) for r in review_app.app.url_map.iter_rules()}


class TestNoRouteLost:
    def test_every_baseline_route_registered(self):
        live = _live_routes()
        missing = sorted(r for r in BASELINE_ROUTES if r not in live)
        assert not missing, (
            f"以下 {len(missing)} 条路由在 Phase 1 蓝图拆分中丢失:\n"
            + "\n".join(f"  {m}" for m in missing)
            + "\n\n检查 review_ui/bp/ 下对应模块, 或 app.py 的蓝图注册块。"
        )


class TestBaselineIntegrity:
    """基线常量本身要可信, 否则上面的比对没有意义。"""

    def test_baseline_matches_pre_split_source(self):
        try:
            r = subprocess.run(
                ["git", "show", f"{BASELINE_COMMIT}:{BASELINE_PATH}"],
                cwd=REPO, capture_output=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:
            pytest.skip(f"拿不到基线源码 (非 git 环境?): {e}")
        if r.returncode != 0:
            pytest.skip(f"git show 失败: {r.stderr.strip()[:120]}")

        tree = ast.parse(r.stdout)
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route":
                    if d.args and isinstance(d.args[0], ast.Constant):
                        found.add(str(d.args[0].value))

        stale = sorted(r for r in BASELINE_ROUTES if r not in found)
        assert not stale, (
            f"BASELINE_ROUTES 中这些路径在拆分前源码里不存在 —— 基线失真:\n"
            + "\n".join(f"  {s}" for s in stale)
            + "\n\n用 tools/gen_route_baseline.py 重新生成。"
        )
        assert len(found) == len(BASELINE_ROUTES), (
            f"基线条数不符: 源码 {len(found)} 条, 常量 {len(BASELINE_ROUTES)} 条"
        )


class TestBlueprintRegistration:
    EXPECTED = BUSINESS_BLOPS | {"dashboard", "app_log"}

    def test_all_blueprints_registered(self):
        from review_ui import app as review_app
        names = {b.name for b in review_app.app.blueprints.values()}
        missing = sorted(self.EXPECTED - names)
        assert not missing, f"这些蓝图未注册: {missing}"

    def test_no_duplicate_rules(self):
        """同一 URL + method 注册多次 = 拆分时复制粘贴出错。

        这条真的抓到过东西: Phase 1 给每个蓝图都写了 static_folder +
        static_url_path, 导致 /static/<path:filename> 被注册 10 次。
        """
        from collections import Counter

        from review_ui import app as review_app

        c: Counter = Counter()
        for r in review_app.app.url_map.iter_rules():
            for m in r.methods - {"HEAD", "OPTIONS"}:
                c[(str(r.rule), m)] += 1
        dupes = {k: v for k, v in c.items() if v > 1}
        assert not dupes, (
            f"重复注册的规则 ({len(dupes)} 条):\n"
            + "\n".join(f"  {k} x{v}" for k, v in dupes.items())
        )

    def test_page_routes_stay_out_of_business_blueprints(self):
        """页面路由(渲染模板)应留在 app.py, 不该被搬进业务蓝图。"""
        from review_ui import app as review_app

        for r in review_app.app.url_map.iter_rules():
            rule = str(r.rule)
            if not rule.startswith("/api/"):
                bp = r.endpoint.split(".")[0] if "." in r.endpoint else "(app)"
                assert bp not in BUSINESS_BLOPS, (
                    f"页面路由 {rule} 被搬进了业务蓝图 {bp} "
                    f"(endpoint={r.endpoint})"
                )

    def test_static_served_by_app_not_blueprints(self):
        """静态资源属应用级, 由 app.py 提供, 蓝图不得重复声明。"""
        from review_ui import app as review_app

        owners = [
            r.endpoint for r in review_app.app.url_map.iter_rules()
            if str(r.rule) == "/static/<path:filename>"
        ]
        assert owners == ["static"], (
            f"/static 应只有 app 自己提供, 实得 {owners}。"
            f"蓝图上的 static_folder 会注册重复路由并可能劫持静态资源。"
        )