"""
state.py — Pipeline 状态机 + Checkpoint 持久化 (v1.2 M5)

设计依据: docs/DESIGN-v1.2-pipeline.md

与 v1 PipelineRunner 的关系:
- v1 管 subprocess 生命周期 (PID/status/cancel) → .pipeline_state.json
- v2 管 stage-level FSM + checkpoint        → .pipeline_checkpoints.json
- 共存: v1 启动 subprocess → 内调 v2.transition() 写 checkpoint
- v2 提供 skip / rerun API, 触发 v1 启动新 subprocess

核心概念:
- StageState: PENDING / RUNNING / DONE / FAILED / SKIPPED
- CheckpointDoc: 1 本 1 章的 7 阶段状态 + artifacts + tokens + error
- FSM transition: 校验合法, 否则 raise PipelineError
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .. import storage
from ..errors import ErrorCode
from .errors import PipelineError

log = logging.getLogger(__name__)

__all__ = [
    "ChapterCheckpoint", "CheckpointDoc", "PipelineError",
    "PipelineV2", "StageCheckpoint", "StageState",
    "checkpoint_path", "checkpoint_snapshot",
    "get_interrupted_chapters", "get_last_snapshot", "get_v2",
    "recover_stage",
    "RESUMABLE_STAGE_ARTIFACTS", "stage_artifacts_status", "stage_resume_decision",
]


# ── 常量 ──────────────────────────────────────────────────────────────────

CHECKPOINT_FILE = ".pipeline_checkpoints.json"
CHECKPOINT_SCHEMA_VERSION = 2


class StageState(str, Enum):
    """阶段状态枚举."""
    PENDING = "PENDING"   # 还没跑
    RUNNING = "RUNNING"   # 跑中
    DONE = "DONE"         # 成功
    FAILED = "FAILED"     # 失败
    SKIPPED = "SKIPPED"   # 用户主动跳过


# 阶段定义 (顺序即执行顺序)
STAGES = ["context", "writing", "extract", "entity_diff", "summary", "state", "self_check", "done"]


# FSM 合法转换矩阵
# key = (from_state, to_state) → bool
_VALID_TRANSITIONS: set[tuple[str, str]] = {
    # 标准流
    (StageState.PENDING.value,  StageState.RUNNING.value),
    (StageState.RUNNING.value,  StageState.DONE.value),
    (StageState.RUNNING.value,  StageState.FAILED.value),
    # 重跑
    (StageState.DONE.value,     StageState.RUNNING.value),
    (StageState.FAILED.value,   StageState.RUNNING.value),
    (StageState.SKIPPED.value,  StageState.RUNNING.value),
    # 显式 skip (任何非 RUNNING 都可)
    (StageState.PENDING.value,  StageState.SKIPPED.value),
    (StageState.FAILED.value,   StageState.SKIPPED.value),
    (StageState.DONE.value,     StageState.SKIPPED.value),  # skip 一个已完成 stage (例如重写 chapter)
    # 2026-10-02: 重复 skip 一个已跳过的 stage 是完全正常的 —— self_check 未启用时
    # 每次运行都会再标一次 SKIPPED。此前这一对不在矩阵里, 于是每次重跑章节都会刷
    # 「非法 FSM 转换: self_check SKIPPED → SKIPPED」(生产实跑观测到), 写入被吞,
    # 状态却恰好还是对的。属于无害但吵的缺陷: 它在正常路径上制造假警报, 真出事时
    # 反而没人看那一行了。幂等转换应当合法。
    (StageState.SKIPPED.value,  StageState.SKIPPED.value),
}


# ── 异常 ──────────────────────────────────────────────────────────────────
# PipelineError 已收编到 .errors, 那里是唯一真相(state.py 曾自定义一个同名类)。
# TransientError / PermanentError 已移除 —— 它们从未被 raise, 详见 errors.py 的说明。


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class StageCheckpoint:
    """单阶段的 checkpoint."""
    status: str = StageState.PENDING.value
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageCheckpoint":
        return cls(
            status=d.get("status", StageState.PENDING.value),
            started_at=d.get("started_at"),
            ended_at=d.get("ended_at"),
            artifacts=d.get("artifacts", {}) or {},
            tokens=d.get("tokens", {}) or {},
            error=d.get("error"),
        )


@dataclass
class ChapterCheckpoint:
    """单章的 checkpoint 容器, 含 7 阶段."""
    book: str
    chapter: int
    stages: dict[str, StageCheckpoint] = field(default_factory=dict)
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self):
        # 初始化 7 个 stage (如果没给)
        for s in STAGES:
            if s not in self.stages:
                self.stages[s] = StageCheckpoint()

    def to_dict(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "chapter": self.chapter,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChapterCheckpoint":
        book = d.get("book", "")
        chapter = int(d.get("chapter", 0))
        stages_raw = d.get("stages", {}) or {}
        stages = {k: StageCheckpoint.from_dict(v) for k, v in stages_raw.items()}
        return cls(book=book, chapter=chapter, stages=stages)

    def is_complete(self) -> bool:
        """全部 DONE 或 SKIPPED → 章节完成."""
        return all(
            s.status in (StageState.DONE.value, StageState.SKIPPED.value)
            for s in self.stages.values()
        )


@dataclass
class CheckpointDoc:
    """一本书的 checkpoint 文档, key = chapter_num (str)."""
    book: str
    chapters: dict[int, ChapterCheckpoint] = field(default_factory=dict)
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "chapters": {str(k): v.to_dict() for k, v in self.chapters.items()},
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CheckpointDoc":
        book = d.get("book", "")
        chapters_raw = d.get("chapters", {}) or {}
        chapters = {}
        for k, v in chapters_raw.items():
            try:
                ch_num = int(k)
            except (ValueError, TypeError):
                continue
            chapters[ch_num] = ChapterCheckpoint.from_dict(v)
        return cls(book=book, chapters=chapters)


# ── 路径 helpers ──────────────────────────────────────────────────────────

def checkpoint_path(book: str) -> Path:
    # 必须走 project_path(纯计算)。之前这里调 project_root(), 而后者会 mkdir ——
    # 于是 PipelineV2.load() 这个纯读动作, 对一本不存在的书也会把 projects/<book>/
    # 建出来。命令行 `pipeline resume --chapter ch_5` 走的就是 load() 这条路,
    # 传错书名就会凭空造一个空项目目录。
    # 写侧不受影响: save() 落到 _atomic_write_json(), 那里自己会 mkdir parent。
    return storage.project_path(book) / CHECKPOINT_FILE


# ── 内部 helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写 JSON: 写临时文件 → rename, 避免部分写入."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 下 NamedTemporaryFile 默认独占打开, 关不掉 → 用 mkstemp
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(path))
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            log.warning("检查点 · _atomic_write_json 第1处兜底步骤失败 (非致命)", exc_info=True)
            pass
        raise


def _read_json_safe(path: Path) -> Optional[dict[str, Any]]:
    """读 JSON。文件不存在 / 损坏都返回 None —— 但两者语义不同,故分别记日志。

    「不存在」是正常路径(首次运行);「损坏」意味着数据丢失,必须可见。
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("检查点文件损坏,按「无」处理: %s | %s: %s",
                    path, type(e).__name__, e)
        return None


def _validate_stage(stage: str) -> None:
    if stage not in STAGES:
        raise PipelineError(
            ErrorCode.INVALID_ARGS,
            f"未知阶段 [{stage}], 合法: {STAGES}",
        )


def _validate_transition(from_state: str, to_state: str, stage: str) -> None:
    """校验 FSM 转换合法."""
    key = (from_state, to_state)
    if key not in _VALID_TRANSITIONS:
        raise PipelineError(
            ErrorCode.GENERIC,
            f"非法 FSM 转换: stage=[{stage}] {from_state} → {to_state}",
            detail=f"合法转换: {sorted(_VALID_TRANSITIONS)}",
        )


# ── PipelineV2 主类 ──────────────────────────────────────────────────────

class PipelineV2:
    """Pipeline 状态机 + Checkpoint 持久化."""

    # ── Checkpoint CRUD ─────────────────────────────────────────

    def load(self, book: str) -> CheckpointDoc:
        """读 checkpoint 文档. 文件不存在 / 损坏 → 返回空 doc."""
        path = checkpoint_path(book)
        data = _read_json_safe(path)
        if data is None:
            return CheckpointDoc(book=book)
        try:
            return CheckpointDoc.from_dict(data)
        except Exception:
            # 损坏 (e.g. 旧 schema / 半截) → 返回空 doc, 不抛
            return CheckpointDoc(book=book)

    def save(self, doc: CheckpointDoc) -> None:
        """写 checkpoint 文档 (atomic)."""
        path = checkpoint_path(doc.book)
        _atomic_write_json(path, doc.to_dict())

    def get_chapter(self, book: str, ch: int) -> ChapterCheckpoint:
        """读 1 章的 checkpoint, 不存在返回新的 (PENDING)."""
        doc = self.load(book)
        return doc.chapters.get(ch) or ChapterCheckpoint(book=book, chapter=ch)

    def save_chapter(self, book: str, ch_doc: ChapterCheckpoint) -> None:
        """写单章 checkpoint (load + modify + save)."""
        doc = self.load(book)
        doc.chapters[ch_doc.chapter] = ch_doc
        self.save(doc)

    def reset_chapter(self, book: str, ch: int) -> ChapterCheckpoint:
        """清空 1 章所有 checkpoint, 回到 PENDING."""
        new_ch = ChapterCheckpoint(book=book, chapter=ch)
        self.save_chapter(book, new_ch)
        return new_ch

    # ── FSM transitions ─────────────────────────────────────────

    def transition(
        self,
        book: str,
        ch: int,
        stage: str,
        new_state: str,
        *,
        started_at: Optional[str] = None,
        ended_at: Optional[str] = None,
        artifacts: Optional[dict[str, Any]] = None,
        tokens: Optional[dict[str, int]] = None,
        error: Optional[str] = None,
    ) -> StageCheckpoint:
        """转换 stage 状态, 校验合法.

        Returns: 更新后的 StageCheckpoint.
        Raises:
            PipelineError(INVALID_ARGS): 未知 stage
            PipelineError(GENERIC): 非法 FSM 转换
        """
        _validate_stage(stage)
        new_state_str = str(new_state)
        # 校验 new_state 是合法 enum 值
        if new_state_str not in (s.value for s in StageState):
            raise PipelineError(
                ErrorCode.INVALID_ARGS,
                f"未知 stage 状态 [{new_state_str}], 合法: {[s.value for s in StageState]}",
            )

        ch_doc = self.get_chapter(book, ch)
        cur = ch_doc.stages[stage]
        _validate_transition(cur.status, new_state_str, stage)

        # 写入
        cur.status = new_state_str
        if started_at is not None:
            cur.started_at = started_at
        elif new_state_str == StageState.RUNNING.value and cur.started_at is None:
            cur.started_at = _now_iso()
        if ended_at is not None:
            cur.ended_at = ended_at
        elif new_state_str in (StageState.DONE.value, StageState.FAILED.value, StageState.SKIPPED.value):
            cur.ended_at = _now_iso()
        if artifacts is not None:
            cur.artifacts = artifacts
        if tokens is not None:
            cur.tokens = tokens
        if error is not None:
            cur.error = error
        # DONE 时清空 error
        if new_state_str == StageState.DONE.value:
            cur.error = None

        self.save_chapter(book, ch_doc)
        return cur

    def get_stage_state(self, book: str, ch: int, stage: str) -> str:
        """读 1 个 stage 的当前状态 (string)."""
        _validate_stage(stage)
        ch_doc = self.get_chapter(book, ch)
        return ch_doc.stages[stage].status

    # ── Skip / Rerun ────────────────────────────────────────────

    def skip_stage(
        self,
        book: str,
        ch: int,
        stage: str,
        *,
        reason: Optional[str] = None,
    ) -> StageCheckpoint:
        """skip 一个 stage.

        规则:
        - PENDING / FAILED → 直接 SKIPPED
        - DONE → SKIPPED (用 artifacts 为空覆盖, 视为不再依赖)
        - RUNNING → raise (正在跑不能 skip, 必须先 cancel)

        Returns: 更新后的 StageCheckpoint.
        """
        _validate_stage(stage)
        ch_doc = self.get_chapter(book, ch)
        cur = ch_doc.stages[stage]

        if cur.status == StageState.RUNNING.value:
            raise PipelineError(
                ErrorCode.GENERIC,
                f"阶段 [{stage}] 正在 RUNNING, 不能 skip. 请先 cancel.",
            )

        # 任意非 RUNNING 都可 skip (FSM 矩阵已覆盖)
        _validate_transition(cur.status, StageState.SKIPPED.value, stage)
        cur.status = StageState.SKIPPED.value
        cur.ended_at = _now_iso()
        if reason:
            cur.artifacts = {**(cur.artifacts or {}), "skip_reason": reason}
        # 清空 artifacts (skip 视为不依赖)
        # 但保留 skip_reason 用于审计
        self.save_chapter(book, ch_doc)
        return cur

    def rerun_from(self, book: str, ch: int, from_stage: str) -> ChapterCheckpoint:
        """从 from_stage 重跑: 保留上游 DONE, 重置下游所有 stage 到 PENDING.

        from_stage 本身 → RUNNING (等 subprocess 启动后转)。

        规则:
        - from_stage 必须是合法 stage
        - from_stage 之后 (含) 所有 stage → PENDING
        - from_stage 之前所有 stage → 保持现状 (DONE 不动)

        Returns: 更新后的 ChapterCheckpoint.
        """
        _validate_stage(from_stage)
        try:
            idx = STAGES.index(from_stage)
        except ValueError:
            raise PipelineError(
                ErrorCode.INVALID_ARGS,
                f"未知阶段 [{from_stage}]",
            )

        ch_doc = self.get_chapter(book, ch)
        # 下游 (含 from_stage) → PENDING
        for i, s in enumerate(STAGES):
            if i >= idx:
                ch_doc.stages[s] = StageCheckpoint()
        # 上游不动
        self.save_chapter(book, ch_doc)
        return ch_doc

    # ── 状态汇总 (给 dashboard / API) ──────────────────────────

    def get_pipeline_view(self, book: str, ch: int) -> dict[str, Any]:
        """返回给前端的状态汇总.

        Schema:
        {
          "book": str,
          "chapter": int,
          "stages": [
            {"name": "context", "status": "DONE", "started_at": ..., "ended_at": ...,
             "tokens": {"in":..., "out":...}, "error": null, "artifacts": {...}},
            ...
          ],
          "current_stage": "writing" | None,  # 第一个非 DONE/SKIPPED 的
          "is_complete": bool,
          "failed_stage": "extract" | None,  # 第一个 FAILED 的
        }
        """
        ch_doc = self.get_chapter(book, ch)
        stages_view = []
        current_stage = None
        failed_stage = None
        for s in STAGES:
            sc = ch_doc.stages[s]
            stages_view.append({
                "name": s,
                "status": sc.status,
                "started_at": sc.started_at,
                "ended_at": sc.ended_at,
                "tokens": sc.tokens,
                "error": sc.error,
                "artifacts": sc.artifacts,
            })
            if current_stage is None and sc.status not in (
                StageState.DONE.value, StageState.SKIPPED.value
            ):
                current_stage = s
            if failed_stage is None and sc.status == StageState.FAILED.value:
                failed_stage = s

        return {
            "book": book,
            "chapter": ch,
            "stages": stages_view,
            "current_stage": current_stage,
            "is_complete": ch_doc.is_complete(),
            "failed_stage": failed_stage,
        }


# ── 单例 ──────────────────────────────────────────────────────────────────

_default: Optional[PipelineV2] = None


def get_v2() -> PipelineV2:
    global _default
    if _default is None:
        _default = PipelineV2()
    return _default


# ── v1.3 M4: snapshot & recovery ──────────────────────────────────────────

def checkpoint_snapshot(book: str, ch: int, stage: str | None = None) -> dict:
    """
    Snapshot the current checkpoint state for recovery across sessions.
    Returns a lightweight dict with stage status summary.
    """
    v2 = get_v2()
    try:
        ch_doc = v2.get_chapter(book, ch)
    except Exception as e:
        # 面板据此显示「无数据」。若此处静默,读取失败会被误读成「一切正常」。
        log.error("读取检查点失败,返回 unavailable: book=%s ch=%s | %s: %s",
                  book, ch, type(e).__name__, e, exc_info=True)
        return {"book": book, "ch": ch, "available": False,
                "error": f"{type(e).__name__}: {e}"}

    snapshot = {
        "book": book,
        "ch": ch,
        "available": True,
        "timestamp": _now_iso(),
        "stages": {s: v.status for s, v in ch_doc.stages.items()},
        "is_complete": ch_doc.is_complete(),
        "current_stage": None,
        "failed_stage": None,
    }

    for s in STAGES:
        sc = ch_doc.stages[s]
        if snapshot["current_stage"] is None and sc.status not in (
            StageState.DONE.value, StageState.SKIPPED.value
        ):
            snapshot["current_stage"] = s
        if snapshot["failed_stage"] is None and sc.status == StageState.FAILED.value:
            snapshot["failed_stage"] = s

    # Save to file (per book, for cross-session recovery)
    from .. import storage as _sto
    # 写路径, 但 mkdir 下一行已经显式做了 —— 用 project_path() 保持全文件一致,
    # 免得"这里用 project_root 是因为要建目录"变成一个会误导后来者的先例。
    snap_path = _sto.project_path(book) / "memory" / "pipeline_snapshot.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    return snapshot


def get_last_snapshot(book: str) -> dict | None:
    """Load the last saved pipeline snapshot (may be from previous session)."""
    from .. import storage as _sto
    # 纯读, 走 project_path()。project_root() 会 mkdir, 于是"查一下有没有快照"
    # 这个查询动作本身就会造出 projects/<book>/memory/ 两级目录。
    snap_path = _sto.project_path(book) / "memory" / "pipeline_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        return json.loads(snap_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("快照文件损坏,按「无快照」处理: %s | %s: %s",
                    snap_path, type(e).__name__, e)
        return None


def get_interrupted_chapters(book: str) -> list[dict]:
    """
    Find all chapters with interrupted pipeline (neither complete nor clean).
    Returns list of {ch, current_stage, failed_stage, timestamp}.
    """
    v2 = get_v2()
    try:
        doc = v2.load(book)
    except Exception as e:
        # 返回值是 [] —— 与「确实没有中断章节」无法区分。
        # 契约不变(不破坏调用方),但失败必须留下 ERROR 记录,否则面板
        # 会在读取失败时显示「一切正常」。
        log.error("读取流水线状态失败,无法判断是否有中断章节: book=%s | %s: %s",
                  book, type(e).__name__, e, exc_info=True)
        return []

    result = []
    for ch_num, ch_doc in sorted(doc.chapters.items()):
        if ch_doc.is_complete():
            continue
        view = v2.get_pipeline_view(book, ch_num)
        if view["current_stage"] is not None or view["failed_stage"] is not None:
            result.append({
                "ch": ch_num,
                "current_stage": view["current_stage"],
                "failed_stage": view["failed_stage"],
                "stages": {s["name"]: s["status"] for s in view["stages"]},
            })
    return result


def recover_stage(book: str, ch: int, from_stage: str | None = None) -> dict:
    """
    Recovery: automatically find the best stage to resume from.

    - If from_stage given → call rerun_from(book, ch, from_stage)
    - If no from_stage → find first FAILED or RUNNING stage, resume there
    - If all complete → raise

    Returns: {
      "ok": bool,
      "chapter": ch,
      "recovered_stage": str | None,
      "message": str,
    }
    """
    # 书不存在就没什么可恢复的, 直接拒掉。
    #
    # 这个函数是【写】的: 它会把下游阶段重置回 PENDING 并落盘。所以对一本不存在
    # 的书跑它, 会在磁盘上凭空建出 projects/<书>/.pipeline_checkpoints.json ——
    # 书名打错一个字母, 就在 projects/ 下多一个永远没人认领的空项目。
    #
    # 只把 checkpoint_path/get_last_snapshot 换成纯计算是挡不住这个的: 那两条是读,
    # 而这里是写, 落盘本来就会建目录。
    #
    # Web 侧(bp/pipeline.py)本来就有 _ensure_book 挡着, 只有 CLI 的
    # `novel.py pipeline resume --chapter` 这条路是裸的。守卫放在这一层而不是
    # 调用方, 是因为"对不存在的书做写操作"这件事本身不该由谁来实现。
    # project_exists() 的语义是"config.json 在不在", 且走纯计算路径, 不会顺手建目录。
    if not storage.project_exists(book):
        return {"ok": False, "chapter": ch, "recovered_stage": None,
                "message": f"项目 [{book}] 不存在, 没什么可恢复的。"}

    v2 = get_v2()
    try:
        ch_doc = v2.get_chapter(book, ch)
    except Exception as e:
        return {"ok": False, "chapter": ch, "recovered_stage": None,
                "message": f"无法读取 checkpoint: {e}"}

    if ch_doc.is_complete():
        return {"ok": False, "chapter": ch, "recovered_stage": None,
                "message": "本章节 pipeline 已完成，无需恢复。"}

    if from_stage:
        try:
            v2.rerun_from(book, ch, from_stage)
        except Exception as e:
            return {"ok": False, "chapter": ch, "recovered_stage": from_stage,
                    "message": f"恢复失败: {e}"}
        return {"ok": True, "chapter": ch, "recovered_stage": from_stage,
                "message": f"从 [{from_stage}] 恢复并重置下游"}

    # Auto-detect: find first non-DONE, non-SKIPPED stage
    #
    # 2026-10-02: context / writing 必须跳过不看。
    #
    # write_chapter 每次运行开头都会把 context 标 RUNNING, 所以自动探测几乎
    # 总是命中 context。而 rerun_from("context") 会把**全部 8 个阶段**重置成
    # PENDING —— 但 context 和 writing 本来就无条件重跑, 重置它们毫无收益,
    # 唯一效果是把下游那些「DONE 且产物完好」的阶段一起清掉。
    # 结果: 刚加的 resume-skip(产物完好就跳过整个阶段)在 resume 之后一个都
    # 触发不了, 功能等于白做。
    #
    # 所以: 探测时跳过这两个「反正会重跑」的头部阶段, 直接找真正值得重置的那一个。
    # 若只剩它们非 DONE, 说明上游本来就是坏的 —— 什么都不用重置, 直接重跑即可。
    _ALWAYS_RERUN_HEAD = ("context", "writing")
    target = None
    head_only = []
    for s in STAGES:
        sc = ch_doc.stages[s]
        if sc.status in (StageState.DONE.value, StageState.SKIPPED.value):
            continue
        if s in _ALWAYS_RERUN_HEAD:
            head_only.append(s)
            continue
        if sc.status in (StageState.RUNNING.value, StageState.FAILED.value,
                         StageState.PENDING.value):
            target = s
            break

    if target is None:
        if head_only:
            return {"ok": True, "chapter": ch, "recovered_stage": head_only[0],
                    "message": (f"未完成阶段只有 {', '.join(head_only)} —— 它们每次"
                                f"运行都会无条件重跑, 无需重置任何状态。直接重跑该章即可。")}
        return {"ok": False, "chapter": ch, "recovered_stage": None,
                "message": "未检测到可恢复的 stage"}

    try:
        v2.rerun_from(book, ch, target)
    except Exception as e:
        return {"ok": False, "chapter": ch, "recovered_stage": target,
                "message": f"恢复失败: {e}"}

    return {"ok": True, "chapter": ch, "recovered_stage": target,
            "message": f"自动检测中断于 [{target}], 已重置为可恢复"}


# ── resume-skip: 跳过判定 + 产物校验 (2026-10-02) ────────────────────────
#
# 背景: get_stage_state() 此前全仓只有 tests 在调用 —— 写章节的流水线从不读
# checkpoint, 于是中断后重跑会把已经成功、产物完好的 extract / summary 全部
# 重烧一遍 (每章 4~5 次 LLM 调用)。
#
# 为什么不能只看 status: extract 曾经「记录为 DONE 但产物是 0 字节」——
# 状态字段说成功、磁盘上什么都没有, 下一章照样把空记忆库塞进上下文。
# 只信 status 会把这个什么都没产出的阶段当成"已完成"跳过去, 静默丢数据。
# 所以判定必须同时满足两条: status == DONE **且** 产物在磁盘上真实存在且非空。

# 可跳过的阶段 → 产物清单 (相对 storage.project_path(book), {ch} 替换为 chapter_id)。
# 清单是「析取」: 任一非空即认为该阶段真的落了东西。extract 的三张记忆表是跨章
# 累积的共享文件, 语义上就必须是 OR (抽不出新角色但抽出了事件, 也算成功);
# 其余阶段各自只有一个产物, OR 与 AND 等价。
# 不在本表里的阶段 (context / writing / self_check / style_anchor) 永远重跑。
RESUMABLE_STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "extract":     ("memory/characters.json", "memory/events.json", "memory/foreshadowing.json"),
    "entity_diff": ("memory/_changelog/{ch}.json",),
    "summary":     ("summaries/{ch}.txt",),
    "state":       ("state.json",),
}


def _artifact_nonempty(path: Path) -> tuple[bool, str]:
    """单个产物文件是否「真实有内容」。

    规则: 必须存在 → .json 要能解析且容器非空 (2 字节的 {} / [] 视同没有产出,
    那正是 2026-10-02 生产实跑踩到的形态) → 文本文件 strip() 后非空。
    读不到 / 解析不了一律算「无产物」, 宁可重跑也不静默跳过。
    """
    try:
        if not path.is_file():
            return False, "missing"
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("产物校验读取失败,按「无产物」处理: %s | %s: %s",
                    path, type(e).__name__, e)
        return False, f"unreadable({type(e).__name__})"

    size = len(raw.encode("utf-8"))
    if not raw.strip():
        return False, f"empty({size}B)"
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("产物 JSON 损坏,按「无产物」处理: %s | %s", path, e)
            return False, "corrupt-json"
        if not isinstance(data, (dict, list)) or len(data) == 0:
            return False, f"empty-json({size}B)"
    return True, f"{path.name}={size}B"


def stage_artifacts_status(book: str, chapter_id: str, stage: str) -> dict[str, Any]:
    """某阶段在磁盘上的产物是否真实存在且非空 (不看 checkpoint)。

    Returns:
        {"ok": bool, "detail": str, "checked": [{"path", "ok", "detail"}, ...]}
        不在 RESUMABLE_STAGE_ARTIFACTS 里的阶段 → ok=False (永不可跳过)。
    """
    templates = RESUMABLE_STAGE_ARTIFACTS.get(stage)
    if not templates:
        return {"ok": False, "detail": f"stage={stage} 不在可跳过清单",
                "checked": []}

    base = storage.project_path(book)   # 纯计算, 不会凭空建目录
    checked: list[dict[str, Any]] = []
    hit = ""
    for tpl in templates:
        rel = tpl.format(ch=chapter_id)
        ok, detail = _artifact_nonempty(base / rel)
        checked.append({"path": rel, "ok": ok, "detail": detail})
        if ok and not hit:
            hit = f"{rel}({detail})"
    return {"ok": bool(hit), "detail": hit or "no-artifact", "checked": checked}


def stage_resume_decision(book: str, ch: int, chapter_id: str, stage: str) -> dict[str, Any]:
    """resume 场景下该阶段能否跳过 —— checkpoint 与磁盘产物【双条件】。

    Returns:
        {"skippable": bool,  # 该阶段是否在可跳过清单里
         "skip": bool,       # 这一次是否真的跳过
         "status": str|None, # checkpoint 里的状态
         "artifact": str,    # 产物校验结论 (给日志看的短描述)
         "reason": str}      # 不跳过的原因 (skippable 且不 skip 时非空)

    判定不出来时抛异常, 由调用方决定 —— 这里不吞: 判不出「能不能跳」时,
    安全的一侧是照常重跑, 但那必须留下 ERROR, 不能静默。
    """
    if stage not in RESUMABLE_STAGE_ARTIFACTS:
        return {"skippable": False, "skip": False, "status": None,
                "artifact": None, "reason": f"[{stage}] 每次都重跑"}

    status = get_v2().get_stage_state(book, ch, stage)
    art = stage_artifacts_status(book, chapter_id, stage)
    if status == StageState.DONE.value and art["ok"]:
        return {"skippable": True, "skip": True, "status": status,
                "artifact": art["detail"], "reason": ""}

    reason = (f"checkpoint={status}" if status != StageState.DONE.value
              else f"checkpoint=DONE 但产物无效({art['detail']})")
    return {"skippable": True, "skip": False, "status": status,
            "artifact": art["detail"], "reason": reason}