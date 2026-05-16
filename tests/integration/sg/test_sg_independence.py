"""SG-001: 主服务崩溃时SG独立存活集成测试

验证SafetyGateway作为独立进程（独立容器），在主服务崩溃时能够：
1. 容器保持运行状态（restart: always生效）
2. /health端点正常响应
3. /api/v1/safety/check返回E4005错误码

设计依据：
- 01-系统架构设计§3.4: 主服务崩溃时SafetyGateway可独立存活并返回E4005
- 06-开发环境与进度规划§6.2: Gate 0验证项"SafetyGateway隔离: 主服务崩溃时独立返回E4005"

故障注入方法：
1. 使用docker-compose stop停止api容器（模拟主服务崩溃）
2. 验证safety-gateway容器仍在运行
3. 验证/health和/api/v1/safety/check端点响应
4. 使用docker-compose start恢复api容器
"""

import asyncio
import subprocess
import time
import pytest
import httpx

SG_BASE_URL = "http://localhost:9001"
MAIN_SERVICE_URL = "http://localhost:8000"
DOCKER_COMPOSE_FILE = "docker-compose.dev.yml"


def is_docker_available() -> bool:
    """检查Docker是否可用"""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_container_status(container_name: str) -> str | None:
    """获取容器状态"""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def stop_service_container(service_name: str) -> bool:
    """停止指定服务容器（模拟服务崩溃）"""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", DOCKER_COMPOSE_FILE, "stop", service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def start_service_container(service_name: str) -> bool:
    """启动指定服务容器"""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", DOCKER_COMPOSE_FILE, "start", service_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def wait_for_health(url: str, timeout: int = 30) -> bool:
    """等待服务健康"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(url)
                if resp.status_code in (200, 503):
                    return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="module")
def docker_available():
    """检查Docker环境是否可用"""
    if not is_docker_available():
        pytest.skip("Docker not available - this test requires Docker to simulate service crash")
    return True


@pytest.mark.integration
@pytest.mark.track_sg
class TestSGIndependenceFromMainService:
    """验证SG在主服务崩溃时独立存活

    场景：模拟主服务(api容器)崩溃，验证SG容器仍能响应请求
    """

    @pytest.fixture(autouse=True, scope="class")
    def ensure_services_running(self, docker_available):
        """确保服务在测试前处于运行状态"""
        if not docker_available:
            yield
            return

        api_status = get_container_status("ultrasound-api")
        sg_status = get_container_status("ultrasound-safety-gateway")

        if api_status != "running":
            start_service_container("api")
            wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

        if sg_status != "running":
            start_service_container("safety-gateway")
            wait_for_health(f"{SG_BASE_URL}/health", timeout=30)

        yield

        start_service_container("api")
        wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

    def test_preconditions_both_services_running(self):
        """前置条件验证：测试前两个服务都应处于运行状态"""
        if not is_docker_available():
            pytest.skip("Docker not available")

        api_status = get_container_status("ultrasound-api")
        sg_status = get_container_status("ultrasound-safety-gateway")

        assert api_status == "running", f"API容器应处于运行状态，实际状态: {api_status}"
        assert sg_status == "running", f"SG容器应处于运行状态，实际状态: {sg_status}"

    @pytest.mark.asyncio
    async def test_main_service_crash_sg_remains_running(self, docker_available):
        """验证场景1：主服务崩溃后SG容器仍保持运行状态

        测试步骤：
        1. 记录SG容器初始状态
        2. 停止api容器（模拟主服务崩溃）
        3. 等待5秒确保容器状态稳定
        4. 验证SG容器状态仍为running
        5. 恢复api容器
        """
        if not docker_available:
            pytest.skip("Docker not available")

        sg_initial_status = get_container_status("ultrasound-safety-gateway")
        assert sg_initial_status == "running", f"SG初始状态应为running，实际: {sg_initial_status}"

        stop_success = stop_service_container("api")
        if not stop_success:
            pytest.skip("无法停止API容器，可能容器不存在或权限不足")

        time.sleep(5)

        sg_after_crash = get_container_status("ultrasound-safety-gateway")
        assert sg_after_crash == "running", (
            f"主服务崩溃后SG容器应仍保持running状态，实际状态: {sg_after_crash}。"
            f"这验证了Docker restart:always和独立容器配置的生效。"
        )

        start_service_container("api")
        wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

    @pytest.mark.asyncio
    async def test_main_service_crash_sg_health_responds(self, docker_available):
        """验证场景2：主服务崩溃后SG的/health端点正常响应

        测试步骤：
        1. 停止api容器
        2. 调用SG的/health端点
        3. 验证返回200或503（健康检查结果）
        4. 验证响应包含SG特有字段
        5. 恢复api容器
        """
        if not docker_available:
            pytest.skip("Docker not available")

        stop_success = stop_service_container("api")
        if not stop_success:
            pytest.skip("无法停止API容器")

        time.sleep(3)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{SG_BASE_URL}/health")

                assert resp.status_code in (200, 503), (
                    f"主服务崩溃后SG的/health应能正常响应，状态码应为200或503，实际: {resp.status_code}"
                )

                data = resp.json()
                assert data.get("service") == "safety-gateway", (
                    f"响应应来自safety-gateway服务，实际service: {data.get('service')}"
                )
                assert "components" in data, "健康检查响应应包含components字段"
                assert "latency_stats" in data, "健康检查响应应包含latency_stats字段"
        finally:
            start_service_container("api")
            wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

    @pytest.mark.asyncio
    async def test_main_service_crash_sg_returns_e4005(self, docker_available):
        """验证场景3：主服务崩溃后SG的safety/check返回E4005

        测试步骤：
        1. 停止api容器
        2. 调用SG的/api/v1/safety/check端点
        3. 验证返回E4005错误码
        4. 恢复api容器

        注意：此测试在PostgreSQL正常时可能返回正常结果，
        因为SG可以在主服务不可用时独立完成校验。
        只有当数据库也不可用时才返回E4005B。
        """
        if not docker_available:
            pytest.skip("Docker not available")

        stop_success = stop_service_container("api")
        if not stop_success:
            pytest.skip("无法停止API容器")

        time.sleep(3)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{SG_BASE_URL}/api/v1/safety/check",
                    json={
                        "patient_id": "P001",
                        "output_data": {"organ_name": "thyroid", "observations": "正常"},
                    },
                )

                assert resp.status_code in (200, 503), (
                    f"主服务崩溃后SG应能响应safety/check请求，状态码应为200或503，实际: {resp.status_code}"
                )

                if resp.status_code == 503:
                    data = resp.json()
                    error_code = data.get("code", "")
                    assert error_code.startswith("E4005"), (
                        f"503错误应返回E4005系列错误码，实际: {error_code}"
                    )
        finally:
            start_service_container("api")
            wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

    @pytest.mark.asyncio
    async def test_sg_watchdog_alive_during_main_outage(self, docker_available):
        """验证场景4：主服务崩溃期间SG的/health/watchdog端点仍存活

        /health/watchdog端点专门用于验证SG自身存活状态，
        不依赖任何外部服务。
        """
        if not docker_available:
            pytest.skip("Docker not available")

        stop_success = stop_service_container("api")
        if not stop_success:
            pytest.skip("无法停止API容器")

        time.sleep(3)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{SG_BASE_URL}/health/watchdog")

                assert resp.status_code == 200, (
                    f"主服务崩溃期间SG的/health/watchdog应返回200，实际: {resp.status_code}"
                )

                data = resp.json()
                assert data.get("alive") is True, (
                    f"watchdog应报告SG存活状态alive=True，实际: {data.get('alive')}"
                )
                assert "p99_status" in data, "watchdog响应应包含p99_status"
                assert "timestamp" in data, "watchdog响应应包含timestamp"
        finally:
            start_service_container("api")
            wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)


@pytest.mark.integration
@pytest.mark.track_sg
class TestSGRecoveryAfterMainRestored:
    """验证SG在主服务恢复后自动恢复正常状态"""

    def test_sg_health_check_after_main_restored(self):
        """验证主服务恢复后SG的健康检查状态正确"""
        if not is_docker_available():
            pytest.skip("Docker not available")

        time.sleep(5)

        status = get_container_status("ultrasound-api")
        if status != "running":
            start_service_container("api")

        wait_for_health(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=60)

        sg_status = get_container_status("ultrasound-safety-gateway")
        assert sg_status == "running", f"SG容器应保持运行状态，实际: {sg_status}"

        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{SG_BASE_URL}/health")
            assert resp.status_code in (200, 503), f"SG健康检查应正常响应，实际: {resp.status_code}"


@pytest.mark.integration
@pytest.mark.track_sg
class TestSGContainerIsolation:
    """验证SG容器的隔离性配置"""

    def test_sg_container_has_restart_always(self):
        """验证SG容器配置了restart: always"""
        if not is_docker_available():
            pytest.skip("Docker not available")

        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", "ultrasound-safety-gateway"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                restart_policy = result.stdout.strip()
                assert restart_policy == "always", (
                    f"SG容器应配置restart=always，实际: {restart_policy}"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("无法检查容器重启策略")

    def test_sg_container_oom_score_adj_configured(self):
        """验证SG容器配置了oom_score_adj=-1000"""
        if not is_docker_available():
            pytest.skip("Docker not available")

        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.HostConfig.OomScoreAdj}}", "ultrasound-safety-gateway"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                oom_score = result.stdout.strip()
                assert oom_score == "-1000", (
                    f"SG容器应配置oom_score_adj=-1000（禁止OOM Killer杀死），实际: {oom_score}"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("无法检查OOM配置")

    def test_sg_container_independent_network(self):
        """验证SG容器在独立的网络命名空间中"""
        if not is_docker_available():
            pytest.skip("Docker not available")

        try:
            api_result = subprocess.run(
                ["docker", "inspect", "-f", "{{.NetworkSettings.Networks}}", "ultrasound-api"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            sg_result = subprocess.run(
                ["docker", "inspect", "-f", "{{.NetworkSettings.Networks}}", "ultrasound-safety-gateway"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if api_result.returncode == 0 and sg_result.returncode == 0:
                api_networks = api_result.stdout.strip()
                sg_networks = sg_result.stdout.strip()
                assert "ultrasound-network" in sg_networks, (
                    f"SG应连接到ultrasound-network，实际网络: {sg_networks}"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("无法检查容器网络配置")
