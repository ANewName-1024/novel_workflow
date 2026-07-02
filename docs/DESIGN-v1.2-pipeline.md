# DESIGN-v1.2-pipeline — Pipeline 状态机细化 (M5)

> 状态: **Draft** · 2026-07-02 · 作者: 小爪 + 魏超

## 1. 现状 (v1.1) & 问题

`lib/pipeline.py` v1.1 实现:
- 7 stages: `context → writing → extract → summary → state → self_check → done`
- `.pipeline_state.json` 只记 `current_stage` (字符串) + PID + status
- 通过 **log 解析** `[PIPELINE]` marker 推 `current_stage` (倒序扫描最后 2KB)
- **没有 checkpoint**: 任何阶段失败重跑 = 整个 chapter 从头开始 (context + writing 浪费 token)
- **没有 skip / rerun**: 用户没办法跳到某个阶段, 或从中间重跑
- **没有 FSM 转换保护**: state 可以随意写任何字符串, log 解析也是字符串匹配

**核心痛点**:
1. writing 阶段最贵 (65536 token 余量里的 2-3k 字输出 + 上下文 build 5000-8000 tok), 失败重跑浪费
2. self_check 误报 → 触发 auto-rewrite → 又要重跑 extract/summary/state
3. 用户想"只重跑 state 阶段因为加了一个角色" → 现在只能全跑

## 2. 设计目标

| # | 目标 | 度量 |
|---|------|------|
| G1 | **每阶段有持久化 checkpoint**, 失败可跳过 | `.pipeline_checkpoints.json` 含 stage→artifact |
| G2 | **FSM 严格状态转换**, 非法转换 raise | `fsm.transition(cur, nxt)` 校验 |
| G3 | **skip/rerun API**: 用户可重跑或跳过指定 stage | `POST /api/pipeline/<book>/{skip,rerun}` |
| G4 | **向后兼容 v1.1**: 老 state.json 仍可读, 老 marker log 仍解析 | `.pipeline_state.json` schema 不破坏 |
| G5 | **零 LLM 调用增量**: 写 checkpoint 不能加 LLM 调用 | 每章 +0 LLM (only file write) |

## 3. 数据模型

### 3.1 Checkpoint (新增)

存到 `.pipeline_checkpoints.json` (per book):

```json
{
  "book": "test_book",
  "current_chapter": 5,
  "stages": {
    "context": {
      "status": "DONE",
      "started_at": "2026-07-02T19:30:00",
      "ended_at": "2026-07-02T19:30:02",
      "artifacts": {
        "context_file": "projects/test_book/cache/ch_005.context.json"
      },
      "tokens": {"in": 4200, "out": 0},
      "error": null
    },
    "writing": {
      "status": "DONE",
      "started_at": "...",
      "ended_at": "...",
      "artifacts": {
        "chapter_file": "projects/test_book/chapters/ch_005.md",
        "word_count": 2543
      },
      "tokens": {"in": 4200, "out": 3300},
      "error": null
    },
    "extract": {
      "status": "FAILED",
      "started_at": "...",
      "ended_at": "...",
      "artifacts": {},
      "tokens": {"in": 2700, "out": 400},
      "error": "LLM JSON parse error"
    }
  },
  "schema_version": 1
}
```

### 3.2 StageState enum

```python
class StageState(str, Enum):
    PENDING = "PENDING"   # 还没跑
    RUNNING = "RUNNING"   # 跑中
    DONE = "DONE"         # 成功
    FAILED = "FAILED"     # 失败
    SKIPPED = "SKIPPED"   # 用户主动跳过 (rerun 时上游 DONE 的 stage 默认 SKIPPED)
```

### 3.3 阶段清单 (FSM)

`STAGES_ORDER = ["context", "writing", "extract", "summary", "state", "self_check", "done"]`

合法转换:
```
PENDING  → RUNNING
RUNNING  → DONE | FAILED
DONE     → RUNNING (rerun 时)
FAILED   → RUNNING (retry 时)
SKIPPED  → RUNNING (rerun 时)
*        → SKIPPED (用户显式 skip)
```

**不允许**:
- DONE → DONE (已完成不能再 DONE, 必须 rerun)
- PENDING → SKIPPED (没跑过不能 skip)

## 4. 核心模块: lib/pipeline_v2.py

### 4.1 PipelineV2 class

```python
class PipelineV2:
    """管理 1 本书 1 章的 7 阶段 FSM + checkpoints.
    
    与 v1 PipelineRunner 不同:
    - v1 管理 subprocess (整章), v2 管理 stage-level state (per 阶段 checkpoint)
    - v1 不持久化 stage 结果, v2 持久化到 .pipeline_checkpoints.json
    - v2 提供 skip/rerun API
    """
    
    STAGES = ["context", "writing", "extract", "summary", "state", "self_check", "done"]
    CHECKPOINT_FILE = ".pipeline_checkpoints.json"
    
    # ── Checkpoint CRUD ─────────────────────────────────────────
    def load_checkpoints(book, ch) -> CheckpointDoc | None
    def save_checkpoints(book, ch, doc) -> None
    def reset_checkpoints(book, ch) -> None  # 全标 PENDING, 用于 rerun
    
    # ── FSM transitions ─────────────────────────────────────────
    def transition(book, ch, stage, new_state) -> None
        # 校验转换合法, 否则 raise PipelineError
    
    def get_stage_state(book, ch, stage) -> StageState
    def is_chapter_complete(book, ch) -> bool  # all DONE/SKIPPED + done stage DONE
    
    # ── Skip / Rerun ────────────────────────────────────────────
    def skip_stage(book, ch, stage) -> dict
        # 标记 stage 为 SKIPPED, 后续 stage 可继续
        # 校验: 不能 skip 已 DONE 的 stage (除非显式 rerun)
    
    def rerun_from(book, ch, from_stage) -> dict
        # 重置 from_stage 之后的所有 stage 为 PENDING, 然后转 RUNNING (第一个)
        # 保留上游已 DONE 的 checkpoint
    
    # ── 状态汇总 (给 dashboard / API) ──────────────────────────
    def get_pipeline_view(book, ch) -> dict
        # 返回 stages 列表 + 当前进度 + 失败信息
```

### 4.2 与 v1 PipelineRunner 共存

不替换 v1 (subprocess 管理层), 在 v1 之上叠加 checkpoint layer:
- v1 启动 subprocess → subprocess 内调 `pipeline_v2.transition(...)` 写 checkpoint
- v1 读 `.pipeline_state.json` 仍然管 PID/status
- v2 读 `.pipeline_checkpoints.json` 管 stage 状态
- skip/rerun API 通过 v1 启动新的 subprocess 触发

### 4.3 chapter.py 集成 (改动小)

`write_chapter()` 内每阶段成功/失败后调:
```python
from .pipeline_v2 import PipelineV2, StageState
v2 = PipelineV2()

# 阶段开始
v2.transition(book, ch, "context", StageState.RUNNING, started_at=now)

# 阶段成功
v2.transition(book, ch, "context", StageState.DONE, 
              ended_at=now, artifacts={"context_file": "..."}, 
              tokens={"in": 4200, "out": 0})

# 阶段失败
v2.transition(book, ch, "extract", StageState.FAILED,
              ended_at=now, error="JSON parse error")
```

## 5. API 设计

### 5.1 现有 (review_ui/app.py 已有的)

- `GET /api/pipeline/<book>/status` — v1 PID/status (保留)
- `POST /api/pipeline/<book>/start` — v1 启动 (保留)
- `POST /api/pipeline/<book>/cancel` — v1 杀进程 (保留)
- `GET /api/pipeline/<book>/log` — v1 tail log (保留)

### 5.2 新增 (M5)

- `GET /api/pipeline/<book>/checkpoints?ch=N` — 查 ch N 的所有 stage checkpoint
- `POST /api/pipeline/<book>/skip` body=`{"ch":5,"stage":"self_check"}` — skip 一个 stage
- `POST /api/pipeline/<book>/rerun` body=`{"ch":5,"from_stage":"extract"}` — 从 stage 重跑
- `POST /api/pipeline/<book>/reset` body=`{"ch":5}` — 清空 ch 5 所有 checkpoint (回 PENDING)

### 5.3 错误码

```
SKIP_DONE: stage 已 DONE, 必须 rerun 才能 skip
SKIP_DURING_RUN: 正在 RUNNING, 不能 skip
RERUN_NO_DOWNSTREAM: from_stage 不存在或已是最后一个 stage
FSM_INVALID: 非法转换
NOT_FOUND: checkpoint 文件不存在
```

## 6. Dashboard UI (改动小)

`review_ui/templates/dashboard.html` 已显示 `current_stage`。加:

1. **Pipeline 进度条** — 7 个 stage chip (✓ DONE / ⚠ FAILED / ⟳ RUNNING / ○ PENDING / ⊘ SKIPPED)
2. **每 stage 详情弹窗** — 点击 chip 显示 tokens / artifacts / error
3. **Skip 按钮** — 仅在 `StageState.FAILED` 时显示
4. **Rerun from here 按钮** — 仅在 `StageState.FAILED` 或 `StageState.DONE` 时显示
5. **Checkpoint 列表** — 默认折叠, 显示历史 ch 的完成度

## 7. 测试 (30+ tests)

`tests/test_pipeline_v2.py`:

```
1. test_load_save_checkpoint
2. test_reset_checkpoints
3. test_stage_state_enum_values
4. test_transition_pending_to_running
5. test_transition_running_to_done
6. test_transition_running_to_failed
7. test_transition_done_to_running_rerun
8. test_transition_pending_to_skipped_raises
9. test_transition_done_to_done_raises
10. test_transition_done_to_skipped_raises
11. test_skip_stage_marks_skipped
12. test_skip_done_stage_raises
13. test_skip_running_stage_raises
14. test_rerun_resets_downstream
15. test_rerun_preserves_upstream_done
16. test_rerun_invalid_stage_raises
17. test_is_chapter_complete_all_done
18. test_is_chapter_complete_with_skip
19. test_is_chapter_complete_with_failure
20. test_get_pipeline_view_returns_stages
21. test_get_pipeline_view_includes_artifacts
22. test_pipeline_error_codes
23. test_checkpoint_persistence_across_loads
24. test_multiple_chapters_independent
25. test_v1_compat_state_json_unchanged
26. test_corrupt_checkpoint_recovery  (JSON 损坏回 PENDING)
27. test_skip_then_rerun_chain
28. test_rerun_from_done_stage
29. test_atomic_save_no_partial_write
30. test_concurrent_save_safety  (last-write-wins 文档化)
```

## 8. 不在范围内 (out of scope)

- 并发多书多章 pipeline (v1 已支持, v2 不变)
- 阶段内 retry / backoff (走 v1 LLM 重试)
- 跨 stage 缓存 (未来 v1.3)
- WebUI 实时推送 (SSE 已 v1, v2 复用)
- checkpoint 压缩 / 归档 (项目级已 backup)

## 9. 里程碑 (M5 子阶段)

| 子阶段 | 内容 | 测试数 | 估计 |
|--------|------|--------|------|
| M5.1 | lib/pipeline_v2.py 核心 (FSM + Checkpoint + Skip/Rerun) | 25 | 2h |
| M5.2 | chapter.py 集成 (5 处 transition 调用) | 3 (regression) | 30min |
| M5.3 | review_ui/app.py 加 4 个 API | 6 | 1h |
| M5.4 | dashboard.html pipeline chip + 按钮 | 4 | 1h |
| M5.5 | docs 同步 (DESIGN + 进展追踪 + CHANGELOG) | - | 30min |

总: ~30 tests, 5 commits, 半天.

## 10. 风险 + 缓解

| 风险 | 缓解 |
|------|------|
| checkpoint 写入与 v1 LLM callback 竞争 | 用 atomic write (temp + rename) |
| 老项目没 checkpoint 文件 | load 时回 None, 当 PENDING 处理 |
| 多章并发 (用户开 2 个 subprocess) | per-chapter 隔离 (key 用 `stages: {ch: {...}}` 不用 dict-by-stage) |
| dashboard UI 改坏老功能 | M5.4 测试跑 dashboard_api 全套 + 视觉确认 |
| self_check skip 导致后续章节状态不对 | skip 需输入 `--reason` + 审计日志 |

## 11. 验收

- [ ] 30/30 tests pass
- [ ] `novel.py write` 跑完一章, `.pipeline_checkpoints.json` 7 个 stage 全 DONE
- [ ] `POST /api/pipeline/<book>/skip?ch=5&stage=self_check` 成功, 后续章节正常
- [ ] `POST /api/pipeline/<book>/rerun?ch=5&from=extract` 触发新 subprocess, 上游 DONE 保留
- [ ] dashboard.html 显示 7 chip + Skip/Rerun 按钮
- [ ] v1 老 API (`/status`, `/start`, `/cancel`) 不破坏