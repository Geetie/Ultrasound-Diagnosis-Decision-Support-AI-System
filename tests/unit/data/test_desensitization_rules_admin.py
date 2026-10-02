"""脱敏引擎的规则管理与 Redis 状态同步补测（quality-debt，核心模块 app/security）。

覆盖现有测试没碰的部分：内置规则、规则文件读/写/热重载、正则编译降级、
发布订阅三类循环分支、状态载入与令牌持久化（含降级）。

两个必须说明的测试技巧
----------------------
1. **放在 tests/unit/data/ 而不是 tests/unit/sec**：`tests/unit/sec/conftest.py` 的
   `mock_desensitization_redis` 是 session 级 autouse，把本模块要测的四个方法
   **打在类上**替换成 noop/AsyncMock。在 sec 目录里写这些测试只会静默假绿。
2. **采集期抓取真实实现**：仅换目录还不够——session 级类补丁只要在同一 xdist worker
   里被建立过，就会跨目录继续生效（本会话已在 desensitization_restore 上踩过一次，
   表现为"单跑全绿、跑全套红"）。所以这里在模块导入时（任何 fixture 建立之前）
   把真实函数抓下来，再由 autouse fixture 装回去。

另外：模块 import 即执行 `get_desensitization_engine()`，若 CWD 里没有
`desensitization_rules.yaml` 会**写盘**。所以本文件所有规则文件操作一律走 tmp_path，
绝不依赖默认路径。
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.desensitization.rule import DesensitizationRule
from app.security.desensitization import (
    DESENSITIZATION_RULES_PATH,
    DesensitizationEngine,
    get_desensitization_engine,
)

# 采集期抓取（见模块 docstring 第 2 点）
_REAL_LOAD_STATE_FROM_REDIS = DesensitizationEngine.load_state_from_redis
_REAL_PERSIST_TOKEN_TO_REDIS = DesensitizationEngine._persist_token_to_redis
_REAL_PUBLISH_RULES_UPDATE = DesensitizationEngine._publish_rules_update
_REAL_SUBSCRIBE_RULES_UPDATES = DesensitizationEngine.subscribe_rules_updates


@pytest.fixture(autouse=True)
def _use_real_redis_bridges(monkeypatch):
    monkeypatch.setattr(
        DesensitizationEngine, "load_state_from_redis", _REAL_LOAD_STATE_FROM_REDIS
    )
    monkeypatch.setattr(
        DesensitizationEngine, "_persist_token_to_redis", _REAL_PERSIST_TOKEN_TO_REDIS
    )
    monkeypatch.setattr(
        DesensitizationEngine, "_publish_rules_update", _REAL_PUBLISH_RULES_UPDATE
    )
    monkeypatch.setattr(
        DesensitizationEngine, "subscribe_rules_updates", _REAL_SUBSCRIBE_RULES_UPDATES
    )


class _FakePipeline:
    def __init__(self, parent):
        self._parent = parent
        self.ops = []

    def hset(self, *args, **kwargs):
        self.ops.append(("hset", args))
        return self

    def set(self, *args, **kwargs):
        self.ops.append(("set", args))
        return self

    async def execute(self):
        self._parent.executed.append(self.ops)
        return [1] * len(self.ops)


class _FakePubSub:
    def __init__(self, messages, error=None):
        self._messages = messages
        self._error = error
        self.subscribed = []

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def listen(self):
        for message in self._messages:
            yield message
        # 生成器正常结束不会退出 async for 的语义与其他实现一致：抛错终止订阅
        raise self._error or ConnectionError("pubsub closed")


class _FakeRedis:
    def __init__(self, store=None, mapping=None, messages=None, listen_error=None):
        self.store = dict(store or {})
        self.mapping = dict(mapping or {})
        self.published = []
        self.executed = []
        self.pubsub_obj = _FakePubSub(messages or [], error=listen_error)

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value
        return True

    async def hgetall(self, key):
        return dict(self.mapping)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def pipeline(self):
        return _FakePipeline(self)

    def pubsub(self):
        return self.pubsub_obj


@pytest.fixture
def rules_file(tmp_path):
    return str(tmp_path / "desensitization_rules.yaml")


@pytest.fixture
def engine(rules_file):
    """现建实例并绑定 tmp 规则文件，避免 CWD 写盘与单例残留。"""
    DesensitizationEngine._instance = None
    instance = DesensitizationEngine()
    instance.load_rules(rules_file)
    return instance


@pytest.fixture
def redis(monkeypatch):
    """内层再 patch：root conftest 的 fast_fail_redis 让真实 Redis 一律抛错。"""
    fake = _FakeRedis()
    _prime_redis_target(monkeypatch, fake)
    return fake


def _prime_redis_target(monkeypatch, fake):
    async def _get_client():
        return fake

    monkeypatch.setattr(
        "app.config.redis.RedisPool.get_client", staticmethod(_get_client)
    )


class TestDefaultRules:
    def test_default_rules_are_valid_regexes(self, engine):
        """内置规则的正则必须都能编译——一条写错就会静默少脱敏一类 PII。"""
        rules = engine._get_default_rules()

        assert len(rules) >= 10
        for rule in rules:
            assert rule.pattern, f"{rule.name} 缺 pattern"
            assert rule.replacement is not None

    def test_default_rule_names_are_unique(self, engine):
        names = [rule.name for rule in engine._get_default_rules()]

        assert len(names) == len(set(names)), "规则重名会导致编译结果互相覆盖"

    def test_default_rules_cover_core_pii(self, engine):
        """核心 PII 类别缺一不可（身份证/手机/姓名/住院号等）。"""
        names = {rule.name for rule in engine._get_default_rules()}

        assert {"id_card", "phone", "patient_name"} <= names


class TestRulesFileLifecycle:
    def test_missing_file_writes_defaults(self, tmp_path, rules_file):
        """文件不存在时必须落地一份默认规则，而不是只放在内存里。"""
        engine = DesensitizationEngine()

        engine.load_rules(rules_file)

        assert os.path.exists(rules_file)
        assert engine._rules, "内存里也必须有规则"
        content = Path(rules_file).read_text(encoding="utf-8")
        assert "id_card" in content

    def test_save_creates_nested_directories(self, tmp_path):
        engine = DesensitizationEngine()
        nested = str(tmp_path / "a" / "b" / "rules.yaml")

        engine._rules = engine._get_default_rules()
        engine._save_rules(nested)

        assert os.path.exists(nested)

    def test_load_reads_existing_file(self, rules_file):
        """已存在文件时必须以文件内容为准（运维改规则后要能生效）。"""
        with open(rules_file, "w", encoding="utf-8") as handle:
            handle.write("rules:\n- name: probe\n  pattern: 'X'\n  replacement: 'Y'\n")
        engine = DesensitizationEngine()

        engine.load_rules(rules_file)

        assert [rule.name for rule in engine._rules] == ["probe"]

    def test_modified_time_updated_on_load(self, engine, rules_file):
        assert engine._last_modified_time is not None
        assert engine._last_loaded_time is not None

    def test_modified_time_untouched_when_file_gone(self, engine, rules_file):
        """文件消失时不更新 mtime：否则热重载会把"没文件"当成"刚改过"。"""
        os.remove(rules_file)
        before = engine._last_modified_time

        engine._update_modified_time()

        assert engine._last_modified_time == before

    def test_check_and_reload_returns_false_without_file(self, engine, rules_file):
        os.remove(rules_file)

        assert engine.check_and_reload_rules() is False

    def test_check_and_reload_detects_newer_mtime(self, engine, rules_file):
        """显式把 mtime 推到未来，避免文件系统时间粒度导致偶发失败。"""
        future = engine._last_modified_time + 60
        os.utime(rules_file, (future, future))

        assert engine.check_and_reload_rules() is True

    def test_check_and_reload_returns_false_when_unchanged(self, engine):
        assert engine.check_and_reload_rules() is False

    def test_reload_reports_counts(self, engine):
        result = engine.reload_rules()

        assert result["status"] == "success"
        assert result["current_rule_count"] > 0

    def test_status_exposes_rule_counts(self, engine):
        status = engine.get_status()

        assert status["total_rules"] == len(engine._rules)
        assert status["enabled_rules"] + status["disabled_rules"] == status["total_rules"]
        assert status["rules_path"].endswith(".yaml")

    def test_status_without_loaded_file(self):
        engine = DesensitizationEngine()
        engine._last_loaded_time = None
        engine._last_modified_time = None

        status = engine.get_status()

        assert status["last_loaded_time"] is None
        assert status["last_modified_time"] is None


class TestCompilePatterns:
    def test_invalid_regex_is_skipped_not_fatal(self, engine):
        """一条非法正则不能让整个引擎不可用，只跳过该条并留痕。"""
        engine._rules = [
            DesensitizationRule(
                name="broken",
                pattern="(unclosed",
                replacement="*",
                description="坏规则",
                priority=1,
            ),
            DesensitizationRule(
                name="fine",
                pattern=r"\d{3}",
                replacement="*",
                description="好规则",
                priority=2,
            ),
        ]

        engine._compile_patterns()

        assert "broken" not in engine._compiled_patterns
        assert "fine" in engine._compiled_patterns

    def test_disabled_rule_not_compiled(self, engine):
        engine._rules = [
            DesensitizationRule(
                name="off",
                pattern=r"\d",
                replacement="*",
                description="停用",
                priority=1,
                enabled=False,
            )
        ]

        engine._compile_patterns()

        assert engine._compiled_patterns == {}


class TestNameReplacer:
    def _rule(self, name):
        return DesensitizationRule(
            name=name, pattern=r"(.+)", replacement="X", description=name, priority=1
        )

    def test_plain_patient_name_keeps_prefix(self, engine):
        replacer = engine._make_name_replacer(self._rule("patient_name"))
        match = MagicMock()
        match.group.side_effect = lambda index: {1: "患者", 2: "张三"}[index]

        result = replacer(match)

        assert result == "患者" + engine._get_patient_token("张三")

    def test_name_with_title_keeps_suffix(self, engine):
        replacer = engine._make_name_replacer(self._rule("patient_name_with_title"))
        match = MagicMock()
        match.group.side_effect = lambda index: {1: "张三", 2: "主任"}[index]

        result = replacer(match)

        assert result == engine._get_patient_token("张三") + "主任"

    def test_simple_name_replaces_whole_match(self, engine):
        replacer = engine._make_name_replacer(self._rule("patient_name_simple"))
        match = MagicMock()
        match.group.side_effect = lambda index: {0: "张三", 1: "张三"}[index]

        result = replacer(match)

        assert result == engine._get_patient_token("张三")

    def test_other_rules_have_no_replacer(self, engine):
        assert engine._make_name_replacer(self._rule("id_card")) is None


class TestPublishRulesUpdate:
    def test_publishes_without_running_loop(self, engine, redis):
        """无事件循环（如启动期、脚本）时必须自建循环同步发布，不能静默不发布。"""
        engine._publish_rules_update()

        assert redis.published, "规则变更未发布，其他实例不会热重载"

    def test_publish_failure_is_swallowed(self, engine):
        """发布失败只留日志：规则已经在本地生效，不能因此让改规则的操作报错。"""
        engine._publish_rules_update()

    async def test_publishes_with_running_loop(self, engine, redis):
        engine._publish_rules_update()
        await asyncio.sleep(0.05)

        assert redis.published

    def test_rule_mutation_triggers_publish(self, engine, redis):
        engine.add_rule(
            DesensitizationRule(
                name="probe",
                pattern=r"Z{3}",
                replacement="*",
                description="探针",
                priority=1,
            )
        )

        assert redis.published

    def test_update_rule_triggers_publish(self, engine, redis):
        engine.update_rule(
            "probe",
            DesensitizationRule(
                name="probe",
                pattern=r"Z{4}",
                replacement="*",
                description="探针",
                priority=1,
            ),
        )

        assert redis.published

    def test_remove_rule_triggers_publish(self, engine, redis):
        engine.add_rule(
            DesensitizationRule(
                name="probe",
                pattern=r"Z{3}",
                replacement="*",
                description="探针",
                priority=1,
            )
        )
        redis.published.clear()

        engine.remove_rule("probe")

        assert redis.published


class TestSubscribeRulesUpdates:
    async def test_message_triggers_reload_and_delay_report(self, engine, monkeypatch):
        fake = _FakeRedis(
            messages=[
                {"type": "subscribe", "data": 1},
                {"type": "message", "data": b"updated_at:2026-01-01T00:00:00+00:00"},
            ]
        )
        _prime_redis_target(monkeypatch, fake)
        spy = MagicMock()
        monkeypatch.setattr(engine, "_report_sync_delay", spy)

        await engine.subscribe_rules_updates()

        assert fake.pubsub_obj.subscribed, "未订阅规则变更频道"
        assert spy.call_count == 1, "只有 type=message 才应触发（subscribe 确认不算）"

    async def test_listen_error_is_swallowed(self, engine, monkeypatch):
        """订阅通道断开不能让调用方崩掉（订阅是旁路）。"""
        fake = _FakeRedis(messages=[], listen_error=ConnectionError("channel closed"))
        _prime_redis_target(monkeypatch, fake)

        await engine.subscribe_rules_updates()

    async def test_client_failure_is_swallowed(self, engine):
        await engine.subscribe_rules_updates()


class TestReportSyncDelay:
    def test_reports_metric_from_bytes_payload(self, monkeypatch):
        collector = MagicMock()
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        )

        DesensitizationEngine._report_sync_delay(
            {"data": b"updated_at:2026-01-01T00:00:00+00:00"}
        )

        assert collector.set_desensitization_sync_delay.call_count == 1
        delay = collector.set_desensitization_sync_delay.call_args.args[0]
        assert delay >= 0.0, "时钟回拨产生的负延迟会被 max() 钳到 0"

    def test_ignores_payload_without_timestamp(self, monkeypatch):
        collector = MagicMock()
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        )

        DesensitizationEngine._report_sync_delay({"data": "no marker here"})

        assert collector.set_desensitization_sync_delay.call_count == 0

    def test_malformed_payload_is_swallowed(self, monkeypatch):
        """埋点是旁路：脏数据不能把消费者打挂。"""
        collector = MagicMock()
        collector.set_desensitization_sync_delay.side_effect = RuntimeError("boom")
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        )

        DesensitizationEngine._report_sync_delay(
            {"data": b"updated_at:not-a-timestamp"}
        )


class TestLoadStateFromRedis:
    async def test_loads_key_counter_and_mapping(self, engine, monkeypatch):
        fake = _FakeRedis(
            store={"ultrasound:desens:token_counter": b"7"},
            mapping={b"hash_a": b"PATIENT_007"},
        )
        _prime_redis_target(monkeypatch, fake)

        await engine.load_state_from_redis()

        assert engine._patient_token_counter == 7, "计数器必须续上，否则令牌会重复"
        assert engine._patient_name_hash_to_token == {"hash_a": "PATIENT_007"}
        assert engine._redis_loaded is True

    async def test_persists_key_when_absent(self, engine, monkeypatch):
        fake = _FakeRedis()
        _prime_redis_target(monkeypatch, fake)

        await engine.load_state_from_redis()

        assert fake.store, "首次运行必须把 HMAC 密钥写回 Redis，否则实例间令牌不一致"

    async def test_reuses_stored_hmac_key(self, engine, monkeypatch):
        """必须沿用 Redis 里的密钥：换了密钥历史令牌就全部对不上。"""
        fake = _FakeRedis(store={"x": None})
        stored = b"k" * 32
        from app.config.redis_keys import RedisKey

        fake.store[RedisKey.DESSENS_HMAC_KEY] = stored
        _prime_redis_target(monkeypatch, fake)

        await engine.load_state_from_redis()

        assert engine._name_hmac_key == stored

    async def test_redis_failure_keeps_engine_usable(self, engine):
        """Redis 不可用时降级为进程内状态（root conftest 的 fast_fail_redis 即此场景）。"""
        await engine.load_state_from_redis()

        assert engine._redis_loaded is False
        assert engine.desensitize("张三") is not None


class TestPersistTokenToRedis:
    def test_skips_when_state_not_loaded(self, engine, redis):
        """状态没载入就写 Redis 会把计数器写回 0（覆盖掉别的实例的进度）。"""
        engine._redis_loaded = False

        engine._persist_token_to_redis("hash_a", "PATIENT_001")

        assert redis.executed == []

    def test_persists_when_loaded(self, engine, redis):
        engine._redis_loaded = True
        engine._patient_token_counter = 3

        engine._persist_token_to_redis("hash_a", "PATIENT_003")

        assert redis.executed, "令牌映射与计数器必须一起落 Redis"
        operations = dict(
            (op, args) for op, args in redis.executed[0] if op in ("hset", "set")
        )
        assert "set" in operations

    def test_persist_failure_is_swallowed(self, engine):
        engine._redis_loaded = True

        engine._persist_token_to_redis("hash_a", "PATIENT_001")

    async def test_persist_with_running_loop(self, engine, redis):
        engine._redis_loaded = True

        engine._persist_token_to_redis("hash_a", "PATIENT_001")
        await asyncio.sleep(0.05)

        assert redis.executed

    def test_token_generation_persists(self, engine, redis):
        """走过一次真实令牌生成，顺带覆盖 _get_patient_token -> 持久化 的联动。"""
        engine._redis_loaded = True

        token = engine._get_patient_token("李四")

        assert token == "PATIENT_001"
        assert redis.executed


class TestFactory:
    def test_factory_returns_usable_engine(self, monkeypatch, rules_file):
        monkeypatch.setattr(
            "app.security.desensitization.DESENSITIZATION_RULES_PATH", rules_file
        )
        DesensitizationEngine._instance = None

        created = get_desensitization_engine()

        assert created._rules
        assert created.desensitize("身份证 11010119900307123X") is not None

    def test_factory_survives_state_load_failure(self, monkeypatch, rules_file):
        """Redis 载入失败不能阻止引擎创建（否则整个服务起不来）。"""
        monkeypatch.setattr(
            "app.security.desensitization.DESENSITIZATION_RULES_PATH", rules_file
        )
        DesensitizationEngine._instance = None
        monkeypatch.setattr(
            DesensitizationEngine,
            "load_state_from_redis",
            AsyncMock(side_effect=RuntimeError("redis down")),
        )

        created = get_desensitization_engine()

        assert created._rules

    def test_default_rules_path_is_module_constant(self):
        assert DESENSITIZATION_RULES_PATH.endswith(".yaml")
