"""SEC测试套件专用配置

提供：
1. 实时进度可视化报告器（SECProgressReporter）
2. SEC专用fixture（覆盖全局autouse fixture以减少开销）
3. 测试阶段自动分类
4. 源码变更检测：HSM/emergency测试在源码未变时自动跳过
5. HSM Redis操作mock：消除测试中不必要的Redis连接开销
6. 共享密钥fixture：减少RSA密钥生成次数
"""
import sys
import os
import time
import hashlib
import json
import threading
import pytest
import structlog
from unittest.mock import patch, AsyncMock

logger = structlog.get_logger()

os.environ.setdefault("HSM_PRIVATE_KEY_PASSWORD", "test_password_for_unit_tests_only")

PHASE_ICONS = {
    "auth": "\U0001f510",
    "hsm": "\U0001f511",
    "token": "\U0001f504",
    "refresh": "\U0001f504",
    "desensitization": "\U0001f6e1",
    "audit": "\U0001f4cb",
    "compliance": "\U0001f4cb",
    "emergency": "\U0001f6a8",
    "consent": "\U0001f512",
    "sg": "\U0001f6e1",
    "security": "\U0001f510",
    "config": "\u2699",
    "redis": "\U0001f4be",
    "dicom": "\U0001f4ca",
    "e2e": "\U0001f517",
    "default": "\u25cf",
}

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
_HASH_CACHE_FILE = os.path.join(_CACHE_DIR, "source_hashes.json")

HSM_SOURCE_FILES = [
    os.path.join(_PROJECT_ROOT, "app", "security", "hsm.py"),
    os.path.join(_PROJECT_ROOT, "app", "security", "emergency_signing.py"),
    os.path.join(_PROJECT_ROOT, "app", "security", "auth.py"),
]

EMERGENCY_SOURCE_FILES = [
    os.path.join(_PROJECT_ROOT, "app", "security", "emergency_signing.py"),
    os.path.join(_PROJECT_ROOT, "app", "security", "hsm.py"),
]


def _compute_file_hash(filepath):
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None


def _compute_source_hash(source_files):
    combined = ""
    for fp in source_files:
        h = _compute_file_hash(fp)
        if h is None:
            return None
        combined += h + ":"
    return hashlib.sha256(combined.encode()).hexdigest()


def _load_hash_cache():
    try:
        if os.path.exists(_HASH_CACHE_FILE):
            with open(_HASH_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_hash_cache(cache):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_HASH_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _has_source_changed(key, source_files):
    current_hash = _compute_source_hash(source_files)
    if current_hash is None:
        return True
    cache = _load_hash_cache()
    cached_hash = cache.get(key)
    if cached_hash is None:
        return True
    if cached_hash != current_hash:
        cache[key] = current_hash
        _save_hash_cache(cache)
        return True
    return False


def _update_source_hash(key, source_files):
    current_hash = _compute_source_hash(source_files)
    if current_hash is not None:
        cache = _load_hash_cache()
        cache[key] = current_hash
        _save_hash_cache(cache)


def _classify_phase(name: str) -> str:
    lower = name.lower()
    for key in PHASE_ICONS:
        if key in lower:
            return key
    return "default"


class SECProgressReporter:
    """SEC测试套件实时进度报告器

    在测试执行过程中实时显示：
    - 当前进度（已完成/总数 百分比）
    - 当前测试阶段（自动分类）
    - 预计剩余时间（ETA）
    - 总耗时
    """

    def __init__(self):
        self.total = 0
        self.completed = 0
        self.start_time = None
        self.current_phase = "default"
        self.phase_counts = {}
        self.test_times = []
        self._last_print_len = 0

    def pytest_collection_modifyitems(self, session, config, items):
        self.total = len(items)
        for item in items:
            phase = _classify_phase(item.nodeid)
            self.phase_counts[phase] = self.phase_counts.get(phase, 0) + 1

    def pytest_runtest_logstart(self, nodeid, location):
        if self.start_time is None:
            self.start_time = time.time()
        self.current_phase = _classify_phase(nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        self.completed += 1
        if self.start_time is None:
            return

        elapsed = time.time() - self.start_time
        if self.completed > 0:
            avg = elapsed / self.completed
            remaining = avg * (self.total - self.completed)
        else:
            remaining = 0

        pct = self.completed / self.total * 100 if self.total > 0 else 0
        icon = PHASE_ICONS.get(self.current_phase, "\u25cf")
        test_name = location[2][:35] if len(location) > 2 else nodeid[:35]

        line = (
            f"  {icon} [{self.completed}/{self.total}] {pct:5.1f}% | "
            f"{test_name:<35} | "
            f"ETA: {remaining:6.1f}s | "
            f"\u23f1 {elapsed:6.1f}s"
        )

        pad = max(self._last_print_len - len(line), 0)
        sys.stderr.write(f"\r{line}{' ' * pad}")
        sys.stderr.flush()
        self._last_print_len = len(line)

    def pytest_runtest_makereport(self, item, call):
        if call.when == "call":
            duration = call.duration
            self.test_times.append((item.nodeid, duration))
            if duration > 5.0:
                logger.warning("sec_slow_test_detected", test=item.nodeid, duration=round(duration, 2))
            elif duration > 1.0:
                logger.info("sec_test_over_1s", test=item.nodeid, duration=round(duration, 2))

    def pytest_sessionfinish(self, session, exitstatus):
        if self.start_time:
            total_time = time.time() - self.start_time
            sys.stderr.write(
                f"\n\n  \u2713 SEC\u6d4b\u8bd5\u5b8c\u6210: "
                f"{self.completed}/{self.total} \u901a\u8fc7, "
                f"\u603b\u8017\u65f6: {total_time:.1f}s\n"
            )

            if self.phase_counts:
                sys.stderr.write("  \u9636\u6bb5\u5206\u5e03:\n")
                for phase, count in sorted(
                    self.phase_counts.items(), key=lambda x: -x[1]
                ):
                    icon = PHASE_ICONS.get(phase, "\u25cf")
                    bar = "\u2588" * min(count, 40)
                    sys.stderr.write(f"    {icon} {phase:<20} {count:>4} {bar}\n")

            slow_tests = [(name, dur) for name, dur in self.test_times if dur > 1.0]
            if slow_tests:
                slow_tests.sort(key=lambda x: -x[1])
                sys.stderr.write("  慢测试 (>1s):\n")
                for name, dur in slow_tests[:10]:
                    short_name = name.split("::")[-1] if "::" in name else name[:50]
                    bar = "█" * min(int(dur), 20)
                    sys.stderr.write(f"    {short_name:<50} {dur:>6.2f}s {bar}\n")

            sys.stderr.write("\n")
            sys.stderr.flush()


class SECStepReporter:
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.steps: list = []

    def step(self, name: str, result=None, expected=None):
        passed = True
        if expected is not None:
            passed = result == expected
        self.steps.append({
            "name": name,
            "result": result,
            "expected": expected,
            "passed": passed,
            "timestamp": time.time(),
        })
        return passed

    def summary(self) -> dict:
        return {
            "test": self.test_name,
            "steps": len(self.steps),
            "passed": sum(1 for s in self.steps if s["passed"]),
            "failed": sum(1 for s in self.steps if not s["passed"]),
        }


@pytest.fixture
def sec_steps(request):
    reporter = SECStepReporter(request.node.name)
    yield reporter
    summary = reporter.summary()
    if summary["failed"] > 0:
        logger.warning("sec_test_steps_failed", **summary)


def pytest_configure(config):
    config.pluginmanager.register(SECProgressReporter(), "sec_progress")
    config.addinivalue_line(
        "markers", "hsm_crypto: HSM crypto tests (skipped when source unchanged)"
    )
    config.addinivalue_line(
        "markers", "emergency_crypto: Emergency signing crypto tests (skipped when source unchanged)"
    )


def pytest_collection_modifyitems(session, config, items):
    skip_hsm = os.environ.get("SKIP_HSM_UNCHANGED", "false").lower() == "true"
    skip_emergency = os.environ.get("SKIP_EMERGENCY_UNCHANGED", "false").lower() == "true"

    if skip_hmm := (skip_hsm and not _has_source_changed("hsm", HSM_SOURCE_FILES)):
        _update_source_hash("hsm", HSM_SOURCE_FILES)

    if skip_em := (skip_emergency and not _has_source_changed("emergency", EMERGENCY_SOURCE_FILES)):
        _update_source_hash("emergency", EMERGENCY_SOURCE_FILES)

    for item in items:
        if skip_hmm and item.get_closest_marker("hsm_crypto"):
            item.add_marker(pytest.mark.skip(reason="HSM source unchanged, set SKIP_HSM_UNCHANGED=false to force run"))
        if skip_em and item.get_closest_marker("emergency_crypto"):
            item.add_marker(pytest.mark.skip(reason="Emergency signing source unchanged, set SKIP_EMERGENCY_UNCHANGED=false to force run"))


@pytest.fixture(autouse=True)
def reset_sec_singletons():
    yield
    from app.security.hsm import HSMManager
    from app.security.desensitization import DesensitizationEngine
    from app.security.refresh_token import MemoryRefreshTokenStore
    import app.security.emergency_signing as _esm
    import app.security.three_layer_desensitization as _tldm
    HSMManager._instance = None
    DesensitizationEngine._instance = None
    MemoryRefreshTokenStore._instance = None
    _esm._emergency_signing_instance = None
    _tldm._three_layer_manager_instance = None


@pytest.fixture(scope="session", autouse=True)
def mock_hsm_redis_operations():
    from app.security.hsm import HSMManager
    def _noop_safe_run(self, coro):
        coro.close()
    with patch.object(HSMManager, '_safe_run_async_task', _noop_safe_run):
        yield


@pytest.fixture(scope="session", autouse=True)
def mock_desensitization_redis():
    from app.security.desensitization import DesensitizationEngine
    with patch.object(DesensitizationEngine, 'load_state_from_redis', new_callable=AsyncMock), \
         patch.object(DesensitizationEngine, '_persist_token_to_redis'), \
         patch.object(DesensitizationEngine, '_publish_rules_update'), \
         patch.object(DesensitizationEngine, 'subscribe_rules_updates', new_callable=AsyncMock):
        yield


@pytest.fixture(scope="session", autouse=True)
def mock_desensitization_restore_redis():
    from app.security.desensitization_restore import DesensitizationRestoreService
    with patch.object(DesensitizationRestoreService, '_get_or_create_aes_key', new_callable=AsyncMock, return_value=(os.urandom(32), 1)), \
         patch.object(DesensitizationRestoreService, '_check_rate_limit_persistent', new_callable=AsyncMock, return_value=True):
        yield


def _make_lightweight_hsm():
    from app.security.hsm import HSMManager, KeySlot
    mgr = HSMManager.__new__(HSMManager)
    mgr._use_soft_hsm = os.environ.get("USE_SOFT_HSM", "false").lower() == "true"
    mgr._hsm_available = False
    mgr._key_slots = {slot: {} for slot in KeySlot}
    mgr._lock = threading.RLock()
    mgr._is_degraded = False
    mgr._degraded_at = None
    mgr._degraded_reason = None
    mgr._soft_hsm_enabled_at = None
    mgr._key_cleanup_timer = None
    mgr._periodic_check_timer = None
    mgr._pending_tasks = set()
    mgr._loop = None
    return mgr


@pytest.fixture(scope="session", autouse=True)
def pregenerate_hsm_keys(mock_hsm_redis_operations):
    from app.security.hsm import hsm_manager, KeySlot
    hsm_manager.generate_key_pair("shared_sign_key", slot=KeySlot.DATA_ENCRYPTION)
    hsm_manager.generate_key_pair("shared_enc_key", slot=KeySlot.DATA_ENCRYPTION)
    hsm_manager.generate_key_pair("shared_jwt_key", slot=KeySlot.JWT_SIGNING)
    hsm_manager.generate_key_pair("shared_emergency_key", slot=KeySlot.EMERGENCY_SIGNING)
    hsm_manager.generate_key_pair("shared_list_key_1", slot=KeySlot.DATA_ENCRYPTION)
    hsm_manager.generate_key_pair("shared_list_key_2", slot=KeySlot.DATA_ENCRYPTION)
    yield


@pytest.fixture(scope="class")
def hsm_with_slot_keys():
    mgr = _make_lightweight_hsm()
    from app.security.hsm import KeySlot
    for slot in KeySlot:
        mgr.generate_key_pair(f"slot_key_{slot.name}", slot=slot)
    yield mgr
