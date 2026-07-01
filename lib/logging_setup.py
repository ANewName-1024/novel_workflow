"""
logging_setup.py - 单例日志配置

设计:
- 整个进程共用一个 root logger 配置 (单例, 多次调用不重复加 handler)
- 同时输出 stdout + RotatingFileHandler
- 默认配置从 config_loader 读 (level/file/rotation), 也可显式覆盖
- rotation 格式: "10MB x 5" (size x backups), 支持 B/KB/MB/GB

子模块用 logging.getLogger(__name__) 拿 logger, 不要再 setup.
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

_configured = False


def _parse_rotation(spec):
    """Parse "10MB x 5" -> (10*1024*1024, 5). 默认 (10MB, 5)."""
    s = (spec or "").strip()
    if not s:
        return 10 * 1024 * 1024, 5
    m = re.match(r"^\s*(\d+)\s*([KMGT]?B)?\s*[xX]\s*(\d+)\s*$", s)
    if not m:
        return 10 * 1024 * 1024, 5
    size = int(m.group(1))
    unit = m.group(2) or "B"
    mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}.get(unit, 1)
    backups = int(m.group(3))
    return size * mult, backups


def setup_logging(
    level=None,
    log_file=None,
    rotation=None,
    *,
    force=False,
    console=True,
):
    """配置 root logger (单例).

    Args:
        level: "DEBUG"/"INFO"/"WARNING"/... 默认从 config_loader 读
        log_file: 相对路径 (相对 novel_workflow/) 或绝对路径; None=不写文件
        rotation: "10MB x 5" 格式; 默认从 config_loader 读
        force: True 时清掉已有 handlers 重新配置 (用于测试)
        console: True 时同时输出 stdout
    """
    global _configured
    if _configured and not force:
        return logging.getLogger()

    # 从 config_loader 读默认值 (避免循环 import: 延迟 import)
    cfg_log = {}
    try:
        from lib.config_loader import get_config
        cfg_log = get_config().get("logging", {}) or {}
    except Exception:
        cfg_log = {}

    level = (level or cfg_log.get("level") or "INFO").upper()
    log_file = log_file if log_file is not None else cfg_log.get("file", "logs/novel_workflow.log")
    rotation = rotation or cfg_log.get("rotation", "10MB x 5")

    lvl = getattr(logging, level, None)
    if not isinstance(lvl, int):
        lvl = logging.INFO

    root = logging.getLogger()
    root.setLevel(lvl)

    # force 模式: 清掉已有 handlers
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
        _configured = False

    if _configured:
        return root

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(lvl)
        root.addHandler(sh)

    if log_file:
        p = Path(log_file)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        size_bytes, backups = _parse_rotation(rotation)
        fh = RotatingFileHandler(
            str(p),
            maxBytes=size_bytes,
            backupCount=backups,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        root.addHandler(fh)

    # 抑制 openai/urllib3 的啰嗦 INFO (他们都打 WARNING/ERROR 即可)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _configured = True
    return root


def reset_logging():
    """测试时清配置 + 清 handlers."""
    global _configured
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    _configured = False


def is_configured():
    return _configured