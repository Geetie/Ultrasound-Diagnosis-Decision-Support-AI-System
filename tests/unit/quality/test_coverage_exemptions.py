"""覆盖率豁免清单守卫（quality-debt）。

为什么需要它：把"没覆盖"和"离线无法覆盖"分开讲，是负责的做法；但这类清单一旦没有
约束，就会退化成"哪里测不动就往清单里塞一条"的借口。所以清单必须满足：

1. 每条都指向**真实存在**的模块文件（防止写了不存在的路径凑数）；
2. 类别必须在白名单内，且必须解释**离线已经覆盖到哪一层**（不谈边界的条目一律不收）；
3. 条目数受棘轮约束——新增条目必须显式改这里的常量，不能悄悄变多；
4. 条目覆盖率一旦达到目标（>=90%）就必须移出清单（防止过期条目长期占位）；
5. Markdown 说明文档必须列出清单里的每个模块（防止文档与数据脱节）。

注意：本清单**不参与降低覆盖率门禁**——CI 的门禁阈值对全库计算，与这份清单无关。
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = REPO_ROOT / "docs" / "合规" / "覆盖率豁免清单.json"
DOC = REPO_ROOT / "docs" / "合规" / "覆盖率豁免清单.md"

ALLOWED_CATEGORIES = {
    "external_service_semantics",
    "hardware_hsm",
    "external_object_storage",
    "real_database",
    "hardware_gpu",
}

# 棘轮：新增条目必须在这里显式上调（并说明为什么），避免清单被顺手扩张。
# 8 -> 6：`dicom_storage.py` 补测到 95%（达标后按规则移出）、
#         `sustained_load_test.py` 改记为 excluded_by_design（纯现场脚本，无离线层可言）。
EXPECTED_ENTRY_COUNT = 6


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


class TestRegistryIntegrity:
    def test_registry_parses(self, registry):
        assert isinstance(registry["entries"], list)
        assert registry["entries"], "空清单等于没有清单"

    def test_doc_exists(self):
        assert DOC.exists(), "清单必须有配套说明文档"

    def test_entry_count_is_ratcheted(self, registry):
        assert len(registry["entries"]) == EXPECTED_ENTRY_COUNT, (
            "条目数变化必须显式修改 EXPECTED_ENTRY_COUNT 并说明理由"
        )

    def test_policy_states_gates_are_not_lowered(self, registry):
        """清单不得被当成降低门禁的理由，这条写进数据里而不是只写在文档里。"""
        assert "不参与降低覆盖率门禁" in registry["policy"]


class TestEntries:
    def test_modules_exist(self, registry):
        missing = [
            entry["module"]
            for entry in registry["entries"]
            if not (REPO_ROOT / entry["module"]).exists()
        ]

        assert missing == [], f"清单里有不存在的模块：{missing}"

    def test_categories_are_whitelisted(self, registry):
        unknown = {
            entry["category"] for entry in registry["entries"]
        } - ALLOWED_CATEGORIES

        assert unknown == set(), (
            f"未登记的类别：{unknown}。若确需新增类别，请同时更新白名单与文档说明。"
        )

    def test_every_entry_states_offline_boundary(self, registry):
        """每条都要说清"离线已经覆盖到哪一层"，否则就是个逃避口。"""
        thin = [
            entry["module"]
            for entry in registry["entries"]
            if len(entry.get("offline_covered", "")) < 20
        ]

        assert thin == [], f"以下条目未说明离线覆盖边界：{thin}"

    def test_every_entry_has_field_verification_action(self, registry):
        """真机/外部依赖项必须给出可执行的现场验证动作，而不是只写"需真机"。"""
        lacking = [
            entry["module"]
            for entry in registry["entries"]
            if entry["field_verification"] and len(entry.get("next_action", "")) < 10
        ]

        assert lacking == [], f"以下真机项缺现场验证动作：{lacking}"

    def test_reasons_are_substantive(self, registry):
        thin = [
            entry["module"]
            for entry in registry["entries"]
            if len(entry.get("reason", "")) < 40
        ]

        assert thin == [], f"以下条目的理由过于简略：{thin}"

    def test_entries_are_below_target(self, registry):
        """已达标（>=90%）的模块不该继续留在清单里占位。

        达标意味着"离线已经能覆盖到位"，若还留着，说明清单在替真实缺口打掩护。
        """
        stale = [
            f"{entry['module']}（{entry['percent']}%）"
            for entry in registry["entries"]
            if entry["percent"] >= registry["target_core_percent"]
        ]

        assert stale == [], f"以下模块已达标，应移出豁免清单：{stale}"

    def test_percentages_are_plausible(self, registry):
        implausible = [
            entry["module"]
            for entry in registry["entries"]
            if not 0.0 <= entry["percent"] <= 100.0 or entry["missing_lines"] <= 0
        ]

        assert implausible == [], f"以下条目的覆盖率或缺口数字不合理：{implausible}"


class TestDocSync:
    def test_doc_mentions_every_registered_module(self, registry):
        """文档与数据必须同步：两者脱节会让读者拿到错误结论。"""
        text = DOC.read_text(encoding="utf-8")

        absent = [
            entry["module"] for entry in registry["entries"] if entry["module"] not in text
        ]

        assert absent == [], f"说明文档未列出以下模块：{absent}"

    def test_doc_declares_source_of_truth(self):
        text = DOC.read_text(encoding="utf-8")

        assert "覆盖率豁免清单.json" in text, "文档必须指明数据源文件"
