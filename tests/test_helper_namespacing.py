"""test_helper_namespacing.py - Regression: 模板 JS 调用 NW helper 必须带 NW. 前缀

Bug 来源: 多个模板里有裸 escapeHtml/toast/confirm/api 调用, 在运行时
"ReferenceError: escapeHtml is not defined" → try/catch 抛出后用户看到
"生成失败" / "加载失败" 等不友好的错误.

修复: outline.html / chapter.html / dashboard.html / entities.html /
overview.html 全部用 NW.escapeHtml / NW.toast 等. 这个测试确保未来
不要再有裸调用.

注意: 函数定义 `function toast(...)` 不算裸调用 — 只有调用站点 `toast(...)`.
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "review_ui" / "templates"
HELPERS = ['escapeHtml', 'toast', 'confirm', 'api', 'fmtTime', 'debounce', 'spinner']


def _count_bare_calls(content: str) -> int:
    """Count occurrences of helper( without NW. prefix.

    Excludes function declarations: `function toast(` is fine.
    """
    total = 0
    for h in HELPERS:
        # (?<!NW\.) 负向后行断言 — 排除 NW.escapeHtml 这种合法用法
        # (?<!function ) 排除 function toast( 这种函数定义
        matches = re.findall(rf'(?<!NW\.)(?<!function )(?<!\basync function )\b{h}\(', content)
        total += len(matches)
    return total


class TestNoBareHelperCalls:
    """所有模板必须用 NW. 前缀调用 NW namespace 下的 helper (调用站点)."""

    def _scan(self, html_file: Path):
        return _count_bare_calls(html_file.read_text(encoding='utf-8'))

    def test_outline_html(self):
        # 这是用户实际碰到的 bug 源文件 (AI 助手生成失败)
        assert self._scan(TEMPLATES_DIR / "outline.html") == 0, \
            "outline.html 仍有裸 helper 调用"

    def test_chapter_html(self):
        assert self._scan(TEMPLATES_DIR / "chapter.html") == 0, \
            "chapter.html 有裸 helper 调用"

    def test_dashboard_html(self):
        assert self._scan(TEMPLATES_DIR / "dashboard.html") == 0, \
            "dashboard.html 有裸 helper 调用"

    def test_entities_html(self):
        assert self._scan(TEMPLATES_DIR / "entities.html") == 0, \
            "entities.html 有裸 helper 调用"

    def test_overview_html(self):
        assert self._scan(TEMPLATES_DIR / "overview.html") == 0, \
            "overview.html 有裸 helper 调用"

    def test_book_html(self):
        assert self._scan(TEMPLATES_DIR / "book.html") == 0, \
            "book.html 有裸 helper 调用"

    def test_llm_config_html(self):
        assert self._scan(TEMPLATES_DIR / "llm_config.html") == 0, \
            "llm_config.html 有裸 helper 调用"

    def test_all_templates(self):
        """完整扫描所有 .html 模板, 没有遗漏."""
        for html_file in sorted(TEMPLATES_DIR.glob("*.html")):
            bare = self._scan(html_file)
            assert bare == 0, f"{html_file.name} 有 {bare} 个裸 helper 调用 (调用站点)"