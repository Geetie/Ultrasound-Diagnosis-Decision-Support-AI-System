"""PHI 脱敏导出包装（A.47）：四类正则命中 + 官方纪律（失败不泄）+ 代理透传。

对应 Langfuse 官方 mask_otel_spans 实践的自托管映射——纯 OTel 链路在导出层
（exporter 包装）脱敏，敏感数据不离开进程；判读代码零改动。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.observability.tracing import PhiMaskingExporter, _mask_phi_attributes, _mask_phi_text


class TestMaskPhiText:
    def test_phone_masked(self):
        assert "13812345678" not in _mask_phi_text("联系 13812345678 确认")
        assert "[手机号]" in _mask_phi_text("联系 13812345678 确认")

    def test_id_card_masked_before_phone(self):
        # 长模式优先：18 位证件不能被手机号正则吃掉前 11 位（A.47 冒烟钉子）
        out = _mask_phi_text("身份证 110101199003077758")
        assert "110101199003077758" not in out
        assert "[证件号]" in out

    def test_person_name_masked_by_sg_pattern(self):
        # SG 同源人名正则（1~4 汉字 + 医学称谓后缀）—— 口径与 SG 一致
        out = _mask_phi_text("主刀：张伟医生")
        assert "张伟" not in out
        assert "[人名]" in out

    def test_non_phi_untouched(self):
        text = "甲状腺右叶低回声结节 TR5 建议穿刺"
        assert _mask_phi_text(text) == text


class TestMaskPhiAttributes:
    def test_string_values_masked(self):
        out = _mask_phi_attributes(
            {"gen_ai.completion": "联系 13812345678", "organ": "thyroid"}
        )
        assert "13812345678" not in out["gen_ai.completion"]
        assert out["organ"] == "thyroid"  # 非 PHI 字段原样

    def test_string_sequence_masked(self):
        out = _mask_phi_attributes({"chunks": ["张伟医生的报告", "正常文本"]})
        assert all("张伟" not in v for v in out["chunks"])

    def test_failure_never_leaks(self, monkeypatch):
        # 官方纪律：mask 抛异常会丢整批导出 —— 本实现 _mask_phi_text 内部
        # try/except 兜底（失败返回占位符），不抛、不泄
        import app.observability.tracing as tr

        class BadPattern:
            def sub(self, repl, string):
                raise RuntimeError("boom")

        monkeypatch.setattr(tr, "_PHI_PATTERNS", [(BadPattern(), "[x]")])
        assert _mask_phi_text("患者张伟医生") == "[MASK_FAILED]"

    def test_empty_attributes(self):
        assert _mask_phi_attributes(None) is None
        assert _mask_phi_attributes({}) is None


class TestPhiMaskingExporter:
    def _span(self, attributes: dict) -> SimpleNamespace:
        return SimpleNamespace(
            name="diagnosis.output",
            attributes=attributes,
            context=SimpleNamespace(trace_id=1, span_id=2),
            span_id=2,
            trace_flags=SimpleNamespace(is_sampled=True),
            parent=None,
            resource={},
            instrumentation_scope=None,
            start_time=1,
            end_time=2,
            status=None,
            events=[],
            links=[],
        )

    def test_export_masks_attributes(self):
        captured: list = []

        class Inner:
            def export(self, spans):
                captured.extend(spans)
                from opentelemetry.sdk.trace.export import SpanExportResult

                return SpanExportResult.SUCCESS

        exporter = PhiMaskingExporter(Inner())
        exporter.export([self._span({"gen_ai.completion": "患者 13812345678"})])
        attrs = captured[0].attributes
        assert "13812345678" not in attrs["gen_ai.completion"]

    def test_non_string_attributes_pass_through(self):
        captured: list = []

        class Inner:
            def export(self, spans):
                captured.extend(spans)
                from opentelemetry.sdk.trace.export import SpanExportResult

                return SpanExportResult.SUCCESS

        PhiMaskingExporter(Inner()).export([self._span({"timings_ms": 123, "ok": True})])
        attrs = captured[0].attributes
        assert attrs["timings_ms"] == 123 and attrs["ok"] is True
