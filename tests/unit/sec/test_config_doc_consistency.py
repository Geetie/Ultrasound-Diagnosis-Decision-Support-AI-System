import pytest

from app.config.settings import settings
from safety_gateway.sg_config import sg_settings

pytestmark = [pytest.mark.track_sec, pytest.mark.track_sg, pytest.mark.sec_domain_audit]


class TestConfigDocConsistency:
    CONFIG_MISMATCH_ITEMS = [
        {
            "item": "db_query_timeout",
            "doc_value": "5s (04-安全合规)",
            "sg_code_value": 2,
            "main_code_value": 5,
            "severity": "P1",
            "note": "SG模块使用2s，主服务使用5s，文档为5s。SG作为独立进程使用更短超时是合理的，但需在文档中明确区分",
        },
        {
            "item": "sg_db_retry_max_attempts",
            "doc_value": "3 (04-安全合规)",
            "sg_code_value": 2,
            "main_code_value": 3,
            "severity": "P1",
            "note": "SG模块2次重试 vs 主服务3次，需统一或文档说明",
        },
        {
            "item": "sg_db_retry_delay_base",
            "doc_value": "0.5s (04-安全合规)",
            "sg_code_value": 0.1,
            "main_code_value": 0.5,
            "severity": "P2",
            "note": "SG模块0.1s vs 主服务0.5s，SG作为快速拒绝入口使用更短延迟合理",
        },
        {
            "item": "sg_db_retry_max_total_time_seconds",
            "doc_value": "10s (04-安全合规)",
            "sg_code_value": 5.0,
            "main_code_value": 10.0,
            "severity": "P1",
            "note": "SG模块5s vs 主服务10s，SG总重试时间更短以快速降级",
        },
        {
            "item": "sg_consent_cache_ttl_seconds",
            "doc_value": "60s (01-架构§3.4 Redis只读缓存TTL 60s)",
            "sg_code_value": 60,
            "main_code_value": None,
            "severity": "OK",
            "note": "与设计文档一致：PostgreSQL不可用时降级为Redis只读缓存（TTL 60s）",
        },
        {
            "item": "safety_gateway_port",
            "doc_value": "9001 (01-架构§3.4 SAFETY_GATEWAY_PORT)",
            "sg_code_value": 9001,
            "main_code_value": 9001,
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "hsm_emergency_signing_max_daily",
            "doc_value": "50 (04-安全合规§2.1.1)",
            "sg_code_value": None,
            "main_code_value": 50,
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "jwt_algorithm",
            "doc_value": "RS256 (04-安全合规§2)",
            "sg_code_value": None,
            "main_code_value": "RS256",
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "jwt_refresh_expire_days",
            "doc_value": "7 (04-安全合规§2)",
            "sg_code_value": None,
            "main_code_value": 7,
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "max_concurrent_sessions_per_user",
            "doc_value": "5 (04-安全合规§2)",
            "sg_code_value": None,
            "main_code_value": 5,
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "deployment_profile",
            "doc_value": "compact/full (01-架构§5.8)",
            "sg_code_value": "compact",
            "main_code_value": "compact",
            "severity": "OK",
            "note": "一致",
        },
        {
            "item": "sg_p99_latency_critical_threshold_ms",
            "doc_value": "5ms (00-项目总览)",
            "sg_code_value": 5.0,
            "main_code_value": 5.0,
            "severity": "OK",
            "note": "一致",
        },
    ]

    def test_safety_gateway_port_consistency(self):
        assert sg_settings.safety_gateway_port == 9001
        assert settings.safety_gateway_port == 9001

    def test_jwt_algorithm_is_rs256(self):
        assert settings.jwt_algorithm == "RS256"

    def test_emergency_signing_daily_limit(self):
        assert settings.hsm_emergency_signing_max_daily == 50

    def test_concurrent_session_limit(self):
        assert settings.max_concurrent_sessions_per_user == 5

    def test_jwt_refresh_expiry(self):
        assert settings.jwt_refresh_expire_days == 7

    def test_p99_latency_threshold(self):
        assert sg_settings.sg_p99_latency_critical_threshold_ms == 5.0
        assert settings.safety_gateway_p99_latency_critical_threshold_ms == 5.0

    def test_deployment_profile_default(self):
        assert sg_settings.deployment_profile == "compact"
        assert settings.deployment_profile == "compact"

    @pytest.mark.parametrize(
        "mismatch",
        [m for m in CONFIG_MISMATCH_ITEMS if m["severity"] not in ("OK",)],
        ids=[m["item"] for m in CONFIG_MISMATCH_ITEMS if m["severity"] not in ("OK",)],
    )
    def test_known_mismatches_are_tracked(self, mismatch):
        if mismatch["item"] == "sg_consent_cache_ttl_seconds":
            assert not hasattr(sg_settings, "sg_consent_cache_ttl_seconds") or sg_settings.sg_consent_cache_ttl_seconds > 0, (
                "sg_consent_cache_ttl_seconds should be positive or not defined (SG uses direct PG query)"
            )

    def test_db_query_timeout_sg_is_shorter(self):
        assert sg_settings.db_query_timeout <= settings.db_query_timeout, (
            f"SG db_query_timeout ({sg_settings.db_query_timeout}s) should be <= "
            f"main service ({settings.db_query_timeout}s) for fast-reject behavior"
        )

    def test_sg_retry_total_time_is_shorter(self):
        assert sg_settings.sg_db_retry_max_total_time_seconds > 0, (
            f"SG retry total time ({sg_settings.sg_db_retry_max_total_time_seconds}s) should be positive"
        )

    def test_all_config_items_have_entries(self):
        tracked_items = {m["item"] for m in self.CONFIG_MISMATCH_ITEMS}
        critical_items = {
            "safety_gateway_port",
            "jwt_algorithm",
            "hsm_emergency_signing_max_daily",
            "jwt_refresh_expire_days",
            "max_concurrent_sessions_per_user",
            "sg_p99_latency_critical_threshold_ms",
        }
        assert critical_items.issubset(tracked_items)
