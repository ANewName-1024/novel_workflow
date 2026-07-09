"""test_novel_py_get_llm_routing.py - novel.py 必须用 get_llm(book=) 而不是 LLM(model=, api_base=)

Bug 来源 (2026-07-09): VPS 上 test_book config 是 {llm_model: deepseek-chat, llm_provider: deepseek},
但 novel.py cmd_write 用 LLM(model="deepseek-chat", api_base=""). api_base 为空字符串, 走到
LLM 构造的 else 分支: api_base = api_base or DEFAULT_API_BASE = 'http://127.0.0.1:60443/v1'.
结果: 用 deepseek 的模型名 + 本地 llama-server 的 base url → LLM API Connection error.

修复: novel.py 所有 LLM 构造点改用 get_llm(book=book), 让 resolve_for_book 用 book config
正确解析 provider+model+api_base+api_key.
"""
import re
from pathlib import Path

import pytest

NOVEL_PY = Path(__file__).resolve().parent.parent / "novel.py"


class TestNovelPyUsesGetLlmWithBook:
    """禁止直接 LLM(model=, api_base=) 调用, 必须用 get_llm(book=)."""

    def test_no_direct_llm_construction_with_empty_api_base(self):
        """明确禁止 LLM(model=cfg.get('llm_model',''), api_base=cfg.get('api_base','')) 模式."""
        content = NOVEL_PY.read_text(encoding="utf-8")
        # 找所有 LLM( 出现
        matches = re.findall(r"LLM\([^)]*api_base[^)]*\)", content)
        assert not matches, (
            "novel.py 仍有 LLM(model=cfg.get('llm_model',''), api_base=cfg.get('api_base','')) 模式. "
            f"应改用 get_llm(book=book). 找到: {matches}"
        )

    def test_write_command_uses_get_llm_with_book(self):
        """cmd_write 必须调 get_llm(book=book)."""
        content = NOVEL_PY.read_text(encoding="utf-8")
        # 找 cmd_write 函数体
        m = re.search(r"def cmd_write\(args:.*?\n(?=\ndef |\nif __name__)", content, re.DOTALL)
        assert m, "cmd_write 函数没找到"
        body = m.group(0)
        assert "get_llm(book=book)" in body, "cmd_write 没用 get_llm(book=book)"
        # 不应直接 LLM( 调用
        assert not re.search(r"^\s*llm = LLM\(", body, re.MULTILINE), (
            "cmd_write 内还有直接 LLM(...) 调用"
        )

    def test_outline_command_uses_get_llm_with_book(self):
        content = NOVEL_PY.read_text(encoding="utf-8")
        m = re.search(r"def cmd_outline\(args:.*?\n(?=\ndef |\nif __name__)", content, re.DOTALL)
        assert m, "cmd_outline 函数没找到"
        body = m.group(0)
        assert "get_llm(book=book)" in body, "cmd_outline 没用 get_llm(book=book)"

    def test_review_command_uses_get_llm_with_book(self):
        content = NOVEL_PY.read_text(encoding="utf-8")
        m = re.search(r"def cmd_review\(args:.*?\n(?=\ndef |\nif __name__)", content, re.DOTALL)
        assert m, "cmd_review 函数没找到"
        body = m.group(0)
        assert "get_llm(book=book)" in body, "cmd_review 没用 get_llm(book=book)"

    def test_review_show_or_approve_doesnt_use_broken_pattern(self):
        """Other cmd functions should also avoid the broken LLM(model=, api_base=) pattern."""
        content = NOVEL_PY.read_text(encoding="utf-8")
        # 全文搜
        bad = re.findall(r"LLM\(model=.*?api_base=.*?\)", content)
        assert not bad, f"novel.py 仍有裸 LLM(model=..., api_base=...) 构造. 找到: {bad}"