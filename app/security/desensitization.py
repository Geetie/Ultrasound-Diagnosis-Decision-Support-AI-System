import hashlib
import hmac
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

from app.common.desensitization.rule import DesensitizationRule

logger = structlog.get_logger()

DESENSITIZATION_RULES_PATH = os.environ.get(
    "DESENSITIZATION_RULES_PATH",
    "desensitization_rules.yaml"
)

class DesensitizationRules(BaseModel):
    rules: list[DesensitizationRule]

class DesensitizationEngine:
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._rules = []
                cls._instance._compiled_patterns = {}
                cls._instance._rules_path = DESENSITIZATION_RULES_PATH
                cls._instance._last_modified_time = None
                cls._instance._last_loaded_time = None
                cls._instance._rules_lock = threading.RLock()
                cls._instance._patient_name_hash_to_token: dict[str, str] = {}
                cls._instance._patient_token_counter: int = 0
                cls._instance._token_lock = threading.Lock()
                cls._instance._name_hmac_key = os.urandom(32)
                cls._instance._redis_loaded = False
        return cls._instance

    def load_rules(self, rules_path: str = None):
        if rules_path is None:
            rules_path = DESENSITIZATION_RULES_PATH

        self._rules_path = rules_path

        if not os.path.exists(rules_path):
            self._rules = self._get_default_rules()
            self._save_rules(rules_path)
        else:
            with open(rules_path, encoding='utf-8') as f:
                rules_data = yaml.safe_load(f)
                self._rules = [DesensitizationRule(**rule) for rule in rules_data.get('rules', [])]

        self._update_modified_time()
        self._compile_patterns()

    def _update_modified_time(self):
        """更新规则文件修改时间"""
        if os.path.exists(self._rules_path):
            self._last_modified_time = os.path.getmtime(self._rules_path)
            self._last_loaded_time = datetime.now()

    def check_and_reload_rules(self) -> bool:
        """检查规则文件是否被修改，如果是则重新加载"""
        if not os.path.exists(self._rules_path):
            return False

        current_modified_time = os.path.getmtime(self._rules_path)

        if self._last_modified_time is None or current_modified_time > self._last_modified_time:
            logger.info("desensitization_rules_changed_detected", path=self._rules_path)
            self.load_rules(self._rules_path)
            return True

        return False

    def reload_rules(self) -> dict[str, Any]:
        """强制重新加载规则文件"""
        previous_count = len(self._rules)
        self.load_rules(self._rules_path)
        current_count = len(self._rules)

        logger.info(
            "desensitization_rules_reloaded",
            previous_count=previous_count,
            current_count=current_count
        )

        return {
            "status": "success",
            "previous_rule_count": previous_count,
            "current_rule_count": current_count,
            "reloaded_at": datetime.now().isoformat()
        }

    def _get_default_rules(self) -> list[DesensitizationRule]:
        return [
            DesensitizationRule(
                name="id_card",
                pattern=r'(?<![*\d])\d{17}[\dXx](?![*\d])',
                replacement='********************',
                description="身份证号脱敏",
                priority=1
            ),
            DesensitizationRule(
                name="phone",
                pattern=r'(?<![*\d])(1[3-9]\d)(\d{4})(\d{4})(?![*\d])',
                replacement='********\\3',
                description="手机号脱敏，保留后4位",
                priority=2
            ),
            DesensitizationRule(
                name="patient_name",
                pattern=r'((?:患者姓名：|患者姓名|患者：|患者:|患者姓名:|患者|病人)\s*)([\u4e00-\u9fa5]{2,4})',
                replacement='\\1[REDACTED_NAME]',
                description="患者姓名脱敏（处理带前缀的姓名）",
                priority=3
            ),
            DesensitizationRule(
                name="patient_name_with_title",
                pattern=r'([\u4e00-\u9fa5]{2,4})(女士|先生|同志|医生)',
                replacement='[REDACTED_NAME]\\2',
                description="患者姓名脱敏（处理带称谓的姓名）",
                priority=4
            ),
            DesensitizationRule(
                name="patient_name_simple",
                pattern=r'(?:^|[：:，,\s\n\r])([\u4e00-\u9fa5]{2,4})(?=[，,。：:\s\n\r]|$)',
                replacement='[REDACTED_NAME]',
                description="患者姓名脱敏（处理独立的2-4字姓名，支持嵌入上下文）",
                priority=5
            ),
            DesensitizationRule(
                name="birth_date",
                pattern=r'(?:(?:出生|生日|诞辰)\s*(?:日期|时间|年月日|年月)?\s*[:：]?\s*)(?P<year_before>\d{4})-\d{2}-\d{2}|(?P<year_after>\d{4})-\d{2}-\d{2}\s*(?:出生|生日|诞辰)',
                replacement='\\g<year_before>\\g<year_after>-**-**',
                description="出生日期脱敏，仅保留年份（需上下文关键词）",
                priority=5
            ),
            DesensitizationRule(
                name="address",
                pattern=r'([\u4e00-\u9fa5]{2,8}?市)([\u4e00-\u9fa5][^，,]*)',
                replacement='\\1***',
                description="住址脱敏，仅保留到市级",
                priority=6
            ),
            DesensitizationRule(
                name="medical_record",
                pattern=r'(?<![A-Za-z\d])MR\d{8,12}(?![A-Za-z\d])',
                replacement='MR********',
                description="病历号脱敏",
                priority=7
            ),
            DesensitizationRule(
                name="email",
                pattern=r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]+',
                replacement='EMAIL_***',
                description="邮箱地址脱敏",
                priority=8
            ),
            DesensitizationRule(
                name="study_instance_uid",
                pattern=r'(?<!\w)\d+\.\d+\.\d+\.\d+\.\d+\.\d+\.\d+(\.\d+)*(?!\w)',
                replacement='STUDY_INSTANCE_UID',
                description="DICOM Study Instance UID脱敏",
                priority=9
            ),
            DesensitizationRule(
                name="ip_address",
                pattern=r'(?<!\d\.)(?<!版本\s)(?<!v\s)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\.\d)',
                replacement='***.***.***.***',
                description="IP地址脱敏",
                priority=10
            ),
            DesensitizationRule(
                name="hospital_id",
                pattern=r'(?<!\w)HOSP_[A-Za-z0-9]{8,12}(?!\w)',
                replacement='HOSPITAL_***',
                description="医院ID脱敏",
                priority=11,
                enabled=False
            )
        ]

    def _save_rules(self, rules_path: str):
        rules_data = {"rules": [rule.model_dump() for rule in self._rules]}
        directory = os.path.dirname(rules_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        with open(rules_path, 'w', encoding='utf-8') as f:
            yaml.dump(rules_data, f, default_flow_style=False, allow_unicode=True)

    def _compile_patterns(self):
        self._compiled_patterns = {}
        for rule in self._rules:
            if rule.enabled:
                try:
                    self._compiled_patterns[rule.name] = re.compile(rule.pattern)
                except re.error as e:
                    logger.warning(
                        "desensitization_rule_compile_failed",
                        rule_name=rule.name,
                        pattern=rule.pattern,
                        error=str(e)
                    )

    def _hash_patient_name(self, original_name: str) -> str:
        return hmac.new(
            self._name_hmac_key,
            original_name.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _get_patient_token(self, original_name: str) -> str:
        name_hash = self._hash_patient_name(original_name)
        with self._token_lock:
            if name_hash in self._patient_name_hash_to_token:
                return self._patient_name_hash_to_token[name_hash]
            self._patient_token_counter += 1
            token = f"PATIENT_{self._patient_token_counter:03d}"
            self._patient_name_hash_to_token[name_hash] = token
            self._persist_token_to_redis(name_hash, token)
            return token

    def _make_name_replacer(self, rule: DesensitizationRule):
        if rule.name in ("patient_name", "patient_name_with_title", "patient_name_simple"):
            def replacer(match):
                if rule.name == "patient_name":
                    original = match.group(2)
                    token = self._get_patient_token(original)
                    return match.group(1) + token
                elif rule.name == "patient_name_with_title":
                    original = match.group(1)
                    token = self._get_patient_token(original)
                    return token + match.group(2)
                else:
                    original = match.group(1)
                    token = self._get_patient_token(original)
                    prefix = match.group(0)[:len(match.group(0)) - len(original)]
                    return prefix + token
            return replacer
        return None

    def desensitize(self, text: str | None) -> str | None:
        if text is None:
            return None
        if not text:
            return text

        with self._rules_lock:
            sorted_rules: list[DesensitizationRule] = sorted(
                [r for r in self._rules if r.enabled],
                key=lambda x: x.priority
            )
            patterns_snapshot = dict(self._compiled_patterns)

        result: str = text

        for rule in sorted_rules:
            pattern: re.Pattern | None = patterns_snapshot.get(rule.name)
            if pattern:
                name_replacer = self._make_name_replacer(rule)
                if name_replacer:
                    result = pattern.sub(name_replacer, result)
                else:
                    result = pattern.sub(rule.replacement, result)

        return result

    MAX_RECURSION_DEPTH = 20

    def _desensitize_list(self, items: list, _depth: int = 0) -> list:
        if _depth >= self.MAX_RECURSION_DEPTH:
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
        return [
            self.desensitize(item) if isinstance(item, str) else
            self.desensitize_dict(item, _depth + 1) if isinstance(item, dict) else
            self._desensitize_list(item, _depth + 1) if isinstance(item, list) else
            item
            for item in items
        ]

    def desensitize_dict(self, data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
        if _depth >= self.MAX_RECURSION_DEPTH:
            logger.error("desensitize_dict_recursion_depth_exceeded", depth=_depth)
            raise ValueError(f"Desensitization recursion depth exceeded (max={self.MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.desensitize(value)
            elif isinstance(value, dict):
                result[key] = self.desensitize_dict(value, _depth + 1)
            elif isinstance(value, list):
                result[key] = self._desensitize_list(value, _depth + 1)
            elif isinstance(value, tuple):
                result[key] = tuple(
                    self.desensitize(item) if isinstance(item, str) else
                    self.desensitize_dict(item, _depth + 1) if isinstance(item, dict) else
                    self._desensitize_list(item, _depth + 1) if isinstance(item, list) else
                    item
                    for item in value
                )
            elif isinstance(value, set):
                result[key] = {
                    self.desensitize(item) if isinstance(item, str) else
                    item
                    for item in value
                }
            else:
                result[key] = value
        return result

    def get_rules(self) -> list[DesensitizationRule]:
        return self._rules

    def add_rule(self, rule: DesensitizationRule):
        with self._rules_lock:
            self._rules.append(rule)
            self._compile_patterns()
        self._publish_rules_update()

    def update_rule(self, rule_name: str, updated_rule: DesensitizationRule):
        with self._rules_lock:
            for i, rule in enumerate(self._rules):
                if rule.name == rule_name:
                    self._rules[i] = updated_rule
                    break
            self._compile_patterns()
        self._publish_rules_update()

    def remove_rule(self, rule_name: str):
        with self._rules_lock:
            self._rules = [r for r in self._rules if r.name != rule_name]
            self._compile_patterns()
        self._publish_rules_update()

    def get_status(self) -> dict[str, Any]:
        """获取脱敏引擎状态"""
        enabled_count = sum(1 for r in self._rules if r.enabled)
        disabled_count = len(self._rules) - enabled_count

        return {
            "rules_path": self._rules_path,
            "total_rules": len(self._rules),
            "enabled_rules": enabled_count,
            "disabled_rules": disabled_count,
            "last_loaded_time": self._last_loaded_time.isoformat() if self._last_loaded_time else None,
            "last_modified_time": datetime.fromtimestamp(self._last_modified_time).isoformat() if self._last_modified_time else None
        }

    def _publish_rules_update(self):
        try:
            import asyncio

            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            async def _do_publish():
                redis_client = await RedisPool.get_client()
                await redis_client.publish(
                    RedisKey.DESSENS_RULES_UPDATE,
                    f"updated_at:{datetime.now(timezone.utc).isoformat()}"
                )
                logger.info("desensitization_rules_update_published")

            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(_do_publish())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_do_publish())
                loop.close()
        except Exception as e:
            logger.warning("desensitization_rules_publish_failed", error=str(e))

    async def subscribe_rules_updates(self):
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(RedisKey.DESSENS_RULES_UPDATE)
            logger.info("desensitization_rules_subscriber_started")
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    logger.info("desensitization_rules_update_received", data=message.get("data"))
                    self.load_rules()
                    self._report_sync_delay(message)
        except Exception as e:
            logger.error("desensitization_rules_subscriber_failed", error=str(e))

    @staticmethod
    def _report_sync_delay(message: dict) -> None:
        try:
            data = message.get("data", "")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if "updated_at:" in data:
                published_str = data.split("updated_at:", 1)[1].strip()
                from datetime import datetime as _dt
                published_time = _dt.fromisoformat(published_str.replace("Z", "+00:00"))
                delay = (_dt.now(timezone.utc) - published_time).total_seconds()
                from app.observability.metrics_collector import get_metrics_collector
                get_metrics_collector().set_desensitization_sync_delay(max(delay, 0.0))
        except Exception:
            pass

    async def load_state_from_redis(self):
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            redis_client = await RedisPool.get_client()

            stored_key = await redis_client.get(RedisKey.DESSENS_HMAC_KEY)
            if stored_key:
                self._name_hmac_key = stored_key if isinstance(stored_key, bytes) else stored_key.encode()
                logger.info("desens_hmac_key_loaded_from_redis")
            else:
                await redis_client.set(RedisKey.DESSENS_HMAC_KEY, self._name_hmac_key)
                logger.info("desens_hmac_key_persisted_to_redis")

            stored_counter = await redis_client.get(RedisKey.DESSENS_TOKEN_COUNTER)
            if stored_counter:
                self._patient_token_counter = int(stored_counter)

            stored_mapping = await redis_client.hgetall(RedisKey.DESSENS_TOKEN_MAPPING)
            if stored_mapping:
                self._patient_name_hash_to_token = {
                    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in stored_mapping.items()
                }

            self._redis_loaded = True
            logger.info(
                "desens_state_loaded_from_redis",
                token_count=len(self._patient_name_hash_to_token),
                counter=self._patient_token_counter,
            )
        except Exception as e:
            logger.warning("desens_state_load_from_redis_failed", error=str(e))

    async def ensure_state_loaded(self) -> None:
        """幂等地把 Redis 侧状态（HMAC 密钥 / 令牌映射 / 计数器）载入本进程。

        必须在**事件循环内**调用（本项目的落点是 lifespan 启动期）。原实现在
        同步工厂里用 ``asyncio.new_event_loop().run_until_complete()`` 硬跑，
        一旦进程已有事件循环就抛 ``RuntimeError: Cannot run the event loop while
        another loop is running`` 并丢弃协程 —— 于是 HMAC 密钥**永远没从 Redis
        恢复**，每次重启都换一把随机密钥，历史令牌与还原映射全部失配。
        详见 :func:`get_desensitization_engine` 的说明。
        """
        if self._redis_loaded:
            return
        await self.load_state_from_redis()

    def _persist_token_to_redis(self, name_hash: str, token: str):
        if not self._redis_loaded:
            return
        try:
            import asyncio

            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            async def _do_persist():
                redis_client = await RedisPool.get_client()
                pipe = redis_client.pipeline()
                pipe.hset(RedisKey.DESSENS_TOKEN_MAPPING, name_hash, token)
                pipe.set(RedisKey.DESSENS_TOKEN_COUNTER, str(self._patient_token_counter))
                await pipe.execute()

            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(_do_persist())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_do_persist())
                loop.close()
        except Exception as e:
            logger.warning("desens_token_persist_failed", error=str(e))

def get_desensitization_engine() -> DesensitizationEngine:
    """返回进程内单例（只加载规则；Redis 状态改由启动期 ``ensure_state_loaded`` 载入）。

    ## 为什么这里不再"顺手"跑一次异步载入（2026-09-28 真机部署暴露）

    原实现为了在同步工厂里调用 ``load_state_from_redis()``，临时
    ``asyncio.new_event_loop().run_until_complete(...)``。而本模块在 **import 期**
    就以 ``desensitization_engine = get_desensitization_engine()`` 触发这段代码，
    那时进程往往已有事件循环 → 抛
    ``RuntimeError: Cannot run the event loop while another loop is running``，
    协程被丢弃（真机日志同时出现 ``RuntimeWarning: coroutine
    'DesensitizationEngine.load_state_from_redis' was never awaited``）。

    后果不是"少了个优化"，而是**正确性**：``_name_hmac_key`` 保持
    ``os.urandom(32)`` 的随机初值，每次重启都换一把密钥 —— 同一个患者姓名前后两次
    跑出的令牌不同，历史令牌与还原映射全部对不上（restore 服务形同失效）。
    因此改为在 lifespan 启动期显式 ``await ensure_state_loaded()``。
    """
    engine = DesensitizationEngine()
    engine.load_rules()
    return engine

desensitization_engine = get_desensitization_engine()
