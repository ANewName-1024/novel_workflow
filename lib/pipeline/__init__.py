"""
lib/pipeline — 流水线包(Phase 2)

审计结论(见 tools/audit_pipeline.py):原 lib/pipeline.py 与 lib/pipeline_v2.py
不是「新旧两代」,而是**两个正交层** —— 公开符号零重叠,依赖单向:

    state.py    状态机 + 检查点(原 pipeline_v2.py)   下层,不依赖 process
    process.py  进程编排(原 pipeline.py)             上层,依赖 state

    PipelineRunner  start / cancel / status / stream_log / tail_log / metrics
                    → 启停、探活、杀进程树、SSE 日志流
    PipelineV2      load / save / transition / skip / rerun / reset / view
                    → 阶段流转、检查点、崩溃恢复

所以这里不做「v2 淘汰 v1」,而是按层拆开,统一从包入口导入。
调用方既可用 `from lib.pipeline import PipelineV2` 直接拿类,
也可用 `from lib.pipeline import process, state` 明确指层。
"""
from __future__ import annotations

# ── 下层:状态机 + 检查点 ──
from .state import (
    ChapterCheckpoint,
    CheckpointDoc,
    PipelineError,
    PipelineV2,
    StageCheckpoint,
    StageState,
    checkpoint_path,
    checkpoint_snapshot,
    get_interrupted_chapters,
    get_last_snapshot,
    get_v2,
    recover_stage,
)

# ── 上层:进程编排 ──
from .process import (
    PipelineRunner,
    get_overview_state,
    get_runner,
    metrics_path,
    pipeline_log_dir,
    pipeline_log_path,
    pipeline_state_path,
)

from . import process, state  # noqa: E402,F401  子模块直引

__all__ = [
    # 子模块
    "process", "state",
    # 状态机层
    "ChapterCheckpoint", "CheckpointDoc", "PipelineError", "PipelineV2",
    "StageCheckpoint", "StageState", "checkpoint_path", "checkpoint_snapshot",
    "get_interrupted_chapters", "get_last_snapshot", "get_v2", "recover_stage",
    # 进程层
    "PipelineRunner", "get_overview_state", "get_runner", "metrics_path",
    "pipeline_log_dir", "pipeline_log_path", "pipeline_state_path",
]
