from prometheus_client import Counter, Gauge, Histogram


class MetricsDefinitions:

    def _init_latency_metrics(self) -> None:
        r = self._registry
        self.nav_latency = Histogram(
            "ultrasound_nav_latency_seconds",
            "Real-time navigation end-to-end latency",
            buckets=[0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0],
            registry=r,
        )
        self.assessment_latency = Histogram(
            "ultrasound_assessment_latency_seconds",
            "Assisted assessment latency",
            buckets=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
            registry=r,
        )
        self.diagnosis_latency = Histogram(
            "ultrasound_diagnosis_latency_seconds",
            "Complete diagnosis latency",
            ["with_retry"],
            buckets=[1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0],
            registry=r,
        )
        self.multi_organ_latency = Histogram(
            "ultrasound_multi_organ_latency_seconds",
            "Multi-organ joint diagnosis latency",
            ["profile", "with_retry"],
            buckets=[5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 45.0],
            registry=r,
        )

    def _init_hallucination_metrics(self) -> None:
        r = self._registry
        self.severe_hallucination_rate = Gauge(
            "ultrasound_severe_hallucination_rate",
            "Severe hallucination rate",
            registry=r,
        )
        self.overall_hallucination_rate = Gauge(
            "ultrasound_overall_hallucination_rate",
            "Overall hallucination rate",
            registry=r,
        )
        self.high_risk_hallucination_rate = Gauge(
            "ultrasound_high_risk_hallucination_rate",
            "High-risk disease hallucination rate",
            registry=r,
        )
        self.retrieval_empty_rate = Gauge(
            "ultrasound_retrieval_empty_rate",
            "Retrieval empty result rate",
            registry=r,
        )
        self.service_health_rate = Gauge(
            "ultrasound_service_health_rate",
            "Service health rate",
            registry=r,
        )

    def _init_gpu_metrics(self) -> None:
        r = self._registry
        self.gpu_memory_utilization = Gauge(
            "ultrasound_gpu_memory_utilization",
            "GPU memory utilization",
            ["device"],
            registry=r,
        )
        self.gpu_fragmentation_rate = Gauge(
            "ultrasound_gpu_fragmentation_rate",
            "GPU memory fragmentation rate",
            ["device"],
            registry=r,
        )
        self.gpu_fragmentation_duration_seconds = Gauge(
            "ultrasound_gpu_fragmentation_duration_seconds",
            "Duration in seconds that GPU fragmentation has been above 30%",
            ["device"],
            registry=r,
        )
        self.gpu_temperature = Gauge(
            "ultrasound_gpu_temperature_celsius",
            "GPU temperature in Celsius",
            ["device"],
            registry=r,
        )
        self.gpu_overheat_duration_seconds = Gauge(
            "ultrasound_gpu_overheat_duration_seconds",
            "GPU sustained over-temperature duration (>90C)",
            ["device"],
            registry=r,
        )

    def _init_model_metrics(self) -> None:
        r = self._registry
        self.model_hot_reload_success_rate = Gauge(
            "ultrasound_model_hot_reload_success_rate",
            "Model hot-reload success rate",
            registry=r,
        )
        self.model_hot_reload_duration = Histogram(
            "ultrasound_model_hot_reload_duration_seconds",
            "Model hot-reload duration",
            buckets=[5.0, 10.0, 20.0, 30.0, 60.0],
            registry=r,
        )

    def _init_cache_metrics(self) -> None:
        r = self._registry
        self.cache_hit_rate = Gauge(
            "ultrasound_cache_hit_rate",
            "Knowledge cache hit rate",
            registry=r,
        )
        self.cache_bloom_filter_interception_rate = Gauge(
            "ultrasound_cache_bloom_filter_interception_rate",
            "Bloom filter interception rate",
            registry=r,
        )
        self.cache_null_value_hit_rate = Gauge(
            "ultrasound_cache_null_value_hit_rate",
            "Null value cache hit rate",
            registry=r,
        )
        self.cache_db_penetration_rate = Gauge(
            "ultrasound_cache_db_penetration_rate",
            "Database penetration rate",
            registry=r,
        )
        self.cache_mutex_lock_success_rate = Gauge(
            "ultrasound_cache_mutex_lock_success_rate",
            "Mutex lock success rate",
            registry=r,
        )
        self.cache_mutex_lock_timeout_count = Gauge(
            "ultrasound_cache_mutex_lock_timeout_count",
            "Mutex lock timeout count (hourly)",
            registry=r,
        )
        self.cache_hotkey_permanent_coverage = Gauge(
            "ultrasound_cache_hotkey_permanent_coverage",
            "Hot key permanent coverage",
            registry=r,
        )
        self.cache_ttl_randomization_offset_percent = Gauge(
            "ultrasound_cache_ttl_randomization_offset_percent",
            "TTL randomization offset percent",
            registry=r,
        )
        self.cache_simultaneous_expiry_5min = Gauge(
            "ultrasound_cache_simultaneous_expiry_5min",
            "Keys expiring in 5-minute window",
            registry=r,
        )
        self.cache_warmup_completion_rate = Gauge(
            "ultrasound_cache_warmup_completion_rate",
            "Cache warmup completion rate",
            registry=r,
        )
        self.cache_warmup_duration_seconds = Gauge(
            "ultrasound_cache_warmup_duration_seconds",
            "Cache warmup duration",
            registry=r,
        )
        self.cache_write_latency_p99_seconds = Gauge(
            "ultrasound_cache_write_latency_p99_seconds",
            "Cache write P99 latency",
            registry=r,
        )
        self.cache_read_latency_p99_seconds = Gauge(
            "ultrasound_cache_read_latency_p99_seconds",
            "Cache read P99 latency",
            registry=r,
        )

    def _init_rag_metrics(self) -> None:
        r = self._registry
        self.crag_supplement_rate = Gauge(
            "ultrasound_crag_supplement_rate",
            "CRAG supplementary retrieval trigger rate",
            registry=r,
        )
        self.self_rag_retry_rate = Gauge(
            "ultrasound_self_rag_retry_rate",
            "Self-RAG retry rate",
            registry=r,
        )
        self.adaptive_rag_route = Counter(
            "ultrasound_adaptive_rag_route_total",
            "Adaptive RAG route distribution",
            ["route"],
            registry=r,
        )

    def _init_safety_metrics(self) -> None:
        r = self._registry
        self.consent_query_latency = Histogram(
            "ultrasound_consent_query_latency_seconds",
            "Consent status query latency",
            buckets=[0.01, 0.02, 0.03, 0.05, 0.1, 0.5],
            registry=r,
        )
        self.safety_gateway_health = Gauge(
            "ultrasound_safety_gateway_health",
            "SafetyGateway health status (1=healthy, 0=unhealthy)",
            registry=r,
        )
        self.desensitization_sync_delay = Gauge(
            "ultrasound_desensitization_sync_delay_seconds",
            "Desensitization rule sync delay",
            registry=r,
        )
        self.circuit_breaker_state = Gauge(
            "ultrasound_circuit_breaker_state",
            "Circuit breaker state (1=open, 0=closed)",
            ["service"],
            registry=r,
        )
        self.bm25_fallback_duration = Gauge(
            "ultrasound_bm25_fallback_duration_seconds",
            "BM25 fallback duration",
            registry=r,
        )
        self.dual_write_queue_backlog = Gauge(
            "ultrasound_dual_write_queue_backlog",
            "Dual-write compensation queue backlog",
            registry=r,
        )

    def _init_diagnosis_metrics(self) -> None:
        r = self._registry
        self.diagnosis_requests = Counter(
            "ultrasound_diagnosis_requests_total",
            "Total diagnosis requests",
            ["organ", "tier"],
            registry=r,
        )
        self.diagnosis_errors = Counter(
            "ultrasound_diagnosis_errors_total",
            "Total diagnosis errors",
            ["error_code"],
            registry=r,
        )

    def _init_infrastructure_metrics(self) -> None:
        r = self._registry
        self.redis_process_alive = Gauge(
            "ultrasound_redis_process_alive",
            "Redis process alive status (1=alive, 0=unreachable)",
            registry=r,
        )
        self.redis_container_restarts = Counter(
            "ultrasound_redis_container_restarts_total",
            "Redis container restart events",
            registry=r,
        )
        self.audit_log_degraded_writes = Counter(
            "ultrasound_audit_log_degraded_writes_total",
            "Audit log degraded write events",
            registry=r,
        )
        self.pg_connection_pool_utilization = Gauge(
            "ultrasound_pg_connection_pool_utilization",
            "PostgreSQL connection pool utilization ratio",
            registry=r,
        )
        self.pg_slow_queries = Counter(
            "ultrasound_pg_slow_queries_total",
            "PostgreSQL slow query count (threshold: 5min>3)",
            registry=r,
        )
        self.edge_nav_latency_seconds = Histogram(
            "ultrasound_edge_nav_latency_seconds",
            "Edge device navigation latency",
            buckets=[0.05, 0.06, 0.08, 0.1, 0.15, 0.2],
            registry=r,
        )
        self.edge_gpu_temperature_celsius = Gauge(
            "ultrasound_edge_gpu_temperature_celsius",
            "Edge device GPU temperature in Celsius",
            registry=r,
        )
        self.victoriametrics_process_resident_memory_bytes = Gauge(
            "ultrasound_process_resident_memory_bytes",
            "VictoriaMetrics process resident memory in bytes",
            registry=r,
        )
        self.consent_cache_warmup_latency_seconds = Histogram(
            "ultrasound_consent_cache_warmup_latency_seconds",
            "Consent status cache warmup latency (Redis recovery scenario)",
            buckets=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0],
            registry=r,
        )
