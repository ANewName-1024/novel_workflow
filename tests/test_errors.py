"""test_errors.py - ErrorCode + NovelError 单测."""
import pytest
from lib.errors import ErrorCode, NovelError, exit_code_from_error


class TestErrorCode:
    def test_all_codes_int_value(self):
        """IntEnum 的值就是 sys.exit() 的退出码."""
        assert int(ErrorCode.OK) == 0
        assert int(ErrorCode.GENERIC) == 1
        assert int(ErrorCode.INVALID_ARGS) == 2
        assert int(ErrorCode.NOT_FOUND) == 3
        assert int(ErrorCode.LLM_FAILURE) == 4
        assert int(ErrorCode.CONFIG_ERROR) == 5
        assert int(ErrorCode.IO_ERROR) == 6
        assert int(ErrorCode.AUTH_ERROR) == 7
        assert int(ErrorCode.REVIEW_FAILED) == 8

    def test_codes_are_unique(self):
        """避免新增 code 时撞值."""
        values = [int(c) for c in ErrorCode]
        assert len(values) == len(set(values))


class TestNovelError:
    def test_basic_construction(self):
        e = NovelError(ErrorCode.NOT_FOUND, "项目不存在")
        assert e.code == ErrorCode.NOT_FOUND
        assert e.message == "项目不存在"
        assert e.detail == ""  # 默认无 detail
        # str() 含 code name + message
        assert "NOT_FOUND" in str(e)
        assert "项目不存在" in str(e)

    def test_with_detail(self):
        e = NovelError(ErrorCode.LLM_FAILURE, "生成失败", detail="openai.RateLimitError")
        assert e.detail == "openai.RateLimitError"
        assert "openai.RateLimitError" in str(e)

    def test_exit_code_int_compatible(self):
        """IntEnum 本身就是 int 子类, 可直接 sys.exit()."""
        e = NovelError(ErrorCode.NOT_FOUND, "x")
        # int() 转换 OK
        assert int(e.code) == 3
        # 直接当 int 用 OK (IntEnum 行为)
        assert e.code + 1 == 4

    def test_exit_code_from_error_normalizes(self):
        """helper: ErrorCode / int 都规整为 int."""
        assert exit_code_from_error(ErrorCode.NOT_FOUND) == 3
        assert exit_code_from_error(ErrorCode.NOT_FOUND) == int(ErrorCode.NOT_FOUND)
        assert exit_code_from_error(42) == 42

    def test_catchable_as_novel_error(self):
        """raise NovelError 可被 except NovelError 捕获."""
        with pytest.raises(NovelError) as exc_info:
            raise NovelError(ErrorCode.INVALID_ARGS, "参数错")
        assert exc_info.value.code == ErrorCode.INVALID_ARGS
        # 不被通用 Exception 漏过 (Exception 是 NovelError 的父类, 也会被抓, 但 code 还在)
        try:
            raise NovelError(ErrorCode.NOT_FOUND, "not found")
        except Exception as e:
            assert isinstance(e, NovelError)
            assert e.code == ErrorCode.NOT_FOUND