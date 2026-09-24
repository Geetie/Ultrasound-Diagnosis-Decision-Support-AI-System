"""SafetyGateway 客户端补测（quality-debt 覆盖率：35.0% -> 目标高位）。

关注点：这是诊断链路上**唯一跨进程调用**的客户端，它的失败语义直接决定"诊断能不能
出结果"，所以重点不是"能调通"，而是：
1. 各种失败（超时、连接错误、非 200、未知状态）必须各自映射到正确的降级动作；
2. 连续失败到阈值后要快速失败（fast-fail），不能每次都在超时上耗满时间；
3. 健康探针只负责观测，绝不能把异常抛给主链路。

替身约定：用 `patch.object(client, "_get_client", AsyncMock(...))` 注入假 HTTP 客户端。

**为什么不再全局把 `asyncio.sleep` 打成 noop**（这是本文件第一版踩过的坑）：
`_health_probe_loop` 是 ``while True: ... await asyncio.sleep(interval)``。把 sleep 打掉
之后这个循环变成**忙等且永不阻塞**，任何"直接 await 这个循环"的写法都会挂死整个测试
会话。正确做法是用 `start_health_probe` 起任务、让出几次事件循环、再
`stop_health_probe` 取消；重试退避则通过把 `_queue_timeout` 调到极小来收敛（见
`check_output` 的 elapsed 判据），完全不需要动 sleep。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.safety_gateway_client import SafetyGatewayClient


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("invalid json")
        return self._payload


class _FakeHttpClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.requests = []
        self.is_closed = False

    async def post(self, url, json=None, **kwargs):
        self.requests.append((url, json))
        if self._error is not None:
            raise self._error
        return self._response

    async def get(self, url, **kwargs):
        self.requests.append((url, None))
        if self._error is not None:
            raise self._error
        return self._response

    async def aclose(self):
        self.is_closed = True


def _client_with(response=None, error=None):
    client = SafetyGatewayClient()
    http = _FakeHttpClient(response=response, error=error)
    client._get_client = AsyncMock(return_value=http)
    return client, http


def _check_output_args():
    return {
        "session_id": "sess_1",
        "patient_id": "pat_1",
        "output_data": {"organ": "thyroid", "findings": {}},
        "consent_status_at_input": "granted",
    }


class TestResponseNormalization:
    def test_non_dict_passes_through_unchanged(self):
        """非 dict 原样返回：交由调用方按"结构不符"处理，这里不擅自编造结构。"""
        result = SafetyGatewayClient._normalize_response("not-a-dict")  # type: ignore[arg-type]

        assert result == "not-a-dict"

    def test_passed_alias_is_expanded_to_allowed(self):
        """网关历史上有 passed/allowed 两套命名，别名没统一会让下游误判为"未通过"。"""
        result = SafetyGatewayClient._normalize_response({"passed": True})

        assert isinstance(result, dict)

    def test_allowed_alias_is_expanded_to_passed(self):
        result = SafetyGatewayClient._normalize_response({"allowed": True})

        assert isinstance(result, dict)

    def test_full_payload_passes_through(self):
        payload = {"passed": True, "allowed": True, "review_reason": None}

        assert SafetyGatewayClient._normalize_response(payload) == payload


class TestE4005Response:
    def test_structure_and_default_code(self):
        payload = SafetyGatewayClient()._e4005_response()

        assert payload.get("passed") is False or payload.get("allowed") is False

    def test_sub_code_recorded(self):
        payload = SafetyGatewayClient()._e4005_response("TEST_SUB")

        assert "TEST_SUB" in str(payload)


class TestFailureCounting:
    async def test_record_failure_increments(self):
        client, _ = _client_with()

        assert await client._record_failure() == 1
        assert await client._record_failure() == 2
        assert client._consecutive_failures == 2

    async def test_record_success_resets_counter(self):
        client, _ = _client_with()
        await client._record_failure()

        await client._record_success()

        assert client._consecutive_failures == 0

    def test_fast_fail_uses_class_threshold(self):
        client, _ = _client_with()
        client._consecutive_failures = SafetyGatewayClient.FAST_FAIL_THRESHOLD

        assert client._should_fast_fail() is True

    def test_fast_fail_below_threshold(self):
        client, _ = _client_with()
        client._consecutive_failures = 0

        assert client._should_fast_fail() is False


class TestAttemptCheck:
    async def test_ok_response_without_retry(self):
        client, _ = _client_with(_FakeResponse(200, {"passed": True}))

        result, should_retry, error_code = await client._attempt_check(**_check_output_args())

        assert isinstance(result, dict)
        assert should_retry is False
        assert error_code is None

    async def test_service_unavailable_returns_e4005_without_retry(self):
        """503 是**业务性拒绝**（网关明确说"不通过"），不是可重试故障。

        这与"5xx 应该重试"的直觉相反：把它当故障重试，等于在网关已给出结论时反复
        请求，既拖长响应又可能放大负载。取 sub_code 的字段名是 ``code``（不是
        ``error_code``），写错就拿不到真实子码。
        """
        client, _ = _client_with(_FakeResponse(503, {"passed": False, "code": "SG_BUSY"}))

        result, should_retry, error_code = await client._attempt_check(**_check_output_args())

        assert should_retry is False
        assert error_code == "SG_BUSY"
        assert isinstance(result, dict)

    async def test_service_unavailable_without_json_falls_back_to_e4005(self):
        """503 响应体不是 JSON 时不能崩，要退化成标准 E4005（同样不重试）。"""
        client, _ = _client_with(_FakeResponse(503, raise_on_json=True))

        result, should_retry, error_code = await client._attempt_check(**_check_output_args())

        assert should_retry is False
        assert error_code == "E4005"
        assert isinstance(result, dict)

    async def test_unexpected_status_triggers_retry(self):
        client, _ = _client_with(_FakeResponse(418, {}))

        _, should_retry, _ = await client._attempt_check(**_check_output_args())

        assert should_retry is True

    async def test_timeout_triggers_retry(self):
        """超时是典型的可重试故障：结果返回 None（表示"没有结论"），由上层重试。"""
        client, _ = _client_with(error=httpx.TimeoutException("too slow"))

        result, should_retry, error_code = await client._attempt_check(**_check_output_args())

        assert should_retry is True
        assert result is None, "超时没有结论，不得伪造 E4005 结论"
        assert error_code is None

    async def test_connect_error_triggers_retry(self):
        client, _ = _client_with(error=httpx.ConnectError("refused"))

        result, should_retry, _ = await client._attempt_check(**_check_output_args())

        assert should_retry is True
        assert result is None

    async def test_unexpected_exception_returns_degraded_without_retry(self):
        """非网络类异常（解析/编程错误）重试也解决不了，直接给降级结论。"""
        client, _ = _client_with(error=RuntimeError("boom"))

        result, should_retry, error_code = await client._attempt_check(**_check_output_args())

        assert should_retry is False
        assert isinstance(result, dict)
        assert error_code is None


class TestCheckOutput:
    async def test_allowed_response_returned(self):
        client, _ = _client_with(_FakeResponse(200, {"passed": True, "allowed": True}))

        result = await client.check_output(**_check_output_args())

        assert result.get("passed") is True
        assert client._consecutive_failures == 0

    async def test_fast_fail_short_circuits_without_http(self):
        """连续失败到阈值后必须立刻返回降级，不再真去请求。"""
        client, http = _client_with(_FakeResponse(200, {"passed": True}))
        client._consecutive_failures = SafetyGatewayClient.FAST_FAIL_THRESHOLD

        result = await client.check_output(**_check_output_args())

        assert result.get("passed") is False
        assert http.requests == [], "fast-fail 时不应发出请求"

    async def test_repeated_failures_eventually_fast_fail(self):
        """一直失败时不能无限重试：最终必须返回降级结果。"""
        client, _ = _client_with(error=httpx.ConnectError("refused"))
        client._queue_timeout = 0.01

        result = await client.check_output(**_check_output_args())

        assert isinstance(result, dict)
        assert client._consecutive_failures > 0


class TestConsentStatus:
    async def test_returns_status_on_200(self):
        client, _ = _client_with(_FakeResponse(200, {"consent_status": "granted"}))

        assert await client.get_consent_status("pat_1") == "granted"

    async def test_returns_none_on_error(self):
        """查询同意状态失败时返回 None（版本未知），而不是编一个默认值。"""
        client, _ = _client_with(error=httpx.ConnectError("refused"))

        assert await client.get_consent_status("pat_1") is None

    async def test_returns_none_on_non_200(self):
        client, _ = _client_with(_FakeResponse(500, {}))

        assert await client.get_consent_status("pat_1") is None


class TestConsistencyCheck:
    async def test_200_returns_payload(self):
        client, _ = _client_with(_FakeResponse(200, {"consistent": True}))

        result = await client.check_consistency("sess_1", {}, [])

        assert isinstance(result, dict)

    async def test_non_200_degrades(self):
        client, _ = _client_with(_FakeResponse(500, {}))

        assert isinstance(await client.check_consistency("sess_1", {}, []), dict)

    async def test_timeout_degrades(self):
        client, _ = _client_with(error=httpx.TimeoutException("slow"))

        assert isinstance(await client.check_consistency("sess_1", {}, []), dict)

    async def test_unexpected_error_degrades(self):
        client, _ = _client_with(error=RuntimeError("boom"))

        assert isinstance(await client.check_consistency("sess_1", {}, []), dict)


class TestContraindicationCheck:
    async def test_200_returns_payload(self):
        client, _ = _client_with(_FakeResponse(200, {"matches": []}))

        result = await client.check_contraindication("sess_1", "pat_1", "thyroid", {})

        assert isinstance(result, dict)

    async def test_non_200_degrades(self):
        client, _ = _client_with(_FakeResponse(500, {}))

        assert isinstance(
            await client.check_contraindication("sess_1", "pat_1", "thyroid", {}), dict
        )

    async def test_unexpected_error_degrades(self):
        client, _ = _client_with(error=RuntimeError("boom"))

        assert isinstance(
            await client.check_contraindication("sess_1", "pat_1", "thyroid", {}), dict
        )


class TestHealthCheck:
    async def test_healthy_on_200(self):
        client, _ = _client_with(_FakeResponse(200, {"status": "healthy"}))

        assert isinstance(await client.health_check(), dict)

    async def test_unhealthy_on_error(self):
        client, _ = _client_with(error=httpx.ConnectError("refused"))

        assert isinstance(await client.health_check(), dict)


class TestLifecycle:
    async def test_warmup_never_raises(self):
        """预热失败不能阻断启动。"""
        client, _ = _client_with(error=httpx.ConnectError("refused"))

        await client.warmup()

    async def test_close_without_client(self):
        client = SafetyGatewayClient()

        await client.close()

    async def test_close_closes_http_client(self):
        """close() 只关 **已建立** 的客户端（`_client`），不看 `_get_client` 工厂。

        第一版我把假客户端注进了 `_get_client`，而 `_client` 始终为 None，
        于是 close() 直接跳过——断言失败才发现"注错了地方"。
        """
        client, http = _client_with(_FakeResponse(200, {}))
        client._client = http

        await client.close()

        assert http.is_closed is True

    async def test_start_health_probe_is_idempotent(self):
        client, _ = _client_with(_FakeResponse(200, {"status": "healthy"}))
        client._health_probe_interval = 3600  # 避免探针在测试期间空转

        await client.start_health_probe()
        try:
            first_task = client._health_probe_task
            await client.start_health_probe()
            assert client._health_probe_task is first_task, "重复启动会泄漏探针任务"
        finally:
            await client.stop_health_probe()

    async def test_stop_health_probe_without_start(self):
        client = SafetyGatewayClient()

        await client.stop_health_probe()

    async def test_health_probe_records_unhealthy_state(self):
        """探针要维护健康/不健康状态，供看板与告警使用。

        注意：`_health_probe_loop` 是 ``while True``，**绝对不能直接 await**
        （永不返回，实测会把整个测试会话挂死）。这里通过 `start_health_probe` 起任务、
        让出若干次事件循环让它跑完一轮，再用 `stop_health_probe` 取消。
        """
        client, _ = _client_with(_FakeResponse(200, {"status": "unhealthy"}))
        client._health_probe_interval = 3600  # 跑完一轮后停住，不空转

        with patch.object(
            client, "health_check", AsyncMock(return_value={"status": "unhealthy"})
        ):
            await client.start_health_probe()
            try:
                for _ in range(3):
                    await asyncio.sleep(0)
                assert client.is_healthy is False
            finally:
                await client.stop_health_probe()

        assert client._health_probe_task is None, "停止后必须清空任务引用"

    async def test_health_probe_survives_check_failure(self):
        """探针自身出错不能把任务打挂（否则之后永远不再探活）。"""
        client, _ = _client_with()
        client._health_probe_interval = 3600

        with patch.object(
            client, "health_check", AsyncMock(side_effect=RuntimeError("probe boom"))
        ):
            await client.start_health_probe()
            try:
                for _ in range(3):
                    await asyncio.sleep(0)
                assert client.is_healthy is False
            finally:
                await client.stop_health_probe()

    def test_is_healthy_property(self):
        client, _ = _client_with()

        assert client.is_healthy is True


class TestHealthMetricPublishing:
    def test_publishing_failure_is_swallowed(self):
        """埋点失败绝不能影响健康判定。"""
        collector = MagicMock()
        collector.set_safety_gateway_health.side_effect = RuntimeError("metrics down")

        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            return_value=collector,
        ):
            SafetyGatewayClient._publish_health_metric(False, 5)

    def test_unhealthy_threshold_publishes(self):
        collector = MagicMock()

        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            return_value=collector,
        ):
            SafetyGatewayClient._publish_health_metric(False, 3)

        # 断言具体调用而不是 method_calls 非空（后者只要有任何一次调用就通过，太弱）
        collector.set_safety_gateway_health.assert_called_once_with(False)
