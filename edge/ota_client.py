"""T-GB-EDG-01 边缘端 OTA 推送客户端（Token 认证 + 断点续传 + 限速）。

规格（`开发计划.md` T-GB-EDG-01）：2GB 模型 34 分钟完成 →
约 1.0 MiB/s。因此默认限速取 ``1 MiB/s``（2 GiB / 2048s ≈ 1.048 MiB/s），
既满足验收时长，又不会把院内上行带宽打满影响诊断业务。

三个能力对应三个现实约束：
- **Token 认证**：复用 ``EdgeDeviceAuth`` 的 HKDF 派生短期 token；
- **断点续传**：模型动辄 GB 级，链路中断重传代价高 → ``Range`` 续传，
  进度落在 ``*.part``，完成后原子改名，避免半成品被当成可用模型；
- **限速**：按写入量计算"应到达时间"，用 sleep 补齐差额（匀速模型）。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Optional, Protocol

import structlog

from edge.device_auth import EdgeDeviceAuth

logger = structlog.get_logger(__name__)

# 2 GiB / 34 min ≈ 1.048 MiB/s
DEFAULT_RATE_LIMIT_BYTES_PER_SECOND = 1024 * 1024
DEFAULT_CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_RETRIES = 3
MIB = 1024 * 1024


class OTAError(Exception):
    """OTA 下载失败。"""


class ByteStreamFactory(Protocol):
    """按 Range 拉取字节流（可注入，便于测试）。

    实现需在 ``start_byte > 0`` 时带上 ``Range: bytes=<start_byte>-``；
    服务端忽略 Range（返回 200 全量）时实现方应自行跳过前 ``start_byte`` 字节。
    """

    def __call__(
        self, url: str, headers: Dict[str, str], start_byte: int
    ) -> AsyncIterator[bytes]:
        ...


async def _httpx_stream(
    url: str, headers: Dict[str, str], start_byte: int
) -> AsyncIterator[bytes]:
    """默认实现：httpx 流式下载，支持 Range 续传。"""
    import httpx

    request_headers = dict(headers)
    if start_byte > 0:
        request_headers["Range"] = f"bytes={start_byte}-"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None)) as client:
        async with client.stream("GET", url, headers=request_headers) as response:
            if response.status_code not in (200, 206):
                raise OTAError(
                    f"OTA 下载失败: HTTP {response.status_code} {url}"
                )
            resumed = response.status_code == 206
            skipped = 0
            async for chunk in response.aiter_bytes(DEFAULT_CHUNK_SIZE):
                if not resumed and skipped < start_byte:
                    # 服务端不支持 Range，丢弃已下载部分
                    remaining = start_byte - skipped
                    if len(chunk) <= remaining:
                        skipped += len(chunk)
                        continue
                    chunk = chunk[remaining:]
                    skipped = start_byte
                if chunk:
                    yield chunk


@dataclass
class OTAProgress:
    """OTA 下载结果/进度。"""

    package: str
    version: str
    total_bytes: int
    downloaded_bytes: int
    resumed_from_bytes: int
    elapsed_seconds: float
    completed: bool
    retries: int = 0
    sha256_ok: Optional[bool] = None

    @property
    def throughput_bytes_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.downloaded_bytes / self.elapsed_seconds

    def as_dict(self) -> Dict[str, Any]:
        return {
            "package": self.package,
            "version": self.version,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "resumed_from_bytes": self.resumed_from_bytes,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "throughput_mib_per_second": round(
                self.throughput_bytes_per_second / MIB, 3
            ),
            "completed": self.completed,
            "retries": self.retries,
            "sha256_ok": self.sha256_ok,
        }


class OTAClient:
    """边缘端 OTA 下载客户端（断点续传 + 限速 + 完整性校验）。"""

    def __init__(
        self,
        base_url: str,
        auth: EdgeDeviceAuth,
        *,
        download_dir: str | Path,
        rate_limit_bytes_per_second: int = DEFAULT_RATE_LIMIT_BYTES_PER_SECOND,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        stream_factory: Optional[ByteStreamFactory] = None,
        sleeper: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_limit_bytes_per_second < 0:
            raise ValueError("rate_limit_bytes_per_second 不能为负")
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须为正数")

        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._download_dir = Path(download_dir)
        self._rate_limit = rate_limit_bytes_per_second
        self._chunk_size = chunk_size
        self._max_retries = max_retries
        self._stream_factory = stream_factory or _httpx_stream
        self._sleeper = sleeper
        self._clock = clock

    @property
    def base_url(self) -> str:
        return self._base_url

    def package_url(self, package: str, version: str) -> str:
        return f"{self._base_url}/api/v1/admin/device/ota/{package}/{version}"

    def target_path(self, package: str, version: str) -> Path:
        return self._download_dir / f"{package}-{version}.bin"

    def part_path(self, package: str, version: str) -> Path:
        return self._download_dir / f"{package}-{version}.bin.part"

    async def download(
        self,
        package: str,
        version: str,
        *,
        expected_sha256: Optional[str] = None,
        total_bytes_hint: int = 0,
    ) -> OTAProgress:
        """下载（或续传）一个 OTA 包。

        Raises:
            OTAError: 重试耗尽或校验失败。
        """
        self._download_dir.mkdir(parents=True, exist_ok=True)
        part = self.part_path(package, version)
        target = self.target_path(package, version)
        url = self.package_url(package, version)

        resumed_from = part.stat().st_size if part.exists() else 0
        started_at = self._clock()
        retries = 0

        while True:
            written = part.stat().st_size if part.exists() else 0
            try:
                await self._stream_to_part(
                    url=url,
                    part=part,
                    start_byte=written,
                    started_at=started_at,
                )
                break
            except Exception as e:
                retries += 1
                logger.warning(
                    "edge_ota_chunk_failed",
                    package=package,
                    version=version,
                    retries=retries,
                    written_bytes=written,
                    error=str(e),
                )
                if retries >= self._max_retries:
                    raise OTAError(
                        f"OTA 下载重试 {retries} 次仍失败: {package}@{version}: {e}"
                    ) from e
                # 断点续传：下一轮从当前已写入字节继续

        downloaded = part.stat().st_size
        sha256_ok: Optional[bool] = None
        if expected_sha256:
            sha256_ok = self._verify_sha256(part, expected_sha256)
            if not sha256_ok:
                part.unlink(missing_ok=True)
                raise OTAError(
                    f"OTA 包完整性校验失败（sha256 不匹配）: {package}@{version}"
                )

        # 原子改名：只有完整校验通过才出现目标文件，避免半成品被加载
        part.replace(target)

        progress = OTAProgress(
            package=package,
            version=version,
            total_bytes=total_bytes_hint or downloaded,
            downloaded_bytes=downloaded,
            resumed_from_bytes=resumed_from,
            elapsed_seconds=self._clock() - started_at,
            completed=True,
            retries=retries,
            sha256_ok=sha256_ok,
        )
        logger.info("edge_ota_completed", **progress.as_dict())
        return progress

    async def _stream_to_part(
        self, *, url: str, part: Path, start_byte: int, started_at: float
    ) -> None:
        """把字节流写入 ``*.part``，并做匀速限速。"""
        written = start_byte
        headers = self._auth.build_headers()

        with part.open("ab") as handle:
            async for chunk in self._stream_factory(url, headers, start_byte):
                handle.write(chunk)
                written += len(chunk)
                await self._throttle(written, started_at)

        if written == start_byte and start_byte > 0:
            # 一个字节都没拿到（服务端 416 / 空响应）→ 视为失败交给重试
            raise OTAError("未收到任何数据（可能 Range 不被支持）")

    async def _throttle(self, written: int, started_at: float) -> None:
        """匀速限速：按已写字节计算应到达时间，用 sleep 补齐差额。"""
        if self._rate_limit <= 0:
            return
        expected = written / self._rate_limit
        actual = self._clock() - started_at
        if actual < expected:
            await self._sleeper(expected - actual)

    @staticmethod
    def _verify_sha256(path: Path, expected: str) -> bool:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().lower() == expected.lower()


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RATE_LIMIT_BYTES_PER_SECOND",
    "ByteStreamFactory",
    "OTAError",
    "OTAProgress",
    "OTAClient",
]
