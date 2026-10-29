"""05-3 S1：审计脚本纯函数口径测试（不连库）。

验收判据（04 号计划 §05-3 ⑤）：
- 10 例（4 漏诊 + 6 已提示）→ missed=4, denominator=10, missed_rate=0.4；
- 无最终诊断 → not_measured_no_final_diagnosis（不输出 0）；
- registry 中缺 source 或 source 非法的条目必须被拒收并计数（两条独立证据）。
"""
import pytest

from scripts.audit.p76_missed_rate_audit import compute_audit

pytestmark = [pytest.mark.track_data]


class TestComputeAudit:
    def test_正常口径(self):
        registry = [
            {"request_id": f"r{i}", "final_diagnosis_source": "pathology"}
            for i in range(4)
        ]
        out = compute_audit(denominator=10, total=10, registry=registry)
        assert out["status"] == "measured"
        assert out["missed"] == 4
        assert out["missed_rate"] == 0.4
        assert out["missing_final_diagnosis_rate"] == 0.0

    def test_无最终诊断_不输出0(self):
        out = compute_audit(denominator=0, total=1071, registry=[])
        assert out["status"] == "not_measured_no_final_diagnosis"
        assert out["missed_rate"] is None
        assert out["missed_rate"] != 0

    def test_缺失率如实上报(self):
        registry = [{"request_id": "r1", "final_diagnosis_source": "followup"}]
        out = compute_audit(denominator=2, total=8, registry=registry)
        assert out["missing_final_diagnosis_rate"] == 0.75

    def test_缺金标准来源的登记被拒收(self):
        """单标记不得定罪：没有 source 的登记不算分子，且拒绝数可见。"""
        registry = [
            {"request_id": "r1"},                                  # 缺 source
            {"request_id": "r2", "final_diagnosis_source": "guess"},  # 非法 source
            {"request_id": "r3", "final_diagnosis_source": "pathology"},
        ]
        out = compute_audit(denominator=10, total=10, registry=registry)
        assert out["missed"] == 1
        assert out["rejected_registry_entries"] == 2
