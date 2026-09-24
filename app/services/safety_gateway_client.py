import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from app.config.settings import settings

logger = structlog.get_logger()


class SafetyGatewayClient:

    FAST_FAIL_THRESHOLD = settings.safety_gateway_client_fast_fail_threshold

    def __init__(self):
        self._base_url = f"http://{settings.safety_gateway_host}:{settings.safety_gateway_port}"
        self._queue_timeout = getattr(settings, 'safety_gateway_queue_timeout_seconds', 10.0)
        self._retry_interval = 1.0
        self._client: httpx.AsyncClient | None = None
        self._consecutive_failures = 0
        self._lock = asyncio.Lock()
        self._health_probe_interval = settings.safety_gateway_health_probe_interval_seconds
        self._health_probe_task: asyncio.Task | None = None
        self._sg_healthy: bool = True
        self._last_health_check_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        timeout=settings.safety_gateway_http_client_timeout_seconds,
                        limits=httpx.Limits(
                            max_connections=20,
                            max_keepalive_connections=10,
                            keepalive_expiry=30,
                        ),
                    )
        return self._client

    async def warmup(self) -> None:
        try:
            client = await self._get_client()
            await client.get(f"{self._base_url}/health")
            logger.info("safety_gateway_client_warmup_success")
        except Exception as e:
            logger.warning("safety_gateway_client_warmup_failed", error=str(e))

    async def start_health_probe(self) -> None:
        if self._health_probe_task is not None:
            return
        self._health_probe_task = asyncio.create_task(self._health_probe_loop())
        logger.info("safety_gateway_health_probe_started", interval=self._health_probe_interval)

    async def stop_health_probe(self) -> None:
        if self._health_probe_task is not None:
            self._health_probe_task.cancel()
            try:
                await self._health_probe_task
            except asyncio.CancelledError:
                pass
            self._health_probe_task = None
            logger.info("safety_gateway_health_probe_stopped")

    async def _health_probe_loop(self) -> None:
        consecutive_unhealthy = 0
        while True:
            try:
                result = await self.health_check()
                is_healthy = result.get("status") == "healthy"
                async with self._lock:
                    prev = self._sg_healthy
                    self._sg_healthy = is_healthy
                    self._last_health_check_time = asyncio.get_running_loop().time()
                if prev and not is_healthy:
                    logger.error("safety_gateway_health_probe_detected_unhealthy")
                    consecutive_unhealthy = 1
                elif not prev and is_healthy:
                    logger.info("safety_gateway_health_probe_recovered")
                    consecutive_unhealthy = 0
                elif not is_healthy:
                    consecutive_unhealthy += 1
                self._publish_health_metric(is_healthy, consecutive_unhealthy)
            except Exception as e:
                logger.warning("safety_gateway_health_probe_error", error=str(e))
                async with self._lock:
                    self._sg_healthy = False
                consecutive_unhealthy += 1
                self._publish_health_metric(False, consecutive_unhealthy)
            await asyncio.sleep(self._health_probe_interval)

    @staticmethod
    def _publish_health_metric(is_healthy: bool, consecutive_unhealthy: int) -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = get_metrics_collector()
            collector.set_safety_gateway_health(is_healthy)
            if consecutive_unhealthy >= 3:
                logger.critical(
                    "safety_gateway_consecutive_unhealthy_critical",
                    consecutive_unhealthy=consecutive_unhealthy,
                )
        except Exception:
            pass

    @property
    def is_healthy(self) -> bool:
        return self._sg_healthy

    async def close(self) -> None:
        await self.stop_health_probe()
        async with self._lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
                self._client = None

    def _should_fast_fail(self) -> bool:
        return self._consecutive_failures >= self.FAST_FAIL_THRESHOLD

    async def _record_failure(self) -> int:
        async with self._lock:
            self._consecutive_failures += 1
            return self._consecutive_failures

    async def _record_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0

    async def _attempt_check(
        self,
        session_id: str,
        patient_id: str,
        output_data: dict[str, Any],
        consent_status_at_input: str,
        desensitization_degraded: bool = False,
    ) -> tuple[dict[str, Any] | None, bool, str | None]:
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/api/v1/safety/check",
                json={
                    "session_id": session_id,
                    "patient_id": patient_id,
                    "output_data": output_data,
                    "consent_status_at_input": consent_status_at_input,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "desensitization_degraded": desensitization_degraded,
                },
            )
            if response.status_code == 200:
                return response.json(), False, None
            elif response.status_code == 503:
                try:
                    error_body = response.json()
                    sub_code = error_body.get("code", "E4005")
                except Exception:
                    sub_code = "E4005"
                logger.error(
                    "safety_gateway_check_failed_503",
                    status_code=503,
                    error_code=sub_code,
                    session_id=session_id,
                )
                return self._e4005_response(sub_code=sub_code), False, sub_code
            else:
                logger.error(
                    "safety_gateway_check_failed",
                    status_code=response.status_code,
                    session_id=session_id,
                )
                return None, True, None
        except httpx.TimeoutException:
            logger.warning("safety_gateway_timeout_retry", session_id=session_id)
            return None, True, None
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            logger.warning(
                "safety_gateway_unavailable_retry",
                error=str(e),
                session_id=session_id,
            )
            return None, True, None
        except Exception as e:
            logger.error("safety_gateway_error", error=str(e), session_id=session_id)
            return self._e4005_response(), False, None

    async def check_output(
        self,
        session_id: str,
        patient_id: str,
        output_data: dict[str, Any],
        consent_status_at_input: str,
        desensitization_degraded: bool = False,
    ) -> dict[str, Any]:
        if self._should_fast_fail():
            logger.error(
                "safety_gateway_fast_fail",
                consecutive_failures=self._consecutive_failures,
                session_id=session_id,
            )
            return self._e4005_response()

        start_time = asyncio.get_running_loop().time()
        retry_attempt = 0

        while True:
            result, should_retry, error_code = await self._attempt_check(
                session_id, patient_id, output_data, consent_status_at_input, desensitization_degraded,
            )

            if result is not None and not should_retry:
                if error_code is None:
                    await self._record_success()
                # T-GA-INT-01 修复：统一暴露 "allowed" 别名
                return self._normalize_response(result)

            failures = await self._record_failure()
            if failures >= self.FAST_FAIL_THRESHOLD:
                logger.error(
                    "safety_gateway_fast_fail_threshold_reached",
                    consecutive_failures=failures,
                    session_id=session_id,
                    error_code=error_code,
                )
                return self._e4005_response(sub_code=error_code)

            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed >= self._queue_timeout:
                logger.error(
                    "safety_gateway_queue_timeout_exceeded",
                    elapsed_seconds=round(elapsed, 2),
                    timeout_seconds=self._queue_timeout,
                    session_id=session_id,
                )
                return self._e4005_response()

            retry_attempt += 1
            backoff_delay = min(self._retry_interval * (2 ** (retry_attempt - 1)), 4.0)
            await asyncio.sleep(min(backoff_delay, self._queue_timeout - elapsed))

    @staticmethod
    def _normalize_response(result: dict[str, Any]) -> dict[str, Any]:
        """T-GA-INT-01 修复：保证返回 dict 同时包含 ``passed`` 和 ``allowed`` 字段。

        历史代码使用 ``sg_result["allowed"]``，新代码统一用 ``sg_result["passed"]``。
        本方法以 SG 返回的 ``passed`` 字段为权威，复制一份到 ``allowed`` 别名；
        若 SG 仅返回 ``allowed``（旧版本），则复制到 ``passed``。

        这是一个**纯本地**字段别名处理，不影响 SG 协议。
        """
        if not isinstance(result, dict):
            return result
        if "passed" in result and "allowed" not in result:
            result["allowed"] = result["passed"]
        elif "allowed" in result and "passed" not in result:
            result["passed"] = result["allowed"]
        return result

    async def get_consent_status(self, patient_id: str) -> str | None:
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self._base_url}/api/v1/consent-status/{patient_id}",
            )
            if response.status_code == 200:
                return response.json().get("consent_status")
            return None
        except Exception as e:
            logger.error("safety_gateway_consent_check_error", error=str(e), patient_id=patient_id)
            return None

    async def check_consistency(
        self,
        session_id: str,
        fusion_output: dict[str, Any],
        specialist_conclusions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/api/v1/safety/consistency",
                json={
                    "session_id": session_id,
                    "fusion_output": fusion_output,
                    "specialist_conclusions": specialist_conclusions,
                },
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    "safety_gateway_consistency_failed",
                    status_code=response.status_code,
                    session_id=session_id,
                )
                return {"consistent": False, "conflicts": [], "message": "一致性校验请求失败"}
        except httpx.TimeoutException:
            logger.error("safety_gateway_consistency_timeout", session_id=session_id)
            return {"consistent": False, "conflicts": [], "message": "一致性校验超时"}
        except Exception as e:
            logger.error("safety_gateway_consistency_error", error=str(e), session_id=session_id)
            return {"consistent": False, "conflicts": [], "message": f"一致性校验异常: {e!s}"}

    async def check_contraindication(
        self,
        session_id: str,
        patient_id: str,
        organ: str,
        diagnosis_data: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/api/v1/safety/contraindication",
                json={
                    "session_id": session_id,
                    "patient_id": patient_id,
                    "organ": organ,
                    "diagnosis_data": diagnosis_data,
                },
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    "safety_gateway_contraindication_failed",
                    status_code=response.status_code,
                    session_id=session_id,
                )
                return {"has_contraindication": True, "contraindications": [], "message": "禁忌校验请求失败，默认存在禁忌"}
        except httpx.TimeoutException:
            logger.error("safety_gateway_contraindication_timeout", session_id=session_id)
            return {"has_contraindication": True, "contraindications": [], "message": "禁忌校验超时，默认存在禁忌"}
        except Exception as e:
            logger.error("safety_gateway_contraindication_error", error=str(e), session_id=session_id)
            return {"has_contraindication": True, "contraindications": [], "message": f"禁忌校验异常，默认存在禁忌: {e!s}"}

    async def health_check(self) -> dict[str, Any]:
        try:
            client = await self._get_client()
            response = await client.get(f"{self._base_url}/health")
            if response.status_code == 200:
                return response.json()
            return {"status": "unhealthy", "service": "safety-gateway"}
        except Exception as e:
            logger.error("safety_gateway_health_check_error", error=str(e))
            return {"status": "unreachable", "service": "safety-gateway", "error": str(e)}

    def _e4005_response(self, sub_code: str | None = None) -> dict[str, Any]:
        error_code = sub_code if sub_code else "E4005"
        messages = {
            "E4005A1": "脱敏防线降级且检测到敏感数据，请转为人工诊断",
            "E4005A2": "安全校验不可用且患者同意状态异常，请转为人工诊断",
            "E4005B": "核心数据服务不可用，请转为人工诊断",
            "E4005C": "系统安全校验不可用，请转为人工诊断",
        }
        return {
            "passed": False,
            # T-GA-INT-01 修复：同时暴露 "allowed" 别名（向后兼容旧代码）
            "allowed": False,
            "error_code": error_code,
            "consent_status_at_output": "unknown_blocked",
            "degraded_mode": True,
            "message": messages.get(error_code, "安全校验服务不可用，请转为人工诊断"),
        }


safety_gateway_client = SafetyGatewayClient()
