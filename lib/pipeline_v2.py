"""
pipeline_v2.py — Pipeline 状态机 + Checkpoint 持久化 (v1.2 M5)

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
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from . import storage
from .errors import ErrorCode, NovelError


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
}


# ── 异常 ──────────────────────────────────────────────────────────────────

class PipelineError(NovelError):
    """pipeline_v2 专用错误, 继承 NovelError 复用现有错误处理."""
    pass


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
    return storage.project_root(book) / CHECKPOINT_FILE


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
            pass
        raise


def _read_json_safe(path: Path) -> Optional[dict[str, Any]]:
    """读 JSON, 文件不存在 / 损坏返回 None."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
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
    except Exception:
        return {"book": book, "ch": ch, "available": False}

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
    from . import storage as _sto
    snap_path = _sto.project_root(book) / "memory" / "pipeline_snapshot.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    return snapshot


def get_last_snapshot(book: str) -> dict | None:
    """Load the last saved pipeline snapshot (may be from previous session)."""
    from . import storage as _sto
    snap_path = _sto.project_root(book) / "memory" / "pipeline_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        return json.loads(snap_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_interrupted_chapters(book: str) -> list[dict]:
    """
    Find all chapters with interrupted pipeline (neither complete nor clean).
    Returns list of {ch, current_stage, failed_stage, timestamp}.
    """
    v2 = get_v2()
    try:
        doc = v2.load(book)
    except Exception:
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
    target = None
    for s in STAGES:
        sc = ch_doc.stages[s]
        if sc.status == StageState.RUNNING.value:
            target = s  # was running, now definitely dead → resume
            break
        if sc.status == StageState.FAILED.value:
            target = s
            break
        if sc.status == StageState.PENDING.value:
            # First pending after some DONE → the next one that should have been run
            target = s
            break

    if target is None:
        return {"ok": False, "chapter": ch, "recovered_stage": None,
                "message": "未检测到可恢复的 stage"}

    try:
        v2.rerun_from(book, ch, target)
    except Exception as e:
        return {"ok": False, "chapter": ch, "recovered_stage": target,
                "message": f"恢复失败: {e}"}

    return {"ok": True, "chapter": ch, "recovered_stage": target,
            "message": f"自动检测中断于 [{target}], 已重置为可恢复"}