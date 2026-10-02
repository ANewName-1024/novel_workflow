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
  --provider NAME     LLM provider（默认：读 config.yaml 的 llm.provider）
  --api-base URL      覆盖 provider 的端点
  --llm-model NAME    覆盖 provider 的默认模型
"""
from __future__ import annotations
import sys, os, json, argparse, datetime, logging, re
from pathlib import Path

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

from lib import storage, memory, outline as outmod, chapter as chapmod
from lib import review as revmod, extract as extmod
from lib import context as ctxmod, summary as summod, state as statemod
from lib import style as stylemod, self_check as scmod
from lib.llm import LLM, get_llm
from lib.errors import ErrorCode, NovelError
from lib.logging_setup import setup_logging

# 模块级 logger: novel.py 里不少辅助函数在 main() 的局部作用域之外,
# 拿不到 main() 里那个 log。setup_logging() 由 main() 调用统一配置。
log = logging.getLogger("novel.cli")

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
    init.add_argument("--provider", default=None,
                      help="LLM provider (留空=读 config.yaml 的 llm.provider)")

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

    # ── v1.3 M3: llm provider 管理 ──
    llm_p = sub.add_parser("llm", help="LLM provider 管理 (列表/切换/测试)")
    llm_sub = llm_p.add_subparsers(dest="llm_cmd", required=True)

    # llm list
    ll_list = llm_sub.add_parser("list", help="列出所有可用 provider 及配置")
    ll_list.add_argument("-v", "--verbose", action="store_true", help="显示完整配置 (含 api_key 前 8 位)")

    # llm switch <book> <provider>
    ll_sw = llm_sub.add_parser("switch", help="切换 book 的 LLM provider")
    add_project(ll_sw)
    ll_sw.add_argument("provider", help="provider 名称 (如 local, deepseek, minimax)")
    ll_sw.add_argument("--model", default="", help="模型名 (留空=provider 默认)")

    # llm test <provider>
    ll_test = llm_sub.add_parser("test", help="测试某个 provider 连通性")
    ll_test.add_argument("provider", nargs="?", default="", help="provider 名称 (留空=测试当前 book 的)")
    add_project(ll_test)
    ll_test.add_argument("--book", default="", help="可选: 测试 book 当前配置的 provider")
    ll_test.add_argument("--model", default="", help="指定模型名 (留空=provider 默认)")
    ll_test.add_argument("--timeout", type=int, default=15, help="超时秒数 (默认 15)")

    # ----- NEW (v1.0 管理化增强) -----
    # doctor 子命令: 环境诊断
    doc = sub.add_parser("doctor", help="环境诊断 (Python/依赖/LLM/端口/磁盘/Git)")
    doc.add_argument("--json", action="store_true", help="输出 JSON 给脚本调用")
    # 2026-10-02: 配置漂移检测默认只做本地静态检查(零网络零费用)。
    # --deep 才会对每本书的端点真发一次 ping —— 这是唯一能测出
    # 「配的模型名那个 API 根本不接受」的办法(实测踩过: 400 而 doctor 全绿)。
    doc.add_argument("--deep", action="store_true",
                     help="额外对每本书的 LLM 端点发真实探测请求(会联网)")

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

    # ── v1.3 M4: pipeline resume ──
    pl = sub.add_parser("pipeline", help="Pipeine 状态/恢复")
    pl_sub = pl.add_subparsers(dest="pipeline_cmd", required=True)
    pr = pl_sub.add_parser("status", help="查看 pipeline 快照")
    add_project(pr)
    pv = pl_sub.add_parser("resume", help="恢复中断的 pipeline")
    add_project(pv)
    pv.add_argument("--chapter", type=str, default="", help="章节号 (如 6 或 ch_006)")
    pv.add_argument("--from-stage", type=str, default=None, help="从指定 stage 开始 (留空则自动检测)")

    return p.parse_args()

# ── commands ────────────────────────────────────────────────────────────────

# init 预检的请求预算: 提示词只有一个词, 要问的是「通不通」不是「写得好」。
# max_tokens 给小值, 但不能是 0 —— 0 在 OpenAI 兼容端点上是硬错, 会把"预检失败"
# 误报成"端点坏了"。
_PREFLIGHT_PROMPT = "ping"
_PREFLIGHT_MAX_TOKENS = 8
# 正式写作的预算是 3 次重试 + 10s 退避 + 600s 客户端超时, 整套套在 init 上最坏
# 30 分钟起步, init 就成了个假死命令。预检只要一个「是/否」, 预算砍到一次。
_PREFLIGHT_TIMEOUT_SEC = 15


def _preflight_llm(book: str, provider: str) -> str | None:
    """连通性预检。通返回 None, 不通返回一行给用户看的错误描述。

    走 get_llm(book=book) 而不是就地拼一个 LLM(...): 后面 outline/write 用的
    就是这条解析路径(resolve_for_book → provider registry), 预检通过 ≈ 后面
    真能调通, 而不是「另配的一套参数碰巧通了」。
    """
    from lib import llm_providers as lp
    try:
        llm = get_llm(book=book)
    except Exception as e:
        # 最常见的一种: provider 配了但 key 没配, OpenAI 客户端在构造时就抛。
        # 这种情况补一句「去哪儿配 key」, 比只丢异常类型有用。
        hint = ""
        try:
            pcfg = lp.get_provider_config(provider)
            if pcfg.get("needs_key") and not pcfg.get("api_key"):
                env_key = pcfg.get("env_key") or f"{provider.upper()}_API_KEY"
                hint = f" — provider {provider} 需要 key, 请在 .env 里设 {env_key}"
        except Exception:
            pass
        return f"构造 LLM 客户端失败: {e}{hint}"

    # 只动这个刚构造出来的实例(下一条命令会重新构造), 不碰全局 singleton。
    # 三个属性都包起来: 测试替身可能没有 client, 预检调不通不该变成 init 崩栈。
    try:
        llm.max_retries = 0
        llm.retry_delay = 0
        llm.client.timeout = _PREFLIGHT_TIMEOUT_SEC   # openai 客户端每次请求现读它
    except Exception:
        log.debug("预检: 无法收紧 LLM 预算, 按默认值发请求", exc_info=True)

    # 预检走 LLM.ping() 而不是 complete(): complete() 会施加 provider 的
    # min_max_tokens 下限(minimax=16384), 并在空响应时 4 倍升级到 65536 ——
    # 一次只想确认"通不通"的探测会变成又慢又贵的重请求, init 直接假死。
    # ping() 直接打底层客户端, 且刻意不检查正文(推理模型在 max_tokens=1
    # 下正文必然为空, 那是正常的)。
    if not hasattr(llm, "ping"):
        # 测试替身可能没实现 ping(), 退回 complete 但把下限关掉。
        try:
            llm.min_max_tokens = 0
        except Exception:
            pass
    try:
        if hasattr(llm, "ping"):
            llm.ping(_PREFLIGHT_PROMPT, timeout_sec=_PREFLIGHT_TIMEOUT_SEC)
        else:
            reply = llm.complete(_PREFLIGHT_PROMPT, max_tokens=_PREFLIGHT_MAX_TOKENS,
                                 temperature=0)
            if not (reply or "").strip():
                return "LLM 返回空响应"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None


def cmd_init(args: argparse.Namespace) -> None:
    book = args.book
    if not args.main_plot:
        raise NovelError(ErrorCode.INVALID_ARGS, "--main-plot 是必填参数")
    if storage.project_exists(book):
        print(f"项目 [{book}] 已存在，覆盖中…")
    if not args.main_plot.strip():
        raise NovelError(ErrorCode.INVALID_ARGS, "--main-plot 不能为空")

    # ── 端点/模型走 provider 注册表, 不再写死 local ──────────────────
    # 之前这里是 args.api_base or "http://127.0.0.1:60443/v1" 外加一个写死的
    # Qwen3.6 模型名。生产服务器实跑: 60443 没有任何进程在监听, init 照样 0.8 秒
    # 返回「✓ 项目已初始化」, 炸点被推迟到第一次 outline/write 的 LLM 调用, 那时
    # 用户已经以为书建好了; 日志里只留一行 "has llm_model but no llm_provider;
    # assuming 'local'"。config.yaml 的 llm.provider 才是这台机器真该用的 provider,
    # ${DEFAULT_PROVIDER:-deepseek} 这类占位符 config_loader 已展开过。
    from lib import config_loader
    from lib import llm_providers as lp
    provider = args.provider or (config_loader.get_config().get("llm", {}).get("provider") or "local")
    try:
        resolved = lp.resolve_model(provider)
    except KeyError as e:
        raise NovelError(ErrorCode.CONFIG_ERROR, f"未知 provider: {provider}", detail=str(e))
    # 显式参数优先(向后兼容), 缺的从 provider 补齐
    api_base = args.api_base or resolved["api_base"]
    llm_model = args.llm_model or resolved["model"]
    # 显式 --api-base 会照落盘, 但运行期 resolve_for_book() 只认 provider 配置里的
    # 地址(book cfg 的 api_base 字段不参与解析)。两者不一致时必须说一声, 否则
    # 用户会以为自己的自定义端点生效了。
    if args.api_base and args.api_base != resolved["api_base"]:
        print(f"⚠  --api-base ({args.api_base}) 与 provider [{provider}] 的默认地址 "
              f"({resolved['api_base']}) 不一致: 已按你给的值落盘, 但 outline/write "
              f"运行时仍以 provider 配置的地址为准")

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
        # provider 一起落盘: 只有 llm_model 的话 resolve_for_book 会退回
        # "assuming 'local'", 于是模型名配的是云端的、地址却指向本机 llama-server。
        "llm_provider": provider,
        "llm_model": llm_model,
        "api_base": api_base,
    }
    storage.init_project(book, cfg)

    # init 写完 config.json 不等于 LLM 真的通。预检放在 init_project 之后,
    # 读的就是刚落盘的那份配置 —— 测的是真实运行路径。
    preflight_err = _preflight_llm(book, provider)
    if preflight_err:
        # 不删也不回滚项目: 用户很可能就是想先把骨架建好, 回头配好 key 再 outline。
        # 但退出码必须非 0 —— 脚本/CI 把 exit 0 当成「可以往下走了」, 正是这个洞
        # 让整条流水线在前两步全绿、第三步才炸。
        print(f"\n✗ 项目 [{book}] 的配置已写入, 但 LLM 预检失败 (项目保留, 未回滚)")
        print(f"  provider: [{provider}] {llm_model} @ {api_base}")
        print(f"  原因: {preflight_err}")
        print(f"  处理: 配好 key/端点后重跑 outline, 或换 provider: "
              f"python novel.py llm switch {book} <provider>")
        raise NovelError(ErrorCode.LLM_FAILURE, f"LLM 预检失败: {preflight_err}")

    print(f"✓ 项目 [{book}] 已初始化")
    print(f"  题材: {args.genre}  |  基调: {args.tone}")
    print(f"  章节数: {args.chapters} × {args.words_per_chapter} 字")
    print(f"  LLM: [{provider}] {llm_model} @ {api_base}")
    print(f"\n下一步: python novel.py outline {book}")

def cmd_outline(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    if not cfg:
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在", detail="请先运行 init")
    ol = storage.read_json(book, "outline.json")
    # init_project 会先落一个 {"meta":{},"volumes":[],"chapters":[]} 的空壳。
    # 之前这里只判「文件存在」, 于是新书的标准流程 init → outline 直接跳过并
    # exit 0: 两步都报成功, 但 chapters/ 一个文件都不会有, 坑在第三步 write 才炸。
    # 空壳 = 没生成过, 继续生成; 只有真的有章节才提示「已存在」。
    has_chapters = bool(ol and ol.get("chapters"))
    if ol and not has_chapters and not args.regenerate:
        print("检测到 0 章的空大纲 (init 留下的占位文件), 继续生成…")
    elif has_chapters and not args.regenerate:
        print(f"大纲已存在（{len(ol.get('chapters',[]))} 章），跳过。\n"
              f"如需重新生成加 --regenerate")
        return  # 正常跳过, 不算错误

    llm = get_llm(book=book)
    print(f"LLM: {llm.model} | {llm.api_base}")
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
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 未初始化大纲", detail="请先运行 outline")

    llm = get_llm(book=book)
    print(f"LLM: {llm.model} | {llm.api_base}")
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
                raise NovelError(ErrorCode.LLM_FAILURE, f"第 {i} 章撰写失败", detail=str(e))

    # ── 收尾: 必须**重读** progress.json, 不能写回循环开始前那份 ──
    # 2026-10-02 实跑抓到: 这里的 prog 是函数开头读的旧对象, 整个循环期间
    # write_chapter → run_post_write_pipeline → mark_chapter_completed 一直
    # 在往 progress.json 里追加 chapters_completed / current_chapter, 而循环
    # 结束后这一行把**旧对象**整个盖了回去。
    # 结果: 章节全部写完、所有产物齐全, progress.json 却是
    #   {"phase": "writing", "current_chapter": 0, "chapters_completed": []}
    # status 命令里章节列表打勾、进度却显示 0/N, 自相矛盾。
    # 实测数据: ch_001 写完 1544 字、extract 抽到 5 事件/5 伏笔/3 角色,
    # progress.json 的 chapters_completed 仍然是空数组。
    fresh = storage.read_json(book, "progress.json") or {}
    done_n = len(fresh.get("chapters_completed") or [])
    total_n = fresh.get("total_chapters") or prog.get("total_chapters", 0) or 0
    fresh["phase"] = "done" if (total_n and done_n >= total_n) else "writing"
    storage.write_json(book, "progress.json", fresh)
    print(f"\n{'='*50}")
    print(f"✓ 章节撰写完成！已完成 {done_n}/{total_n} 章")
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
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")
    chapter_id = ""
    if args.chapter:
        # 章节号是手输的, 拼错是常态: "1" 直通到 review_chapter 会变成
        # FileNotFoundError: Chapter 1 not found + 一整段 novel.cli 未处理异常
        # traceback。先规整成 ch_XXX, 再自己查一次文件。
        chapter_id = normalize_chapter_id(args.chapter)
        if not storage.read_chapter(book, chapter_id):
            raise NovelError(
                ErrorCode.NOT_FOUND, f"章节 {chapter_id} 不存在",
                detail=f"用 python novel.py status {book} 看已写出的章节")
    # 章节查得到再构造 LLM: 反过来的话, 拼错章节号会先撞上 LLM 构造失败
    # (比如 key 没配时 OpenAI 客户端直接抛), 用户看到的报错会指向错误的方向。
    llm = get_llm(book=book)

    if chapter_id:
        rev = revmod.review_chapter(book, chapter_id, llm)
        print(f"\n=== [{chapter_id}] 审查结果 ===\n{rev}")
        # 2026-10-02: 把结果接进评审队列。
        # 之前 CLI 的 review 走 lib/review.py(自由文本), 而 review-queue 与
        # Web UI 走 review_service —— 两套互不相通, 于是审校跑完, 队列里
        # 依然显示「✓ 评审队列为空」, 人工看不到任何待处理的东西。
        _record_cli_review(book, chapter_id, rev)
    else:
        print("正在进行全书审查…\n")
        rev = revmod.full_book_review(book, llm)
        print(f"\n=== 全书审查结果 ===\n{rev}")
        for cid in [c["id"] for c in storage.list_chapters(book)]:
            _record_cli_review(book, cid, f"(来自全书审查)\n\n{rev}")


def _record_cli_review(book: str, chapter_id: str, review_text: str) -> None:
    """把自由文本审校落到 review_service 记录里; 失败只警告, 不打断审校输出。"""
    try:
        from lib import review_service as revserv
        rec = revserv.record_cli_review(book, chapter_id, review_text, by="CLI")
        print(f"  → 已进评审队列 (status={rec.get('status')}): "
              f"python novel.py review-queue {book}")
    except Exception as e:
        # 记录失败不该让整次审校白跑 —— 正文已经拿到并打印了。
        log.warning("审校结果入库失败 (book=%s ch=%s): %s: %s",
                    book, chapter_id, type(e).__name__, e, exc_info=True)
        print(f"  ⚠ 审校结果未能入队: {e}")

def cmd_status(args: argparse.Namespace) -> None:
    book = args.book
    cfg  = storage.read_json(book, "config.json")
    prog = storage.read_json(book, "progress.json")
    ol   = storage.read_json(book, "outline.json")
    if not cfg:
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")

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
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")
    if args.kv:
        key, _, val = args.kv.partition("=")
        key = key.strip()
        if not key:
            raise NovelError(ErrorCode.INVALID_ARGS, "用法: novel.py config <书名> key=value")
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
    # 2026-10-02: 拼 Markdown 的逻辑抽到 lib/exporter.py(纯读), 供 Web UI
    # 复用; 落盘与两行控制台输出保持原样 —— 有脚本按文件名抓稿子。
    from lib import exporter

    book = args.book
    markdown, n_chapters, total_wc = exporter.build_full_book_markdown(book)
    out_path = storage.project_root(book) / exporter.default_filename(book)
    out_path.write_text(markdown, encoding="utf-8")
    print(f"✓ 已导出: {out_path}")
    print(f"  {n_chapters} 章 | {total_wc} 字")

# ── review service commands ───────────────────────────────────────────────────

def _ensure_review_for_existing(book: str) -> None:
    """Backfill review records for chapters that don't have one yet.

    实现已收进 review_service.backfill_missing_reviews()。此前本文件与
    review_ui/bp/review.py 的 _ensure_review_backfill() 各有一份拷贝,
    6dd453b 修了这里之后 Web UI 那份仍带着同一个 bug(损坏的 self_check
    被当成「没有自检」→ 写成 AUTO_PASSED)。两份实现正是 bug 能只被修一半的
    根本原因, 现在只保留一份。
    """
    from lib import review_service as revserv
    # force=True: CLI 是用户显式触发的修复动作, 不该被 Web 端的指纹短路挡掉。
    revserv.backfill_missing_reviews(book, force=True)

def cmd_review_queue(args: argparse.Namespace) -> None:
    from lib import review_service as revserv
    book = args.book
    if not storage.project_exists(book):
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")
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
        raise NovelError(ErrorCode.NOT_FOUND, f"未找到 {chapter_id} 的评审记录")
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
        raise NovelError(ErrorCode.NOT_FOUND, f"文件不存在: {src}")
    new_text = src.read_text(encoding="utf-8").strip()
    if not new_text:
        raise NovelError(ErrorCode.INVALID_ARGS, f"文件为空: {src}")
    record = revserv.edit(book, chapter_id, args.reviewer, new_text, args.notes)
    print(f"✓ {chapter_id} 已保存人工版本 ({len(new_text)} 字)")
    print(f"  位置: reviews/{chapter_id}.v2.md")
    if args.apply:
        if revserv.apply_edit_to_chapter(book, chapter_id):
            print(f"  ⤷ 已同步覆盖到 chapters/{chapter_id}.md")
            # Re-run summary & state for new content
            try:
                llm = get_llm(book=book)
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
            raise NovelError(ErrorCode.NOT_FOUND, f"未找到 {args.chapter} 的评审记录")
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


def normalize_chapter_id(spec: str) -> str:
    """把单章参数规整成 'ch_XXX': '1' → 'ch_001', 'ch_001' → 'ch_001'。

    review 这类「一次只处理一章」的命令, 历史上只能收 ch_001 这种从别处
    复制来的 id, 而用户更习惯敲 1。范围参数(1-3 / 1,3)在这里明确不支持,
    给 INVALID_ARGS 而不是让下游拿 'ch_1-3' 去拼路径 —— 那个错会一路走到
    FileNotFoundError 才暴露。
    """
    s = str(spec or "").strip()
    if not s:
        raise NovelError(ErrorCode.INVALID_ARGS, "章节参数为空 (如 1 或 ch_001)")
    if "-" in s or "," in s:
        raise NovelError(
            ErrorCode.INVALID_ARGS, f"章节参数只接受单章, 收到 {s!r}",
            detail="范围请用: python novel.py write <书名> --chapters 1-3")
    m = re.fullmatch(r"(?i)ch[_-]?(\d+)", s)
    if not m:
        m = re.fullmatch(r"(\d+)", s)
    if not m:
        raise NovelError(
            ErrorCode.INVALID_ARGS, f"无法识别的章节参数: {s!r}",
            detail="只接受 1 或 ch_001 这样的单章")
    return f"ch_{int(m.group(1)):03d}"

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

    failed = any(r.status == "fail" for r in results)

    # ── 配置漂移检测 (2026-10-02 新增) ─────────────────────────────────
    # 为什么需要: 原来的 doctor 全绿, 但线上有一本书配的模型名那个 API
    # 根本不接受 —— 传过去直接 400, 而书看起来配得好好的。这类问题只有
    # 「实际问一次端点」才测得出来, 纯本地检查只能比对静态的已知模型表。
    #
    # --deep 才会真的发请求。默认只做本地静态检查(模型名/ token 预算/ 端点覆盖),
    # 零网络零费用 —— doctor 是最常被随手敲的命令, 不该每次都去打 API。
    try:
        from lib.doctor import check_llm_config_drift, format_drift_report
        drift = check_llm_config_drift(probe=bool(getattr(args, "deep", False)))
        if args.json:
            print(_json.dumps(drift.as_dict(), ensure_ascii=False, indent=2))
        else:
            print()
            print(format_drift_report(drift))
        if not drift.ok:
            failed = True
    except Exception as e:
        # doctor 自身的检查项出问题不该让整条命令崩掉; 但必须让人看见。
        log.warning("配置漂移检测失败 (非致命): %s: %s", type(e).__name__, e,
                    exc_info=True)
        print(f"⚠ 配置漂移检测未能运行: {e}")

    if failed:
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
        raise NovelError(ErrorCode.NOT_FOUND, f"找不到 {app_path}")
    sys.argv = ["review_ui.app", "--host", host, "--port", str(port)]
    if args.debug:
        sys.argv.append("--debug")
    # 把 novel_workflow/ ROOT 加 sys.path, 让 review_ui 当真 package 加载
    # (review_ui/ 有 __init__.py, 不是 namespace package)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import importlib
    if "review_ui.app" in sys.modules:
        del sys.modules["review_ui.app"]
    app_mod = importlib.import_module("review_ui.app")
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
        raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")
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


# ── v1.3 M4: pipeline resume / status ─────────────────────────────────────

def cmd_pipeline_resume(args: argparse.Namespace) -> None:
    """Recover and resume interrupted pipeline."""
    from lib.pipeline import state as pv
    if args.chapter:
        m = re.match(r"ch_?(\d+)", str(args.chapter))
        ch = int(m.group(1)) if m else int(args.chapter)
        result = pv.recover_stage(args.book, ch, args.from_stage)
        if result["ok"]:
            print(f"✅ {result['message']}")
            print(f"  运行: python novel.py write {args.book} --chapters {ch}")
        else:
            print(f"❌ {result['message']}")
    else:
        interrupted = pv.get_interrupted_chapters(args.book)
        if not interrupted:
            print("✅ 所有章节管道状态正常, 无中断。")
            return
        print(f"⚠️  发现 {len(interrupted)} 个中断章节:")
        for ic in interrupted:
            ch = ic["ch"]
            cur = ic.get("current_stage") or ic.get("failed_stage") or "?"
            print(f"  ch_{ch:03d}: 中断于 [{cur}]")
            print(f"    恢复: python novel.py pipeline resume {args.book} --chapter {ch}")
        print("\n提示: 也可在 WebUI 章节页面点击 [▶ 恢复管道] 按钮。")


def cmd_pipeline_status(args: argparse.Namespace) -> None:
    """Show pipeline checkpoint snapshot."""
    from lib.pipeline import state as pv
    snapshot = pv.get_last_snapshot(args.book)
    if snapshot is None or not snapshot.get("available"):
        print("📭 无可用的 pipeline snapshot")
        return
    print(f"📋 Pipeline Snapshot ({snapshot['timestamp']}):")
    print(f"  章节: ch_{snapshot['ch']:03d}")
    print(f"  完成: {'✅' if snapshot['is_complete'] else '⏳'}")
    print(f"  当前阶段: {snapshot.get('current_stage', '-')}")
    print(f"  失败阶段: {snapshot.get('failed_stage', '-')}")
    print("  各阶段状态:")
    stages = snapshot.get("stages", {})
    for s, status in stages.items():
        icon = {"DONE": "✅", "FAILED": "❌", "RUNNING": "⏳", "PENDING": "⬜", "SKIPPED": "⏭️"}.get(status, "❓")
        print(f"    {icon} {s}: {status}")


# ── v1.3 M3: llm provider 管理 ──────────────────────────────────────────

def cmd_llm_list(args: argparse.Namespace) -> None:
    """List all available LLM providers."""
    from lib import llm_providers as lp
    providers = lp.BUILTIN_PROVIDERS
    if not providers:
        print("❌ 没有可用的 provider (可能 llm_providers 未加载)")
        return
    print(f"📋 已注册 provider ({len(providers)} 个):")
    for name, cfg in sorted(providers.items()):
        model = cfg.get("model", "?")
        api_base = cfg.get("api_base", "?")
        key = cfg.get("api_key", "")
        key_preview = (key[:8] + "..." + key[-4:]) if key and args.verbose else "••••••••" if key else "(无 key)"
        print(f"\n  [{name}]")
        print(f"    模型:    {model}")
        print(f"    地址:    {api_base}")
        print(f"    密钥:    {key_preview}")
    # Also show book-level overrides
    print("\n💡 提示: 切换到某个 book: python novel.py llm switch <book> <provider>")
    print("  测试联通: python novel.py llm test <provider>")


def cmd_llm_switch(args: argparse.Namespace) -> None:
    """Switch LLM provider for a book."""
    from lib import storage as _st
    from lib import llm_providers as lp
    cfg = _st.read_json(args.book, "config.json") or {}
    old_provider = cfg.get("llm_provider", "(未设置)")
    old_model = cfg.get("llm_model", cfg.get("model", "(未设置)"))
    cfg["llm_provider"] = args.provider
    if args.model:
        cfg["llm_model"] = args.model
    else:
        # provider 换的是端点+模型名这一整套, 只改 provider 的话 llm_model 还留着
        # 上一个 provider 的名字。实跑: 切到 deepseek 后 config.json 里还是
        # Qwen3.6-35B-A3B-UD-Q4_K_M.gguf, 发给 DeepSeek 直接 400。
        try:
            cfg["llm_model"] = lp.resolve_model(args.provider)["model"]
        except KeyError as e:
            raise NovelError(ErrorCode.CONFIG_ERROR, f"未知 provider: {args.provider}",
                             detail=str(e))
    _st.write_json(args.book, "config.json", cfg)
    print(f"✅ [{args.book}] 切换成功:")
    print(f"   {old_provider}:{old_model} → {args.provider}:{cfg['llm_model']}")
    print(f"  可用: python novel.py llm test {args.provider}  --book {args.book}")


def cmd_llm_test(args: argparse.Namespace) -> None:
    """Test provider connectivity."""
    from lib import llm_providers as lp
    import requests, json, sys

    # Resolve provider
    provider = args.provider or (args.book and "")
    if not provider and args.book:
        from . import storage as _st
        cfg = _st.read_json(args.book, "config.json") or {}
        provider = cfg.get("llm_provider", "")
    if not provider:
        provider = "local"  # fallback

    # Get provider config
    pcfg = lp.get_provider_config(provider)
    if not pcfg:
        print(f"❌ 未知 provider: {provider}")
        sys.exit(1)

    model = args.model or pcfg.get("model", "")
    api_base = pcfg.get("api_base", "")
    api_key = pcfg.get("api_key", "")

    print(f"🔌 测试 provider [{provider}]...")
    print(f"   模型: {model}")
    print(f"   地址: {api_base}")
    print(f"   密钥: {'已配置 (前8位: ' + api_key[:8] + '...)' if api_key else '未配置 ❌'}")

    if not api_key and provider not in ("local",):
        print(f"❌ {provider} 未配置 API key")
        print(f"  在 .env 中设置 {provider.upper()}_API_KEY")
        sys.exit(1)
    if not api_base:
        print(f"❌ {provider} 未配置 api_base")
        sys.exit(1)

    # Send a simple chat completion to test connectivity
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "回答一个字: 1+1=?"}],
        "max_tokens": 10,
        "temperature": 0,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=args.timeout)
        r.raise_for_status()
        data = r.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        print(f"✅ 连通成功! HTTP {r.status_code}")
        print(f"   回复: {reply[:60]}")
        tok_in = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        tok_out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        print(f"   用量: {tok_in} in / {tok_out} out")
    except requests.exceptions.Timeout:
        print(f"❌ 超时 (>{args.timeout}s)")
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 连接失败: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP {e.response.status_code}: {e.response.text[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        sys.exit(1)


def main() -> None:
    args = parse_args()
    if args.version:
        print(f"novel.py v{VERSION}")
        return

    # Windows 默认 stdout 是 GBK, 中文字符 / emoji 会炸.
    # chcp 65001 不影响已启动进程, 只能这里 reconfigure.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 初始化日志 (单例, 配置来自 config.yaml)
    setup_logging()
    log = logging.getLogger("novel.cli")

    try:
        _dispatch(args, log)
    except NovelError as e:
        log.error(str(e))
        if e.detail:
            log.debug("detail: %s", e.detail)
        sys.exit(e.code)
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        log.exception("未处理异常: %s", e)
        sys.exit(ErrorCode.GENERIC)


def _dispatch(args: argparse.Namespace, log: logging.Logger) -> None:
    """分发到子命令. raise NovelError → main() 顶层捕获."""
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
            raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 无章节, 无可提取")
        last = chapters[-1]["id"]
        text = storage.read_chapter(book, last) or ""
        cfg  = storage.read_json(book, "config.json") or {}
        llm  = get_llm(book=book)
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
    elif cmd == "pipeline":
        pc = args.pipeline_cmd
        if pc == "status":
            cmd_pipeline_status(args)
        elif pc == "resume":
            cmd_pipeline_resume(args)
        else:
            raise NovelError(ErrorCode.INVALID_ARGS, f"未知 pipeline 子命令: {pc}")
    elif cmd == "llm":
        lc = args.llm_cmd
        if lc == "list":
            cmd_llm_list(args)
        elif lc == "switch":
            cmd_llm_switch(args)
        elif lc == "test":
            cmd_llm_test(args)
        else:
            raise NovelError(ErrorCode.INVALID_ARGS, f"未知 llm 子命令: {lc}")
    else:
        raise NovelError(ErrorCode.INVALID_ARGS, f"未知命令: {cmd}")

if __name__ == "__main__":
    main()
