"""
doctor.py — 环境诊断 (novel doctor)

检测:
- Python 版本 ≥ 3.12
- llama-server 可达 (默认 :60443/v1/models 返回 200)
- 依赖包都装了 (openai / flask / pytest ...)
- 项目目录可写
- 磁盘空间 ≥ 1GB
- 端口 21199 没被占 (review_ui 还没跑)

输出: 9 个 ✅/⚠/❌ + 修复建议
"""
from __future__ import annotations

import os
import sys
import shutil
import socket
import logging
import platform
from pathlib import Path
from typing import NamedTuple

from .config_loader import get_config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

MIN_PY = (3, 12)
MIN_DISK_GB = 1.0


class CheckResult(NamedTuple):
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str


def check_python() -> CheckResult:
    v = sys.version_info
    if v >= MIN_PY:
        return CheckResult("Python 版本", "ok", f"{v.major}.{v.minor}.{v.micro} (≥ {MIN_PY[0]}.{MIN_PY[1]})")
    return CheckResult("Python 版本", "fail", f"{v.major}.{v.minor}.{v.micro} < {MIN_PY[0]}.{MIN_PY[1]}")


def check_deps() -> CheckResult:
    missing = []
    for mod in ("openai", "flask", "yaml"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return CheckResult("依赖", "ok", "openai + flask + yaml 都在")
    return CheckResult("依赖", "fail", f"缺: {', '.join(missing)} (pip install -r requirements.txt)")


def check_llm(cfg: dict) -> CheckResult:
    """探测 llama-server /v1/models."""
    try:
        import urllib.request
        import urllib.error
        api_base = cfg.get("llm", {}).get("api_base", "http://127.0.0.1:60443/v1")
        # api_base 通常 .../v1, 改 .../models
        url = api_base.rstrip("/")
        if not url.endswith("/models"):
            if url.endswith("/v1"):
                url += "/models"
            else:
                url += "/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="ignore")[:200]
            return CheckResult("LLM (llama-server)", "ok", f"{url} → 200 ({len(body)} bytes)")
    except Exception as e:
        return CheckResult("LLM (llama-server)", "fail",
                           f"{cfg.get('llm', {}).get('api_base', 'http://127.0.0.1:60443/v1')} 不可达: {type(e).__name__}: {e}")


def check_disk() -> CheckResult:
    try:
        total, used, free = shutil.disk_usage(ROOT)
        free_gb = free / (1024 ** 3)
        if free_gb >= MIN_DISK_GB:
            return CheckResult("磁盘空间", "ok", f"{free_gb:.1f} GB ≥ {MIN_DISK_GB} GB")
        return CheckResult("磁盘空间", "fail", f"{free_gb:.1f} GB < {MIN_DISK_GB} GB")
    except Exception as e:
        return CheckResult("磁盘空间", "warn", f"无法检测: {e}")


def check_port_free(cfg: dict) -> CheckResult:
    port = cfg.get("review_ui", {}).get("port", 21199)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return CheckResult(f"端口 {port}", "ok", "空闲")
        except OSError:
            return CheckResult(f"端口 {port}", "warn", "已被占用 (review_ui 可能在跑)")


def check_projects_dir(cfg: dict) -> CheckResult:
    root_rel = cfg.get("projects", {}).get("root", "projects")
    p = ROOT / root_rel
    if not p.exists():
        return CheckResult("项目目录", "warn", f"{p} 不存在, 但会自动创建")
    if not os.access(str(p), os.W_OK):
        return CheckResult("项目目录", "fail", f"{p} 不可写")
    return CheckResult("项目目录", "ok", str(p))


def check_git() -> CheckResult:
    git_dir = ROOT / ".git"
    if git_dir.exists():
        return CheckResult("Git", "ok", "已 init")
    return CheckResult("Git", "warn", "未初始化 (可选, 推荐)")


def check_paths() -> CheckResult:
    critical = ["novel.py", "lib/__init__.py", "lib/llm.py", "lib/storage.py",
                "lib/outline.py", "lib/chapter.py", "lib/review_service.py",
                "review_ui/app.py"]
    missing = [p for p in critical if not (ROOT / p).exists()]
    if not missing:
        return CheckResult("关键文件", "ok", f"{len(critical)} 个都在")
    return CheckResult("关键文件", "fail", f"缺: {', '.join(missing)}")


def run_all() -> list[CheckResult]:
    cfg = get_config()
    return [
        check_python(),
        check_paths(),
        check_deps(),
        check_git(),
        check_llm(cfg),
        check_disk(),
        check_port_free(cfg),
        check_projects_dir(cfg),
    ]


def format_report(results: list[CheckResult]) -> str:
    icon = {"ok": "✅", "warn": "⚠ ", "fail": "❌"}
    lines = [f"=== novel doctor ({platform.system()} {platform.release()}) ===", ""]
    for r in results:
        lines.append(f"  {icon[r.status]} {r.name}: {r.detail}")
    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    ok = sum(1 for r in results if r.status == "ok")
    lines.append("")
    lines.append(f"  汇总: ✅ {ok}  ⚠ {warns}  ❌ {fails}")
    return "\n".join(lines)
