"""
pipeline.py — 1 本书的 1 个写章节子进程管理 (v1.1)

设计:
- 1 本 1 进程 (v1.1); v1.2 升级队列
- 跨平台: psutil 杀进程 (Win/Linux/Mac 通吃)
- 状态持久化: projects/<book>/.pipeline_state.json
- 日志: projects/<book>/logs/pipeline.log (subprocess stdout 写入)
- 指标: projects/<book>/metrics.jsonl (append-only, 每行 1 次 LLM 调用)
- 锁: 内存 dict, 防同本书重复 start (单进程内有效)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    import psutil
except ImportError:  # 测试环境可能没装
    psutil = None  # type: ignore

from . import storage
from .errors import ErrorCode, NovelError

# ── 状态文件 schema ─────────────────────────────────────────────────────────

STATE_FILE = ".pipeline_state.json"
LOG_SUBDIR = "logs"
LOG_FILE = "pipeline.log"
METRICS_FILE = "metrics.jsonl"

# 阶段常量
STAGES = ["context", "writing", "extract", "summary", "state", "self_check", "done"]

# 锁: book -> PID
_active: dict[str, int] = {}


# ── 路径 helpers ───────────────────────────────────────────────────────────

def pipeline_state_path(book: str) -> Path:
    return storage.project_root(book) / STATE_FILE

def pipeline_log_dir(book: str) -> Path:
    d = storage.project_root(book) / LOG_SUBDIR
    d.mkdir(exist_ok=True)
    return d

def pipeline_log_path(book: str) -> Path:
    return pipeline_log_dir(book) / LOG_FILE

def metrics_path(book: str) -> Path:
    return storage.project_root(book) / METRICS_FILE


# ── 进程状态检查 ───────────────────────────────────────────────────────────

def _is_pid_alive(pid: int) -> bool:
    """跨平台 PID 存活检查."""
    if pid is None or pid <= 0:
        return False
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False
    # fallback: POSIX only
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_pid_tree(pid: int, grace_sec: float = 5.0) -> bool:
    """跨平台杀进程树 (含子进程), 宽限期 5s."""
    if psutil is None:
        # fallback: POSIX only
        try:
            os.killpg(os.getpgid(pid), 15)  # SIGTERM
        except (OSError, ProcessLookupError):
            return False
        time.sleep(grace_sec)
        try:
            os.killpg(os.getpgid(pid), 9)  # SIGKILL
        except (OSError, ProcessLookupError):
            pass
        return True

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            return True
        # 宽限
        gone, alive = psutil.wait_procs(children + [parent], timeout=grace_sec)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception as e:
        print(f"[pipeline] kill 异常: {e}", file=sys.stderr)
        return False


# ── 状态文件读写 ───────────────────────────────────────────────────────────

def _read_state(book: str) -> Optional[dict[str, Any]]:
    path = pipeline_state_path(book)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(book: str, state: dict[str, Any]) -> None:
    path = pipeline_state_path(book)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── PipelineRunner 主体 ───────────────────────────────────────────────────

class PipelineRunner:
    """管理 1 本书的 1 个写章节子进程."""

    def start(
        self,
        book: str,
        chapter_num: int,
        auto_rewrite: bool = True,
    ) -> dict[str, Any]:
        """启动子进程, 返回初始 state.

        Raises:
            NovelError(NOT_FOUND): 项目不存在
            NovelError(GENERIC): 已有任务在跑
        """
        if not storage.project_exists(book):
            raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 不存在")

        # 检查是否已在跑
        cur = self.status(book)
        if cur and cur.get("status") == "running":
            raise NovelError(
                ErrorCode.GENERIC,
                f"项目 [{book}] 已有任务在跑 (PID={cur.get('pid')})",
            )

        # 启动子进程
        log_path = pipeline_log_path(book)
        # 追加模式, 不清空历史
        log_fp = open(log_path, "ab", buffering=0)

        cmd = [
            sys.executable,
            "novel.py",
            "write",
            book,
            "--chapters",
            str(chapter_num),
        ]
        if auto_rewrite:
            cmd.append("--auto-rewrite-on-critical")

        # Windows: CREATE_NEW_PROCESS_GROUP 方便后面用 psutil.Process 杀
        kwargs: dict[str, Any] = {
            "stdout": log_fp,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "cwd": str(Path(__file__).resolve().parent.parent),
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as e:
            log_fp.close()
            raise NovelError(
                ErrorCode.GENERIC,
                f"启动流水线失败: {e}",
                detail=str(e),
            ) from e

        # 写 state
        state = {
            "book": book,
            "status": "running",
            "pid": proc.pid,
            "started_at": _now_iso(),
            "ended_at": None,
            "current_chapter": chapter_num,
            "current_stage": "context",
            "stage_started_at": _now_iso(),
            "exit_code": None,
            "log_path": str(log_path),
            "error": None,
        }
        _write_state(book, state)
        _active[book] = proc.pid
        return state

    def status(self, book: str) -> Optional[dict[str, Any]]:
        """读 .pipeline_state.json + 校准 PID 状态.

        校准逻辑:
        - status=running 但 PID 已死 → 标 failed (exit_code=-1)
        - status=done/failed/cancelled → 直接返回
        """
        state = _read_state(book)
        if state is None:
            return None

        if state.get("status") == "running":
            pid = state.get("pid")
            if not _is_pid_alive(pid):
                # PID 死了但 state 没更新, 自动校准
                state["status"] = "failed"
                state["ended_at"] = _now_iso()
                state["exit_code"] = -1
                state["error"] = "进程异常退出 (无更新)"
                _write_state(book, state)
                _active.pop(book, None)
        elif book in _active:
            # 不在 running 但 _active 还有, 清理
            _active.pop(book, None)

        return state

    def cancel(self, book: str) -> dict[str, Any]:
        """取消运行中的子进程.

        Raises:
            NovelError(NOT_FOUND): state 不存在
            NovelError(GENERIC): 没有任务在跑
        """
        state = self.status(book)
        if state is None:
            raise NovelError(ErrorCode.NOT_FOUND, f"项目 [{book}] 没有流水线记录")
        if state.get("status") != "running":
            raise NovelError(ErrorCode.GENERIC, f"项目 [{book}] 没有任务在跑 (status={state.get('status')})")

        pid = state.get("pid")
        killed = _kill_pid_tree(pid, grace_sec=5.0)
        # 等 0.5s 让状态落盘
        time.sleep(0.5)
        # 更新 state
        new_state = self.status(book) or state
        new_state["status"] = "cancelled"
        new_state["ended_at"] = _now_iso()
        if not killed:
            new_state["error"] = f"杀进程失败 (PID={pid})"
        _write_state(book, new_state)
        _active.pop(book, None)
        return new_state

    def tail_log(self, book: str, n: int = 100) -> list[str]:
        """读 log 文件最后 N 行."""
        path = pipeline_log_path(book)
        if not path.exists():
            return []
        try:
            # 简单实现: 读全部行, 取最后 N
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-n:]
        except OSError:
            return []

    def stream_log(self, book: str, poll_interval: float = 1.0) -> Iterator[str]:
        """SSE 用: 持续 yield log 新行. 客户端断开时抛 GeneratorExit 停止.

        用文件 inode + size 跟踪位置, 避免重复读.
        退出条件: state 状态是 done/failed/cancelled OR state 文件不存在.
        """
        path = pipeline_log_path(book)

        # 没 state = 没流水线在跑, 立即退出
        state0 = self.status(book)
        if state0 is None:
            return

        # 没 log 文件 = 还没写一行, 等 1 次
        if not path.exists():
            time.sleep(poll_interval)
            state0 = self.status(book)
            if state0 is None or state0.get("status") in ("done", "failed", "cancelled"):
                return

        # 初始位置: 文件末尾 (SSE 客户端不应该看历史)
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)  # 末尾
                last_pos = f.tell()
        except OSError:
            return

        while True:
            try:
                with open(path, "rb") as f:
                    f.seek(last_pos)
                    chunk = f.read()
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    for line in text.splitlines():
                        if line:
                            yield line + "\n"
                    last_pos += len(chunk)
                # 检查进程状态, 跑完就退出
                state = self.status(book)
                if state is None:
                    return  # state 被删了, 退出
                if state.get("status") in ("done", "failed", "cancelled"):
                    # 再 yield 一次残留 log
                    time.sleep(0.3)
                    try:
                        with open(path, "rb") as f:
                            f.seek(last_pos)
                            tail = f.read()
                        if tail:
                            text = tail.decode("utf-8", errors="replace")
                            for line in text.splitlines():
                                if line:
                                    yield line + "\n"
                    except OSError:
                        pass
                    return
                time.sleep(poll_interval)
            except GeneratorExit:
                return
            except Exception as e:
                yield f"[pipeline log stream error: {e}]\n"
                time.sleep(poll_interval)

    # ── metrics ────────────────────────────────────────────────────────────

    def append_metric(
        self,
        book: str,
        stage: str,
        ch: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
    ) -> None:
        """append 一行到 metrics.jsonl. 由 chapter.py 通过 LLM callback 调用."""
        path = metrics_path(book)
        rec = {
            "ts": _now_iso(),
            "stage": stage,
            "ch": ch,
            "model": model,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "latency_ms": round(float(latency_ms), 1),
        }
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        # append 模式, 进程退出时可能并发, 用 try/except 容错
        try:
            with open(path, "ab") as f:
                f.write(line.encode("utf-8"))
        except OSError as e:
            print(f"[pipeline] append_metric 失败: {e}", file=sys.stderr)

    def get_metrics(self, book: str, range_str: str = "all") -> dict[str, Any]:
        """聚合 metrics.jsonl, 返回按 chapter 分组的 token 用量.

        Args:
            range_str: "all" | "7d" | "1d"
        """
        path = metrics_path(book)
        if not path.exists():
            return {"chapters": [], "total_in": 0, "total_out": 0, "calls": 0}

        # 解析时间范围
        now = datetime.now()
        if range_str == "1d":
            cutoff = now.timestamp() - 86400
        elif range_str == "7d":
            cutoff = now.timestamp() - 7 * 86400
        else:
            cutoff = 0

        per_ch: dict[int, dict[str, int]] = {}
        total_in = total_out = calls = 0
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 时间过滤
            try:
                ts = datetime.fromisoformat(rec.get("ts", "")).timestamp()
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                continue

            ch = rec.get("ch", 0)
            in_t = rec.get("input_tokens", 0)
            out_t = rec.get("output_tokens", 0)
            if ch not in per_ch:
                per_ch[ch] = {"in": 0, "out": 0, "calls": 0, "latency_ms": 0}
            per_ch[ch]["in"] += in_t
            per_ch[ch]["out"] += out_t
            per_ch[ch]["calls"] += 1
            per_ch[ch]["latency_ms"] += rec.get("latency_ms", 0)
            total_in += in_t
            total_out += out_t
            calls += 1

        chapters = []
        for ch in sorted(per_ch.keys()):
            d = per_ch[ch]
            chapters.append({
                "ch": ch,
                "input_tokens": d["in"],
                "output_tokens": d["out"],
                "calls": d["calls"],
                "avg_latency_ms": round(d["latency_ms"] / d["calls"], 1) if d["calls"] else 0,
            })
        return {
            "chapters": chapters,
            "total_in": total_in,
            "total_out": total_out,
            "calls": calls,
        }


# ── 单例 ───────────────────────────────────────────────────────────────────

_default_runner: Optional[PipelineRunner] = None


def get_runner() -> PipelineRunner:
    """全局单例 (v1.1 不需要多实例)."""
    global _default_runner
    if _default_runner is None:
        _default_runner = PipelineRunner()
    return _default_runner
