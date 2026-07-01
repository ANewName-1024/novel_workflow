"""test_logging.py - logging_setup 单测."""
import logging
from pathlib import Path

import pytest

from lib.logging_setup import (
    _parse_rotation,
    is_configured,
    reset_logging,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """每个 case 前清配置, 避免污染."""
    reset_logging()
    yield
    reset_logging()


class TestParseRotation:
    def test_default_empty(self):
        """空串走默认."""
        size, backups = _parse_rotation("")
        assert size == 10 * 1024 * 1024
        assert backups == 5

    def test_mb(self):
        size, backups = _parse_rotation("10MB x 5")
        assert size == 10 * 1024 * 1024
        assert backups == 5

    def test_kb(self):
        size, backups = _parse_rotation("512KB x 3")
        assert size == 512 * 1024
        assert backups == 3

    def test_gb(self):
        size, backups = _parse_rotation("1GB x 2")
        assert size == 1024 ** 3
        assert backups == 2

    def test_lowercase_x(self):
        size, backups = _parse_rotation("1MB x 7")
        assert size == 1024 ** 2
        assert backups == 7

    def test_bad_format_falls_back(self):
        """乱七八糟的格式不抛, 用默认."""
        size, backups = _parse_rotation("garbage")
        assert size == 10 * 1024 ** 2
        assert backups == 5


class TestSetupLogging:
    def test_singleton_no_duplicate_handlers(self, tmp_path):
        """重复 setup 不重复加 handler."""
        log_file = tmp_path / "test.log"
        setup_logging(level="INFO", log_file=str(log_file), console=False)
        n1 = len(logging.getLogger().handlers)
        setup_logging(level="INFO", log_file=str(log_file), console=False)
        n2 = len(logging.getLogger().handlers)
        assert n1 == n2, f"handlers 重复添加: {n1} -> {n2}"
        assert is_configured()

    def test_file_handler_writes_to_disk(self, tmp_path):
        """实际写日志到文件."""
        log_file = tmp_path / "test.log"
        setup_logging(level="INFO", log_file=str(log_file), console=False)
        log = logging.getLogger("test.mod")
        log.info("hello world")
        # 强制 flush
        for h in logging.getLogger().handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "hello world" in content
        assert "test.mod" in content

    def test_force_clears_old_handlers(self, tmp_path):
        """force=True 清掉旧 handlers 重新配置."""
        setup_logging(level="INFO", log_file=str(tmp_path / "a.log"), console=False)
        old_handlers = list(logging.getLogger().handlers)
        setup_logging(level="DEBUG", log_file=str(tmp_path / "b.log"), console=False, force=True)
        # force 后 handler 列表应该是新一组 (数量可能相同但引用不同)
        new_handlers = list(logging.getLogger().handlers)
        assert old_handlers != new_handlers, "force 没清掉旧 handlers"

    def test_reset_clears_singleton(self, tmp_path):
        """reset 后再 setup 重新生效."""
        setup_logging(level="INFO", log_file=str(tmp_path / "a.log"), console=False)
        assert is_configured()
        reset_logging()
        assert not is_configured()
        setup_logging(level="DEBUG", log_file=str(tmp_path / "b.log"), console=False)
        assert is_configured()

    def test_level_invalid_falls_back_to_info(self, tmp_path):
        """无效 level 字符串不抛, 落到 INFO."""
        setup_logging(level="INVALID", log_file=str(tmp_path / "x.log"), console=False)
        assert logging.getLogger().level == logging.INFO