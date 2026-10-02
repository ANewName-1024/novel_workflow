"""
exporter.py — 全书 Markdown 导出(纯读, 零 LLM 调用)

2026-10-02 从 novel.py 的 cmd_export 抽出来。原因: cmd_export 里
「拼 Markdown」和「写文件 + 打印」是缠在一起的, Web UI 想复用拼装逻辑
(把成稿带走) 就必须连带把写盘副作用一起继承过来。

抽出来之后两边各取所需:
    build_full_book_markdown(book)  纯读, 只读 config.json + chapters/*.md
    default_filename(book)          CLI 与 Web 共用的文件名约定

为什么不顺手把写盘也搬进来: CLI 的落盘路径 (storage.project_root 会 mkdir)
与「读出 Markdown」是两件事, 搬过来会让只想读一份文本的调用方多出写副作用。
所以写盘仍留在 novel.py, 调用方自己决定往哪写。

注意: 输出的 Markdown 文本与抽离前逐字节一致 —— 有用户的脚本按
「书名_全书.md」这个名字抓稿子, 改格式等于让他们静默拿到空文件。
"""
from __future__ import annotations

from lib import storage
from lib.errors import ErrorCode, NovelError

# CLI 落盘的文件名后缀。抽出来后这里成了唯一的定义处。
DEFAULT_SUFFIX = "_全书.md"


def default_filename(book: str) -> str:
    """导出文件的默认文件名: {book}_全书.md

    与 novel.py `export` 子命令写的文件名保持一致, 两者不可分叉。
    """
    return f"{book}{DEFAULT_SUFFIX}"


def build_full_book_markdown(book: str) -> tuple[str, int, int]:
    """拼出全书 Markdown, 返回 (markdown, 章节数, 总字数)。

    纯读: 只调 list_chapters / read_json / read_chapter, 不写任何文件。
    无章节可导出时抛 NovelError(NOT_FOUND) —— CLI 与 Web 各自把它翻译成
    各自的错误形态(CLI 退出码 3, Web 404), 库层不替调用方决定。
    """
    chapters = storage.list_chapters(book)
    if not chapters:
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 没有章节可导出")
    cfg = storage.read_json(book, "config.json") or {}
    total_wc = sum(c["word_count"] for c in chapters)

    lines = [f"# {cfg.get('book_name', book)}\n",
             f"\n## 基本信息\n",
             f"- 题材：{cfg.get('genre')}\n",
             f"- 基调：{cfg.get('tone')}\n",
             f"- 主角：{cfg.get('protagonist')}\n",
             f"- 字数：{total_wc} 字\n",
             f"\n---\n"]

    for ch in chapters:
        text = storage.read_chapter(book, ch["id"]) or ""
        lines.append(f"\n{text}\n\n---\n")

    return "".join(lines), len(chapters), total_wc
