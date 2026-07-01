#!/usr/bin/env python3
"""
novel.py — 长篇小说编写工作流 CLI

Usage:
  python novel.py init <书名> [options]     初始化新项目
  python novel.py outline <书名>             生成三阶大纲
  python novel.py write <书名> [chapters]    写章节（默认全部）
  python novel.py continue <书名>            从断点继续写
  python novel.py review <书名> [chapter]    审阅单章或全书
  python novel.py status <书名>             查看进度
  python novel.py config <书名> <key=value> 查看/修改配置
  python novel.py export <书名>             导出全书为单个 Markdown

Options for init:
  --genre TEXT        题材（默认：都市）
  --tone TEXT         基调（默认：现实主义）
  --protagonist TEXT  主角（默认：待设定）
  --antagonist TEXT   对手/反派（默认：待设定）
  --main-plot TEXT    主线剧情概述（必填）
  --style TEXT        写作风格（默认：简洁有力）
  --chapters N        目标章节数（默认：20）
  --words-per-chapter N  每章字数（默认：2500）
  --language TEXT     语言（默认：zh）
"""
from __future__ import annotations
import sys, os, json, argparse, datetime
from pathlib import Path

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from lib import storage, memory, outline as outmod, chapter as chapmod
from lib import review as revmod, extract as extmod
from lib import context as ctxmod, summary as summod, state as statemod
from lib import style as stylemod, self_check as scmod
from lib.llm import LLM, get_llm

VERSION = "0.1.0"

# ── arg parser ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="novel", description="长篇小说编写工作流")
    p.add_argument("--version", action="store_true", help="显示版本")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_project(parser):
        parser.add_argument("book", help="书名（项目目录名）")

    # init
    init = sub.add_parser("init", help="初始化新项目")
    add_project(init)
    init.add_argument("--genre", default="都市")
    init.add_argument("--tone", default="现实主义")
    init.add_argument("--protagonist", default="待设定")
    init.add_argument("--antagonist", default="待设定")
    init.add_argument("--main-plot", default="", help="主线剧情概述（必填）")
    init.add_argument("--style", default="简洁有力")
    init.add_argument("--chapters", type=int, default=20)
    init.add_argument("--words-per-chapter", type=int, default=2500)
    init.add_argument("--language", default="zh")
    init.add_argument("--llm-model", default="")
    init.add_argument("--api-base", default="")

    # outline
    ol = sub.add_parser("outline", help="生成三阶大纲")
    add_project(ol)
    ol.add_argument("--regenerate", action="store_true", help="重新生成（覆盖）")

    # write
    wr = sub.add_parser("write", help="写章节")
    add_project(wr)
    wr.add_argument("--chapters", type=str, default="", help="逗号分隔或范围，如 3,5,7-10")
    wr.add_argument("--auto-continue", action="store_true", help="某章失败时自动继续下一章")
    wr.add_argument("--auto-rewrite-on-critical", action="store_true",
                    help="自检为 critical 时自动重写本章 (一次性覆盖 config 设置)")
    wr.add_argument("--self-check-strict", action="store_true",
                    help="自检 moderate 也触发自动重写 (一次性覆盖 config 设置)")

    # continue
    cont = sub.add_parser("continue", help="从断点继续写")
    add_project(cont)
    cont.add_argument("--auto-continue", action="store_true")

    # review
    rv = sub.add_parser("review", help="审阅章节")
    add_project(rv)
    rv.add_argument("chapter", nargs="?", default="", help="章节ID（空则审全书）")

    # status
    sub.add_parser("status", help="查看项目状态").add_argument("book")

    # config
    cfg = sub.add_parser("config", help="查看/修改配置")
    add_project(cfg)
    cfg.add_argument("kv", nargs="?", default="", help="key=value 或空查看")

    # export
    sub.add_parser("export", help="导出全书").add_argument("book")

    # ── manual review service (人工审核) ──
    rq = sub.add_parser("review-queue", help="查看待人工评审的章节")
    add_project(rq)

    rs = sub.add_parser("review-show", help="查看某章评审详情（含正文）")
    add_project(rs)
    rs.add_argument("chapter", help="章节ID (如 ch_006)")
    rs.add_argument("--full", action="store_true", help="显示完整正文（默认 800 字）")

    rap = sub.add_parser("review-approve", help="人工通过某章")
    add_project(rap)
    rap.add_argument("chapter", help="章节ID")
    rap.add_argument("--reviewer", default="wei_chao", help="审核人")
    rap.add_argument("--notes", default="", help="备注")

    rrj = sub.add_parser("review-reject", help="人工拒退某章（标记需重写）")
    add_project(rrj)
    rrj.add_argument("chapter", help="章节ID")
    rrj.add_argument("--reviewer", default="wei_chao", help="审核人")
    rrj.add_argument("--reason", required=True, help="拒退原因")

    red = sub.add_parser("review-edit", help="人工编辑某章（提供新文本）")
    add_project(red)
    red.add_argument("chapter", help="章节ID")
    red.add_argument("--file", required=True, help="新文本文件路径")
    red.add_argument("--reviewer", default="wei_chao", help="审核人")
    red.add_argument("--notes", default="", help="备注")
    red.add_argument("--apply", action="store_true", help="同时将人工版本覆盖到 chapters/")

    rfp = sub.add_parser("review-false-positive", help="标记自检为误报")
    add_project(rfp)
    rfp.add_argument("chapter", help="章节ID")
    rfp.add_argument("--reviewer", default="wei_chao", help="审核人")
    rfp.add_argument("--notes", required=True, help="误报说明")

    rh = sub.add_parser("review-history", help="查看项目评审历史（所有章节汇总）")
    add_project(rh)
    rh.add_argument("--chapter", default="", help="只看某章（可选）")

    # run-extract
    ext = sub.add_parser("extract", help="从最新章节提取记忆")
    add_project(ext)

    # ----- NEW (v1.0 管理化增强) -----
    # doctor 子命令: 环境诊断
    doc = sub.add_parser("doctor", help="环境诊断 (Python/依赖/LLM/端口/磁盘/Git)")
    doc.add_argument("--json", action="store_true", help="输出 JSON 给脚本调用")

    # serve 子命令: 启动 review_ui Flask
    srv = sub.add_parser("serve", help="启动 review_ui Web 界面")
    srv.add_argument("--host", default=None, help="覆盖 config.yaml 的 host")
    srv.add_argument("--port", type=int, default=None, help="覆盖 config.yaml 的 port")
    srv.add_argument("--debug", action="store_true", help="Flask debug 模式")

    # backup 子命令: 立即备份某个书 (也可交给 Task Scheduler 自动跑)
    bk = sub.add_parser("backup", help="立即备份某书 (生成 projects/<书>/backups/yyyymmdd.tar.gz)")
    add_project(bk)
    bk.add_argument("--no-compress", action="store_true", help="不压 tar.gz")
    bk.add_argument("--clean", action="store_true", help="顺手清理超过 retention_days 的旧快照")

    return p.parse_args()

# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> None:
    book = args.book
    if not args.main_plot:
        print("错误：--main-plot 是必填参数")
        sys.exit(1)
    if storage.project_exists(book):
        print(f"项目 [{book}] 已存在，覆盖中…")
    if not args.main_plot.strip():
        print("错误：--main-plot 不能为空")
        sys.exit(1)

    api_base = args.api_base or "http://127.0.0.1:60443/v1"
    llm_model = args.llm_model or "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"

    cfg = {
        "book_name": book,
        "genre": args.genre,
        "tone": args.tone,
        "protagonist": args.protagonist,
        "antagonist": args.antagonist,
        "main_plot": args.main_plot,
        "style": args.style,
        "target_chapters": args.chapters,
        "words_per_chapter": args.words_per_chapter,
        "language": args.language,
        "llm_model": llm_model,
        "api_base": api_base,
    }
    storage.init_project(book, cfg)
    print(f"✓ 项目 [{book}] 已初始化")
    print(f"  题材: {args.genre}  |  基调: {args.tone}")
    print(f"  章节数: {args.chapters} × {args.words_per_chapter} 字")
    print(f"  LLM: {llm_model} @ {api_base}")
    print(f"\n下一步: python novel.py outline {book}")

def cmd_outline(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    if not cfg:
        print(f"项目 [{book}] 不存在，请先运行 init")
        sys.exit(1)
    ol = storage.read_json(book, "outline.json")
    if ol and not args.regenerate:
        print(f"大纲已存在（{len(ol.get('chapters',[]))} 章），跳过。\n"
              f"如需重新生成加 --regenerate")
        sys.exit(0)

    llm = LLM(model=cfg.get("llm_model",""), api_base=cfg.get("api_base",""))
    print(f"LLM: {cfg.get('llm_model')} | {cfg.get('api_base')}")
    print("正在生成三阶大纲…（预计 30-60 秒）\n")

    ol = outmod.generate_outline(
        book=book,
        llm=llm,
        genre=cfg.get("genre",""),
        tone=cfg.get("tone",""),
        main_plot=cfg.get("main_plot",""),
        style=cfg.get("style",""),
        protagonist=cfg.get("protagonist",""),
        antagonist=cfg.get("antagonist",""),
        target_chapters=cfg.get("target_chapters", 20),
        words_per_chapter=cfg.get("words_per_chapter", 2500),
        language=cfg.get("language","zh"),
    )

    total = ol.get("meta",{}).get("target_chapters", len(ol.get("chapters",[])))
    print(f"\n✓ 大纲生成完成: {len(ol.get('volumes',[]))} 卷, {total} 章")
    for vol in ol.get("volumes", []):
        print(f"\n  【{vol.get('title','')}】{vol.get('summary','')[:40]}")
        for ch in vol.get("chapters", [])[:3]:
            print(f"    · {ch}")
    if total > 3:
        print(f"    … 共 {total} 章")

    # Update progress phase
    prog = storage.read_json(book, "progress.json") or {}
    prog["phase"] = "outline"
    prog["total_chapters"] = total
    storage.write_json(book, "progress.json", prog)
    print(f"\n下一步: python novel.py write {book}")

def cmd_write(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    ol   = storage.read_json(book, "outline.json")
    prog = storage.read_json(book, "progress.json") or {}
    if not cfg or not ol:
        print(f"项目 [{book}] 未初始化大纲，请先运行 outline")
        sys.exit(1)

    llm = LLM(model=cfg.get("llm_model",""), api_base=cfg.get("api_base",""))
    print(f"LLM: {cfg.get('llm_model')}")
    print(f"\n滑动窗口上下文预算估算（基于 build_writing_context）：")
    for i in [1, 5, 10, 15, 20]:
        if i > (prog.get("total_chapters") or cfg.get("target_chapters", 20)):
            continue
        try:
            ctx = ctxmod.build_writing_context(book, i)
            in_tok  = ctxmod.estimate_context_tokens(ctx, llm)
            out_tok = cfg.get("words_per_chapter", 2500) * 1.3
            margin  = 65536 - in_tok - int(out_tok)
            ok = "✓" if margin > 10000 else "⚠"
            win = ctx["context_window_strategy"]
            print(f"  第{i:02d}章: 输入~{in_tok:>5} tok + 输出~{int(out_tok):>4} tok "
                  f"| 余量 {margin:>6} {ok} | 窗口: {win}")
        except Exception as e:
            print(f"  第{i}章: 估算失败 ({e})")

    # Determine chapter range
    if args.chapters:
        nums = parse_chapter_range(args.chapters)
    else:
        start = (prog.get("current_chapter", 0) or 0) + 1
        nums  = list(range(start, prog.get("total_chapters", cfg.get("target_chapters",20))+1))

    print(f"\n将撰写章节: {nums}")
    print(f"自动继续（失败时）: {'是' if args.auto_continue else '否'}")

    prog["phase"] = "writing"
    storage.write_json(book, "progress.json", prog)

    for i in nums:
        print(f"\n{'═'*50}")
        print(f"  第 {i} / {prog.get('total_chapters')} 章")
        print(f"{'═'*50}")
        try:
            # Merge CLI flags into cfg for this run only (overrides config)
            run_cfg = dict(cfg)
            if getattr(args, 'auto_rewrite_on_critical', False):
                run_cfg["auto_rewrite_on_critical"] = True
            if getattr(args, 'self_check_strict', False):
                run_cfg["self_check_strict"] = True

            # write_chapter now runs the full post-write pipeline internally
            # (extract → memory → summary → state → [self-check → maybe rewrite])
            text = chapmod.write_chapter(book, i, llm, ol, cfg_override=run_cfg)
            print(f"  ✓ 完成 ({len(text)} 字)")

            # Context budget report
            ctx = ctxmod.build_writing_context(book, i)
            in_tok = ctxmod.estimate_context_tokens(ctx, llm)
            out_tok = cfg.get("words_per_chapter", 2500) * 1.3
            margin  = 65536 - in_tok - int(out_tok)
            print(f"  上下文: {in_tok}tok 输入 | {int(out_tok)}tok 输出 | 余量 {margin}")

        except Exception as e:
            print(f"  ✗ 失败: {e}")
            if not args.auto_continue:
                print("中断。修复后可运行 continue 从断点继续。")
                sys.exit(1)

    # Final progress
    prog["phase"] = "done" if len(prog.get("chapters_completed",[])) >= prog.get("total_chapters",0) else "writing"
    storage.write_json(book, "progress.json", prog)
    print(f"\n{'='*50}")
    print(f"✓ 章节撰写完成！")
    print(f"  下一步: python novel.py review {book}")
    print(f"  或直接: python novel.py export {book}")

def cmd_continue(args: argparse.Namespace) -> None:
    """Resume from last checkpoint."""
    args.chapters = ""
    args.auto_continue = args.auto_continue or True
    cmd_write(args)

def cmd_review(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    if not cfg:
        print(f"项目 [{book}] 不存在")
        sys.exit(1)
    llm = LLM(model=cfg.get("llm_model",""), api_base=cfg.get("api_base",""))

    if args.chapter:
        rev = revmod.review_chapter(book, args.chapter, llm)
        print(f"\n=== [{args.chapter}] 审查结果 ===\n{rev}")
    else:
        print("正在进行全书审查…\n")
        rev = revmod.full_book_review(book, llm)
        print(f"\n=== 全书审查结果 ===\n{rev}")

def cmd_status(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    prog = storage.read_json(book, "progress.json")
    ol   = storage.read_json(book, "outline.json")
    if not cfg:
        print(f"项目 [{book}] 不存在")
        sys.exit(1)

    print(f"\n{'═'*50}")
    print(f"  《{cfg.get('book_name', book)}》")
    print(f"{'═'*50}")
    print(f"  题材: {cfg.get('genre')}  |  基调: {cfg.get('tone')}")
    print(f"  主角: {cfg.get('protagonist')}  |  配角: {cfg.get('antagonist')}")
    print(f"  目标: {cfg.get('target_chapters')} 章 × {cfg.get('words_per_chapter')} 字")
    print(f"  LLM:  {cfg.get('llm_model')} @ {cfg.get('api_base')}")
    print(f"  阶段: {prog.get('phase','init') if prog else 'init'}")
    print(f"  进度: {len(prog.get('chapters_completed',[]) if prog else [])} / {prog.get('total_chapters','?') if prog else '?'} 章")

    # Memory stats
    import lib.memory as mem
    chars = mem.get_characters(book)
    fs    = mem.get_foreshadowing(book)
    evts  = mem.get_events(book)
    print(f"\n  记忆库:")
    print(f"    角色: {len(chars)} 人")
    print(f"    事件: {len(evts)} 条")
    print(f"    伏笔: {len(fs)} 条（{sum(1 for f in fs if f.get('status')=='已回收')} 已回收）")

    # Continuity features status
    style_anchor = stylemod.get_style_anchor(book)
    state_snap   = statemod.get_state(book)
    sums         = summod.get_all_chapter_summaries(book)
    print(f"\n  连贯性保障:")
    print(f"    风格锚点: {'✓ 已锁定' if style_anchor else '✗ 未生成（需写完第1章后自动建立）'}")
    print(f"    状态快照: 第 {state_snap.get('current_chapter',0)} 章 / 位于: {state_snap.get('current_location','?')}")
    print(f"    滚动摘要: {len(sums)} 章")
    self_check_on = cfg.get('self_check', False)
    strict = cfg.get('self_check_strict', False)
    auto_rw = cfg.get('auto_rewrite_on_critical', False)
    print(f"    自检二遍: {'✓ 启用' if self_check_on else '✗ 未启用'} (strict={strict}, auto_rewrite={auto_rw})")

    # Review queue stats
    from lib import review_service as revserv
    try:
        rev_stats = revserv.get_review_stats(book)
        queue     = revserv.get_review_queue(book)
        print(f"\n  评审队列:")
        print(f"    待人工审: {rev_stats.get('pending_review', 0)}")
        print(f"    需重写:   {rev_stats.get('needs_rewrite', 0)}")
        print(f"    已批准:   {rev_stats.get('approved', 0)}")
        print(f"    人工编辑: {rev_stats.get('human_edited', 0)}")
        print(f"    自动通过: {rev_stats.get('auto_passed', 0)}")
        print(f"    误报:     {rev_stats.get('false_positive', 0)}")
        if queue:
            print(f"    命令: novel.py review-queue {book}")
    except Exception as e:
        print(f"    评审服务: ⚠ {e}")

    # Chapters
    chapters = storage.list_chapters(book)
    if chapters:
        print(f"\n  章节列表:")
        for ch in chapters:
            print(f"    ✓ {ch['id']} {ch['title']} (~{ch['word_count']}字)")
    else:
        print(f"\n  章节:（暂无）")

def cmd_config(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    if not cfg:
        print(f"项目 [{book}] 不存在")
        sys.exit(1)
    if args.kv:
        key, _, val = args.kv.partition("=")
        key = key.strip()
        if not key:
            print("用法: novel.py config <书名> key=value")
            sys.exit(1)
        # Type inference
        if val.isdigit():
            val = int(val)
        elif val.lower() in ("true","false"):
            val = val.lower() == "true"
        cfg[key] = val
        storage.write_json(book, "config.json", cfg)
        print(f"✓ {key} = {val}")
    else:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))

def cmd_export(args: argparse.Namespace) -> None:
    book = args.book
    chapters = storage.list_chapters(book)
    if not chapters:
        print("没有章节可导出")
        sys.exit(1)
    cfg = storage.read_json(book, "config.json") or {}

    out_path = storage.project_root(book) / f"{book}_全书.md"
    lines = [f"# {cfg.get('book_name', book)}\n",
             f"\n## 基本信息\n",
             f"- 题材：{cfg.get('genre')}\n",
             f"- 基调：{cfg.get('tone')}\n",
             f"- 主角：{cfg.get('protagonist')}\n",
             f"- 字数：{sum(c['word_count'] for c in chapters)} 字\n",
             f"\n---\n"]

    for ch in chapters:
        text = storage.read_chapter(book, ch["id"]) or ""
        lines.append(f"\n{text}\n\n---\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    total_wc = sum(c["word_count"] for c in chapters)
    print(f"✓ 已导出: {out_path}")
    print(f"  {len(chapters)} 章 | {total_wc} 字")

# ── review service commands ───────────────────────────────────────────────────

def _ensure_review_for_existing(book: str) -> None:
    """Backfill review records for chapters that don't have one yet
    (e.g. chapters written before review service was added, or written
    with self_check disabled). Only initializes AUTO_PASSED if no record."""
    from lib import review_service as revserv
    chapters = storage.list_chapters(book)
    for ch in chapters:
        if not revserv.get_review(book, ch["id"]):
            # Try to read existing self-check if present
            sc_path = storage.project_root(book) / "self_checks" / f"{ch['id']}.json"
            sc_result = None
            if sc_path.exists():
                try:
                    import json as _json
                    sc_result = _json.loads(sc_path.read_text(encoding="utf-8"))
                except Exception:
                    sc_result = None
            if sc_result:
                revserv.auto_flag(book, ch["id"], sc_result, by="AI-backfill")
            else:
                # No self-check data → mark as auto_passed (no flag)
                empty = revserv._empty_record(ch["id"])
                empty["status"] = revserv.REVIEW_STATUS["AUTO_PASSED"]
                revserv.save_review(book, empty)
                revserv.append_audit(book, ch["id"], "backfilled_no_selfcheck", "system",
                                     notes="章节无自检数据，默认通过")

def cmd_review_queue(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    if not storage.project_exists(book):
        print(f"项目 [{book}] 不存在")
        sys.exit(1)
    _ensure_review_for_existing(book)
    queue = revserv.get_review_queue(book)
    print(revserv.render_queue(book, queue))

def cmd_review_show(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    chapter_id = args.chapter
    _ensure_review_for_existing(book)
    record = revserv.get_review(book, chapter_id)
    if not record:
        print(f"未找到 {chapter_id} 的评审记录")
        sys.exit(1)
    print(revserv.format_review_record(
        book, record, include_chapter=True,
        chapter_text_chars=100000 if args.full else 800,
    ))

def cmd_review_approve(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    record = revserv.approve(book, args.chapter, args.reviewer, args.notes)
    print(f"✓ {args.chapter} 已批准 (reviewer={args.reviewer})")
    print(f"  备注: {args.notes or '(无)'}")

def cmd_review_reject(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    record = revserv.reject(book, args.chapter, args.reviewer, args.reason)
    print(f"✗ {args.chapter} 已拒退 (reviewer={args.reviewer})")
    print(f"  原因: {args.reason}")
    print(f"  下一步: python novel.py write {book} --chapters {args.chapter.split('_')[1]} --auto-rewrite-on-critical")

def cmd_review_edit(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    chapter_id = args.chapter
    src = Path(args.file)
    if not src.exists():
        print(f"文件不存在: {src}")
        sys.exit(1)
    new_text = src.read_text(encoding="utf-8").strip()
    if not new_text:
        print("文件为空")
        sys.exit(1)
    record = revserv.edit(book, chapter_id, args.reviewer, new_text, args.notes)
    print(f"✓ {chapter_id} 已保存人工版本 ({len(new_text)} 字)")
    print(f"  位置: reviews/{chapter_id}.v2.md")
    if args.apply:
        if revserv.apply_edit_to_chapter(book, chapter_id):
            print(f"  ⤷ 已同步覆盖到 chapters/{chapter_id}.md")
            # Re-run summary & state for new content
            try:
                llm = LLM(model=(storage.read_json(book,'config.json') or {}).get('llm_model',''),
                          api_base=(storage.read_json(book,'config.json') or {}).get('api_base',''))
                summod.generate_chapter_summary(book, chapter_id, llm)
                statemod.update_state_after_chapter(book, int(chapter_id.split('_')[1]), llm)
                print(f"  ⤷ 已重新生成摘要 + 更新状态")
            except Exception as e:
                print(f"  ⚠ 重新生成摘要/状态失败: {e}")
        else:
            print(f"  ✗ 应用失败 (源文件不存在)")

def cmd_review_false_positive(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    record = revserv.mark_false_positive(book, args.chapter, args.reviewer, args.notes)
    print(f"✓ {args.chapter} 标记为误报")
    print(f"  说明: {args.notes}")

def cmd_review_history(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    if args.chapter:
        record = revserv.get_review(book, args.chapter)
        if not record:
            print(f"未找到 {args.chapter} 的评审记录")
            sys.exit(1)
        print(revserv.format_review_record(book, record, include_chapter=False))
    else:
        stats = revserv.get_review_stats(book)
        queue = revserv.get_review_queue(book)
        print(f"\n{'═' * 60}")
        print(f"  《{book}》评审总览")
        print(f"{'═' * 60}")
        for k, v in stats.items():
            label = {
                "auto_passed":    "自动通过",
                "pending_review": "待人工审",
                "approved":       "已批准",
                "needs_rewrite":  "需重写",
                "human_edited":   "已人工编辑",
                "false_positive": "误报",
                "total":          "总计",
            }.get(k, k)
            print(f"  {label:>10} : {v}")
        if queue:
            print(revserv.render_queue(book, queue))
        # Show recent audit log entries
        log_path = revserv.audit_log_path(book)
        if log_path.exists():
            print(f"\n  最近审计 (audit.log):")
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            for ln in lines[-10:]:
                print(f"    {ln}")

# ── helpers ─────────────────────────────────────────────────────────────────

def parse_chapter_range(spec: str) -> list[int]:
    """Parse '1,3,5-10' → [1,3,5,6,7,8,9,10]."""
    import re
    spec = spec.strip()
    if not spec:
        return []
    nums: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            nums.update(range(int(start.strip()), int(end.strip())+1))
        elif part.isdigit():
            nums.add(int(part))
    return sorted(nums)

# ── main ────────────────────────────────────────────────────────────────────



# --- v1.0 NEW: doctor / serve / backup ---
def cmd_doctor(args: argparse.Namespace) -> None:
    """novel doctor — 环境诊断."""
    import json as _json
    from lib.doctor import run_all, format_report
    results = run_all()
    if args.json:
        print(_json.dumps([r._asdict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(format_report(results))
    if any(r.status == "fail" for r in results):
        sys.exit(1)


def cmd_serve(args: argparse.Namespace) -> None:
    """novel serve — 启动 review_ui Web 界面."""
    from lib.config_loader import get_config
    cfg = get_config()
    host = args.host or cfg["review_ui"]["host"]
    port = args.port or int(cfg["review_ui"]["port"])
    print(f"\U0001f680 启动 review_ui Flask (config: {host}:{port})")
    review_ui_dir = Path(__file__).resolve().parent / "review_ui"
    app_path = review_ui_dir / "app.py"
    if not app_path.exists():
        print(f"\u274c 找不到 {app_path}")
        sys.exit(1)
    sys.argv = ["app.py", "--host", host, "--port", str(port)]
    if args.debug:
        sys.argv.append("--debug")
    sys.path.insert(0, str(review_ui_dir))
    import importlib
    if "app" in sys.modules:
        del sys.modules["app"]
    app_mod = importlib.import_module("app")
    if hasattr(app_mod, "main"):
        app_mod.main()
    else:
        app_mod.app.run(host=host, port=port, debug=args.debug, use_reloader=False)


def cmd_backup(args: argparse.Namespace) -> None:
    """novel backup — 立即备份某书."""
    import shutil
    import tarfile
    from datetime import datetime
    from lib.config_loader import get_config
    from lib.backup import clean_old_backups
    book = args.book
    proj_root = storage.project_root(book)
    if not proj_root.exists():
        print(f"\u274c 项目 [{book}] 不存在")
        sys.exit(1)
    backup_dir = proj_root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    cfg = get_config()
    compress = (not args.no_compress) and cfg.get("backup", {}).get("compress_tar", True)
    suffix = ".tar.gz" if compress else ".zip"
    out = backup_dir / f"snapshot-{ts}{suffix}"
    print(f"\U0001f4e6 备份 [{book}] \u2192 {out.name}")
    if compress:
        with tarfile.open(out, "w:gz") as tf:
            for item in proj_root.iterdir():
                if item.name == "backups":
                    continue
                if item.is_dir():
                    tf.add(str(item), arcname=item.name)
                else:
                    tf.add(str(item), arcname=item.name)
    else:
        shutil.make_archive(str(out).rsplit(".", 1)[0], "zip", proj_root)
    out_size = out.stat().st_size
    print(f"\u2705 完成: {out.name} ({out_size/1024:.1f} KB)")
    if args.clean:
        retention = int(cfg.get("backup", {}).get("retention_days", 7))
        removed = clean_old_backups(backup_dir, retention)
        if removed:
            print(f"🧹 清理旧快照 {len(removed)} 个 (>{retention} 天): {', '.join(removed)}")


def main() -> None:
    args = parse_args()
    if args.version:
        print(f"novel.py v{VERSION}")
        return
    cmd = args.cmd
    if cmd == "init":
        cmd_init(args)
    elif cmd == "outline":
        cmd_outline(args)
    elif cmd == "write":
        cmd_write(args)
    elif cmd == "continue":
        cmd_continue(args)
    elif cmd == "review":
        cmd_review(args)
    elif cmd == "status":
        cmd_status(args)
    elif cmd == "config":
        cmd_config(args)
    elif cmd == "export":
        cmd_export(args)
    elif cmd == "extract":
        book = args.book
        chapters = storage.list_chapters(book)
        if not chapters:
            print("无章节")
            sys.exit(1)
        last = chapters[-1]["id"]
        text = storage.read_chapter(book, last) or ""
        cfg  = storage.read_json(book, "config.json") or {}
        llm  = LLM(model=cfg.get("llm_model",""), api_base=cfg.get("api_base",""))
        ex   = extmod.extract_from_chapter(text, llm)
        memory.merge_extraction(book, ex)
        print(f"✓ 从 [{last}] 提取并更新记忆库完成")
        print(f"  新事件: {len(ex.get('new_events',[]))}")
        print(f"  新角色: {len(ex.get('new_characters',[]))}")
        print(f"  新伏笔: {len(ex.get('new_foreshadowing',[]))}")
    elif cmd == "review-queue":
        cmd_review_queue(args)
    elif cmd == "review-show":
        cmd_review_show(args)
    elif cmd == "review-approve":
        cmd_review_approve(args)
    elif cmd == "review-reject":
        cmd_review_reject(args)
    elif cmd == "review-edit":
        cmd_review_edit(args)
    elif cmd == "review-false-positive":
        cmd_review_false_positive(args)
    elif cmd == "review-history":
        cmd_review_history(args)
    elif cmd == "doctor":
        cmd_doctor(args)
    elif cmd == "serve":
        cmd_serve(args)
    elif cmd == "backup":
        cmd_backup(args)
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
