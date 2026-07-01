"""
Chapter writer: generates one chapter with full sliding-window context.
Pipeline after write: extract → merge memory → generate summary → update state → [self-check].
"""
from __future__ import annotations
import re, datetime, sys
from .llm import LLM
from . import storage, memory, outline as outmod
from . import summary as summod
from . import state as statemod
from . import style as stylemod
from . import self_check as scmod
from . import review_service as revserv
from .prompts import CHAPTER_SYSTEM, CHAPTER_USER

def write_chapter(
    book: str,
    chapter_num: int,
    llm: LLM,
    outline: dict,
    cfg_override: dict | None = None,
) -> str:
    """
    Write chapter_num and return the raw text.
    Saves to storage automatically.

    cfg_override: optional dict merged into disk config (CLI flags take precedence)
    """
    cfg     = {**(storage.read_json(book, "config.json") or {}), **(cfg_override or {})}
    wpc     = cfg.get("words_per_chapter", 2500)
    ch_info = outmod.get_chapter_info(outline, chapter_num)
    if not ch_info:
        raise ValueError(f"Chapter {chapter_num} not found in outline")

    # v1.1: 注入 metrics callback (LLM 调用后写 metrics.jsonl)
    from .pipeline import get_runner
    _runner = get_runner()
    _book_for_cb = book
    llm.set_metrics_callback(
        lambda stage, ch, model, input_tokens, output_tokens, latency_ms:
            _runner.append_metric(_book_for_cb, stage=stage, ch=ch, model=model,
                                  input_tokens=input_tokens, output_tokens=output_tokens,
                                  latency_ms=latency_ms)
    )

    # ── Build sliding-window context ──
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=context status=start")
    from .context import build_writing_context, estimate_context_tokens
    ctx = build_writing_context(book, chapter_num)
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=context status=done")

    system_prompt = CHAPTER_SYSTEM.format(
        chapter_id        = ctx["chapter_id"],
        chapter_title     = ctx["chapter_title"],
        chapter_num       = chapter_num,
        target_words      = wpc,
        style_anchor      = ctx["style_anchor"],
        current_state     = ctx["current_state"],
        pov               = ctx["pov"],
        key_events        = ctx["key_events"],
        foreshadow        = ctx["foreshadow"],
        hard_constraints  = ctx["hard_constraints"],
        memory_characters       = ctx["memory_characters"],
        memory_world            = ctx["memory_world"],
        memory_events           = ctx["memory_events"],
        memory_foreshadowing    = ctx["memory_foreshadowing"],
        recent_full_chapters    = ctx["recent_full_chapters"],
        chapter_summaries       = ctx["chapter_summaries"],
        chapter_outline         = ctx.get("chapter_outline", ch_info.get("summary","")),
    )

    user_prompt = CHAPTER_USER.format(
        chapter_id    = ctx["chapter_id"],
        chapter_title = ctx["chapter_title"],
        chapter_num   = chapter_num,
        target_words  = wpc,
    )

    # ── Token budget report ──
    est_in = estimate_context_tokens(ctx, llm)
    win    = ctx["context_window_strategy"]
    print(f"  [Chapter {chapter_num}] 视角: {ctx['pov']} | 字数目标: {wpc}")
    print(f"  [Chapter {chapter_num}] 上下文策略: {win} | 估算输入: ~{est_in} tok")
    print(f"  [Chapter {chapter_num}] 关键事件: {ctx['key_events'][:60]}...")

    print(f"[PIPELINE] book={book} ch={chapter_num} stage=writing status=start")
    llm.set_stage_context("writing", chapter_num)
    text = llm.complete(
        prompt=user_prompt,
        system=system_prompt,
        temperature=0.65,
        max_tokens=8192,
        # NOTE: do NOT stop on "## 第" — the chapter H2 title itself contains that string,
        # would stop generation right after the title.
        stop=["<stop>", "<END>", "### ", "---END---"],
    )
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=writing status=done")

    # Clean & save
    text = clean_chapter_text(text, ctx["chapter_title"], chapter_num)
    storage.write_chapter(book, ctx["chapter_id"], text)

    # ── Post-write pipeline ──
    run_post_write_pipeline(book, chapter_num, ctx["chapter_id"], llm, cfg)

    print(f"[PIPELINE] book={book} ch={chapter_num} stage=done status=start")
    return text


def run_post_write_pipeline(
    book: str,
    chapter_num: int,
    chapter_id: str,
    llm: LLM,
    cfg: dict,
) -> None:
    """
    Pipeline called after a chapter is written:
      1. Extract → merge into memory libraries
      2. Generate narrative summary (200 chars)
      3. Update state snapshot
      4. If ch_001 and no style anchor yet → extract style anchor
      5. If self_check enabled in config → run anti-drift check
    """
    # 1) Extract & merge
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=extract status=start")
    llm.set_stage_context("extract", chapter_num)
    try:
        from . import extract as extmod
        text = storage.read_chapter(book, chapter_id) or ""
        extraction = extmod.extract_from_chapter(text, llm)
        memory.merge_extraction(book, extraction)
        print(f"  ✓ 记忆更新: "
              f"{len(extraction.get('new_events',[]))} 事件, "
              f"{len(extraction.get('new_foreshadowing',[]))} 伏笔, "
              f"{len(extraction.get('new_characters',[]))} 角色")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=extract status=done")
    except Exception as e:
        print(f"  ⚠ extract 失败 (非致命): {e}")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=extract status=failed")

    # 2) Generate rolling summary
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=summary status=start")
    llm.set_stage_context("summary", chapter_num)
    try:
        summod.generate_chapter_summary(book, chapter_id, llm)
        print(f"  ✓ 章节摘要生成")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=summary status=done")
    except Exception as e:
        print(f"  ⚠ 摘要生成失败 (非致命): {e}")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=summary status=failed")

    # 3) Update state snapshot
    print(f"[PIPELINE] book={book} ch={chapter_num} stage=state status=start")
    llm.set_stage_context("state", chapter_num)
    try:
        statemod.update_state_after_chapter(book, chapter_num, llm)
        print(f"  ✓ 状态快照更新")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=state status=done")
    except Exception as e:
        print(f"  ⚠ 状态更新失败 (非致命): {e}")
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=state status=failed")

    # 4) Style anchor (only after ch_001 first write)
    if chapter_num == 1 and not stylemod.get_style_anchor(book):
        llm.set_stage_context("style_anchor", chapter_num)
        try:
            stylemod.extract_style_anchor(book, llm)
            print(f"  ✓ 风格锚点已建立 (基于第 1 章)")
        except Exception as e:
            print(f"  ⚠ 风格锚点提取失败 (非致命): {e}")

    # 5) Self-check (optional, doubles per-chapter LLM calls)
    if cfg.get("self_check", False):
        print(f"[PIPELINE] book={book} ch={chapter_num} stage=self_check status=start")
        llm.set_stage_context("self_check", chapter_num)
        try:
            result = scmod.self_check_chapter(book, chapter_id, llm)
            sev = result.get("severity", "unknown")
            print(f"[PIPELINE] book={book} ch={chapter_num} stage=self_check status=done severity={sev}")

            # ── Auto-flag in review service (regardless of rewrite path) ──
            try:
                revserv.auto_flag(book, chapter_id, result, by="AI")
            except Exception as flag_err:
                print(f"  ⚠ 评审记录创建失败 (非致命): {flag_err}")

            if scmod.has_critical_issues(result, strict=cfg.get("self_check_strict", False)):
                # critical / moderate-with-overall-ok-false
                auto_rewrite = cfg.get("auto_rewrite_on_critical", False)
                if auto_rewrite:
                    print(f"  ⚠ 自检 critical (severity={sev}) → 触发自动重写")
                    try:
                        new_text = scmod.rewrite_chapter(
                            book, chapter_id, llm, result,
                            target_words=cfg.get("words_per_chapter", 2500),
                        )
                        # Re-run summary & state (content changed)
                        try:
                            summod.generate_chapter_summary(book, chapter_id, llm)
                        except Exception:
                            pass
                        try:
                            statemod.update_state_after_chapter(book, chapter_num, llm)
                        except Exception:
                            pass
                        # Re-self-check ONCE (avoid infinite loop)
                        retry = scmod.self_check_chapter(book, chapter_id, llm)
                        retry_sev = retry.get("severity", "unknown")
                        # Update review record with retry result
                        try:
                            revserv.auto_flag(book, chapter_id, retry, by="AI-retry")
                        except Exception:
                            pass
                        if scmod.has_critical_issues(retry, strict=cfg.get("self_check_strict", False)):
                            print(f"  ⚠ 重写后仍 critical (severity={retry_sev}) → 标记需人工 review")
                        else:
                            print(f"  ✓ 重写后通过 (severity={retry_sev}, {len(new_text)} 字)")
                    except Exception as e2:
                        print(f"  ✗ 自动重写失败: {e2} → 保留原版，需人工 review")
                        print(f"    → self_checks/{chapter_id}.json")
                else:
                    print(f"  ⚠ 自检发现严重问题 (severity={sev}) → self_checks/{chapter_id}.json")
                    print(f"    启用自动重写: python novel.py config {book} auto_rewrite_on_critical=true")
                    print(f"    或人工审核: novel.py review-show {book} {chapter_id}")
            else:
                print(f"  ✓ 自检通过 (severity={sev})")
        except Exception as e:
            print(f"  ⚠ 自检失败 (非致命): {e}")

    # Update progress (use shared helper so review/human-edit paths also stay in sync)
    storage.mark_chapter_completed(book, chapter_id, chapter_num)


def write_all_chapters(book: str, llm: LLM, outline: dict, auto_continue: bool = False) -> None:
    """
    Loop through all chapters: write → extract → memory merge → summary → state → [self-check].
    """
    total = outline.get("meta", {}).get("target_chapters", 20)
    print(f"\n{'='*60}")
    print(f"Starting chapter loop: {total} chapters")
    print(f"{'='*60}\n")

    for i in range(1, total + 1):
        print(f"\n{'─'*50}")
        print(f"Chapter {i}/{total}")
        print(f"{'─'*50}")
        try:
            text = write_chapter(book, i, llm, outline)
            print(f"  ✓ Chapter {i} written ({len(text)} chars)")
        except Exception as e:
            print(f"  ✗ Chapter {i} failed: {e}")
            if not auto_continue:
                raise
            print(f"  → Continuing to next chapter (auto_continue=True)")


def clean_chapter_text(text: str, title: str, chapter_num: int) -> str:
    """Remove any stray thinking content, normalize whitespace."""
    lines = text.splitlines()
    first_h2 = -1
    for i, line in enumerate(lines):
        if re.match(r"^##\s+第", line):
            first_h2 = i
            break
    if first_h2 > 0:
        text = "\n".join(lines[first_h2:])
    if not text.strip().startswith("## "):
        text = f"## 第{chapter_num}章 {title}\n\n{text.strip()}"
    text = re.sub(r'```json\s*[\s\S]*?```', '', text)
    text = re.sub(r'^\s*\{[\s\S]*?\}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()