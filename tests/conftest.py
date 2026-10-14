import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

# === 必须在导入 app.* 之前设置：隔离 SoftHSM 的时间戳状态 ===
# SoftHSMManager._load_timestamp() 会读取仓库根的 soft_hsm_enabled_at.txt
# （当前记录启用日 2026-05-09）。SoftHSM 开发模式有 90 天有效期，该时间戳
# 早已过期，EncryptionService.sign() 会因此抛 "SoftHSM has expired"，
# 让所有签名/审计相关用例连锁失败——且失败与否取决于运行日期，属于
# 时间漂移导致的非确定性。测试改用独立临时时间戳文件并默认不启用
# SoftHSM；显式设置 USE_SOFT_HSM / SOFT_HSM_TIMESTAMP_PATH 的环境仍优先。
os.environ.setdefault(
    "SOFT_HSM_TIMESTAMP_PATH",
    os.path.join(tempfile.gettempdir(), "ultrasound_test_soft_hsm_enabled_at.txt"),
)
os.environ.setdefault("USE_SOFT_HSM", "false")
# === ENV 必须是 test，否则 HSM 走生产路径 = 本地假红 ===
# HSMManager.__init__ 的密钥恢复/校验分支按 is_test 判定跳过，而 is_test 依赖
# ENV=test（CI 在 ci.yml 里设了，**本地没设**）→ 本地 `pytest tests/unit/api`
# 会在 `from app.main import app` 处抛
# "HSM_KEY_ENCRYPTION_KEY environment variable is not set"，一次假红 48 个用例，
# 极易被误判成回归。CI 已有该变量，这里 setdefault 只影响本地。
os.environ.setdefault("ENV", "test")

import httpx
import pytest

from app.config.redis import RedisPool

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def set_hsm_env_vars():
    os.environ.setdefault("HSM_PRIVATE_KEY_PASSWORD", "test_password_for_unit_tests_only")
    yield


@pytest.fixture(scope="session", autouse=True)
def fast_fail_redis():
    """测试环境让真实 Redis 连接"瞬时失败"。

    默认 ``REDIS_URL`` 指向 Docker 服务名（``redis:6379``），本机不可达。
    ``RedisPool`` 的失败代价是 ``socket_connect_timeout`` × ``Retry(3)``，
    实测**每个 Redis 命令阻塞 9~18s**（RedisPool.get_client 里
    socket_timeout=settings.redis_pool_timeout, retry=Retry(retries=3)）。
    任何触及 Redis 的用例都会被这条链路拖慢十秒以上。

    单元测试不依赖真实 Redis，这里统一改为瞬时抛 ``ConnectionError``，
    使各模块既有的"优雅降级"分支（catch RedisError → 返回 None/False）
    被毫秒级验证。

    需要真实 Redis 的用例请自行 ``patch("app.config.redis.RedisPool.get_client", ...)``
    （内层 patch 优先，不受本守卫影响），或设置 ``USE_REAL_REDIS=1`` 关闭守卫。
    """
    if os.environ.get("USE_REAL_REDIS", "false").lower() == "true":
        yield
        return

    from redis.exceptions import ConnectionError as RedisConnectionError

    async def _fail_fast(*args, **kwargs):
        raise RedisConnectionError("Redis is disabled during tests (fast_fail_redis)")

    with patch.object(RedisPool, "get_client", _fail_fast):
        yield


@pytest.fixture(scope="session")
async def redis_client():
    client = await RedisPool.get_client()
    yield client
    await client.flushdb()
    await RedisPool.close()


@pytest.fixture
async def llama_engine():
    from app.inference.llama_cpp import LlamaCppEngine
    engine = LlamaCppEngine()
    yield engine
    await engine.close()


@pytest.fixture
async def vllm_engine():
    from app.inference.vllm import VLLMEngine
    engine = VLLMEngine(default_model="test-model")
    yield engine
    await engine.close()


@pytest.fixture(autouse=True)
def reset_desensitization_engine():
    from app.security.desensitization import DesensitizationEngine
    DesensitizationEngine._instance = None
    yield
    DesensitizationEngine._instance = None


@pytest.fixture(autouse=True)
def reset_verification_config():
    from app.inference.verification_config import reset_verification_config
    reset_verification_config()
    yield
    reset_verification_config()


# ==================== Phase0A LLM Service Fixtures ====================

_LLM_TEST_MODE = os.environ.get("LLM_TEST_MODE", "skip")
_LLAMA_CPP_HOST = os.environ.get("LLAMA_CPP_HOST", "localhost")
_LLAMA_CPP_PORT = int(os.environ.get("LLAMA_CPP_PORT", "8001"))
_VLLM_HOST = os.environ.get("VLLM_HOST", "localhost")
_VLLM_PORT = int(os.environ.get("VLLM_PORT", "8002"))
_SERVICE_START_TIMEOUT = float(os.environ.get("LLM_SERVICE_START_TIMEOUT", "300"))
_VLLM_MODE = os.environ.get("VLLM_MODE", "auto")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS_DIR = os.path.join(_PROJECT_ROOT, "models")
_GGUF_MODEL_PATH = os.path.join(_MODELS_DIR, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
_HF_MODEL_PATH = os.path.join(_MODELS_DIR, "Qwen2.5-1.5B-Instruct")
_VLLM_CONTAINER_NAME = "ultrasound-vllm-test"


def _find_gguf_model() -> str:
    if os.path.exists(_GGUF_MODEL_PATH):
        return _GGUF_MODEL_PATH
    if os.path.exists(_MODELS_DIR):
        for f in os.listdir(_MODELS_DIR):
            if f.endswith(".gguf"):
                return os.path.join(_MODELS_DIR, f)
    raise FileNotFoundError(f"No GGUF model found in {_MODELS_DIR}")


def _find_hf_model() -> str:
    if os.path.exists(os.path.join(_HF_MODEL_PATH, "config.json")):
        return _HF_MODEL_PATH
    if os.path.exists(_MODELS_DIR):
        for d in os.listdir(_MODELS_DIR):
            config_path = os.path.join(_MODELS_DIR, d, "config.json")
            if os.path.exists(config_path) and d != ".git":
                return os.path.join(_MODELS_DIR, d)
    raise FileNotFoundError(f"No HuggingFace model found in {_MODELS_DIR}")


def _wait_for_health(url: str, timeout: float = _SERVICE_START_TIMEOUT) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{url}/health")
                if resp.status_code == 200:
                    return True
        except Exception:
            logger.warning("_wait_for_health: 未预期异常，按原逻辑降级", exc_info=True)
            pass
        time.sleep(3.0)
    return False


def _is_service_running(url: str) -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{url}/health")
            return resp.status_code == 200
    except Exception:
        logger.warning("_is_service_running: 未预期异常，按原逻辑降级", exc_info=True)
        return False


def _is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        logger.warning("_is_docker_available: 未预期异常，按原逻辑降级", exc_info=True)
        return False


skip_no_llm_services = pytest.mark.skipif(
    _LLM_TEST_MODE != "local",
    reason="Set LLM_TEST_MODE=local to run real LLM tests. "
           "Use: python scripts/start_local_test_services.py",
)


@pytest.fixture(scope="session")
def llama_cpp_service():
    if _LLM_TEST_MODE != "local":
        pytest.skip("Set LLM_TEST_MODE=local to run real LLM tests")

    base_url = f"http://{_LLAMA_CPP_HOST}:{_LLAMA_CPP_PORT}"

    if _is_service_running(base_url):
        print(f"\n[conftest] llama.cpp already running at {base_url}")
        yield base_url
        return

    gguf_model = _find_gguf_model()
    print(f"\n[conftest] Starting llama.cpp server with {gguf_model}")

    cmd = [
        sys.executable, os.path.join(_PROJECT_ROOT, "scripts", "llama_cpp_server.py"),
        "--host", _LLAMA_CPP_HOST,
        "--port", str(_LLAMA_CPP_PORT),
        "--n_ctx", "4096",
    ]

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(cmd, **popen_kwargs)

    if not _wait_for_health(base_url, timeout=_SERVICE_START_TIMEOUT):
        process.terminate()
        process.wait(timeout=10)
        pytest.fail(
            f"llama.cpp failed to start within {_SERVICE_START_TIMEOUT}s. "
            f"Manual: python scripts/llama_cpp_server.py --port {_LLAMA_CPP_PORT}"
        )

    print(f"[conftest] llama.cpp healthy at {base_url} (PID={process.pid})")
    yield base_url

    print("\n[conftest] Stopping llama.cpp...")
    try:
        process.terminate()
        process.wait(timeout=15)
    except Exception:
        logger.warning("llama_cpp_service: 未预期异常，按原逻辑降级", exc_info=True)
        process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="session")
def vllm_service():
    """vLLM service fixture.

    Deployment strategy (VLLM_MODE env var):
    - "auto" (default): Auto-detect best available mode
      - Windows without GPU: llama_compat (reuse llama.cpp on port 8001)
      - Linux with GPU: Docker with GPU passthrough
      - Linux without GPU: llama_compat or Docker CPU
    - "docker": Start vLLM via Docker container (requires GPU or vllm-cpu image)
    - "native": Start vLLM via python scripts/vllm_server.py (Linux only)
    - "external": Assume vLLM is already running at VLLM_HOST:VLLM_PORT
    - "llama_compat": Use llama.cpp's /v1/chat/completions endpoint as vLLM substitute
      This is the fallback for environments where vLLM cannot run natively.
      llama.cpp supports both /v1/completions and /v1/chat/completions, so
      LlamaCppEngine uses /v1/completions and VLLMEngine uses /v1/chat/completions
      on the same llama.cpp server, testing the two API format paths.
    """
    if _LLM_TEST_MODE != "local":
        pytest.skip("Set LLM_TEST_MODE=local to run real LLM tests")

    base_url = f"http://{_VLLM_HOST}:{_VLLM_PORT}"

    if _is_service_running(base_url):
        print(f"\n[conftest] vLLM already running at {base_url}")
        yield base_url
        return

    if _VLLM_MODE == "external":
        if not _wait_for_health(base_url, timeout=30):
            pytest.fail(f"vLLM not available at {base_url} in external mode")
        yield base_url
        return

    if _VLLM_MODE == "llama_compat":
        yield from _start_vllm_llama_compat()
        return

    if _VLLM_MODE == "native":
        if sys.platform == "win32":
            pytest.skip("vLLM native mode not supported on Windows. Use VLLM_MODE=llama_compat")
        yield from _start_vllm_native(base_url)
        return

    if _VLLM_MODE == "docker":
        yield from _start_vllm_docker(base_url)
        return

    # Auto mode: detect best available
    # On Windows, vLLM native doesn't work and Docker CPU mode requires vllm-cpu image
    # Best fallback: use llama.cpp's chat/completions endpoint as vLLM substitute
    if sys.platform == "win32":
        print("\n[conftest] Windows detected, using llama_compat mode for vLLM")
        yield from _start_vllm_llama_compat()
    else:
        # On Linux, try Docker first, then fallback to llama_compat
        if _is_docker_available():
            yield from _start_vllm_docker(base_url)
        else:
            print("\n[conftest] Docker not available, using llama_compat mode for vLLM")
            yield from _start_vllm_llama_compat()


def _start_vllm_llama_compat():
    """Use llama.cpp's /v1/chat/completions endpoint as vLLM substitute.

    llama.cpp server supports both /v1/completions and /v1/chat/completions.
    LlamaCppEngine uses /v1/completions, VLLMEngine uses /v1/chat/completions.
    This allows testing the two API format paths on the same underlying model.
    """
    llama_url = f"http://{_LLAMA_CPP_HOST}:{_LLAMA_CPP_PORT}"
    if not _is_service_running(llama_url):
        pytest.fail(
            f"llama.cpp not running at {llama_url}. "
            f"Start it first: python scripts/llama_cpp_server.py"
        )
    print(f"\n[conftest] Using llama_compat mode: VLLMEngine -> {llama_url}/v1/chat/completions")
    yield llama_url


def _start_vllm_docker(base_url: str):
    if not _is_docker_available():
        pytest.skip("Docker not available. Install Docker or use VLLM_MODE=external")

    hf_model_path = _find_hf_model()
    models_dir = os.path.dirname(hf_model_path)
    model_name = os.path.basename(hf_model_path)

    print(f"\n[conftest] Starting vLLM via Docker (model={model_name})")

    # Remove existing container if any
    subprocess.run(
        ["docker", "rm", "-f", _VLLM_CONTAINER_NAME],
        capture_output=True, timeout=10,
    )

    cmd = [
        "docker", "run", "-d",
        "--name", _VLLM_CONTAINER_NAME,
        "-p", f"{_VLLM_PORT}:8002",
        "-v", f"{models_dir}:/models:ro",
        "vllm/vllm-openai:latest",
        "--model", f"/models/{model_name}",
        "--served-model-name", "Qwen2.5-1.5B-Instruct",
        "--host", "0.0.0.0",
        "--port", "8002",
        "--max-model-len", "2048",
        "--dtype", "auto",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        pytest.fail(f"Docker vLLM start failed: {result.stderr}")

    container_id = result.stdout.strip()[:12]
    print(f"[conftest] Docker container started: {container_id}")

    if not _wait_for_health(base_url, timeout=_SERVICE_START_TIMEOUT):
        logs = subprocess.run(
            ["docker", "logs", _VLLM_CONTAINER_NAME, "--tail", "50"],
            capture_output=True, text=True, timeout=10,
        )
        subprocess.run(["docker", "rm", "-f", _VLLM_CONTAINER_NAME], capture_output=True, timeout=10)
        pytest.fail(
            f"vLLM Docker failed to start within {_SERVICE_START_TIMEOUT}s.\n"
            f"Logs:\n{logs.stderr[-2000:]}"
        )

    print(f"[conftest] vLLM healthy at {base_url} (container={container_id})")
    yield base_url

    print("\n[conftest] Stopping vLLM Docker container...")
    subprocess.run(["docker", "rm", "-f", _VLLM_CONTAINER_NAME], capture_output=True, timeout=30)


def _start_vllm_native(base_url: str):
    hf_model = _find_hf_model()
    print(f"\n[conftest] Starting vLLM natively with {hf_model}")

    script_path = os.path.join(_PROJECT_ROOT, "scripts", "vllm_server.py")
    cmd = [
        sys.executable, script_path,
        "--model", hf_model,
        "--host", _VLLM_HOST,
        "--port", str(_VLLM_PORT),
        "--device", "cpu",
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_health(base_url, timeout=_SERVICE_START_TIMEOUT):
        process.terminate()
        process.wait(timeout=10)
        pytest.fail(f"vLLM native failed to start within {_SERVICE_START_TIMEOUT}s")

    print(f"[conftest] vLLM healthy at {base_url} (PID={process.pid})")
    yield base_url

    print("\n[conftest] Stopping vLLM...")
    try:
        process.terminate()
        process.wait(timeout=15)
    except Exception:
        logger.warning("_start_vllm_native: 未预期异常，按原逻辑降级", exc_info=True)
        process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="session")
def dual_engine_services(llama_cpp_service, vllm_service):
    return {
        "llama_cpp": llama_cpp_service,
        "vllm": vllm_service,
        "llama_cpp_host": _LLAMA_CPP_HOST,
        "llama_cpp_port": _LLAMA_CPP_PORT,
        "vllm_host": _VLLM_HOST,
        "vllm_port": _VLLM_PORT,
    }


@pytest.fixture
def consistency_test_cases():
    cases_path = os.path.join(
        _PROJECT_ROOT, "app", "inference", "data", "consistency_test_cases.json"
    )
    with open(cases_path, encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


@pytest.fixture
async def llama_cpp_engine_real(llama_cpp_service):
    from app.inference.llama_cpp import LlamaCppEngine
    engine = LlamaCppEngine(
        base_url=llama_cpp_service,
        default_max_tokens=512,
        default_temperature=0.1,
        timeout_seconds=120.0,
        max_retries=2,
    )
    yield engine
    await engine.close()


@pytest.fixture
async def vllm_engine_real(vllm_service):
    from app.inference.vllm import VLLMEngine
    engine = VLLMEngine(
        base_url=vllm_service,
        default_model="Qwen2.5-1.5B-Instruct",
        default_max_tokens=512,
        default_temperature=0.05,
        require_model=True,
        timeout_seconds=120.0,
        max_retries=2,
    )
    yield engine
    await engine.close()
