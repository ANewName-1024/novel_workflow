"""test_common_js.py — 验证 static/js/common.js 可加载且暴露 NW API"""
from pathlib import Path

COMMON_JS = Path(__file__).parent.parent / "review_ui" / "static" / "js" / "common.js"
MAIN_CSS = Path(__file__).parent.parent / "review_ui" / "static" / "css" / "main.css"
BASE_HTML = Path(__file__).parent.parent / "review_ui" / "templates" / "_base.html"


def test_common_js_exists():
    assert COMMON_JS.is_file(), f"missing: {COMMON_JS}"


def test_common_js_has_nw_namespace():
    content = COMMON_JS.read_text(encoding="utf-8")
    assert "window.NW" in content, "NW namespace must be exposed"
    assert "NW.api" in content
    assert "NW.toast" in content
    assert "NW.confirm" in content
    assert "NW.escapeHtml" in content
    assert "NW.fmtTime" in content
    assert "NW.debounce" in content
    assert "NW.spinner" in content


def test_common_js_uses_iife():
    """必须 IIFE 包装, 避免全局污染"""
    content = COMMON_JS.read_text(encoding="utf-8")
    assert "(function()" in content
    assert '"use strict"' in content


def test_main_css_has_toast_styles():
    """main.css 必须配套 toast 样式"""
    content = MAIN_CSS.read_text(encoding="utf-8")
    assert ".toast-host" in content
    assert ".toast-" in content  # .toast-ok/.toast-err
    assert ".nw-spinner-bg" in content
    assert ".modal-bg" in content


def test_base_loads_common_js():
    """_base.html 必须加载 common.js"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "common.js" in content
    assert "scripts" in content


def test_common_js_size_reasonable():
    """common.js 不应该超过 10KB"""
    size = COMMON_JS.stat().st_size
    assert size < 10_000, f"common.js too big: {size} bytes"