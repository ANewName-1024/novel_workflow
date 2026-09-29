"""tools/check_nav_endpoints.py — 验证导航栏高亮的 endpoint 匹配是否仍有效。

风险溯源(Phase 1, cda4324): review_ui/app.py 的 _nav_context 用
    endpoint = request.endpoint
    if endpoint == ep: ...
精确匹配 endpoint 名来判断「当前激活哪个导航项」。

Blueprint 会给 endpoint 加蓝图名前缀: book_page -> 仍是 book_page(留在 app.py),
但 dashboard_page 若被搬进 dashboard 蓝图, endpoint 会变成 dashboard.dashboard_page,
精确匹配直接失效 —— 导航高亮静默消失, 不报错、不影响功能, 只是 UI 不对了。

这类问题单测基本测不到, 所以单独查一次。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from review_ui import app as ra  # noqa: E402

# app.py 里 _nav_context 实际使用的两张表
WANT_BOOK = {
    "book_page": "book",
    "outline_page": "outline",
    "dashboard_page": "dashboard",
    "entities_page": "entities",
    "chapter_page": "chapter",
    "notifications_page": "notifications",
}
WANT_GLOBAL = {
    "index": "home",
    "overview_page": "overview",
    "llm_config_page": "llm",
}


def main() -> int:
    actual = {r.endpoint: str(r.rule) for r in ra.app.url_map.iter_rules()}
    broken = []

    print("=" * 74)
    print("导航栏 endpoint 匹配检查")
    print("=" * 74)
    print(f"  {'期望 endpoint':<22}{'实际 endpoint':<34}状态")
    print("  " + "-" * 70)

    for ep in list(WANT_BOOK) + list(WANT_GLOBAL):
        if ep in actual:
            real, status = ep, "OK"
        else:
            cand = [e for e in actual if e.endswith("." + ep)]
            if cand:
                real = cand[0]
                status = "BROKEN (已加蓝图前缀)"
                broken.append((ep, real))
            else:
                real, status = "(未注册)", "MISSING"
                broken.append((ep, "未注册"))
        print(f"  {ep:<22}{real:<34}{status}")

    print()
    if broken:
        print(f"✗ {len(broken)} 处失效 —— 导航高亮在这些页面会消失:")
        for ep, real in broken:
            print(f"    {ep}  ->  {real}")
        print()
        print("修法: _nav_context 改为取 endpoint 末段比较, 例如")
        print("      endpoint = (request.endpoint or '').rsplit('.', 1)[-1]")
        return 1

    print("✓ 全部匹配正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())