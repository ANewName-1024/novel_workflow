"""test_template_literal_books.py - Regression: AI 助手 modal 的 fetch() 必须用 template literal

Bug 来源 (2026-07-09): 用户点击 🤖 生成建议, 显示 "生成失败: not_found".
根因: outline.html L828/L895 写的是 fetch("/api/outline/${BOOK}/ai-suggest", ...)
用双引号包裹的字符串, JS 不做 ${BOOK} 插值, 字面发送过去 404.

修复: 改成 fetch(`/api/outline/${BOOK}/ai-suggest`, ...) — template literal.

检测方法: 先去除 backtick 包裹的 template literal (这些是合法的), 然后
在剩下的代码里找双引号字符串含 ${BOOK} — 这些就是 bug.
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "review_ui" / "templates"


def _strip_template_literals(script: str) -> str:
    """Replace backtick-quoted template literals with empty strings (preserve positions)."""
    # Non-greedy match: handle simple cases without nested ${...} containing backticks
    return re.sub(r'`[^`]*`', '``', script)


def _extract_script_blocks(html: str):
    blocks = []
    for m in re.finditer(r'<script>(.*?)</script>', html, re.DOTALL):
        blocks.append(m.group(1))
    return blocks


def _find_dq_strings_with_book(script: str):
    """Find double-quoted JS string literals containing ${BOOK}."""
    script = _strip_template_literals(script)
    bad = []
    for m in re.finditer(r'"([^"\\]*(?:\\.[^"\\]*)*)"', script):
        s = m.group(1)
        if '${BOOK}' in s:
            bad.append(s)
    return bad


class TestTemplateLiteralsWithBOOK:
    """JS 里含 ${BOOK} 的 URL 字符串必须用 backtick, 否则 404."""

    def _bad_in_file(self, html_file: Path):
        content = html_file.read_text(encoding='utf-8')
        bad = []
        for script in _extract_script_blocks(content):
            for s in _find_dq_strings_with_book(script):
                bad.append(s)
        return bad

    def test_outline_html_no_doublequoted_book(self):
        bad = self._bad_in_file(TEMPLATES_DIR / "outline.html")
        assert bad == [], (
            "outline.html 有双引号 JS 字符串含 ${BOOK}, 不会被插值, 浏览器 404.\n"
            "修复: 改成 template literal (backtick)。 错误字符串:\n"
            + "\n".join(f"  {s}" for s in bad)
        )

    def test_outline_html_book_link_uses_jinja(self):
        """href="/book/${BOOK}" 是 HTML, 必须用 Jinja {{ book }}."""
        content = (TEMPLATES_DIR / "outline.html").read_text(encoding='utf-8')
        assert 'href="/book/${BOOK}"' not in content, (
            'outline.html 用 href="/book/${BOOK}" — Jinja 不会插值 ${BOOK}, '
            '改成 href="/book/{{ book }}"'
        )

    def test_all_templates_no_doublequoted_book(self):
        for f in sorted(TEMPLATES_DIR.glob("*.html")):
            bad = self._bad_in_file(f)
            assert bad == [], (
                f"{f.name} 有 {len(bad)} 个双引号字符串含 ${{BOOK}}, JS 不会插值."
            )