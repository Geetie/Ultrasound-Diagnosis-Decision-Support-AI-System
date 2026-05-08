import pytest
from app.config.settings import settings
from app.config.redis import RedisPool


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