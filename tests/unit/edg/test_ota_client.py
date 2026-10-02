"""T-GB-EDG-01 边缘端 OTA 客户端（断点续传 + 限速 + 校验）单测。"""
import hashlib
from collections.abc import AsyncIterator

import pytest

from edge.device_auth import HEADER_DEVICE_ID, EdgeDeviceAuth
from edge.ota_client import OTAClient, OTAError

pytestmark = pytest.mark.track_edg

PACKAGE = "nav-model"
VERSION = "v1.2.0"


class _StreamRecorder:
    """可注入的字节流工厂：记录调用参数，按脚本产出分片或抛错。"""

    def __init__(self, chunks=None, failures=0):
        self.calls: list[dict[str, object]] = []
        self.chunks = chunks if chunks is not None else [b"x" * 1024] * 3
        self.remaining_failures = failures

    def __call__(self, url: str, headers: dict[str, str], start_byte: int):
        self.calls.append({"url": url, "headers": headers, "start_byte": start_byte})

        async def _gen() -> AsyncIterator[bytes]:
            if self.remaining_failures > 0:
                self.remaining_failures -= 1
                raise ConnectionError("stream interrupted")
            for chunk in self.chunks:
                yield chunk

        return _gen()


class _FakeClock:
    """可控假时钟：配合 sleeper 推进，让限速断言可预测。"""

    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _SleeperRecorder:
    """记录 sleep 时长，并推进假时钟（模拟真实等待）。"""

    def __init__(self, clock: "_FakeClock | None" = None):
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._clock is not None:
            self._clock.advance(delay)


def _auth():
    return EdgeDeviceAuth(device_id="dev_edge_001", api_key="edge-api-key-0123456789abcdef")


def _client(tmp_path, stream, **kwargs):
    clock = _FakeClock()
    sleeper = _SleeperRecorder(clock)  # sleep 会推进假时钟，模拟真实等待
    client = OTAClient(
        "http://backend:8000",
        _auth(),
        download_dir=tmp_path,
        stream_factory=stream,
        sleeper=sleeper,
        clock=clock,
        **kwargs,
    )
    return client, sleeper


class TestPaths:
    def test_package_url(self, tmp_path):
        client, _ = _client(tmp_path, _StreamRecorder())

        assert client.package_url(PACKAGE, VERSION) == (
            "http://backend:8000/api/v1/admin/device/ota/nav-model/v1.2.0"
        )

    def test_target_and_part_paths(self, tmp_path):
        client, _ = _client(tmp_path, _StreamRecorder())

        assert client.target_path(PACKAGE, VERSION).name == "nav-model-v1.2.0.bin"
        assert client.part_path(PACKAGE, VERSION).name == "nav-model-v1.2.0.bin.part"


class TestDownload:
    @pytest.mark.asyncio
    async def test_fresh_download_writes_target_and_clears_part(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"a" * 10, b"b" * 20])
        client, _ = _client(tmp_path, stream)

        progress = await client.download(PACKAGE, VERSION)

        target = client.target_path(PACKAGE, VERSION)
        assert target.read_bytes() == b"a" * 10 + b"b" * 20
        assert client.part_path(PACKAGE, VERSION).exists() is False
        assert progress.completed is True
        assert progress.downloaded_bytes == 30
        assert progress.resumed_from_bytes == 0
        assert stream.calls[0]["start_byte"] == 0

    @pytest.mark.asyncio
    async def test_auth_headers_attached(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"z"])
        client, _ = _client(tmp_path, stream)

        await client.download(PACKAGE, VERSION)

        headers = stream.calls[0]["headers"]
        assert headers[HEADER_DEVICE_ID] == "dev_edge_001"

    @pytest.mark.asyncio
    async def test_resume_from_existing_part(self, tmp_path):
        part = tmp_path / "nav-model-v1.2.0.bin.part"
        part.write_bytes(b"head" * 5)  # 已下载 20 字节
        stream = _StreamRecorder(chunks=[b"tail" * 5])
        client, _ = _client(tmp_path, stream)

        progress = await client.download(PACKAGE, VERSION)

        assert stream.calls[0]["start_byte"] == 20
        assert progress.resumed_from_bytes == 20
        assert client.target_path(PACKAGE, VERSION).read_bytes() == b"head" * 5 + b"tail" * 5

    @pytest.mark.asyncio
    async def test_retry_then_success_keeps_downloaded_bytes(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"data"], failures=1)
        client, _ = _client(tmp_path, stream)

        progress = await client.download(PACKAGE, VERSION)

        # 第一次流中断 → 第二次从 0 继续
        assert len(stream.calls) == 2
        assert stream.calls[1]["start_byte"] == 0
        assert progress.completed is True
        assert progress.retries == 1

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_ota_error(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"data"], failures=99)
        client, _ = _client(tmp_path, stream, max_retries=2)

        with pytest.raises(OTAError):
            await client.download(PACKAGE, VERSION)

        assert len(stream.calls) == 2


class TestIntegrity:
    @pytest.mark.asyncio
    async def test_sha256_match_passes(self, tmp_path):
        payload = b"model-bytes"
        stream = _StreamRecorder(chunks=[payload])
        client, _ = _client(tmp_path, stream)
        digest = hashlib.sha256(payload).hexdigest()

        progress = await client.download(PACKAGE, VERSION, expected_sha256=digest)

        assert progress.sha256_ok is True
        assert client.target_path(PACKAGE, VERSION).exists()

    @pytest.mark.asyncio
    async def test_sha256_mismatch_raises_and_discards_part(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"corrupted"])
        client, _ = _client(tmp_path, stream)

        with pytest.raises(OTAError):
            await client.download(PACKAGE, VERSION, expected_sha256="0" * 64)

        assert client.target_path(PACKAGE, VERSION).exists() is False
        assert client.part_path(PACKAGE, VERSION).exists() is False


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_sleeps_to_match_rate_limit(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"a" * 1024, b"b" * 1024])
        client, sleeper = _client(tmp_path, stream, rate_limit_bytes_per_second=1024)

        await client.download(PACKAGE, VERSION)

        # 固定时钟下 actual=0，每次写入 1KiB 应补 sleep 1s
        assert len(sleeper.delays) == 2
        assert all(abs(delay - 1.0) < 1e-6 for delay in sleeper.delays)

    @pytest.mark.asyncio
    async def test_zero_rate_limit_disables_throttling(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"a" * 1024])
        client, sleeper = _client(tmp_path, stream, rate_limit_bytes_per_second=0)

        await client.download(PACKAGE, VERSION)

        assert sleeper.delays == []

    def test_negative_rate_limit_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            OTAClient(
                "http://backend:8000",
                _auth(),
                download_dir=tmp_path,
                rate_limit_bytes_per_second=-1,
            )

    def test_default_rate_limit_matches_2gib_in_34min(self):
        from edge.ota_client import DEFAULT_RATE_LIMIT_BYTES_PER_SECOND

        # 2 GiB / 34 min ≈ 1.048 MiB/s
        two_gib = 2 * 1024**3
        thirty_four_minutes = 34 * 60
        required = two_gib / thirty_four_minutes

        assert required * 0.98 <= DEFAULT_RATE_LIMIT_BYTES_PER_SECOND
        assert required * 1.02 >= DEFAULT_RATE_LIMIT_BYTES_PER_SECOND


class TestProgressFormatting:
    @pytest.mark.asyncio
    async def test_as_dict_exposes_throughput(self, tmp_path):
        stream = _StreamRecorder(chunks=[b"a" * 1024])
        client, _ = _client(tmp_path, stream)

        progress = await client.download(PACKAGE, VERSION, total_bytes_hint=4096)

        payload = progress.as_dict()
        assert payload["package"] == PACKAGE
        assert payload["total_bytes"] == 4096
        assert payload["completed"] is True
        assert "throughput_mib_per_second" in payload
