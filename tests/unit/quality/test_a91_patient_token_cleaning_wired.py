"""A.91 守卫：语料脱敏标记**在生成侧就要清洗**（不是事后洗库）。

## 这条接线补的是什么（真机实测暴露的"治标不治本"）

时间线：
1. **A.77（2026-10-21）**：发现 `患者PATIENT_001` 是**知识库语料脱敏占位符**
   （库里 22 个块含它），模型在"患者解释"段会抄出来。当时写好
   `detect_patient_token_leak()` 并明确"处置是清洗，不是拒收"——
   但**从此没有任何生产路径调用它**。
2. **A.89.4**：这个串还会让安全网关误判成"患者姓名"→ 整条结论降级抑制
   （已修规则，但串本身还在）。
3. **A.90**：写 `p63` 手工清洗历史 47 条 → 库内 `PATIENT_` 归零。
4. **A.91（本条）**：清洗后立刻跑一条**新**判读，正文里 `PATIENT_` **又出现了**：
   `... 证据: 无 患者PATIENT_001: PATIENT_061医生建议进行进一步检查 ...`

**结论**：只洗历史 = 治标。生成侧不阻断，每次新判读都会重新污染。

## 为什么是"清洗"而不是"拒收"（这个区分很关键）

| 缺陷类型 | 特征 | 正确处置 |
|---|---|---|
| 模板回显 | `chunk_a1b2` 占位证据 ID / 重复 `# 输出` 块 | **整条拒收**（内容本身就是假的） |
| 语料标记外泄 | 有真实分级、有鉴别诊断，只是把 `PATIENT_001` 抄进患者解释 | **清洗**（内容可用） |

A.77 的血泪：把后者当前者 → **误删 415 条真结论**。

## 本守卫钉什么

* `clean_patient_token_leak` 三种形态都清、**幂等**、**不改分级/置信度**；
* 清洗后绝不残留 `PATIENT_`；
* 生产链路（`specialist/base.py` 4c 步）**真的调用**它（反证：删掉调用即红）；
* 清洗有留痕（`specialist_patient_token_leak_cleaned`），不静默改正文；
* `detect_patient_token_leak` 不再"零调用方"。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.inference.output_guard import (
    clean_patient_token_leak,
    detect_patient_token_leak,
)

pytestmark = [pytest.mark.track_quality]

_REPO = Path(__file__).resolve().parents[3]
_SPECIALIST = _REPO / "app" / "agents" / "specialist" / "base.py"


class TestCleanPatientTokenLeak:
    def test_patient_prefix_with_colon(self):
        got, hits = clean_patient_token_leak("患者PATIENT_001: 解释: 请复查")
        assert got.startswith("患者：")
        assert "PATIENT_" not in got
        assert hits >= 1

    def test_token_as_title_prefix(self):
        got, hits = clean_patient_token_leak("安抚: PATIENT_061医生建议活检")
        assert "PATIENT_" not in got
        assert "医生建议活检" in got
        assert hits >= 1

    def test_bare_token_becomes_patient(self):
        got, _ = clean_patient_token_leak("见 PATIENT_007 记录")
        assert "PATIENT_" not in got
        assert "患者" in got

    def test_keeps_grading_and_confidence(self):
        """清洗**绝不动**分级与置信度（那是临床结论，不是标记）。"""
        src = "甲状腺 · TR4 — 分级: TR4 总分: 4 置信度: 0.9 患者PATIENT_001: 解释: x"
        got, _ = clean_patient_token_leak(src)
        assert "分级: TR4" in got
        assert "总分: 4" in got
        assert "置信度: 0.9" in got
        assert "PATIENT_" not in got

    def test_idempotent(self):
        src = "患者PATIENT_001: 解释: x 安抚: PATIENT_061医生建议"
        once, _ = clean_patient_token_leak(src)
        twice, hits2 = clean_patient_token_leak(once)
        assert once == twice, "清洗不幂等 → 重复处理会改坏正文"
        assert hits2 == 0

    def test_clean_input_untouched(self):
        src = "甲状腺 · TR2 分级: TR2 置信度: 0.85"
        got, hits = clean_patient_token_leak(src)
        assert got == src
        assert hits == 0


class TestDetectStillWorks:
    @pytest.mark.parametrize(
        "text",
        ["患者PATIENT_001: x", "PATIENT_010医生", "见 PATIENT_003"],
    )
    def test_detects(self, text):
        assert detect_patient_token_leak(text) is True

    def test_clean_text_not_detected(self):
        assert detect_patient_token_leak("甲状腺 · TR2 分级: TR2") is False


class TestProductionWiring:
    """**核心**：这条清洗必须真的在生成侧被调用（否则又是一处"实现了没接线"）。"""

    def test_specialist_imports_and_calls_cleaner(self):
        """反证腿：删掉 4c 步的调用 → 本用例红。"""
        src = _SPECIALIST.read_text(encoding="utf-8")
        assert "clean_patient_token_leak" in src, "没 import 清洗函数"
        assert "detect_patient_token_leak(llm_text)" in src, "没有在生成侧检测"
        assert "llm_text = cleaned" in src, "检测到了但没把清洗结果接回去"

    def test_cleaning_is_traced(self):
        src = _SPECIALIST.read_text(encoding="utf-8")
        assert "specialist_patient_token_leak_cleaned" in src, (
            "清洗必须留痕（不许静默改正文）"
        )
        assert "patient_token_leak_cleaned: {" in src or "patient_token_leak_cleaned:" in src

    def test_cleaner_runs_after_echo_rejection(self):
        """清洗必须在 4b（拒收模板回显）**之后** —— 否则会把该拒收的也洗掉。"""
        src = _SPECIALIST.read_text(encoding="utf-8")
        i_echo = src.index("echo_reason = detect_prompt_echo(llm_text)")
        i_clean = src.index("detect_patient_token_leak(llm_text)")
        assert i_echo < i_clean, (
            "清洗在拒收之前？那会把'模板回显'也洗成看似正常的文本，绕过拒收"
        )

    def test_cleaner_runs_before_parse(self):
        """清洗必须在解析（`_parse_response`）**之前** —— 否则脏标记会进分级抽取。"""
        src = _SPECIALIST.read_text(encoding="utf-8")
        i_clean = src.index("detect_patient_token_leak(llm_text)")
        i_parse = src.index("self._parse_response(llm_text")
        assert i_clean < i_parse, "清洗晚于解析 → 脏标记会先被分级抽取读到"


class TestNoTokenRemainsInDbAfterPipeline:
    """真机侧的证据登记（不是单测能跑的，用契约断言记录口径）。"""

    def test_cleaning_covers_all_forms_seen_live(self):
        """真机实际出现过的两种形态都必须被清掉。"""
        live = (
            "甲状腺 · TR4 — 分级: 无法判断 置信度: 0.0 理由: 当前信息不足。 证据: 无 "
            "患者PATIENT_001: PATIENT_061医生建议进行进一步检查"
        )
        got, hits = clean_patient_token_leak(live)
        assert not re.search(r"PATIENT_\d+", got), f"仍有残留: {got!r}"
        assert hits >= 2


class TestWhyCRAGWasNotPatched:
    """记录一次**被真机证据否定的修复**：CRAG 不是 `PATIENT_` 的来源。

    ## 我一度给 `fusion/crag.py` 加了清洗，后来删掉了 —— 原因如下

    推理链（当时看起来合理）：
    1. `specialist/base.py` 4c 加了清洗，真机跑新判读，`PATIENT_` 仍在；
    2. 日志显示 `crag_triggered=True / crag_recovered=True`，且
       `session_id=crag_reinvoke`（CRAG 真的重跑了专科 LLM）；
    3. 于是推断"CRAG 重生成的正文绕过了 4c"，给它也加了清洗。

    **真机探针否定了这个推断**（三层探针同时打）：
    * `CRAG_PROBE_ENTRY   orig_has_token=False`
    * `CRAG_PROBE_RETURN  has_token=False`      ← CRAG 返回时**是干净的**
    * `DBPROBE_CONTENT    has_token=False`      ← 落库前一刻**也是干净的**

    继续查才发现真相：**`RAWLLM_PROBE raw_has_token=False`** ——
    模型原始输出干净；污染发生在 `findings` 组装**之后**的 L2 融合层脱敏，
    而脱敏引擎的两条 `patient_name*` 规则会把「请结合图」/「咨询主治」
    当患者姓名，**替换成 `PATIENT_00N`**。

    结论：CRAG 不需要这段清洗，加了就是**没有证据的防御代码**
    （它会让人误以为"CRAG 是风险点"，掩盖真正的源头）。
    已删除；真正的修复在 `desensitization_rules.yaml`。

    ## 这条测试保留的意义

    这是一条"反向守卫"：它**记录了一个被推翻的假设**，防止后来者
    重走同样的弯路（看到 CRAG 触发就以为 CRAG 脏）。
    """

    def test_crag_is_not_patched_with_cleaner(self):
        """CRAG 里**不该**有清洗代码（有 = 又回到了被否定的假设）。"""
        src = (_REPO / "app" / "fusion" / "crag.py").read_text(encoding="utf-8")
        assert "clean_patient_token_leak" not in src, (
            "CRAG 又被加了清洗 —— 真机探针已证明 CRAG 返回时是干净的，"
            "真源在 desensitization_rules.yaml 的 patient_name* 规则"
        )

    def test_real_source_is_the_desens_rules(self):
        """真正的修复点在脱敏规则文件里（且有专门守卫）。"""
        rules = (_REPO / "desensitization_rules.yaml").read_text(encoding="utf-8")
        assert "patient_name" in rules
        guard = _REPO / "tests" / "unit" / "quality" / "test_a91_desens_must_not_mint_token.py"
        assert guard.is_file(), "缺脱敏规则守卫（真正源头没有守卫）"
