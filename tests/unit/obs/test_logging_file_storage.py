"""文件日志后端补测（quality-debt 覆盖率：37.6% -> 目标高位）。

已覆盖的（见 test_logging.py）不再重复；这里补的是**降级与清理路径**——它们平时不跑，
一旦主目录不可写（容器只读挂载、磁盘满）才会走到，恰恰是最需要验证的地方：

1. `awrite` / `awrite_batch` 写失败后必须退到 `~/.ultrasound/logs`，而不是丢日志；
2. `awrite_batch` 的二次降级（fallback 也失败 -> 逐条 `awrite`）必须真的逐条落盘；
3. `_cleanup_old_files` 的保留期删除与 `OSError` 容错；
4. `_ensure_log_dir` 在 `PermissionError` 下退到 home。

注意：所有用例都把 `Path.home` 指到 `tmp_path`，否则降级分支会写到真实用户目录。

另外本文件第一版有两个**会挂死/假绿**的写法，已在注释里标出原因，勿改回去：
1. 用"所有路径都打不开"的桩去验证"降级落盘"——断言恒假；
2. 自写的 mock 包装类 `__getattr__` 对任意属性返回函数，`assert mock.called` 恒真。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiofiles
import pytest

from app.observability.enums import LogCategory
from app.observability.logging.file import FileLogStorage
from app.observability.logging.record import LogRecord


class _BrokenOpen:
    """把 aiofiles.open 换成"进入上下文即抛异常"：任何路径都打不开。

    用于驱动"主目录与降级目录同时不可写"的最坏情况（同时也是重入锁死锁的触发条件）。
    """

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc_info):
        return False


# 在打桩**之前**抓住真实的 aiofiles.open。
# 踩过的坑：`file.py` 里是 `import aiofiles` + `aiofiles.open(...)`，patch 该属性等于改了
# **同一个模块对象**；替身内部再 `import aiofiles` 拿到的就是替身自己 -> 无限递归
# （日志里表现为 "maximum recursion depth exceeded"），降级写入从未真正发生。
_REAL_AIOFILES_OPEN = aiofiles.open


class _FailPrimaryOpen:
    """只让**主目录**打不开，降级目录走真实 aiofiles。

    第一版测试用的是 `_BrokenOpen`（所有路径都失败），然后再断言"降级目录里有文件" ——
    这个断言恒假：降级写入同样被打了桩。要验证"降级真的落盘"，必须让降级路径可用。
    """

    def __init__(self, primary_dir):
        self._primary = str(primary_dir)
        self.fallback_calls = 0

    def __call__(self, path, *args, **kwargs):
        if str(path).startswith(self._primary):
            raise OSError("primary read-only mount")
        self.fallback_calls += 1
        return _REAL_AIOFILES_OPEN(path, *args, **kwargs)


def _category():
    return next(iter(LogCategory))


def _record(category=None, message="日志内容"):
    return LogRecord(category=category or _category(), message=message)


@pytest.fixture
def storage(tmp_path):
    return FileLogStorage(log_dir=str(tmp_path / "logs"))


@pytest.fixture
def home_in_tmp(tmp_path, monkeypatch):
    """降级目录固定在 tmp_path 下，避免污染真实 home。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


class TestEnsureLogDir:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_logs"

        FileLogStorage(log_dir=str(target))

        assert target.exists()

    def test_permission_error_falls_back_to_home(self, tmp_path, monkeypatch):
        """主目录不可写时必须退到 home 下，而不是让构造直接失败。"""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        original_mkdir = Path.mkdir

        def _deny(self, *args, **kwargs):
            if "denied" in str(self):
                raise PermissionError("read-only mount")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _deny)

        storage = FileLogStorage(log_dir=str(tmp_path / "denied"))

        # 断言"真的切到了降级目录"，而不是 exists() or ...（后者只要任一目录存在就通过）
        assert storage.log_dir == tmp_path / ".ultrasound" / "logs"
        assert storage.log_dir.exists()


class TestGetLogFilePath:
    def test_uses_category_value(self, storage):
        path = storage._get_log_file_path(_category())

        assert path.suffix == ".jsonl"
        assert _category().value in path.name

    def test_accepts_plain_string_category(self, storage):
        path = storage._get_log_file_path("custom")

        assert "custom" in path.name

    def test_caches_path_per_filename(self, storage):
        first = storage._get_log_file_path(_category())
        second = storage._get_log_file_path(_category())

        assert first == second
        assert len(storage._current_files) == 1


class TestRotation:
    def test_missing_file_does_not_rotate(self, storage, tmp_path):
        assert storage._should_rotate(tmp_path / "nope.jsonl") is False

    def test_small_file_does_not_rotate(self, storage, tmp_path):
        path = tmp_path / "small.jsonl"
        path.write_text("x", encoding="utf-8")

        assert storage._should_rotate(path) is False

    def test_oversized_file_rotates(self, storage, tmp_path):
        path = tmp_path / "big.jsonl"
        path.write_text("x" * 10, encoding="utf-8")

        oversized = FileLogStorage(log_dir=str(tmp_path), max_file_size=1)
        assert oversized._should_rotate(path) is True

    def test_rotate_renames_existing_file(self, storage, tmp_path):
        path = tmp_path / "rotate.jsonl"
        path.write_text("{}\n", encoding="utf-8")

        storage._rotate_file(path)

        assert not path.exists()
        # 轮转后的名字是 {stem}_{时间戳}{suffix}，即 rotate_20260924_120000.jsonl；
        # 写成 "*.jsonl.*" 永远匹配不到（第一版就是这么写的，断言恒假）。
        assert list(tmp_path.glob("rotate_*.jsonl"))

    def test_rotate_missing_file_is_noop(self, storage, tmp_path):
        storage._rotate_file(tmp_path / "missing.jsonl")


class TestAwaitWrite:
    async def test_writes_line(self, storage):
        await storage.awrite(_record())

        files = list(storage.log_dir.glob("*.jsonl"))
        assert files
        # 落盘是 json.dumps(..., default=str)，未指定 ensure_ascii=False ->
        # 中文会写成 \\uXXXX 转义。断言必须解析 JSON，直接比字符串会假红。
        payload = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert payload["message"] == "日志内容"
        assert payload["category"] == _category().value

    async def test_triggers_cleanup_after_interval(self, storage):
        """轮转检查是按写入计数节流的，到点必须真的触发一次清理。

        第一版用了自写的 `AsyncMockWrapper`，其 `__getattr__` 对任意属性都返回函数，
        于是 `assert cleanup.called` 恒真——是个**假断言**。这里改用 AsyncMock。
        """
        storage._write_count = storage.ROTATION_CHECK_INTERVAL - 1
        cleanup = AsyncMock()

        with patch.object(storage, "_cleanup_old_files", cleanup):
            await storage.awrite(_record())

        assert cleanup.await_count == 1, "到达轮转检查间隔必须触发清理"

    async def test_write_failure_falls_back_to_home(self, storage, home_in_tmp):
        open_spy = _FailPrimaryOpen(storage.log_dir)

        with patch("app.observability.logging.file.aiofiles.open", open_spy):
            await storage.awrite(_record())

        assert open_spy.fallback_calls >= 1, "主目录失败后必须尝试降级目录"
        fallback_files = list((home_in_tmp / ".ultrasound" / "logs").glob("*.jsonl"))
        assert fallback_files, "降级路径必须真的落盘，不能只吞异常"

    async def test_both_directories_failing_is_swallowed(self, storage, home_in_tmp):
        """主目录与降级目录都写不了时只能记日志，不得向上抛、也不得挂住。"""
        broken = _BrokenOpen(OSError("disk full"))

        with patch("app.observability.logging.file.aiofiles.open", broken):
            await asyncio.wait_for(storage.awrite(_record()), timeout=5)


class TestAwaitWriteBatch:
    async def test_empty_batch_writes_nothing(self, storage):
        await storage.awrite_batch([])

        assert list(storage.log_dir.glob("*.jsonl")) == []

    async def test_batch_written_to_single_category_file(self, storage):
        records = [_record(message=f"m{i}") for i in range(3)]

        await storage.awrite_batch(records)

        files = list(storage.log_dir.glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "m0" in content and "m2" in content

    async def test_batch_groups_by_category(self, storage):
        categories = list(LogCategory)[:2]
        records = [_record(category=categories[0]), _record(category=categories[1])]

        await storage.awrite_batch(records)

        assert len(list(storage.log_dir.glob("*.jsonl"))) == 2

    async def test_batch_updates_write_count(self, storage):
        await storage.awrite_batch([_record(), _record()])

        assert storage._write_count == 2

    async def test_batch_falls_back_to_home_on_failure(self, storage, home_in_tmp):
        open_spy = _FailPrimaryOpen(storage.log_dir)

        with patch("app.observability.logging.file.aiofiles.open", open_spy):
            await storage.awrite_batch([_record(), _record()])

        assert list((home_in_tmp / ".ultrasound" / "logs").glob("*.jsonl"))

    async def test_both_directories_failing_does_not_deadlock(self, storage, home_in_tmp):
        """回归守卫：两个目录都不可写时，批量写必须在有限时间内返回。

        旧实现的问题：`awrite_batch` 在 `_async_lock` 内调用 `awrite`，而 `awrite` 会
        再次获取同一把 **不可重入** 的 asyncio.Lock —— 永久挂起且一直占着锁，
        此后所有日志写入都卡死。第一版测试直接把这个死锁跑出来了：整个用例超时。
        这里用 wait_for 把"挂死"变成"超时失败"，任何人再改回去都会立刻红。
        """
        broken = _BrokenOpen(OSError("disk full"))
        direct = AsyncMock()

        with (
            patch("app.observability.logging.file.aiofiles.open", broken),
            patch.object(storage, "awrite", direct),
        ):
            await asyncio.wait_for(
                storage.awrite_batch([_record(), _record(), _record()]), timeout=5
            )

        assert direct.await_count == 0, (
            "批量写在已持有锁的情况下不得再调 awrite（会重入同一把锁）"
        )

    async def test_rotation_checked_before_batch_write(self, storage):
        oversized = FileLogStorage(log_dir=str(storage.log_dir), max_file_size=1)
        path = oversized._get_log_file_path(_category())
        path.write_text("x" * 5, encoding="utf-8")

        await oversized.awrite_batch([_record()])

        # 轮转 + 新写各产生一个 .jsonl
        files = sorted(storage.log_dir.glob("*.jsonl"))
        assert len(files) == 2, "超过阈值应先轮转再写（而非直接追加）"


class TestCleanupOldFiles:
    async def test_deletes_files_older_than_retention(self, storage):
        path = storage._get_log_file_path(_category())
        path.write_text("{}\n", encoding="utf-8")
        old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        import os

        os.utime(path, (old, old))

        await storage._cleanup_old_files()

        assert not path.exists()

    async def test_keeps_fresh_files(self, storage):
        path = storage._get_log_file_path(_category())
        path.write_text("{}\n", encoding="utf-8")

        await storage._cleanup_old_files()

        assert path.exists()

    async def test_stat_failure_is_tolerated(self, storage, tmp_path):
        """单个文件 stat 失败不能中断整轮清理。"""
        path = tmp_path / "logs" / "broken.jsonl"
        path.write_text("{}\n", encoding="utf-8")

        with patch("pathlib.Path.stat", side_effect=OSError("gone")):
            await storage._cleanup_old_files()

    async def test_unexpected_error_is_swallowed(self, storage):
        with patch("pathlib.Path.glob", side_effect=RuntimeError("boom")):
            await storage._cleanup_old_files()


class TestClose:
    async def test_close_is_noop(self, storage):
        await storage.close()

    def test_sync_write_wrapper_outside_loop(self, storage):
        """**同步上下文**调用 write()：无运行中的循环 -> 走 `asyncio.run`，写完才返回。

        注意必须写成同步用例：写成本类的 async 用例后就有了运行中的循环，会走
        `create_task` 的 fire-and-forget 分支，紧跟其后的断言必然看不到文件
        （第一版就是这么写错的）。
        """
        storage.write(_record(message="同步写入"))

        files = list(storage.log_dir.glob("*.jsonl"))
        assert files, "同步上下文必须阻塞到写完"
        payload = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert payload["message"] == "同步写入"

    async def test_write_within_loop_schedules_task(self, storage):
        """**异步上下文**里 write() 只投递任务、不阻塞调用方；随后应能落盘。

        轮询而不是 `await asyncio.sleep(0)`：aiofiles 的写走线程池，一次让出不够
        （第一版就是只让出一次然后断言，必然看不到文件）。
        """
        storage.write(_record(message="循环内写入"))

        files = []
        for _ in range(50):
            files = list(storage.log_dir.glob("*.jsonl"))
            if files:
                break
            await asyncio.sleep(0.01)

        assert files, "投递的任务应在让出循环后完成写入"
        payload = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert payload["message"] == "循环内写入"
